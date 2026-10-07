"""Drives, a data directory, rclone and an HTTP client, for the settings-GUI tests.

Everything goes through the routes, so a test sees what the settings section and
the dashboard see. The stored file lives under the patched `config.DATA_DIR`.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

import app.auth as auth
import app.config as core_config
from addons.cloud_sync import router as router_module
from addons.cloud_sync import service

BASE = "/api/addons/cloud-sync"
EMPTY_CONFIG = {"schedule": None, "timezone": None, "max_delete": 200, "mappings": []}


def body(**overrides) -> dict:
    out = dict(EMPTY_CONFIG)
    out.update(overrides)
    return out


def row(drive: str, path: str, remote: str) -> dict:
    return {"drive": drive, "path": path, "remote": remote}


def without_message(errors: list[dict]) -> list[dict]:
    return [{k: v for k, v in e.items() if k != "message"} for e in errors]


def err(field: str, code: str, mapping: int | None = None, other: int | None = None) -> dict:
    return {"mapping": mapping, "other": other, "field": field, "code": code}


class ListProc:
    """`rclone listremotes` as the addon's subprocess call sees it."""

    def __init__(self, world: "SettingsWorld") -> None:
        self.world = world
        mode = world.listremotes
        self.hang = mode == "hang"
        self.returncode = 1 if mode == "fail" else 0
        if mode == "fail":
            self._out, self._err = b"", b"Failed to load config file\n"
        else:
            self._out = "".join(f"{r}\n" for r in world.remotes).encode()
            self._err = b""
        self._killed = asyncio.Event()
        if self.hang:
            self.returncode = None
        outer = self

        class _Stream:
            async def read(self, n: int = -1) -> bytes:
                if outer.hang:
                    await outer._killed.wait()
                    return b""
                return outer._out

        self.stdout = _Stream()
        self.stderr = _Stream()

    async def communicate(self, input=None):
        if self.hang:
            await self._killed.wait()
            return b"", b""
        return self._out, self._err

    async def wait(self) -> int:
        if self.hang:
            await self._killed.wait()
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9
        self.world.killed += 1
        self._killed.set()

    def terminate(self) -> None:
        self.kill()


class SyncProc:
    """An `rclone sync` run that stays alive while its source is held."""

    def __init__(self, world: "SettingsWorld", source: str) -> None:
        self.world = world
        self.source = source
        self.returncode = world.rclone_returncode
        self._release = world.holds.get(source)

    @property
    def stderr(self):
        async def lines():
            if self._release is not None:
                await self._release.wait()
            for line in self.world.rclone_lines:
                yield line

        return lines()

    async def wait(self) -> int:
        if self._release is not None:
            await self._release.wait()
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15
        self.world.terminated.append(self.source)
        if self._release is not None:
            self._release.set()


