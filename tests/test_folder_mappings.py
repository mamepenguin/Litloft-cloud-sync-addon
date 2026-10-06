"""SPEC-ADDON-003: a mapping mirrors a folder of a drive and is identified by (drive, path)."""
from __future__ import annotations

import asyncio
import os
import threading

import pytest

from addons.cloud_sync import service
from addons.cloud_sync.service import PolicyBlocked
from mapping_world import (  # noqa: F401
    LIMIT_LINE,
    SOURCE_EMPTY_MESSAGE,
    STATS_LINE,
    folder_log_name,
    mworld,
    settle,
)


async def run(world, drive: str, path: str) -> None:
    await world.manager.start_sync(drive, path)
    await settle()


class TestSource:
    """I1, I2, I3, I7: what rclone is handed, and when it is not started at all."""

    async def test_a_folder_mapping_syncs_the_joined_folder(self, mworld):
        root = mworld.add_drive("Photos")
        mworld.add_folder("Photos", "仕事/録画")
        mworld.max_delete = 9
        mworld.map("Photos", "仕事/録画", remote="gdrive:litloft/録画")

        await run(mworld, "Photos", "仕事/録画")

        (argv,) = mworld.launches
        argv = list(argv)
        assert argv[:4] == ["rclone", "sync", f"{root}/仕事/録画", "gdrive:litloft/録画"]
        assert argv[argv.index("--max-delete") + 1] == "9"

    async def test_an_unnormalized_path_still_names_that_folder_only(self, mworld):
        root = mworld.add_drive("Photos")
        mworld.add_folder("Photos", "仕事/録画")
        mworld.map("Photos", "./仕事//録画/")

        await run(mworld, "Photos", "仕事/録画")

        (source,) = mworld.sources()
        assert os.path.normpath(source) == os.path.normpath(f"{root}/仕事/録画")

    async def test_a_whole_drive_mapping_syncs_the_drive_root_as_before(self, mworld):
        root = mworld.add_drive("Photos")
        mworld.max_delete = 4
        mworld.map("Photos")

        await run(mworld, "Photos", "")

        (argv,) = mworld.launches
        assert argv[2] == str(root)
        assert argv[list(argv).index("--max-delete") + 1] == "4"

    async def test_rclone_receives_the_path_as_written_not_resolved(self, mworld):
        root = mworld.add_drive("Photos")
        real = mworld.add_folder("Photos", "real")
        (root / "link").symlink_to(real)
        mworld.map("Photos", "link")

        await run(mworld, "Photos", "link")

        assert mworld.sources() == [f"{root}/link"]

    async def test_a_drive_root_reached_through_a_symlink_is_still_contained(self, mworld):
        real = mworld.add_drive("Real")
        mworld.add_folder("Real", "a")
        alias = mworld.drive_dir / "Alias"
        alias.symlink_to(real)
        mworld.drives["Photos"] = alias
        mworld.policy["Photos"] = True
        mworld.map("Photos", "a")

        await run(mworld, "Photos", "a")

        assert mworld.sources() == [f"{alias}/a"]

    async def test_a_folder_whose_files_are_all_in_subfolders_is_synced(self, mworld):
        mworld.add_drive("Photos", files=0)
        mworld.add_folder("Photos", "a/b/c")
        mworld.map("Photos", "a")

        await run(mworld, "Photos", "a")

        assert len(mworld.launches) == 1

    @staticmethod
    def _break(world, how: str) -> None:
        root = world.drives["Photos"]
        if how == "missing":
            return
        if how == "file":
            (root / "a").write_text("not a folder")
        elif how == "empty":
            (root / "a").mkdir()
        elif how == "only-empty-subfolders":
            (root / "a" / "sub").mkdir(parents=True)
        elif how == "symlink-out":
            outside = world.tmp_path / "outside"
            outside.mkdir()
            (outside / "secret.txt").write_text("x")
            (root / "a").symlink_to(outside)
        elif how == "symlink-to-prefix-sibling":
            sibling = world.drive_dir / "Photos2"
            sibling.mkdir()
            (sibling / "x.txt").write_text("x")
            (root / "a").symlink_to(sibling)
        elif how == "symlink-dotdot":
            (root / "a").symlink_to("..")

    @pytest.mark.parametrize(
        "how",
        [
            "missing",
            "file",
            "empty",
            "only-empty-subfolders",
            "symlink-out",
            "symlink-to-prefix-sibling",
            "symlink-dotdot",
        ],
    )
    async def test_an_unusable_folder_is_never_handed_to_rclone(
        self, mworld, broadcasts, how
    ):
        mworld.add_drive("Photos")
        self._break(mworld, how)
        mworld.map("Photos", "a")

        await run(mworld, "Photos", "a")

        assert mworld.launches == []
        entry = mworld.entry("Photos", "a")
        assert entry.status == "error"
        assert entry.error_kind == "source_empty"
        assert entry.error_message == SOURCE_EMPTY_MESSAGE
        event, payload = broadcasts[-1]
        assert event == "sync:error"
        assert (payload["drive"], payload["path"], payload["kind"]) == (
            "Photos",
            "a",
            "source_empty",
        )

    async def test_a_nested_folder_reached_through_an_outward_symlink_is_refused(
        self, mworld
    ):
        root = mworld.add_drive("Photos")
        outside = mworld.tmp_path / "outside"
        (outside / "sub").mkdir(parents=True)
        (outside / "sub" / "secret.txt").write_text("x")
        (root / "out").symlink_to(outside)
        mworld.map("Photos", "out/sub")

        await run(mworld, "Photos", "out/sub")

        assert mworld.launches == []
        assert mworld.entry("Photos", "out/sub").error_kind == "source_empty"

    async def test_an_empty_drive_root_gets_the_same_message(self, mworld):
        mworld.add_drive("Photos", files=0)
        mworld.map("Photos")

        await run(mworld, "Photos", "")

        assert mworld.launches == []
        entry = mworld.entry("Photos", "")
        assert entry.error_kind == "source_empty"
        assert entry.error_message == SOURCE_EMPTY_MESSAGE

    async def test_a_failed_check_keeps_the_last_good_result(self, mworld):
        mworld.add_drive("Photos")
        folder = mworld.add_folder("Photos", "a")
        mworld.map("Photos", "a")
        await run(mworld, "Photos", "a")
        good = mworld.entry("Photos", "a")
        assert good.last_synced_at is not None
        for child in folder.iterdir():
            child.unlink()

        await run(mworld, "Photos", "a")

        after = mworld.entry("Photos", "a")
        assert after.error_kind == "source_empty"
        assert after.last_synced_at == good.last_synced_at
        assert after.last_result == good.last_result


