"""SPEC-ADDON-003: start, cancel and log address a mapping by drive and `?path=` (I10)."""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.auth as auth
from addons.cloud_sync import router as router_module
from mapping_world import folder_log_name, mworld  # noqa: F401

BASE = "/api/addons/cloud-sync"


@pytest.fixture()
def client(mworld, monkeypatch):
    monkeypatch.setattr(router_module, "sync_manager", mworld.manager)
    api = FastAPI()
    api.include_router(router_module.router)
    api.dependency_overrides[auth.require_admin] = lambda: None
    with TestClient(api) as c:
        yield c


def wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        time.sleep(0.01)


def wait_idle(world, drive: str, path: str) -> None:
    wait_until(lambda: world.entry(drive, path).status != "syncing")


@pytest.mark.parametrize("query", ["仕事/録画", "仕事/録画/", "./仕事/録画", "./仕事//録画/"])
def test_start_normalizes_the_query_before_the_lookup(mworld, client, query):
    root = mworld.add_drive("Photos")
    mworld.add_folder("Photos", "仕事/録画")
    mworld.map("Photos")
    mworld.map("Photos", "仕事/録画")

    response = client.post(f"{BASE}/Photos/start", params={"path": query})
    wait_idle(mworld, "Photos", "仕事/録画")

    assert response.status_code == 200
    assert response.json() == {"status": "started", "drive": "Photos", "path": "仕事/録画"}
    assert mworld.sources() == [f"{root}/仕事/録画"]


def test_start_without_a_query_is_the_whole_drive(mworld, client):
    root = mworld.add_drive("Photos")
    mworld.add_folder("Photos", "a")
    mworld.map("Photos", "a")
    mworld.map("Photos")

    response = client.post(f"{BASE}/Photos/start")
    wait_idle(mworld, "Photos", "")

    assert response.status_code == 200
    assert response.json() == {"status": "started", "drive": "Photos", "path": ""}
    assert mworld.sources() == [str(root)]


def test_a_folder_name_with_query_characters_reaches_its_mapping(mworld, client):
    name = "a&b#c+d?e %f=g"
    root = mworld.add_drive("Photos")
    mworld.add_folder("Photos", name)
    mworld.map("Photos", name)

    response = client.post(f"{BASE}/Photos/start", params={"path": name})
    wait_idle(mworld, "Photos", name)

    assert response.status_code == 200
    assert mworld.sources() == [f"{root}/{name}"]


@pytest.mark.parametrize(
    "query",
    ["/abs", "//abs", "..", "../abs", "x/../abs", "abs/..", "ab\0s"],
)
@pytest.mark.parametrize("route", ["start", "cancel", "log"])
def test_a_query_the_path_rule_rejects_is_404_and_does_nothing(
    mworld, client, query, route
):
    root = mworld.add_drive("Photos")
    mworld.add_folder("Photos", "abs")
    mworld.map("Photos", "abs")
    mworld.map("Photos")
    release = mworld.hold_launch(f"{root}/abs")
    if route == "cancel":
        # The mapping the query would reach if it were not rejected is running.
        client.post(f"{BASE}/Photos/start", params={"path": "abs"})
        wait_until(lambda: len(mworld.launches) == 1)
    launched = len(mworld.launches)

    method = client.get if route == "log" else client.post
    response = method(f"{BASE}/Photos/{route}", params={"path": query})
    release.set()
    wait_idle(mworld, "Photos", "abs")

    assert response.status_code == 404
    assert len(mworld.launches) == launched
    assert mworld.terminated == []
    assert mworld.entry("Photos", "").status == "idle"


@pytest.mark.parametrize(
    ("route", "detail"),
    [
        ("start", "Mapping not found in sync config"),
        ("log", "Mapping not found in sync config"),
        ("cancel", "No sync in progress for this mapping"),
    ],
)
def test_a_pair_not_in_the_file_is_404(mworld, client, route, detail):
    mworld.add_drive("Photos")
    mworld.add_folder("Photos", "a")
    mworld.map("Photos", "a")

    method = client.get if route == "log" else client.post
    response = method(f"{BASE}/Photos/{route}", params={"path": "b"})

    assert response.status_code == 404
    assert response.json()["detail"] == detail
    assert mworld.launches == []


def test_starting_a_mapping_of_an_unknown_drive_is_404(mworld, client):
    mworld.map("Gone", "a")

    response = client.post(f"{BASE}/Gone/start", params={"path": "a"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Drive not found"


def test_a_rejected_file_answers_404_for_a_mapping_it_contains(mworld, client):
    mworld.add_drive("Photos")
    mworld.add_folder("Photos", "a")
    mworld.map("Photos", "a", remote="gd:x")
    mworld.map("Photos", "a/", remote="gd:y")

    response = client.post(f"{BASE}/Photos/start", params={"path": "a"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Mapping not found in sync config"
    assert mworld.launches == []


@pytest.mark.parametrize("policy", [False, RuntimeError("unreadable")], ids=["off", "raises"])
def test_a_folder_mapping_of_a_drive_turned_off_is_403(mworld, client, policy):
    mworld.add_drive("Photos", policy=policy)
    mworld.add_folder("Photos", "a")
    mworld.map("Photos", "a")

    response = client.post(f"{BASE}/Photos/start", params={"path": "a"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Cloud sync is turned off for this drive"
    assert mworld.launches == []
    assert mworld.entry("Photos", "a").status == "disabled"


def test_running_mapping_start_409_and_cancel_by_normalized_query(mworld, client):
    root = mworld.add_drive("Photos")
    mworld.add_folder("Photos", "a")
    mworld.map("Photos")
    mworld.map("Photos", "a")
    release = mworld.hold_launch(f"{root}/a")

    started = client.post(f"{BASE}/Photos/start", params={"path": "a/"})
    wait_until(lambda: len(mworld.launches) == 1)
    again = client.post(f"{BASE}/Photos/start", params={"path": "./a"})
    idle_sibling = client.post(f"{BASE}/Photos/cancel")
    cancelled = client.post(f"{BASE}/Photos/cancel", params={"path": "./a/"})
    release.set()
    wait_idle(mworld, "Photos", "a")

    assert started.status_code == 200
    assert again.status_code == 409
    assert again.json()["detail"] == "Sync already in progress"
    assert idle_sibling.status_code == 404
    assert idle_sibling.json()["detail"] == "No sync in progress for this mapping"
    assert cancelled.status_code == 200
    body = cancelled.json()
    assert (body["drive"], body["path"]) == ("Photos", "a")
    assert mworld.terminated == [f"{root}/a"]
    assert len(mworld.launches) == 1


def test_log_reads_the_file_of_the_named_mapping(mworld, client):
    mworld.add_drive("Photos")
    mworld.add_folder("Photos", "a")
    mworld.map("Photos")
    mworld.map("Photos", "a")
    mworld.log_dir.mkdir()
    (mworld.log_dir / "Photos.log").write_text("whole-drive log")
    (mworld.log_dir / folder_log_name("Photos", "Photos", "a")).write_text("folder log")

    texts = {}
    for label, params in [("none", {}), ("folder", {"path": "a"}), ("unnormalized", {"path": "./a/"})]:
        response = client.get(f"{BASE}/Photos/log", params=params)
        assert response.status_code == 200, label
        texts[label] = response.text

    assert "whole-drive log" in texts["none"] and "folder log" not in texts["none"]
    for label in ["folder", "unnormalized"]:
        assert "folder log" in texts[label], label
        assert "whole-drive log" not in texts[label], label
