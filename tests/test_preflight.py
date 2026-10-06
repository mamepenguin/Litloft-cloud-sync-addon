"""A sync never reaches rclone when it should not run, and carries the delete cap when it does."""
from __future__ import annotations

import asyncio
import threading

import pytest

from addons.cloud_sync import service
from addons.cloud_sync.schemas import (
    SyncConfig,
    SyncDriveStatus,
    SyncMapping,
    SyncResult,
)
from addons.cloud_sync.service import PolicyBlocked

LIMIT_LINE = (
    b'{"level":"error","msg":"Got fatal error on delete: '
    b'--max-delete threshold reached","object":"f1"}\n'
)


class _FakeProc:
    def __init__(self, lines: list[bytes], returncode: int) -> None:
        self._lines = lines
        self.returncode = returncode

    @property
    def stderr(self):
        async def lines():
            for line in self._lines:
                yield line

        return lines()

    async def wait(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        pass


class World:
    """Drives, policy and a stand-in for rclone, with a record of every launch."""

    def __init__(self, tmp_path) -> None:
        self.tmp_path = tmp_path
        self.drives: dict[str, object] = {}
        self.policy: dict[str, object] = {}
        self.policy_asked: list[tuple] = []
        self.launches: list[tuple] = []
        self.rclone_lines: list[bytes] = []
        self.rclone_returncode = 0
        self.max_delete = 200
        self.mapped: list[str] = []

    def add_drive(self, name: str, *, files: int = 1, policy: object = True) -> None:
        path = self.tmp_path / name
        path.mkdir()
        for i in range(files):
            (path / f"f{i}.txt").write_text("x")
        self.drives[name] = path
        self.policy[name] = policy
        self.mapped.append(name)

    def config(self) -> SyncConfig:
        return SyncConfig(
            max_delete=self.max_delete,
            mappings=[SyncMapping(drive=d, remote=f"r:{d}") for d in self.mapped],
        )


@pytest.fixture()
def world(monkeypatch, tmp_path, manager_under_test):
    w = World(tmp_path)

    def get_drive_path(name):
        if name not in w.drives:
            raise ValueError(name)
        return w.drives[name]

    def is_enabled(drive, addon, feature):
        w.policy_asked.append((drive, addon, feature))
        value = w.policy[drive]
        if isinstance(value, Exception):
            raise value
        return value

    async def fake_exec(*argv, **kwargs):
        w.launches.append(argv)
        return _FakeProc(w.rclone_lines, w.rclone_returncode)

    monkeypatch.setattr(service.config, "get_drive_path", get_drive_path)
    monkeypatch.setattr(service.config, "is_addon_feature_enabled", is_enabled)
    monkeypatch.setattr(service.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(service, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(manager_under_test, "_load_config", w.config)
    w.manager = manager_under_test
    return w


async def settle() -> None:
    others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    await asyncio.gather(*others)


OFF = [False, RuntimeError("drives.json unreadable")]


@pytest.mark.parametrize("policy", OFF, ids=["off", "lookup-raises"])
class TestPolicy:
    async def test_manual_start_is_refused_without_launching(self, world, policy):
        world.add_drive("Photos", policy=policy)

        with pytest.raises(PolicyBlocked):
            await world.manager.start_sync("Photos")
        await settle()

        assert world.launches == []
        assert "Photos" not in world.manager._status

    async def test_the_schedule_skips_the_drive_and_still_syncs_the_others(
        self, world, policy
    ):
        world.add_drive("Photos", policy=policy)
        world.add_drive("Movies")

        await world.manager._run_scheduled_sync()
        await settle()

        assert [a[2] for a in world.launches] == [str(world.drives["Movies"])]


async def test_the_policy_asked_about_is_the_umbrella_feature_of_this_addon(world):
    world.add_drive("Photos")

    await world.manager.start_sync("Photos")
    await settle()

    assert world.policy_asked == [("Photos", "cloud-sync", "index")]


@pytest.mark.parametrize("policy", OFF, ids=["off", "lookup-raises"])
def test_status_reports_a_blocked_drive_as_disabled_without_storing_it(world, policy):
    world.add_drive("Photos", policy=policy)
    world.manager._status["Photos"] = SyncDriveStatus(
        drive="Photos",
        remote="r:Photos",
        status="error",
        error_kind="auth_expired",
        last_synced_at="2026-01-01T00:00:00+00:00",
        last_result=SyncResult(transferred_files=3),
    )

    (drive,) = world.manager.get_status().drives

    assert drive.status == "disabled"
    assert drive.error_kind is None
    assert drive.last_synced_at is None
    assert drive.last_result is None
    assert world.manager._status["Photos"].status == "error"


async def test_a_running_sync_is_reported_as_syncing_after_the_policy_turns_off(world):
    world.add_drive("Photos")
    await world.manager.start_sync("Photos")
    world.policy["Photos"] = False

    (drive,) = world.manager.get_status().drives
    await settle()

    assert drive.status == "syncing"


@pytest.mark.parametrize("second", ["manual", "schedule"])
async def test_a_second_start_during_the_first_never_launches_twice(world, second):
    world.add_drive("Photos")

    await world.manager.start_sync("Photos")
    if second == "manual":
        with pytest.raises(RuntimeError):
            await world.manager.start_sync("Photos")
    else:
        await world.manager._run_scheduled_sync()
    await settle()

    assert len(world.launches) == 1


@pytest.mark.parametrize("failure", ["source_empty", "launch_raises", "log_dir_fails"])
async def test_a_run_that_fails_releases_the_drive_for_the_next_start(
    world, monkeypatch, failure
):
    world.add_drive("Photos")
    fake_exec = service.asyncio.create_subprocess_exec
    ensure_log_dir = world.manager._ensure_log_dir
    if failure == "source_empty":
        for child in world.drives["Photos"].iterdir():
            child.unlink()
    elif failure == "launch_raises":
        async def boom(*argv, **kwargs):
            raise FileNotFoundError("rclone")

        monkeypatch.setattr(service.asyncio, "create_subprocess_exec", boom)
    else:
        def no_log_dir():
            raise OSError("read-only file system")

        monkeypatch.setattr(world.manager, "_ensure_log_dir", no_log_dir)

    await world.manager.start_sync("Photos")
    await settle()
    (world.drives["Photos"] / "back.txt").write_text("x")
    monkeypatch.setattr(service.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(world.manager, "_ensure_log_dir", ensure_log_dir)

    await world.manager.start_sync("Photos")
    await settle()

    assert len(world.launches) == 1


async def test_cancel_during_the_source_check_launches_nothing(world, monkeypatch):
    world.add_drive("Photos")
    world.manager._status["Photos"] = SyncDriveStatus(
        drive="Photos",
        remote="r:Photos",
        last_synced_at="2026-01-01T00:00:00+00:00",
        last_result=SyncResult(transferred_files=3),
    )
    checking = threading.Event()
    release = threading.Event()

    def slow_check(path):
        checking.set()
        release.wait(5)
        return True

    monkeypatch.setattr(world.manager, "_source_usable", slow_check)

    await world.manager.start_sync("Photos")
    await asyncio.to_thread(checking.wait, 5)
    cancelled = await world.manager.cancel_sync("Photos")
    release.set()
    await settle()

    assert cancelled is True
    assert world.launches == []
    status = world.manager._status["Photos"]
    assert status.status == "error"
    assert status.last_synced_at == "2026-01-01T00:00:00+00:00"


async def test_cancel_while_rclone_is_spawning_stops_it(world, monkeypatch):
    world.add_drive("Photos")
    spawning = asyncio.Event()
    release = asyncio.Event()
    terminated: list[bool] = []

    class _Proc(_FakeProc):
        def terminate(self) -> None:
            terminated.append(True)

    async def slow_exec(*argv, **kwargs):
        world.launches.append(argv)
        spawning.set()
        await release.wait()
        return _Proc([], -15)

    monkeypatch.setattr(service.asyncio, "create_subprocess_exec", slow_exec)

    await world.manager.start_sync("Photos")
    await spawning.wait()
    cancelled = await world.manager.cancel_sync("Photos")
    release.set()
    await settle()

    assert cancelled is True
    assert terminated == [True]


def test_status_leaves_an_enabled_drive_as_it_was(world):
    world.add_drive("Photos")

    (drive,) = world.manager.get_status().drives

    assert drive.status == "idle"


async def test_a_drive_missing_from_the_config_is_never_synced(world):
    world.add_drive("Photos")
    world.add_drive("Movies")
    world.mapped.remove("Photos")

    with pytest.raises(ValueError):
        await world.manager.start_sync("Photos")
    await settle()

    assert world.launches == []


class TestSource:
    @staticmethod
    def _break(world, how):
        path = world.drives["Photos"]
        if how == "empty":
            for child in path.iterdir():
                child.unlink()
        elif how == "missing":
            for child in path.iterdir():
                child.unlink()
            path.rmdir()
        elif how == "subdirs":
            for child in path.iterdir():
                child.unlink()
            (path / "empty-folder").mkdir()
        elif how == "file":
            for child in path.iterdir():
                child.unlink()
            path.rmdir()
            path.write_text("not a directory")

    @pytest.mark.parametrize("how", ["empty", "subdirs", "missing", "file"])
    async def test_an_unusable_source_is_not_synced(self, world, broadcasts, how):
        world.add_drive("Photos")
        self._break(world, how)

        await world.manager.start_sync("Photos")
        await settle()

        assert world.launches == []
        status = world.manager._status["Photos"]
        assert status.status == "error"
        assert status.error_kind == "source_empty"
        event, payload = broadcasts[-1]
        assert event == "sync:error"
        assert payload["kind"] == status.error_kind

    async def test_a_failed_check_keeps_the_last_good_result(self, world, broadcasts):
        world.add_drive("Photos")
        await world.manager.start_sync("Photos")
        await settle()
        good = world.manager._status["Photos"]
        assert good.last_synced_at is not None
        self._break(world, "empty")

        await world.manager.start_sync("Photos")
        await settle()

        after = world.manager._status["Photos"]
        assert after.error_kind == "source_empty"
        assert after.last_synced_at == good.last_synced_at
        assert after.last_result == good.last_result

    async def test_a_drive_with_files_is_synced(self, world):
        world.add_drive("Photos")

        await world.manager.start_sync("Photos")
        await settle()

        assert len(world.launches) == 1

    async def test_a_drive_whose_files_are_all_in_folders_is_synced(self, world):
        world.add_drive("Photos", files=0)
        folder = world.drives["Photos"] / "2024" / "trip"
        folder.mkdir(parents=True)
        (folder / "a.jpg").write_text("x")

        await world.manager.start_sync("Photos")
        await settle()

        assert len(world.launches) == 1


class TestDeleteCap:
    async def test_the_configured_cap_reaches_rclone(self, world):
        world.max_delete = 7
        world.add_drive("Photos")

        await world.manager.start_sync("Photos")
        await settle()

        argv = list(world.launches[0])
        assert argv[argv.index("--max-delete") + 1] == "7"

    async def test_a_run_stopped_by_the_cap_says_so(self, world, broadcasts):
        world.add_drive("Photos")
        world.rclone_lines = [LIMIT_LINE]
        world.rclone_returncode = 7

        await world.manager.start_sync("Photos")
        await settle()

        status = world.manager._status["Photos"]
        assert status.status == "error"
        assert status.error_kind == "delete_limit"
        assert "max_delete" in status.error_message
        event, payload = broadcasts[-1]
        assert event == "sync:error"
        assert payload["kind"] == status.error_kind

    async def test_exit_7_without_the_cap_line_is_not_a_delete_limit(
        self, world, broadcasts
    ):
        world.add_drive("Photos")
        world.rclone_lines = [b'{"level":"error","msg":"something else fatal"}\n']
        world.rclone_returncode = 7

        await world.manager.start_sync("Photos")
        await settle()

        assert world.manager._status["Photos"].error_kind is None
        assert broadcasts[-1][1]["kind"] is None

    async def test_the_cap_line_on_a_successful_run_is_ignored(
        self, world, broadcasts
    ):
        world.add_drive("Photos")
        world.rclone_lines = [LIMIT_LINE]
        world.rclone_returncode = 0

        await world.manager.start_sync("Photos")
        await settle()

        assert world.manager._status["Photos"].status == "idle"
