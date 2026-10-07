"""SPEC-ADDON-006: a saved configuration takes effect without a restart, the schedule
runs in its time zone, and a save never disturbs a run in progress."""
from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone

import pytest

from addons.cloud_sync import router as router_module
from settings_world import BASE, body, row, sworld  # noqa: F401

UTC = timezone.utc


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None, value
    return parsed.astimezone(UTC)


def pairs(status: dict) -> list[tuple[str, str]]:
    return [(d["drive"], d["path"]) for d in status["drives"]]


def entry(status: dict, drive: str, path: str) -> dict:
    (found,) = [d for d in status["drives"] if (d["drive"], d["path"]) == (drive, path)]
    return found


# --- time zone (I6) -------------------------------------------------------


# SPEC-ADDON-006 (I6): the schedule fires at the wall-clock time of its zone.
@pytest.mark.parametrize(
    ("schedule", "tz", "utc_hour", "utc_weekday"),
    [
        ("0 3 * * *", "Asia/Tokyo", 18, None),
        ("0 3 * * *", None, 3, None),
        ("0 3 * * *", "UTC", 3, None),
        ("30 9 * * 1", "Asia/Tokyo", 0, 0),
    ],
    ids=["tokyo-daily", "null-is-utc", "utc", "tokyo-weekly"],
)
async def test_next_sync_is_computed_in_the_configured_zone(
    sworld, schedule, tz, utc_hour, utc_weekday
):
    before = datetime.now(UTC)

    saved = await sworld.put_config(body(schedule=schedule, timezone=tz))
    got = (await sworld.get_config()).json()
    status = await sworld.status()

    assert saved.status_code == 200, saved.text
    for value in [saved.json()["next_sync_at"], got["next_sync_at"], status["next_sync_at"]]:
        when = parse_utc(value)
        assert when.hour == utc_hour
        assert when.minute == (30 if schedule.startswith("30") else 0)
        if utc_weekday is not None:
            assert when.weekday() == utc_weekday
        assert before < when <= before + timedelta(days=7, minutes=1)
    assert status["timezone"] == tz
    assert status["schedule"] == schedule


# SPEC-ADDON-006 (I6): a stored file with a zone is read in that zone too.
async def test_a_stored_zone_moves_the_next_sync(sworld):
    sworld.write_file({
        "schema_version": 1, "schedule": "0 3 * * *", "timezone": "Asia/Tokyo",
        "max_delete": 200, "mappings": [],
    })

    status = await sworld.status()

    assert status["timezone"] == "Asia/Tokyo"
    assert parse_utc(status["next_sync_at"]).hour == 18


# SPEC-ADDON-006: a tzdata alias is a name ZoneInfo accepts.
@pytest.mark.parametrize("tz", ["Etc/UTC", "Asia/Calcutta"])
async def test_a_zone_alias_is_accepted(sworld, tz):
    response = await sworld.put_config(body(schedule="0 3 * * *", timezone=tz))

    assert response.status_code == 200, response.text
    assert sworld.stored()["timezone"] == tz


# SPEC-ADDON-006: the zone is saved even when the schedule is off.
async def test_a_zone_without_a_schedule_is_kept(sworld):
    response = await sworld.put_config(body(schedule=None, timezone="Asia/Tokyo"))

    assert response.status_code == 200
    assert sworld.stored()["timezone"] == "Asia/Tokyo"
    assert response.json()["next_sync_at"] is None
    assert (await sworld.status())["next_sync_at"] is None


# --- takes effect without a restart (I4) ----------------------------------


# SPEC-ADDON-006 (I4), normal flow step 6: /status follows the save at once.
async def test_status_reflects_a_save_without_a_restart(sworld):
    sworld.add_drive("動画")
    sworld.add_folder("動画", "仕事/録画")
    assert (await sworld.status())["drives"] == []

    await sworld.put_config(body(
        schedule="0 */6 * * *",
        timezone="Asia/Tokyo",
        mappings=[row("動画", "仕事/録画/", "gdrive:rec"), row("動画", "", "other:all")],
    ))
    status = await sworld.status()

    assert pairs(status) == [("動画", "仕事/録画"), ("動画", "")]
    assert [d["remote"] for d in status["drives"]] == ["gdrive:rec", "other:all"]
    assert status["schedule"] == "0 */6 * * *"
    assert status["timezone"] == "Asia/Tokyo"
    assert status["next_sync_at"] is not None