class TestEvents:
    """I8: every event names the mapping it is about."""

    async def test_progress_and_completion_carry_drive_and_path(
        self, mworld, broadcasts
    ):
        mworld.add_drive("Photos")
        mworld.add_folder("Photos", "a")
        mworld.map("Photos", "a")
        mworld.rclone_lines = [STATS_LINE]

        await run(mworld, "Photos", "a")

        events = [e for e, _ in broadcasts]
        assert "sync:progress" in events
        assert events[-1] == "sync:complete"
        for _, payload in broadcasts:
            assert (payload["drive"], payload["path"]) == ("Photos", "a")

    @pytest.mark.parametrize(
        ("lines", "code", "kind"),
        [([], 1, None), ([LIMIT_LINE], 7, "delete_limit")],
        ids=["rclone-fails", "delete-limit"],
    )
    async def test_a_failure_event_carries_drive_and_path(
        self, mworld, broadcasts, lines, code, kind
    ):
        mworld.add_drive("Photos")
        mworld.add_folder("Photos", "a")
        mworld.map("Photos", "a")
        mworld.rclone_lines = lines
        mworld.rclone_returncode = code

        await run(mworld, "Photos", "a")

        event, payload = broadcasts[-1]
        assert event == "sync:error"
        assert (payload["drive"], payload["path"], payload["kind"]) == (
            "Photos",
            "a",
            kind,
        )
        assert mworld.entry("Photos", "a").error_kind == kind

    async def test_a_whole_drive_mapping_reports_an_empty_path(
        self, mworld, broadcasts
    ):
        mworld.add_drive("Photos")
        mworld.map("Photos")
        mworld.rclone_lines = [STATS_LINE]

        await run(mworld, "Photos", "")

        assert broadcasts
        for _, payload in broadcasts:
            assert (payload["drive"], payload["path"]) == ("Photos", "")


