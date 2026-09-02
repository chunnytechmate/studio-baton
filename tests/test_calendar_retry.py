"""Google Calendar's transient faults, and where they land.

The calendar was the only adapter calling its vendor client raw: no retry, and
a `googleapiclient.HttpError` escaping as a traceback rather than an exit code.
It is also the adapter where that hurts most: `book` creates the event only
after the session document is already marked in progress, so a fault here is
the one thing that can still leave the two records disagreeing.
"""

from __future__ import annotations

import pytest

from baton.adapters.cal.google import _calendar_call, _http_status
from baton.errors import ConfigError, UpstreamError


class FakeHttpError(Exception):
    """Shaped like googleapiclient's, which is all this code reads of it."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status_code = status


class LegacyHttpError(Exception):
    """The older client exposed the status on `resp` and nowhere else."""

    class _Resp:
        def __init__(self, status: int) -> None:
            self.status = status

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.resp = self._Resp(status)


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """Backoff is real in production and pointless in a test."""
    monkeypatch.setattr("baton.core.retry.time.sleep", lambda _seconds: None)


# -- reading the status off whatever the client raised -----------------------


def test_the_status_is_read_from_the_modern_attribute():
    assert _http_status(FakeHttpError(429)) == 429


def test_the_status_is_read_from_the_legacy_response_too():
    """Pinning either attribute alone means the retry silently stops working
    after a client upgrade."""
    assert _http_status(LegacyHttpError(503)) == 503


def test_an_error_carrying_no_status_reads_as_none():
    assert _http_status(OSError("connection reset")) is None


# -- what gets another attempt ----------------------------------------------


def test_rate_limiting_is_retried_until_it_succeeds():
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise FakeHttpError(429)
        return "booked"

    assert _calendar_call("booking", flaky) == "booked"
    assert len(attempts) == 3


def test_the_five_hundred_family_is_retried():
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 2:
            raise FakeHttpError(503)
        return "listed"

    assert _calendar_call("listing", flaky) == "listed"


def test_a_dropped_connection_is_retried():
    """No HTTP status at all, but plainly worth another go."""
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 2:
            raise OSError("connection reset by peer")
        return "ok"

    assert _calendar_call("booking", flaky) == "ok"


def test_exhausted_retries_become_one_upstream_error():
    attempts = []

    def always_busy():
        attempts.append(1)
        raise FakeHttpError(429)

    with pytest.raises(UpstreamError) as caught:
        _calendar_call("booking", always_busy, attempts=3)

    assert len(attempts) == 3
    assert caught.value.details["service"] == "google-calendar"
    assert caught.value.details["attempts"] == 3
    assert "nothing partial was written" in (caught.value.remedy or "")


# -- what does not ----------------------------------------------------------


def test_a_refusal_is_not_retried():
    """Looping against a revoked token is how a nightly job burns an hour
    proving something that was never going to change."""
    attempts = []

    def refused():
        attempts.append(1)
        raise FakeHttpError(401)

    with pytest.raises(UpstreamError, match="refused the booking"):
        _calendar_call("booking", refused)

    assert len(attempts) == 1


def test_a_missing_event_is_not_retried_either():
    attempts = []

    def gone():
        attempts.append(1)
        raise FakeHttpError(404)

    with pytest.raises(UpstreamError):
        _calendar_call("delete", gone)

    assert len(attempts) == 1


def test_a_programming_error_is_not_retried_three_times():
    """Something with no status that is not a network fault is a bug here, and
    retrying a bug only delays the traceback while hiding what caused it."""
    attempts = []

    def broken():
        attempts.append(1)
        raise TypeError("expected str, got dict")

    with pytest.raises(UpstreamError, match="expected str"):
        _calendar_call("booking", broken)

    assert len(attempts) == 1


def test_a_baton_error_passes_straight_through():
    """Already inside the contract: re-wrapping it would bury the remedy the
    original error was carrying."""

    def already_ours():
        raise ConfigError("No refresh token configured.", remedy="Set it in the profile.")

    with pytest.raises(ConfigError, match="No refresh token"):
        _calendar_call("booking", already_ours)


# -- the contract this all exists to keep ------------------------------------


def test_every_vendor_failure_reaches_the_caller_as_a_baton_error():
    """The CLI shell catches BatonError and nothing else. Anything that escapes
    as a vendor exception reaches the operator as a traceback, which is the one
    thing `doctor` is built never to print."""
    for failure in (FakeHttpError(400), LegacyHttpError(500), OSError("boom"), RuntimeError("x")):

        def raiser(exc=failure):
            raise exc

        with pytest.raises(UpstreamError):
            _calendar_call("booking", raiser, attempts=2)