# SPEC-ADDON-006: a mapping saved through the GUI is the one a start syncs.
async def test_a_saved_mapping_can_be_started_with_its_saved_cap(sworld):
    root = sworld.add_drive("Photos")
    sworld.add_folder("Photos", "a")
    await sworld.put_config(body(max_delete=11, mappings=[row("Photos", "a", "gdrive:a")]))

    response = await sworld.client.post(f"{BASE}/Photos/start", params={"path": "a"})
    await sworld.wait_not_syncing("Photos", "a")

    assert response.status_code == 200
    (argv,) = sworld.launches
    assert argv[2] == f"{root}/a"
    assert "gdrive:a" in argv
    assert argv[list(argv).index("--max-delete") + 1] == "11"


# --- scheduler (I2, I4) ---------------------------------------------------


STORED = {
    "schema_version": 1, "schedule": "0 3 * * *", "timezone": "Asia/Tokyo",
    "max_delete": 200, "mappings": [],
}


# SPEC-ADDON-006, item 3 "Scheduler": startup follows the stored file.
@pytest.mark.parametrize(
    ("content", "loops"),
    [(STORED, 1), ({**STORED, "schedule": None}, 0), (None, 0)],
    ids=["scheduled", "no-schedule", "no-file"],
)
async def test_startup_starts_at_most_one_loop_from_the_stored_file(
    sworld, monkeypatch, content, loops
):
    # The old location holds the opposite answer, so reading it would show.
    legacy_schedule = None if loops else "*/5 * * * *"
    sworld.plant_legacy(monkeypatch, {"schedule": legacy_schedule, "mappings": []})
    if content is not None:
        sworld.write_file(content)

    await router_module.on_startup()

    assert len(sworld.scheduler_loops()) == loops


# SPEC-ADDON-006 (I4): startup twice, or startup and a save, never leaves two loops.
async def test_startup_and_saves_never_leave_two_loops(sworld):
    sworld.write_file(STORED)

    await router_module.on_startup()
    await router_module.on_startup()
    assert len(sworld.scheduler_loops()) == 1

    await sworld.put_config(body(schedule="0 4 * * *", timezone="UTC"))
    assert len(sworld.scheduler_loops()) == 1


# SPEC-ADDON-006 (I4): each save replaces the loop; a null schedule leaves none.
async def test_each_save_replaces_the_loop(sworld):
    await sworld.put_config(body(schedule="0 3 * * *", timezone="UTC"))
    (first,) = sworld.scheduler_loops()

    await sworld.put_config(body(schedule="0 5 * * *", timezone="Asia/Tokyo"))
    loops = sworld.scheduler_loops()
    await asyncio.sleep(0)

    assert len(loops) == 1
    assert loops[0] is not first
    assert first.done()

    await sworld.put_config(body(schedule=None, timezone="UTC"))
    await asyncio.sleep(0)

    assert sworld.scheduler_loops() == []
    assert loops[0].done()


# SPEC-ADDON-006 (I4): overlapping saves still leave exactly one loop.
async def test_overlapping_saves_leave_one_loop_and_the_last_file(sworld):
    schedules = [f"0 {h} * * *" for h in range(1, 9)]

    responses = await asyncio.gather(*[
        sworld.put_config(body(schedule=s, timezone="Asia/Tokyo")) for s in schedules
    ])
    await asyncio.sleep(0)

    assert [r.status_code for r in responses] == [200] * len(schedules)
    assert len(sworld.scheduler_loops()) == 1
    stored = sworld.stored()["schedule"]
    assert stored in schedules
    assert (await sworld.status())["schedule"] == stored
    assert sworld.stray_files() == []