class TestIsolation:
    """I5: one mapping's run never moves another mapping of the same drive."""

    async def test_two_mappings_of_one_drive_run_side_by_side(self, mworld):
        root = mworld.add_drive("Photos")
        mworld.add_folder("Photos", "a")
        mworld.map("Photos")
        mworld.map("Photos", "a")
        release = mworld.hold_launch(f"{root}/a")

        await mworld.manager.start_sync("Photos", "a")
        await asyncio.sleep(0.05)
        assert mworld.entry("Photos", "a").status == "syncing"
        assert mworld.entry("Photos", "").status == "idle"

        await mworld.manager.start_sync("Photos", "")
        with pytest.raises(RuntimeError):
            await mworld.manager.start_sync("Photos", "a")
        release.set()
        await settle()

        assert sorted(mworld.sources()) == sorted([str(root), f"{root}/a"])
        assert mworld.entry("Photos", "a").status == "idle"
        assert mworld.entry("Photos", "").status == "idle"

    async def test_a_failing_mapping_leaves_its_sibling_as_it_was(self, mworld):
        mworld.add_drive("Photos")
        mworld.map("Photos")
        mworld.map("Photos", "a")
        await run(mworld, "Photos", "")
        before = mworld.entry("Photos", "")

        await run(mworld, "Photos", "a")

        assert mworld.entry("Photos", "a").error_kind == "source_empty"
        assert mworld.entry("Photos", "") == before

    async def test_cancelling_one_mapping_leaves_the_other_running(self, mworld):
        root = mworld.add_drive("Photos")
        mworld.add_folder("Photos", "a")
        mworld.map("Photos")
        mworld.map("Photos", "a")
        hold_root = mworld.hold_launch(str(root))
        hold_a = mworld.hold_launch(f"{root}/a")

        await mworld.manager.start_sync("Photos", "")
        await mworld.manager.start_sync("Photos", "a")
        await asyncio.sleep(0.05)
        cancelled = await mworld.manager.cancel_sync("Photos", "a")
        hold_a.set()
        hold_root.set()
        await settle()

        assert cancelled is True
        assert mworld.terminated == [f"{root}/a"]
        assert mworld.entry("Photos", "a").status == "error"
        assert mworld.entry("Photos", "").status == "idle"

    async def test_cancelling_an_idle_sibling_leaves_no_mark_on_either(self, mworld):
        root = mworld.add_drive("Photos")
        mworld.add_folder("Photos", "a")
        mworld.map("Photos")
        mworld.map("Photos", "a")
        hold_root = mworld.hold_launch(str(root))

        await mworld.manager.start_sync("Photos", "")
        await asyncio.sleep(0.05)
        cancelled = await mworld.manager.cancel_sync("Photos", "a")
        hold_root.set()
        await settle()
        await run(mworld, "Photos", "a")

        assert cancelled is False
        assert mworld.terminated == []
        assert mworld.entry("Photos", "").status == "idle"
        assert mworld.entry("Photos", "a").status == "idle"

    async def test_each_mapping_writes_its_own_log_file(self, mworld):
        mworld.add_drive("Photos")
        mworld.add_folder("Photos", "a")
        mworld.add_folder("Photos", "b")
        mworld.map("Photos")
        mworld.map("Photos", "a")
        mworld.map("Photos", "b")

        for path in ["", "a", "b"]:
            await run(mworld, "Photos", path)

        assert sorted(p.name for p in mworld.log_dir.iterdir()) == sorted(
            [
                "Photos.log",
                folder_log_name("Photos", "Photos", "a"),
                folder_log_name("Photos", "Photos", "b"),
            ]
        )


