"""Shared fixtures for cloud-sync addon tests.

`SyncManager` broadcasts over the core WebSocket manager, which has no
listeners under test. Each test gets a recorder in its place so the payloads
that reach the browser can be asserted directly — that wire, not the in-memory
status dict, is what the dashboard actually reads while a sync is running.
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def broadcasts(monkeypatch):
    """Every (event, payload) the manager would have sent, in order."""
    sent: list[tuple[str, dict]] = []

    async def record(event: str, payload: dict) -> None:
        sent.append((event, payload))

    from addons.cloud_sync import service

    monkeypatch.setattr(service.manager, "broadcast", record)
    return sent


@pytest.fixture()
def manager_under_test():
    from addons.cloud_sync.service import SyncManager

    return SyncManager()