# SPEC-ADDON-006 (I4): overlapping saves, the last of which turns the schedule off.
async def test_overlapping_saves_ending_with_no_schedule_leave_none_or_one(sworld):
    payloads = [body(schedule="0 1 * * *", timezone="UTC"), body(schedule=None, timezone="UTC")]

    responses = await asyncio.gather(*[sworld.put_config(p) for p in payloads])
    await asyncio.sleep(0)

    assert [r.status_code for r in responses] == [200, 200]

    expected = 0 if sworld.stored()["schedule"] is None else 1
    assert len(sworld.scheduler_loops()) == expected


# --- runs survive saves (I5) ----------------------------------------------


# SPEC-ADDON-006 (I5), "Running runs in /status": a removed mapping's run keeps going.
async def test_a_run_whose_mapping_is_removed_stays_listed_and_cancellable(sworld):
    root = sworld.add_drive("Photos")
    sworld.add_folder("Photos", "a")
    sworld.add_folder("Photos", "b")
    await sworld.put_config(body(mappings=[
        row("Photos", "a", "gdrive:a"), row("Photos", "b", "gdrive:b"),
    ]))
    sworld.hold(f"{root}/a")
    started = await sworld.client.post(f"{BASE}/Photos/start", params={"path": "a"})
    assert started.status_code == 200

    async def launched():
        return len(sworld.launches) == 1

    await sworld.wait_until(launched)

    saved = await sworld.put_config(body(mappings=[
        row("Photos", "b", "gdrive:b"), row("Photos", "", "other:root"),
    ]))
    during = await sworld.status()
    log_during = await sworld.client.get(f"{BASE}/Photos/log", params={"path": "a"})

    assert saved.status_code == 200, saved.text
    assert pairs(during) == [("Photos", "b"), ("Photos", ""), ("Photos", "a")]
    running = entry(during, "Photos", "a")
    assert running["status"] == "syncing"
    assert running["remote"] == "gdrive:a"
    assert sworld.terminated == []
    assert log_during.status_code != 404

    cancelled = await sworld.client.post(f"{BASE}/Photos/cancel", params={"path": "a"})
    await sworld.wait_not_syncing("Photos", "a")
    after = await sworld.status()
    log_after = await sworld.client.get(f"{BASE}/Photos/log", params={"path": "a"})

    assert cancelled.status_code == 200
    assert sworld.terminated == [f"{root}/a"]
    assert pairs(after) == [("Photos", "b"), ("Photos", "")]
    assert log_after.status_code == 404


# SPEC-ADDON-006 (I5): runs not in the file are listed in the order they were reserved.
async def test_removed_runs_are_listed_in_reservation_order(sworld):
    root = sworld.add_drive("Photos")
    for name in ["a", "b", "c"]:
        sworld.add_folder("Photos", name)
        sworld.hold(f"{root}/{name}")
    await sworld.put_config(body(mappings=[
        row("Photos", "a", "gdrive:a"), row("Photos", "b", "gdrive:b"), row("Photos", "c", "gdrive:c"),
    ]))
    for name in ["c", "a"]:
        assert (await sworld.client.post(f"{BASE}/Photos/start", params={"path": name})).status_code == 200

    await sworld.put_config(body(mappings=[row("Photos", "b", "gdrive:b")]))

    assert pairs(await sworld.status()) == [("Photos", "b"), ("Photos", "c"), ("Photos", "a")]


# SPEC-ADDON-006 (I5): remote and max_delete are fixed when the run is reserved.
async def test_a_save_during_the_preflight_does_not_retarget_the_run(sworld, monkeypatch):
    sworld.add_drive("Photos")
    sworld.add_folder("Photos", "a")
    first = await sworld.put_config(body(max_delete=9, mappings=[row("Photos", "a", "gdrive:old")]))
    assert first.status_code == 200, first.text
    checking = threading.Event()
    release = threading.Event()

    def slow_check(*args):
        checking.set()
        release.wait(5)
        return True

    monkeypatch.setattr(sworld.manager, "_source_usable", slow_check)

    started = await sworld.client.post(f"{BASE}/Photos/start", params={"path": "a"})
    await asyncio.to_thread(checking.wait, 5)
    saved = await sworld.put_config(body(max_delete=4, mappings=[row("Photos", "a", "gdrive:new")]))
    release.set()

    async def launched():
        return len(sworld.launches) == 1

    await sworld.wait_until(launched)
    await sworld.wait_not_syncing("Photos", "a")

    assert started.status_code == 200
    assert saved.status_code == 200, saved.text
    (argv,) = sworld.launches
    assert "gdrive:old" in argv
    assert "gdrive:new" not in argv
    assert argv[list(argv).index("--max-delete") + 1] == "9"


