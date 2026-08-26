"""Webhook health must not pass a receiver that rejects the probe (M20).

The health check sends a GET because webhooks have no introspection
endpoint. It used to fail only on 5xx — a receiver answering 405 to every
GET passed, which proved something was listening and nothing else. Anything
from 400 up is now a failed check.
"""

from __future__ import annotations

import pytest

from baton.adapters.chat import drivers
from baton.errors import UpstreamError


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _messenger_answering(monkeypatch, status_code: int) -> drivers.WebhookMessenger:
    messenger = drivers.WebhookMessenger("https://example.invalid/hook", config=None)
    monkeypatch.setattr(drivers, "http_request", lambda *args, **kwargs: _Response(status_code))
    return messenger


def test_two_xx_is_healthy(monkeypatch):
    _messenger_answering(monkeypatch, 204).health()  # no raise


def test_method_not_allowed_fails_the_check(monkeypatch):
    """The exact case the old criterion waved through: alive, but nothing
    about webhook delivery was verified."""
    with pytest.raises(UpstreamError, match="405"):
        _messenger_answering(monkeypatch, 405).health()


def test_five_xx_still_fails(monkeypatch):
    with pytest.raises(UpstreamError, match="503"):
        _messenger_answering(monkeypatch, 503).health()
