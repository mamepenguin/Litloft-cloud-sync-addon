"""The failure kind has to survive the trip to the browser.

`_classify_error` already distinguishes an expired token from any other rclone
failure, and the card offers a recovery step only for the former. That
distinction used to be recovered on the client by testing the English message
for the words "Authentication expired", which tied the panel to one wording;
carrying a `kind` replaces it. These tests pin the part that broke when the
field was first added — it reached the status dict but not the event, so the
live path (press Sync Now, watch it fail) never saw it.
"""
from __future__ import annotations

import pytest

from addons.cloud_sync.schemas import SyncResult


def _result() -> SyncResult:
    return SyncResult(
        transferred_files=0, transferred_bytes=0, errors=1, elapsed_seconds=0.5
    )


AUTH_LOG = [b"2026/09/03 ERROR : couldn't fetch token: oauth2: token expired"]
OTHER_LOG = [b"2026/09/03 ERROR : directory not found"]


class TestErrorKindReachesTheBrowser:
    async def test_auth_failure_broadcasts_its_kind(
        self, manager_under_test, broadcasts
    ):
        await manager_under_test._handle_completion("photos", 1, _result(), AUTH_LOG)

        event, payload = broadcasts[-1]
        assert event == "sync:error"
        assert payload["kind"] == "auth_expired"

    async def test_auth_failure_records_the_same_kind_in_status(
        self, manager_under_test, broadcasts
    ):
        await manager_under_test._handle_completion("photos", 1, _result(), AUTH_LOG)

        assert manager_under_test._status["photos"].error_kind == "auth_expired"

    async def test_the_event_and_the_status_never_disagree(
        self, manager_under_test, broadcasts
    ):
        """The regression was exactly this: one carried the kind, the other did not."""
        await manager_under_test._handle_completion("photos", 1, _result(), AUTH_LOG)

        _, payload = broadcasts[-1]
        assert payload["kind"] == manager_under_test._status["photos"].error_kind

    async def test_an_unclassified_failure_says_so_rather_than_staying_silent(
        self, manager_under_test, broadcasts
    ):
        """A missing key would let the client keep a previous drive's kind."""
        await manager_under_test._handle_completion("photos", 1, _result(), OTHER_LOG)

        event, payload = broadcasts[-1]
        assert event == "sync:error"
        assert "kind" in payload
        assert payload["kind"] is None
        assert manager_under_test._status["photos"].error_kind is None

    async def test_a_later_plain_failure_clears_an_earlier_auth_kind(
        self, manager_under_test, broadcasts
    ):
        """Reconnect, retry, fail for another reason: the panel must move on."""
        await manager_under_test._handle_completion("photos", 1, _result(), AUTH_LOG)
        assert manager_under_test._status["photos"].error_kind == "auth_expired"

        await manager_under_test._handle_completion("photos", 1, _result(), OTHER_LOG)

        assert manager_under_test._status["photos"].error_kind is None
        assert broadcasts[-1][1]["kind"] is None

    async def test_success_leaves_no_error_behind(
        self, manager_under_test, broadcasts
    ):
        await manager_under_test._handle_completion("photos", 1, _result(), AUTH_LOG)
        await manager_under_test._handle_completion("photos", 0, _result(), [])

        status = manager_under_test._status["photos"]
        assert status.status == "idle"
        assert status.error_kind is None
        assert status.error_message is None


class TestFailuresRaisedOutsideRclone:
    """`_handle_error` reports the failures that never reached a log to classify.

    It has no kind to offer, and must say so rather than omit the key: a client
    that keeps the previous value would go on showing a re-authentication
    prompt for a drive whose config simply went missing.
    """

    async def test_broadcasts_an_explicit_absence_of_kind(
        self, manager_under_test, broadcasts
    ):
        await manager_under_test._handle_error("photos", "remote not configured")

        event, payload = broadcasts[-1]
        assert event == "sync:error"
        assert "kind" in payload
        assert payload["kind"] is None

    async def test_records_no_kind_in_status(self, manager_under_test, broadcasts):
        await manager_under_test._handle_error("photos", "remote not configured")

        assert manager_under_test._status["photos"].error_kind is None
        assert manager_under_test._status["photos"].error_message == (
            "remote not configured"
        )

    async def test_displaces_an_earlier_auth_kind(
        self, manager_under_test, broadcasts
    ):
        await manager_under_test._handle_completion("photos", 1, _result(), AUTH_LOG)
        assert manager_under_test._status["photos"].error_kind == "auth_expired"

        await manager_under_test._handle_error("photos", "remote not configured")

        assert manager_under_test._status["photos"].error_kind is None
        assert broadcasts[-1][1]["kind"] is None


class TestClassifier:
    @pytest.mark.parametrize(
        "line",
        [
            b"oauth2: token expired",
            b"oauth2: cannot fetch token",
            b"Failed to copy: invalid_grant",
            b"token has been expired or revoked",
            b"NoCredentialProviders: no valid providers in chain",
        ],
    )
    def test_recognises_an_expired_credential(self, manager_under_test, line):
        assert manager_under_test._classify_error([line]) == "auth_expired"

    def test_leaves_an_ordinary_failure_unclassified(self, manager_under_test):
        assert manager_under_test._classify_error(OTHER_LOG) is None

    def test_survives_a_log_line_that_is_not_utf8(self, manager_under_test):
        """rclone echoes filenames, which are bytes and need not decode."""
        assert manager_under_test._classify_error([b"\xff\xfe not utf-8"]) is None
