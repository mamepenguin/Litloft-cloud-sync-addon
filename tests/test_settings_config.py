"""SPEC-ADDON-005: the configuration lives in the data directory, behind admin-only
`GET`/`PUT /config`, and a replacement that breaks any rule is refused whole."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

import app.auth as auth
from addons.cloud_sync import router as router_module
from settings_world import (  # noqa: F401
    BASE,
    EMPTY_CONFIG,
    body,
    err,
    row,
    sworld,
    without_message,
)


def assert_config_response(data: dict, *, source: str, config: dict) -> None:
    assert set(data) == {
        "source", "error", "config", "drives", "remotes", "remotes_error", "next_sync_at",
    }
    assert data["source"] == source
    assert data["config"] == config


# --- GET /config ------------------------------------------------------------


# SPEC-ADDON-005, failure table "No saved file"; I1.
async def test_with_no_saved_file_get_returns_an_empty_configuration(sworld):
    sworld.add_drive("動画")
    sworld.add_drive("Photos", enabled=False)

    response = await sworld.get_config()

    assert response.status_code == 200
    data = response.json()
    assert_config_response(data, source="none", config=EMPTY_CONFIG)
    assert data["error"] is None
    assert data["drives"] == [
        {"name": "動画", "enabled": True},
        {"name": "Photos", "enabled": False},
    ]
    assert data["remotes"] == ["gdrive:", "other:", "x:"]
    assert data["remotes_error"] is None
    assert data["next_sync_at"] is None


# SPEC-ADDON-005 (I1): the old file next to the addon is never read.
async def test_the_file_beside_the_addon_is_never_read(sworld, monkeypatch):
    sworld.add_drive("Photos")
    sworld.plant_legacy(monkeypatch, {
        "schedule": "0 3 * * *",
        "max_delete": 7,
        "mappings": [{"drive": "Photos", "remote": "gdrive:photos"}],
    })

    status = await sworld.status()
    await router_module.on_startup()
    loops = sworld.scheduler_loops()
    response = await sworld.get_config()

    assert status["drives"] == []
    assert status["schedule"] is None
    assert loops == []
    assert response.status_code == 200
    assert response.json()["source"] == "none"
    assert response.json()["config"] == EMPTY_CONFIG


# SPEC-ADDON-005, failure table: rclone listremotes fails or is missing.
@pytest.mark.parametrize("mode", ["fail", "missing"])
async def test_get_answers_with_no_remotes_when_rclone_cannot_list_them(sworld, mode):
    sworld.add_drive("Photos")
    sworld.listremotes = mode

    response = await sworld.get_config()

    assert response.status_code == 200
    data = response.json()
    assert data["remotes"] == []
    assert isinstance(data["remotes_error"], str) and data["remotes_error"]
    assert data["drives"] == [{"name": "Photos", "enabled": True}]


# SPEC-ADDON-005, item 5: a listremotes that runs past 10 s is killed.
async def test_a_listremotes_that_hangs_is_killed_and_reported(sworld):
    sworld.add_drive("Photos")
    sworld.listremotes = "hang"

    response = await sworld.get_config()

    assert response.status_code == 200
    assert response.json()["remotes"] == []
    assert response.json()["remotes_error"]
    assert sworld.killed >= 1


# SPEC-ADDON-005, failure table: drives.json cannot be read.
async def test_get_answers_500_when_drives_json_cannot_be_read(sworld):
    sworld.drives_error = ValueError("drives.json must be a JSON array")

    response = await sworld.get_config()

    assert response.status_code == 500


# SPEC-ADDON-005, item 10: one listremotes per call.
async def test_get_and_put_each_list_the_remotes_once(sworld):
    await sworld.get_config()
    assert sworld.listremote_calls == 1
    await sworld.put_config(body())
    assert sworld.listremote_calls == 2


# --- PUT /config, success -------------------------------------------------


# SPEC-ADDON-005, normal flow step 5 and "Stored file".
async def test_a_valid_put_writes_the_file_and_answers_like_get(sworld):
    sworld.add_drive("動画")
    sworld.add_folder("動画", "仕事/録画")
    payload = body(
        schedule="0 3 * * *",
        timezone="Asia/Tokyo",
        max_delete=50,
        mappings=[row("動画", "仕事//録画/", "gdrive:litloft/録画")],
        schema_version=99,
    )

    response = await sworld.put_config(payload)

    assert response.status_code == 200, response.text
    expected = {
        "schedule": "0 3 * * *",
        "timezone": "Asia/Tokyo",
        "max_delete": 50,
        "mappings": [row("動画", "仕事/録画", "gdrive:litloft/録画")],
    }
    assert_config_response(response.json(), source="saved", config=expected)
    assert response.json()["error"] is None
    assert sworld.stored() == {"schema_version": 1, **expected}
    assert sworld.stray_files() == []
    got = (await sworld.get_config()).json()
    assert_config_response(got, source="saved", config=expected)


# SPEC-ADDON-005, "Remote field": the remote is stored exactly as typed.
@pytest.mark.parametrize("remote", ["x:/a", "x:a", "x:a//b/", "x:a:b", "x: a "])
async def test_the_remote_is_stored_as_typed(sworld, remote):
    sworld.add_drive("Photos")

    response = await sworld.put_config(body(mappings=[row("Photos", "", remote)]))

    assert response.status_code == 200, response.text
    assert sworld.stored()["mappings"] == [row("Photos", "", remote)]
    assert response.json()["config"]["mappings"][0]["remote"] == remote


# SPEC-ADDON-005, item 3 "States": deleting every mapping saves a valid empty file.
async def test_deleting_every_mapping_saves_a_valid_file(sworld):
    sworld.add_drive("Photos")
    await sworld.put_config(body(mappings=[row("Photos", "", "gdrive:a")]))

    response = await sworld.put_config(body())

    assert response.status_code == 200
    assert response.json()["source"] == "saved"
    assert sworld.stored() == {"schema_version": 1, **EMPTY_CONFIG}
    assert (await sworld.status())["drives"] == []


# SPEC-ADDON-005, item 4: a PUT with no mappings never fails on listremotes.
@pytest.mark.parametrize("mode", ["fail", "missing"])
async def test_a_put_with_no_mappings_succeeds_without_remotes(sworld, mode):
    sworld.listremotes = mode

    response = await sworld.put_config(body(schedule="0 3 * * *", timezone="UTC"))

    assert response.status_code == 200, response.text
    assert response.json()["remotes"] == []
    assert response.json()["remotes_error"]
    assert sworld.stored()["schedule"] == "0 3 * * *"


# SPEC-ADDON-005, "Save-time rules": a drive whose policy is off is accepted.
async def test_a_drive_whose_policy_is_off_can_be_saved(sworld):
    sworld.add_drive("Photos", enabled=False)

    response = await sworld.put_config(body(mappings=[row("Photos", "", "gdrive:p")]))

    assert response.status_code == 200, response.text
    (entry,) = (await sworld.status())["drives"]
    assert entry["status"] == "disabled"


# SPEC-ADDON-005, "Save-time rules": an empty folder is accepted at save time.
async def test_a_folder_with_no_files_is_accepted(sworld):
    sworld.add_drive("Photos")
    sworld.add_folder("Photos", "empty", files=0)

    response = await sworld.put_config(body(mappings=[row("Photos", "empty", "gdrive:p")]))

    assert response.status_code == 200, response.text


# SPEC-ADDON-005, states: invalid -> saved on a successful PUT.
async def test_a_successful_put_replaces_an_invalid_file(sworld):
    sworld.write_file("{not json")

    response = await sworld.put_config(body(max_delete=3))

    assert response.status_code == 200
    assert response.json()["source"] == "saved"
    assert sworld.stored()["max_delete"] == 3


# --- PUT /config, refusals (I3, I7) ---------------------------------------


def _valid_world(world) -> None:
    world.add_drive("Photos")
    world.add_drive("Movies")
    world.add_folder("Photos", "a")
    world.add_folder("Photos", "b")


BASELINE = body(
    schedule="0 3 * * *",
    timezone="Asia/Tokyo",
    max_delete=9,
    mappings=[row("Photos", "a", "gdrive:base")],
)

REFUSALS = [
    # body shape
    ("not-object-list", [], [err("body", "invalid_body")]),
    ("not-object-string", "x", [err("body", "invalid_body")]),
    ("not-object-null", None, [err("body", "invalid_body")]),
    ("not-json", b"{not json", [err("body", "invalid_body")]),
    # top-level keys
    ("missing-schedule", {k: v for k, v in body().items() if k != "schedule"},
     [err("schedule", "missing_field")]),
    ("missing-timezone", {k: v for k, v in body().items() if k != "timezone"},
     [err("timezone", "missing_field")]),
    ("missing-max-delete", {k: v for k, v in body().items() if k != "max_delete"},
     [err("max_delete", "missing_field")]),
    ("missing-mappings", {k: v for k, v in body().items() if k != "mappings"},
     [err("mappings", "missing_field")]),
    ("mappings-not-list", body(mappings="x"), [err("mappings", "invalid_type")]),
    ("mappings-object", body(mappings={}), [err("mappings", "invalid_type")]),
    ("max-delete-string", body(max_delete="5"), [err("max_delete", "invalid_type")]),
    ("max-delete-float", body(max_delete=1.5), [err("max_delete", "invalid_type")]),
    ("max-delete-null", body(max_delete=None), [err("max_delete", "invalid_type")]),
    ("schedule-number", body(schedule=5), [err("schedule", "invalid_type")]),
    ("timezone-number", body(timezone=5), [err("timezone", "invalid_type")]),
    # top-level values
    ("max-delete-zero", body(max_delete=0), [err("max_delete", "max_delete_below_one")]),
    ("max-delete-negative", body(max_delete=-1), [err("max_delete", "max_delete_below_one")]),
    ("schedule-empty", body(schedule=""), [err("schedule", "invalid_cron")]),
    ("schedule-four-fields", body(schedule="0 3 * *"), [err("schedule", "invalid_cron")]),
    ("schedule-six-fields", body(schedule="0 0 3 * * *"), [err("schedule", "invalid_cron")]),
    ("schedule-garbage", body(schedule="a b c d e"), [err("schedule", "invalid_cron")]),
    ("timezone-unknown", body(timezone="Mars/Olympus"), [err("timezone", "invalid_timezone")]),
    ("timezone-empty", body(timezone=""), [err("timezone", "invalid_timezone")]),
    # row shape
    ("row-not-object", body(mappings=["x"]), [err("mappings", "invalid_type", 0)]),
    ("row-missing-drive", body(mappings=[{"path": "a", "remote": "gdrive:a"}]),
     [err("drive", "missing_field", 0)]),
    ("row-missing-path", body(mappings=[{"drive": "Photos", "remote": "gdrive:a"}]),
     [err("path", "missing_field", 0)]),
    ("row-missing-remote", body(mappings=[{"drive": "Photos", "path": "a"}]),
     [err("remote", "missing_field", 0)]),
    ("row-drive-number", body(mappings=[{"drive": 1, "path": "a", "remote": "gdrive:a"}]),
     [err("drive", "invalid_type", 0)]),
    ("row-path-null", body(mappings=[{"drive": "Photos", "path": None, "remote": "gdrive:a"}]),
     [err("path", "invalid_type", 0)]),
    ("row-remote-list", body(mappings=[{"drive": "Photos", "path": "a", "remote": ["gdrive:a"]}]),
     [err("remote", "invalid_type", 0)]),
    # row values
    ("path-absolute", body(mappings=[row("Photos", "/a", "gdrive:a")]),
     [err("path", "invalid_path", 0)]),
    ("path-parent", body(mappings=[row("Photos", "..", "gdrive:a")]),
     [err("path", "invalid_path", 0)]),
    ("path-inner-parent", body(mappings=[row("Photos", "a/../b", "gdrive:a")]),
     [err("path", "invalid_path", 0)]),
    ("remote-root", body(mappings=[row("Photos", "a", "gdrive:")]),
     [err("remote", "remote_root", 0)]),
    ("remote-root-slash", body(mappings=[row("Photos", "a", "gdrive:/")]),
     [err("remote", "remote_root", 0)]),
    ("remote-root-slashes", body(mappings=[row("Photos", "a", "gdrive://")]),
     [err("remote", "remote_root", 0)]),
    # save-time rules (I7)
    ("unknown-drive", body(mappings=[row("Gone", "", "gdrive:a")]),
     [err("drive", "unknown_drive", 0)]),
    ("folder-missing", body(mappings=[row("Photos", "nope", "gdrive:a")]),
     [err("path", "folder_not_found", 0)]),
    ("folder-is-a-file", body(mappings=[row("Photos", "f0.txt", "gdrive:a")]),
     [err("path", "folder_not_found", 0)]),
    ("remote-not-listed", body(mappings=[row("Photos", "a", "nowhere:a")]),
     [err("remote", "remote_not_listed", 0)]),
]


# SPEC-ADDON-005, "Refusal shape" and the malformed-body table (I3, I7).
@pytest.mark.parametrize(
    ("payload", "expected"),
    [(p, e) for _, p, e in REFUSALS],
    ids=[i for i, _, _ in REFUSALS],
)
async def test_a_refused_put_answers_422_with_every_error_and_changes_nothing(
    sworld, payload, expected
):
    _valid_world(sworld)
    assert (await sworld.put_config(BASELINE)).status_code == 200
    before = sworld.config_path.read_bytes()
    loops_before = sworld.scheduler_loops()

    if isinstance(payload, bytes):
        response = await sworld.put_config(payload)
    else:
        response = await sworld.client.request(
            "PUT", f"{BASE}/config",
            content=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 422, response.text
    data = response.json()
    assert set(data) == {"errors"}
    for e in data["errors"]:
        assert set(e) == {"mapping", "other", "field", "code", "message"}
        assert isinstance(e["message"], str) and e["message"]
    assert without_message(data["errors"]) == expected
    assert sworld.config_path.read_bytes() == before
    assert sworld.stray_files() == []
    assert sworld.scheduler_loops() == loops_before


# SPEC-ADDON-005, remote format: `-` prefix or no `:` is refused.
@pytest.mark.parametrize("remote", ["-gdrive:a", "gdrive"])
async def test_a_malformed_remote_is_refused(sworld, remote):
    _valid_world(sworld)

    response = await sworld.put_config(body(mappings=[row("Photos", "a", remote)]))

    assert response.status_code == 422
    errors = without_message(response.json()["errors"])
    assert err("remote", "remote_format", 0) in errors
    assert not sworld.config_path.exists()


# SPEC-ADDON-005, I7: a folder that escapes its drive root through a symlink.
@pytest.mark.parametrize("target", ["outside", "sibling-drive"])
async def test_a_folder_resolving_outside_its_drive_is_refused(sworld, tmp_path, target):
    _valid_world(sworld)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.txt").write_text("x")
    dest = outside if target == "outside" else sworld.drive_root("Movies")
    (sworld.drive_root("Photos") / "link").symlink_to(dest)

    response = await sworld.put_config(body(mappings=[row("Photos", "link", "gdrive:a")]))

    assert response.status_code == 422
    assert without_message(response.json()["errors"]) == [err("path", "folder_not_found", 0)]


# SPEC-ADDON-005, I7: an empty folder field checks the drive root itself.
async def test_a_whole_drive_mapping_whose_root_is_gone_is_refused(sworld):
    _valid_world(sworld)
    root = sworld.drive_root("Movies")
    for child in root.iterdir():
        child.unlink()
    root.rmdir()

    response = await sworld.put_config(body(mappings=[row("Movies", "", "gdrive:a")]))

    assert response.status_code == 422
    assert without_message(response.json()["errors"]) == [err("path", "folder_not_found", 0)]


# SPEC-ADDON-005: a folder inside its drive reached through a symlink is accepted.
async def test_a_folder_symlinked_within_its_drive_is_accepted(sworld):
    _valid_world(sworld)
    (sworld.drive_root("Photos") / "alias").symlink_to(sworld.drive_root("Photos") / "a")

    response = await sworld.put_config(body(mappings=[row("Photos", "alias", "gdrive:a")]))

    assert response.status_code == 200, response.text


# SPEC-ADDON-005: duplicates and overlaps name the later row and the earlier one.
@pytest.mark.parametrize(
    ("mappings", "code"),
    [
        ([row("Photos", "a", "gdrive:one"), row("Photos", "a/", "gdrive:two")], "duplicate_mapping"),
        ([row("Photos", "a", "gdrive:one"), row("Photos", "./a", "gdrive:two")], "duplicate_mapping"),
        ([row("Photos", "a", "gdrive:x"), row("Photos", "b", "gdrive:x/y")], "remotes_overlap"),
        ([row("Photos", "a", "gdrive:x/y"), row("Movies", "", "gdrive:x")], "remotes_overlap"),
        ([row("Photos", "a", "gdrive:x"), row("Photos", "b", "gdrive:/x")], "remotes_overlap"),
    ],
)
async def test_a_cross_row_conflict_names_both_rows(sworld, mappings, code):
    _valid_world(sworld)

    response = await sworld.put_config(body(mappings=mappings))

    assert response.status_code == 422
    (error,) = response.json()["errors"]
    assert (error["mapping"], error["other"], error["code"]) == (1, 0, code)
    assert not sworld.config_path.exists()


# SPEC-ADDON-005: every violation is collected, not only the first.
async def test_every_violation_is_reported_at_once(sworld):
    _valid_world(sworld)
    payload = body(
        schedule="nope",
        timezone="Mars/Olympus",
        max_delete=0,
        mappings=[
            row("Photos", "a", "gdrive:a"),
            row("Gone", "", "gdrive:b"),
            row("Photos", "/abs", "gdrive:c"),
            row("Photos", "b", "nowhere:d"),
        ],
    )

    response = await sworld.put_config(payload)

    assert response.status_code == 422
    errors = without_message(response.json()["errors"])
    for expected in [
        err("schedule", "invalid_cron"),
        err("timezone", "invalid_timezone"),
        err("max_delete", "max_delete_below_one"),
        err("drive", "unknown_drive", 1),
        err("path", "invalid_path", 2),
        err("remote", "remote_not_listed", 3),
    ]:
        assert expected in errors
    assert all(e["mapping"] != 0 for e in errors)


# SPEC-ADDON-005: a row with a shape error gets no other rule.
async def test_a_row_with_a_shape_error_gets_only_that_error(sworld):
    _valid_world(sworld)
    payload = body(
        mappings=[
            row("Photos", "a", "gdrive:x"),
            {"drive": 5, "path": "/abs", "remote": "gdrive:x/y"},
        ]
    )

    response = await sworld.put_config(payload)

    assert response.status_code == 422
    assert without_message(response.json()["errors"]) == [err("drive", "invalid_type", 1)]


# SPEC-ADDON-005, failure table: listremotes fails during a PUT that has mappings.
@pytest.mark.parametrize("mode", ["fail", "missing"])
async def test_unlisted_remotes_during_a_put_give_one_remotes_unavailable(sworld, mode):
    _valid_world(sworld)
    sworld.listremotes = mode

    response = await sworld.put_config(
        body(mappings=[row("Photos", "a", "gdrive:a"), row("Photos", "b", "gdrive:b")])
    )

    assert response.status_code == 422
    assert without_message(response.json()["errors"]) == [err("mappings", "remotes_unavailable")]
    assert not sworld.config_path.exists()


# SPEC-ADDON-005, failure table: drives.json unreadable during a PUT.
async def test_a_put_answers_500_when_drives_json_cannot_be_read(sworld):
    _valid_world(sworld)
    assert (await sworld.put_config(BASELINE)).status_code == 200
    before = sworld.config_path.read_bytes()
    loops_before = sworld.scheduler_loops()
    sworld.drives_error = ValueError("drives.json must be a JSON array")

    response = await sworld.put_config(body(mappings=[row("Photos", "b", "gdrive:z")]))

    assert response.status_code == 500
    assert sworld.config_path.read_bytes() == before
    assert sworld.scheduler_loops() == loops_before


# SPEC-ADDON-005, failure table and I3: a failed write leaves the old file and no temp file.
async def test_a_failed_write_keeps_the_previous_file_and_removes_the_temp(
    sworld, monkeypatch, caplog
):
    _valid_world(sworld)
    assert (await sworld.put_config(BASELINE)).status_code == 200
    before = sworld.config_path.read_bytes()
    loops_before = sworld.scheduler_loops()
    real_replace = os.replace

    def failing_replace(src, dst, *args, **kwargs):
        if Path(dst).parent == sworld.config_dir:
            raise OSError(28, "No space left on device")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", failing_replace)

    with caplog.at_level(logging.ERROR):
        response = await sworld.put_config(
            body(schedule="0 4 * * *", mappings=[row("Photos", "b", "gdrive:z")])
        )

    assert response.status_code == 500
    assert sworld.config_path.read_bytes() == before
    assert sworld.stray_files() == []
    assert sworld.scheduler_loops() == loops_before
    assert any(r.levelno == logging.ERROR for r in caplog.records)


# SPEC-ADDON-005, item 4: the directory is created on the first save.
async def test_the_first_save_creates_the_directory(sworld):
    assert not (sworld.data_dir / "addons").exists()

    response = await sworld.put_config(body())

    assert response.status_code == 200
    assert sworld.config_path.is_file()


# --- load-time rules (I2) -------------------------------------------------


VALID_FILE = {
    "schema_version": 1,
    "schedule": "0 3 * * *",
    "timezone": "Asia/Tokyo",
    "max_delete": 200,
    "mappings": [{"drive": "Photos", "path": "", "remote": "gdrive:p"}],
}


def _variant(**changes):
    out = json.loads(json.dumps(VALID_FILE))
    out.update(changes)
    return out


INVALID_FILES = [
    ("schema-2", _variant(schema_version=2)),
    ("schema-0", _variant(schema_version=0)),
    ("schema-string", _variant(schema_version="1")),
    ("remote-root", _variant(mappings=[{"drive": "Photos", "path": "", "remote": "gdrive:"}])),
    ("remote-root-slash", _variant(mappings=[{"drive": "Photos", "path": "", "remote": "gdrive:/"}])),
    ("cron-six-fields", _variant(schedule="0 0 3 * * *")),
    ("cron-empty", _variant(schedule="")),
    ("timezone-unknown", _variant(timezone="Mars/Olympus")),
    ("max-delete-zero", _variant(max_delete=0)),
    ("bad-path", _variant(mappings=[{"drive": "Photos", "path": "/abs", "remote": "gdrive:p"}])),
    ("overlap", _variant(mappings=[
        {"drive": "Photos", "path": "", "remote": "gdrive:p"},
        {"drive": "Movies", "path": "", "remote": "gdrive:p/q"},
    ])),
    ("not-json", "{not json"),
]


# SPEC-ADDON-005, load-time rules; failure table "Saved file ... fails a load-time rule"; I2.
@pytest.mark.parametrize("content", [c for _, c in INVALID_FILES], ids=[i for i, _ in INVALID_FILES])
async def test_a_stored_file_that_fails_a_load_time_rule_is_invalid(sworld, caplog, content):
    sworld.add_drive("Photos")
    sworld.add_drive("Movies")
    sworld.write_file(content)

    with caplog.at_level(logging.ERROR):
        got = await sworld.get_config()
        status = await sworld.status()
        await router_module.on_startup()

    assert got.status_code == 200
    data = got.json()
    assert_config_response(data, source="invalid", config=EMPTY_CONFIG)
    assert isinstance(data["error"], str) and data["error"]
    assert data["next_sync_at"] is None
    assert status["drives"] == []
    assert status["schedule"] is None
    assert sworld.scheduler_loops() == []
    assert any(r.levelno == logging.ERROR for r in caplog.records)


# SPEC-ADDON-005, failure table: a stored file that cannot be read is invalid.
async def test_an_unreadable_stored_file_is_invalid(sworld):
    sworld.config_path.mkdir(parents=True)

    data = (await sworld.get_config()).json()

    assert data["source"] == "invalid"
    assert data["error"]
    assert data["config"] == EMPTY_CONFIG


# SPEC-ADDON-005: save-time rules are not re-checked on load.
async def test_a_stored_mapping_of_a_drive_that_is_gone_still_loads(sworld):
    sworld.add_drive("Photos")
    sworld.write_file(_variant(mappings=[{"drive": "Gone", "path": "x", "remote": "nowhere:p"}]))

    data = (await sworld.get_config()).json()
    status = await sworld.status()

    assert data["source"] == "saved"
    assert data["config"]["mappings"] == [row("Gone", "x", "nowhere:p")]
    assert [(d["drive"], d["path"]) for d in status["drives"]] == [("Gone", "x")]


# SPEC-ADDON-005, "Stored file": a null or absent schedule and timezone load.
@pytest.mark.parametrize("drop", ["null", "absent"])
async def test_schedule_and_timezone_may_be_null_or_absent(sworld, drop):
    sworld.add_drive("Photos")
    content = _variant()
    if drop == "null":
        content.update(schedule=None, timezone=None)
    else:
        del content["schedule"], content["timezone"]
    sworld.write_file(content)

    data = (await sworld.get_config()).json()

    assert data["source"] == "saved"
    assert data["config"]["schedule"] is None
    assert data["config"]["timezone"] is None


# --- authorization (I8) ---------------------------------------------------


# SPEC-ADDON-005 (I8): non-admins get 403 and the file is never touched.
async def test_a_viewer_who_is_not_an_admin_cannot_read_or_replace_the_config(
    sworld, monkeypatch
):
    sworld.add_drive("Photos")
    monkeypatch.setattr(auth, "is_admin", lambda groups: False)
    api = FastAPI()
    api.include_router(router_module.router)
    calls: list[str] = []
    real_open = Path.open

    def watching_open(self, *args, **kwargs):
        if "cloud-sync" in str(self):
            calls.append(str(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", watching_open)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url="http://test"
    ) as client:
        got = await client.get(f"{BASE}/config")
        put = await client.put(f"{BASE}/config", json=body())

    assert got.status_code == 403
    assert put.status_code == 403
    assert not (sworld.data_dir / "addons").exists()
    assert calls == []
    assert sworld.listremote_calls == 0


# SPEC-ADDON-007: the section is offered to the admin-settings-sections slot.
def test_the_settings_section_is_declared_for_the_admin_settings_slot():
    entries = router_module.ADDON_META["slots"].get("admin-settings-sections", [])

    assert [e["id"] for e in entries] == ["cloud-sync-settings"]