class TestLogName:
    """I1, I2: the whole-drive log keeps its name; a folder gets <safe>.<h>.log."""

    @pytest.mark.parametrize(
        ("drive", "path", "expected"),
        [
            ("Photos", "", "Photos.log"),
            ("Photos", "仕事/録画", folder_log_name("Photos", "Photos", "仕事/録画")),
            ("my.drive", "a", folder_log_name("my_drive", "my.drive", "a")),
        ],
        ids=["whole-drive", "folder", "dotted-drive"],
    )
    async def test_a_run_writes_only_its_own_log_file(self, mworld, drive, path, expected):
        mworld.add_drive(drive)
        if path:
            mworld.add_folder(drive, path)
        mworld.map(drive, path)

        await run(mworld, drive, path)

        assert [p.name for p in mworld.log_dir.iterdir()] == [expected]


OFF = [False, RuntimeError("drives.json unreadable")]


@pytest.mark.parametrize("policy", OFF, ids=["off", "lookup-raises"])
class TestPolicy:
    """I6: the drive's policy covers every mapping of that drive."""

    async def test_no_mapping_of_the_drive_starts_manually(self, mworld, policy):
        mworld.add_drive("Photos", policy=policy)
        mworld.add_folder("Photos", "a")
        mworld.map("Photos")
        mworld.map("Photos", "a")

        for path in ["", "a"]:
            with pytest.raises(PolicyBlocked):
                await mworld.manager.start_sync("Photos", path)
        await settle()

        assert mworld.launches == []
        assert [d.status for d in mworld.manager.get_status().drives] == [
            "disabled",
            "disabled",
        ]

    async def test_the_schedule_skips_every_mapping_of_the_drive(self, mworld, policy):
        mworld.add_drive("Photos", policy=policy)
        mworld.add_folder("Photos", "a")
        movies = mworld.add_drive("Movies")
        mworld.add_folder("Movies", "m")
        mworld.map("Photos")
        mworld.map("Photos", "a")
        mworld.map("Movies", "m")

        await mworld.manager._run_scheduled_sync()
        await settle()

        assert mworld.sources() == [f"{movies}/m"]


async def test_a_running_mapping_stays_syncing_when_its_drive_turns_off(mworld):
    root = mworld.add_drive("Photos")
    mworld.add_folder("Photos", "a")
    mworld.map("Photos")
    mworld.map("Photos", "a")
    release = mworld.hold_launch(f"{root}/a")

    await mworld.manager.start_sync("Photos", "a")
    await asyncio.sleep(0.05)
    mworld.policy["Photos"] = False
    syncing = mworld.entry("Photos", "a").status
    sibling = mworld.entry("Photos", "").status
    release.set()
    await settle()

    assert (syncing, sibling) == ("syncing", "disabled")


async def test_the_schedule_skips_a_mapping_whose_drive_is_gone(mworld):
    movies = mworld.add_drive("Movies")
    mworld.add_folder("Movies", "m")
    mworld.map("Gone", "a")
    mworld.map("Movies", "m")

    await mworld.manager._run_scheduled_sync()
    await settle()

    assert mworld.sources() == [f"{movies}/m"]