class SettingsWorld:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.data_dir = tmp_path / "data"
        self.data_dir.mkdir()
        self.drive_dir = tmp_path / "drives"
        self.drive_dir.mkdir()
        self.drives: list[dict] = []
        self.drives_error: Exception | None = None
        self.remotes: list[str] = ["gdrive:", "other:", "x:"]
        self.listremotes = "ok"  # ok | fail | missing | hang
        self.listremote_calls = 0
        self.killed = 0
        self.launches: list[tuple] = []
        self.holds: dict[str, asyncio.Event] = {}
        self.terminated: list[str] = []
        self.rclone_lines: list[bytes] = []
        self.rclone_returncode = 0
        self.manager = None
        self.client: httpx.AsyncClient | None = None
        self.planted: list[Path] = []

    # --- drives -------------------------------------------------------------
    def add_drive(self, name: str, *, enabled: bool = True, files: int = 1) -> Path:
        path = self.drive_dir / name
        path.mkdir()
        for i in range(files):
            (path / f"f{i}.txt").write_text("x")
        entry: dict = {"name": name, "path": str(path)}
        if not enabled:
            entry["addons"] = {"cloud-sync": False}
        self.drives.append(entry)
        return path

    def add_folder(self, drive: str, rel: str, *, files: int = 1) -> Path:
        path = self.drive_root(drive) / rel
        path.mkdir(parents=True)
        for i in range(files):
            (path / f"g{i}.txt").write_text("x")
        return path

    def drive_root(self, name: str) -> Path:
        return next(Path(d["path"]) for d in self.drives if d["name"] == name)

    # --- stored file --------------------------------------------------------
    @property
    def config_dir(self) -> Path:
        return self.data_dir / "addons" / "cloud-sync"

    @property
    def config_path(self) -> Path:
        return self.config_dir / "sync-config.json"

    def write_file(self, content) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        self.config_path.write_text(text, encoding="utf-8")

    def stored(self) -> dict:
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def stray_files(self) -> list[str]:
        if not self.config_dir.exists():
            return []
        return sorted(p.name for p in self.config_dir.iterdir() if p.name != "sync-config.json")

    def plant_legacy(self, monkeypatch, content: dict) -> list[Path]:
        """Put a valid file wherever the addon used to read it from."""
        legacy_dir = self.tmp_path / "legacy"
        legacy_dir.mkdir(exist_ok=True)
        (legacy_dir / "sync-config.json").write_text(json.dumps(content))
        monkeypatch.setattr(service, "_RESOLVED_DIR", legacy_dir, raising=False)
        package_dir = Path(service.__file__).resolve().parent
        planted = []
        for directory in [package_dir, package_dir.parent, Path.cwd()]:
            target = directory / "sync-config.json"
            if not target.exists():
                target.write_text(json.dumps(content))
                planted.append(target)
        self.planted.extend(planted)
        return planted

    # --- scheduler ----------------------------------------------------------
    @staticmethod
    def scheduler_loops() -> list[asyncio.Task]:
        return [
            t for t in asyncio.all_tasks()
            if not t.done() and "scheduler" in getattr(t.get_coro(), "__qualname__", "")
        ]

    # --- runs ---------------------------------------------------------------
    def hold(self, source: Path | str) -> asyncio.Event:
        event = asyncio.Event()
        self.holds[str(source)] = event
        return event

    def launched_sources(self) -> list[str]:
        return [argv[2] for argv in self.launches]

    # --- HTTP ---------------------------------------------------------------
    async def get_config(self) -> httpx.Response:
        return await self.client.get(f"{BASE}/config")

    async def put_config(self, payload) -> httpx.Response:
        if isinstance(payload, (bytes, str)):
            return await self.client.put(
                f"{BASE}/config",
                content=payload,
                headers={"Content-Type": "application/json"},
            )
        return await self.client.put(f"{BASE}/config", json=payload)

    async def status(self) -> dict:
        response = await self.client.get(f"{BASE}/status")
        assert response.status_code == 200, response.text
        return response.json()

    async def wait_until(self, predicate, timeout: float = 5.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not await predicate():
            if loop.time() > deadline:
                raise AssertionError("condition not reached")
            await asyncio.sleep(0.01)

    async def wait_not_syncing(self, drive: str, path: str) -> None:
        async def done() -> bool:
            entries = [
                d for d in (await self.status())["drives"]
                if d["drive"] == drive and d["path"] == path
            ]
            return all(d["status"] != "syncing" for d in entries)

        await self.wait_until(done)


@pytest.fixture()
async def sworld(monkeypatch, tmp_path, broadcasts):
    w = SettingsWorld(tmp_path)

    def load_drives():
        if w.drives_error is not None:
            raise w.drives_error
        return [dict(d) for d in w.drives]

    async def fake_exec(*argv, **kwargs):
        if "listremotes" in argv:
            w.listremote_calls += 1
            if w.listremotes == "missing":
                raise FileNotFoundError("rclone")
            return ListProc(w)
        w.launches.append(argv)
        return SyncProc(w, argv[2])

    manager = service.SyncManager()
    monkeypatch.setattr(core_config, "DATA_DIR", w.data_dir)
    monkeypatch.setattr(core_config, "load_drives", load_drives)
    monkeypatch.setattr(service, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(service.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(service, "sync_manager", manager)
    monkeypatch.setattr(router_module, "sync_manager", manager)
    w.manager = manager

    api = FastAPI()
    api.include_router(router_module.router)
    api.dependency_overrides[auth.require_admin] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url="http://test"
    ) as client:
        w.client = client
        try:
            yield w
        finally:
            for target in w.planted:
                target.unlink(missing_ok=True)
            for event in w.holds.values():
                event.set()
            current = asyncio.current_task()
            pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
