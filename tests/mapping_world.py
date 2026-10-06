"""A sync-config file, drives and a stand-in for rclone, for folder-mapping tests.

The config is written to disk and read back by the manager on every use, so a
test exercises the same loading and validation a hand-edited file goes through.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from pathlib import Path

import pytest

from addons.cloud_sync import service

ABSENT = object()

STATS_LINE = (
    json.dumps(
        {
            "level": "info",
            "msg": "stats",
            "stats": {
                "bytes": 50,
                "totalBytes": 100,
                "transfers": 1,
                "totalTransfers": 2,
                "eta": 5,
                "speed": 10,
                "checks": 0,
                "totalChecks": 0,
                "errors": 0,
                "elapsedTime": 1.0,
            },
        }
    ).encode()
    + b"\n"
)

LIMIT_LINE = (
    b'{"level":"error","msg":"Got fatal error on delete: '
    b'--max-delete threshold reached","object":"f1"}\n'
)

SOURCE_EMPTY_MESSAGE = (
    "The folder is missing or empty, so nothing was synced. "
    "Check that the drive is mounted and the folder exists."
)


def folder_log_name(safe_drive: str, drive: str, path: str) -> str:
    digest = hashlib.sha256(
        drive.encode("utf-8") + b"\0" + path.encode("utf-8")
    ).hexdigest()[:16]
    return f"{safe_drive}.{digest}.log"


class FakeProc:
    def __init__(self, lines: list[bytes], returncode: int, on_terminate=None) -> None:
        self._lines = lines
        self.returncode = returncode
        self._on_terminate = on_terminate

    @property
    def stderr(self):
        async def lines():
            for line in self._lines:
                yield line

        return lines()

    async def wait(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15
        if self._on_terminate is not None:
            self._on_terminate()


class World:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.drive_dir = tmp_path / "drives"
        self.drive_dir.mkdir()
        self.config_dir = tmp_path / "conf"
        self.config_dir.mkdir()
        self.log_dir = tmp_path / "logs"
        self.drives: dict[str, Path] = {}
        self.policy: dict[str, object] = {}
        self.mappings: list[dict] = []
        self.schedule: str | None = None
        self.max_delete = 200
        self.launches: list[tuple] = []
        self.rclone_lines: list[bytes] = []
        self.rclone_returncode = 0
        self.hold: dict[str, threading.Event] = {}
        self.terminated: list[str] = []
        self.manager = None

    def add_drive(self, name: str, *, files: int = 1, policy: object = True) -> Path:
        path = self.drive_dir / name
        path.mkdir()
        for i in range(files):
            (path / f"f{i}.txt").write_text("x")
        self.drives[name] = path
        self.policy[name] = policy
        return path

    def add_folder(self, drive: str, rel: str, *, files: int = 1) -> Path:
        path = self.drives[drive] / rel
        path.mkdir(parents=True)
        for i in range(files):
            (path / f"g{i}.txt").write_text("x")
        return path

    def map(self, drive: str, path: object = ABSENT, remote: str | None = None) -> None:
        entry: dict = {"drive": drive}
        if path is not ABSENT:
            entry["path"] = path
        entry["remote"] = remote or f"r:m{len(self.mappings)}"
        self.mappings.append(entry)
        self.write_config()

    def write_config(self) -> None:
        body: dict = {"mappings": self.mappings, "max_delete": self.max_delete}
        if self.schedule is not None:
            body["schedule"] = self.schedule
        self.write_raw(json.dumps(body, ensure_ascii=False))

    def write_raw(self, text: str) -> None:
        (self.config_dir / "sync-config.json").write_text(text, encoding="utf-8")

    def sources(self) -> list[str]:
        return [argv[2] for argv in self.launches]

    def entry(self, drive: str, path: str):
        matches = [
            d for d in self.manager.get_status().drives
            if d.drive == drive and d.path == path
        ]
        assert len(matches) == 1, f"expected one status entry for {(drive, path)!r}"
        return matches[0]

    def pairs(self) -> list[tuple[str, str]]:
        return [(d.drive, d.path) for d in self.manager.get_status().drives]

    def hold_launch(self, source: str) -> threading.Event:
        """Keep the rclone run for `source` alive until the returned event is set."""
        event = threading.Event()
        self.hold[source] = event
        return event


@pytest.fixture()
def mworld(monkeypatch, tmp_path, manager_under_test):
    w = World(tmp_path)

    def get_drive_path(name):
        if name not in w.drives:
            raise ValueError(name)
        return w.drives[name]

    def is_enabled(drive, addon, feature):
        value = w.policy[drive]
        if isinstance(value, Exception):
            raise value
        return value

    async def fake_exec(*argv, **kwargs):
        w.launches.append(argv)
        source = argv[2]
        hold = w.hold.get(source)
        if hold is not None:
            await asyncio.to_thread(hold.wait, 5)
        return FakeProc(
            list(w.rclone_lines),
            w.rclone_returncode,
            on_terminate=lambda: w.terminated.append(source),
        )

    monkeypatch.setattr(service.config, "get_drive_path", get_drive_path)
    monkeypatch.setattr(service.config, "is_addon_feature_enabled", is_enabled)
    monkeypatch.setattr(service.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(service, "LOG_DIR", w.log_dir)
    monkeypatch.setattr(service, "_RESOLVED_DIR", w.config_dir)
    w.write_config()
    w.manager = manager_under_test
    return w


async def settle() -> None:
    others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    await asyncio.gather(*others)