class TestCancel:
    async def test_a_running_mapping_can_be_cancelled_after_the_file_breaks(
        self, mworld
    ):
        root = mworld.add_drive("Photos")
        mworld.add_folder("Photos", "a")
        mworld.map("Photos", "a")
        release = mworld.hold_launch(f"{root}/a")

        await mworld.manager.start_sync("Photos", "a")
        await asyncio.sleep(0.05)
        mworld.write_raw("{not json")
        cancelled = await mworld.manager.cancel_sync("Photos", "a")
        release.set()
        await settle()

        assert cancelled is True
        assert mworld.terminated == [f"{root}/a"]

    async def test_cancelling_a_pair_not_in_the_file_changes_nothing(self, mworld):
        mworld.add_drive("Photos")
        mworld.add_folder("Photos", "a")
        mworld.map("Photos", "a")

        cancelled = await mworld.manager.cancel_sync("Photos", "b")
        await run(mworld, "Photos", "a")

        assert cancelled is False
        assert mworld.entry("Photos", "a").status == "idle"


@pytest.mark.parametrize(
    "ending",
    [
        "success",
        "rclone-fails",
        "source-empty",
        "cancel-before-launch",
        "cancel-after-launch",
        "exception",
    ],
)
async def test_a_mapping_can_start_again_after_any_ending(mworld, monkeypatch, ending):
    """I9."""
    root = mworld.add_drive("Photos")
    folder = mworld.add_folder("Photos", "a")
    mworld.map("Photos", "a")
    source = f"{root}/a"
    fake_exec = service.asyncio.create_subprocess_exec

    if ending == "rclone-fails":
        mworld.rclone_returncode = 1
        await run(mworld, "Photos", "a")
    elif ending == "source-empty":
        for child in folder.iterdir():
            child.unlink()
        await run(mworld, "Photos", "a")
        (folder / "back.txt").write_text("x")
    elif ending == "cancel-before-launch":
        checking = threading.Event()
        release = threading.Event()

        original_check = mworld.manager._source_usable

        def slow_check(*args, **kwargs):
            checking.set()
            release.wait(5)
            return True

        monkeypatch.setattr(mworld.manager, "_source_usable", slow_check)
        await mworld.manager.start_sync("Photos", "a")
        await asyncio.to_thread(checking.wait, 5)
        assert await mworld.manager.cancel_sync("Photos", "a") is True
        release.set()
        await settle()
        monkeypatch.setattr(mworld.manager, "_source_usable", original_check)
        assert mworld.launches == []
    elif ending == "cancel-after-launch":
        hold = mworld.hold_launch(source)
        await mworld.manager.start_sync("Photos", "a")
        await asyncio.sleep(0.05)
        assert await mworld.manager.cancel_sync("Photos", "a") is True
        hold.set()
        await settle()
        del mworld.hold[source]
    elif ending == "exception":
        async def boom(*argv, **kwargs):
            raise FileNotFoundError("rclone")

        monkeypatch.setattr(service.asyncio, "create_subprocess_exec", boom)
        await run(mworld, "Photos", "a")
        monkeypatch.setattr(service.asyncio, "create_subprocess_exec", fake_exec)
    else:
        await run(mworld, "Photos", "a")

    mworld.rclone_returncode = 0
    launched_before = len(mworld.launches)
    await run(mworld, "Photos", "a")

    assert len(mworld.launches) == launched_before + 1
    entry = mworld.entry("Photos", "a")
    assert entry.status == "idle"
    assert entry.error_message is None


class _StreamingProc:
    """An rclone that has launched and keeps its output open until released."""

    def __init__(self, source: str, lines: list[bytes], terminated: list[str]) -> None:
        self.source = source
        self.returncode: int | None = None
        self._lines = lines
        self._terminated = terminated
        self.release = asyncio.Event()

    @property
    def stderr(self):
        async def lines():
            for line in self._lines:
                yield line
            await self.release.wait()

        return lines()

    async def wait(self) -> int:
        await self.release.wait()
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self) -> None:
        self._terminated.append(self.source)
        self.returncode = -15
        self.release.set()


