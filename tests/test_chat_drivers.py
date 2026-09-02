"""The HTTP messenger drivers: mainly the property that keeps a retried send
from reaching a parent twice.

``core.retry.http_request`` retries a transient failure automatically. For
LINE, a retry that fires after the platform already accepted the first
attempt (the response was merely lost, not the delivery) must not read as
a second, different message. LINE's own answer to this is an idempotency
header (``X-Line-Retry-Key``); this file pins that Baton actually sends it,
and sends the *same* one for a genuine retry of the same content.
"""

from __future__ import annotations

from baton.adapters.chat.drivers import LineMessenger, TelegramMessenger, WebhookMessenger


class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "{}") -> None:
        self.status_code = status_code
        self.text = text


def _capture(monkeypatch, module):
    calls: list[dict] = []

    def fake_http_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        return _FakeResponse()

    monkeypatch.setattr(module, "http_request", fake_http_request)
    return calls


def test_line_send_carries_a_retry_key(monkeypatch):
    import baton.adapters.chat.drivers as drivers

    calls = _capture(monkeypatch, drivers)
    messenger = LineMessenger(token="tok-12345", api_url="https://line.invalid/push", config=None)

    messenger.send("U123", "hello")

    assert len(calls) == 1
    assert "X-Line-Retry-Key" in calls[0]["headers"]


def test_line_retry_key_is_stable_for_the_same_message(monkeypatch):
    import baton.adapters.chat.drivers as drivers

    calls = _capture(monkeypatch, drivers)
    messenger = LineMessenger(token="tok-12345", api_url="https://line.invalid/push", config=None)

    messenger.send("U123", "hello")
    messenger.send("U123", "hello")  # a genuine retry of the identical send

    key_a = calls[0]["headers"]["X-Line-Retry-Key"]
    key_b = calls[1]["headers"]["X-Line-Retry-Key"]
    assert key_a == key_b, "retrying the same message must reuse one delivery key"


def test_line_retry_key_changes_with_the_message(monkeypatch):
    import baton.adapters.chat.drivers as drivers

    calls = _capture(monkeypatch, drivers)
    messenger = LineMessenger(token="tok-12345", api_url="https://line.invalid/push", config=None)

    messenger.send("U123", "hello")
    messenger.send("U123", "a different lesson entirely")

    key_a = calls[0]["headers"]["X-Line-Retry-Key"]
    key_b = calls[1]["headers"]["X-Line-Retry-Key"]
    assert key_a != key_b, "two distinct messages must not share a delivery key"


def test_line_retry_key_changes_with_the_recipient(monkeypatch):
    import baton.adapters.chat.drivers as drivers

    calls = _capture(monkeypatch, drivers)
    messenger = LineMessenger(token="tok-12345", api_url="https://line.invalid/push", config=None)

    messenger.send("U123", "hello")
    messenger.send("U999", "hello")

    key_a = calls[0]["headers"]["X-Line-Retry-Key"]
    key_b = calls[1]["headers"]["X-Line-Retry-Key"]
    assert key_a != key_b, "the same text to a different person must not share a delivery key"


def test_telegram_send_has_no_retry_key(monkeypatch):
    """Telegram's sendMessage has no idempotency header to set: confirms the
    hook is opt-in per driver rather than something LINE-specific leaking in."""
    import baton.adapters.chat.drivers as drivers

    calls = _capture(monkeypatch, drivers)
    messenger = TelegramMessenger(token="tok", api_url="https://tg.invalid", config=None)

    messenger.send("123", "hello")

    assert "X-Line-Retry-Key" not in calls[0]["headers"]


def test_webhook_send_has_no_retry_key(monkeypatch):
    import baton.adapters.chat.drivers as drivers

    calls = _capture(monkeypatch, drivers)
    messenger = WebhookMessenger(url="https://hook.invalid", config=None)

    messenger.send("anyone", "hello")

    assert "X-Line-Retry-Key" not in calls[0]["headers"]