# --- stale results (I10) --------------------------------------------------


async def _finish_one_run(world, remote: str, returncode: int = 0) -> None:
    world.rclone_returncode = returncode
    await world.put_config(body(mappings=[row("Photos", "a", remote)]))
    assert (await world.client.post(f"{BASE}/Photos/start", params={"path": "a"})).status_code == 200
    await world.wait_not_syncing("Photos", "a")


def _assert_fresh(item: dict, remote: str) -> None:
    assert item["remote"] == remote
    assert item["status"] == "idle"
    assert item["last_synced_at"] is None
    assert item["last_result"] is None
    assert item.get("error_message") is None
    assert item.get("error_kind") is None


# SPEC-ADDON-006 (I10): a result recorded against another remote is not shown.
@pytest.mark.parametrize("returncode", [0, 1], ids=["succeeded", "failed"])
async def test_a_changed_remote_shows_a_fresh_idle_entry(sworld, returncode):
    sworld.add_drive("Photos")
    sworld.add_folder("Photos", "a")
    await _finish_one_run(sworld, "gdrive:one", returncode)
    before = entry(await sworld.status(), "Photos", "a")
    assert before["last_synced_at"] is not None or before["status"] == "error"

    await sworld.put_config(body(mappings=[row("Photos", "a", "gdrive:two")]))

    _assert_fresh(entry(await sworld.status(), "Photos", "a"), "gdrive:two")


# SPEC-ADDON-006 (I10): the same remote keeps its last result across a save.
async def test_an_unchanged_remote_keeps_its_last_result(sworld):
    sworld.add_drive("Photos")
    sworld.add_folder("Photos", "a")
    await _finish_one_run(sworld, "gdrive:one")
    before = entry(await sworld.status(), "Photos", "a")

    await sworld.put_config(body(max_delete=5, mappings=[row("Photos", "a", "gdrive:one")]))
    after = entry(await sworld.status(), "Photos", "a")

    assert after["last_synced_at"] == before["last_synced_at"] is not None
    assert after["last_result"] == before["last_result"]


# SPEC-ADDON-006 (I10): removed and re-added with another remote.
async def test_a_mapping_removed_and_readded_with_another_remote_is_fresh(sworld):
    sworld.add_drive("Photos")
    sworld.add_folder("Photos", "a")
    await _finish_one_run(sworld, "gdrive:one")

    await sworld.put_config(body())
    await sworld.put_config(body(mappings=[row("Photos", "a", "gdrive:three")]))

    _assert_fresh(entry(await sworld.status(), "Photos", "a"), "gdrive:three")


# SPEC-ADDON-006 (I10): a remote changed while its run was going.
async def test_a_remote_changed_during_a_run_shows_fresh_after_it_ends(sworld):
    root = sworld.add_drive("Photos")
    sworld.add_folder("Photos", "a")
    first = await sworld.put_config(body(mappings=[row("Photos", "a", "gdrive:one")]))
    assert first.status_code == 200, first.text
    release = sworld.hold(f"{root}/a")
    await sworld.client.post(f"{BASE}/Photos/start", params={"path": "a"})

    async def launched():
        return len(sworld.launches) == 1

    await sworld.wait_until(launched)
    await sworld.put_config(body(mappings=[row("Photos", "a", "gdrive:two")]))
    release.set()
    await sworld.wait_not_syncing("Photos", "a")

    _assert_fresh(entry(await sworld.status(), "Photos", "a"), "gdrive:two")