@pytest.fixture()
def streaming(mworld, monkeypatch):
    """Launches that stay running after their process is handed back."""
    procs: dict[str, _StreamingProc] = {}

    async def exec_streaming(*argv, **kwargs):
        mworld.launches.append(argv)
        proc = _StreamingProc(argv[2], list(mworld.rclone_lines), mworld.terminated)
        procs[argv[2]] = proc
        return proc

    monkeypatch.setattr(service.asyncio, "create_subprocess_exec", exec_streaming)
    return procs


async def _until(predicate) -> None:
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not reached")


# SPEC-ADDON-003 (I5, I8): two mappings of one drive, both past launch.
class TestSiblingsWhileRunning:
    async def test_cancel_terminates_only_its_own_process(self, mworld, streaming):
        root = mworld.add_drive("Photos")
        mworld.add_folder("Photos", "a")
        mworld.map("Photos")
        mworld.map("Photos", "a")

        await mworld.manager.start_sync("Photos", "")
        await mworld.manager.start_sync("Photos", "a")
        await _until(lambda: len(streaming) == 2)
        await asyncio.sleep(0.02)
        await mworld.manager.cancel_sync("Photos", "a")
        await _until(lambda: mworld.entry("Photos", "a").status != "syncing")

        assert mworld.terminated == [f"{root}/a"]
        assert mworld.entry("Photos", "").status == "syncing"
        streaming[str(root)].release.set()
        await settle()

    async def test_progress_and_result_stay_with_their_mapping(self, mworld, streaming):
        root = mworld.add_drive("Photos")
        mworld.add_folder("Photos", "a")
        mworld.map("Photos")
        mworld.map("Photos", "a")
        mworld.rclone_lines = [STATS_LINE]

        await mworld.manager.start_sync("Photos", "")
        await mworld.manager.start_sync("Photos", "a")
        await _until(lambda: mworld.entry("Photos", "a").progress is not None
                     and mworld.entry("Photos", "a").progress.percent == 50.0)
        mworld.rclone_lines = []

        assert mworld.entry("Photos", "a").progress.percent == 50.0
        streaming[f"{root}/a"].release.set()
        await _until(lambda: mworld.entry("Photos", "a").status == "idle")

        assert mworld.entry("Photos", "a").last_result.transferred_bytes == 50
        assert mworld.entry("Photos", "").status == "syncing"
        streaming[str(root)].release.set()
        await settle()

    async def test_the_schedule_starts_a_mapping_whose_sibling_is_running(
        self, mworld, streaming
    ):
        root = mworld.add_drive("Photos")
        mworld.add_folder("Photos", "a")
        mworld.map("Photos")
        mworld.map("Photos", "a")

        await mworld.manager.start_sync("Photos", "")
        await _until(lambda: len(streaming) == 1)
        await mworld.manager._run_scheduled_sync()
        await _until(lambda: len(streaming) == 2)

        assert sorted(mworld.sources()) == sorted([str(root), f"{root}/a"])
        for proc in streaming.values():
            proc.release.set()
        await settle()


# SPEC-ADDON-003 (I9): a finished run leaves nothing behind that a cancel of
# the next run would act on instead of the next run itself.
async def test_a_cancel_during_the_next_runs_check_is_not_lost(mworld, monkeypatch):
    mworld.add_drive("Photos")
    mworld.add_folder("Photos", "a")
    mworld.map("Photos", "a")
    await run(mworld, "Photos", "a")
    checking = threading.Event()
    release = threading.Event()

    def slow_check(*args, **kwargs):
        checking.set()
        release.wait(5)
        return True

    monkeypatch.setattr(mworld.manager, "_source_usable", slow_check)
    await mworld.manager.start_sync("Photos", "a")
    await asyncio.to_thread(checking.wait, 5)
    await mworld.manager.cancel_sync("Photos", "a")
    release.set()
    await settle()

    assert len(mworld.launches) == 1
