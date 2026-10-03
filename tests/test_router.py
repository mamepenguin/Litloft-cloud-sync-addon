from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.auth as auth
from addons.cloud_sync import router as router_module
from addons.cloud_sync.service import PolicyBlocked


@pytest.fixture()
def client(monkeypatch):
    api = FastAPI()
    api.include_router(router_module.router)
    api.dependency_overrides[auth.require_admin] = lambda: None
    return TestClient(api)


@pytest.mark.parametrize(
    ("raised", "status"),
    [
        (None, 200),
        (PolicyBlocked("off"), 403),
        (ValueError("unmapped"), 404),
        (RuntimeError("busy"), 409),
    ],
    ids=["started", "policy-off", "unmapped", "already-running"],
)
def test_starting_a_sync_answers_with_the_reason_it_was_refused(
    client, monkeypatch, raised, status
):
    async def start_sync(drive):
        if raised is not None:
            raise raised

    monkeypatch.setattr(router_module.sync_manager, "start_sync", start_sync)

    assert client.post("/api/addons/cloud-sync/Photos/start").status_code == status


def test_a_viewer_who_is_not_an_admin_is_refused(monkeypatch):
    api = FastAPI()
    api.include_router(router_module.router)
    monkeypatch.setattr(auth, "is_admin", lambda groups: False)
    client = TestClient(api)

    assert client.get("/api/addons/cloud-sync/status").status_code == 403
    assert client.post("/api/addons/cloud-sync/Photos/start").status_code == 403
