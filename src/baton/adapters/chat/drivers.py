"""Message drivers: LINE, Telegram, and a generic webhook.

All three are plain HTTP with a token, so they share one shape and differ only
in envelope. That sameness is deliberate — the original system had LINE logic
duplicated across three skills, and every copy had drifted by the time anyone
looked.
"""

from __future__ import annotations

import hmac
from typing import Any
from urllib.parse import quote

from ...core.config import Config
from ...core.retry import http_request
from ...errors import ConfigError, UpstreamError
from .base import SendOutcome, resolve_contact


class _HttpMessenger:
    """Shared plumbing for the HTTP drivers."""

    driver = "http"

    def __init__(self, token: str, *, api_root: str, service: str, timeout: float = 20.0) -> None:
        self.token = token
        self.api_root = api_root.rstrip("/")
        self.service = service
        self.timeout = timeout

    # -- to be provided by each driver ------------------------------------

    # Stubs, not abstract methods: each driver overrides all four, and an
    # ABC would force an import-time metaclass dance for four one-liners.
    def _endpoint(self, recipient_id: str) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def _payload(self, recipient_id: str, text: str) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def _headers(self) -> dict[str, str]:  # pragma: no cover - overridden
        raise NotImplementedError

    def _check(self, response_status: int, body: str) -> str | None:
        """Return an error message when the platform refused the send, else ``None``."""
        return None if response_status < 400 else f"HTTP {response_status}: {body[:200]}"

    # -- Messenger ----------------------------------------------------------

    def resolve(self, name: str) -> str:
        _, recipient_id = resolve_contact(self._config_for_resolve(), name)
        return recipient_id

    def _config_for_resolve(self) -> Config:  # pragma: no cover - replaced per driver
        raise NotImplementedError

    def send(self, recipient_id: str, text: str) -> SendOutcome:
        body = self._payload(recipient_id, text)
        headers = self._headers()
        self._sign(body, headers)
        response = http_request(
            "POST",
            self._endpoint(recipient_id),
            service=self.service,
            headers=headers,
            json=body,
            timeout=self.timeout,
        )
        problem = self._check(response.status_code, response.text)
        if problem:
            # The platform answered and refused. Not retryable, and not
            # something a caller may treat as success.
            raise UpstreamError(
                f"{self.service} refused the message: {problem}",
                service=self.service,
                status=response.status_code,
                remedy="The message was NOT delivered. Check the recipient id and "
                "the bot's ability to message them.",
            )
        return SendOutcome(sent=True, recipient=recipient_id)

    def _sign(self, body: dict[str, Any], _headers: dict[str, str]) -> None:
        """Hook for drivers that authenticate the request body."""

    def health(self) -> None:
        """Default: a get-me style probe supplied by the driver."""
        raise ConfigError(f"The {self.service} driver has no health check.")


class LineMessenger(_HttpMessenger):
    """LINE Messaging API push."""

    driver = "line"

    def __init__(self, token: str, *, api_url: str, config: Config, timeout: float = 20.0) -> None:
        super().__init__(token, api_root=api_url, service="line", timeout=timeout)
        self._config = config

    @classmethod
    def from_config(cls, config: Config) -> LineMessenger:
        return cls(
            token=str(config.secret("chat.line.token_env")),
            api_url=str(config.get("chat.line.api_url", "https://api.line.me/v2/bot/message/push")),
            config=config,
        )

    def _config_for_resolve(self) -> Config:
        return self._config

    def _endpoint(self, _recipient_id: str) -> str:
        return self.api_root

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _payload(self, recipient_id: str, text: str) -> dict[str, Any]:
        return {
            "to": recipient_id,
            "messages": [{"type": "text", "text": text}],
        }


class TelegramMessenger(_HttpMessenger):
    """Telegram Bot API sendMessage."""

    driver = "telegram"

    def __init__(self, token: str, *, api_url: str, config: Config, timeout: float = 20.0) -> None:
        super().__init__(token, api_root=api_url, service="telegram", timeout=timeout)
        self._config = config

    @classmethod
    def from_config(cls, config: Config) -> TelegramMessenger:
        return cls(
            token=str(config.secret("chat.telegram.token_env")),
            api_url=str(config.get("chat.telegram.api_url", "https://api.telegram.org")),
            config=config,
        )

    def _config_for_resolve(self) -> Config:
        return self._config

    def _endpoint(self, recipient_id: str) -> str:
        # The recipient travels in the path, so it must be quoted — a chat id
        # is numeric today but nothing guarantees that tomorrow.
        return f"{self.api_root}/bot{quote(self.token, safe='')}/sendMessage"

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def _payload(self, recipient_id: str, text: str) -> dict[str, Any]:
        return {"chat_id": recipient_id, "text": text}

    def _check(self, response_status: int, body: str) -> str | None:
        if response_status < 400:
            return None
        # Telegram reports "chat not found" as a 400 with a description; keep
        # it, because it names the fix (the recipient must message the bot
        # first before a bot can initiate).
        return f"HTTP {response_status}: {body[:200]}"


class WebhookMessenger(_HttpMessenger):
    """POST to a URL of your choosing — for Slack, n8n, or anything else.

    The recipient id is passed through in the payload, so the receiving end
    decides what to do with it. An optional shared secret is signed rather
    than sent, so the receiver can verify the call came from Baton.
    """

    driver = "webhook"

    def __init__(
        self,
        url: str,
        *,
        secret: str | None = None,
        config: Config,
        timeout: float = 20.0,
    ) -> None:
        super().__init__("", api_root=url, service="webhook", timeout=timeout)
        self.secret = secret
        self._config = config

    @classmethod
    def from_config(cls, config: Config) -> WebhookMessenger:
        return cls(
            url=str(config.secret("chat.webhook.url_env")),
            secret=config.secret("chat.webhook.secret_env", required=False),
            config=config,
        )

    def _config_for_resolve(self) -> Config:
        return self._config

    def _endpoint(self, recipient_id: str) -> str:
        return self.api_root

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def _payload(self, recipient_id: str, text: str) -> dict[str, Any]:
        return {"recipient": recipient_id, "text": text}

    def _sign(self, body: dict[str, Any], headers: dict[str, str]) -> None:
        """Sign the serialised body, so the receiver can verify the request.

        The body is serialised the same way the HTTP layer serialises it
        (``json=`` uses compact separators), which is what makes the signature
        verifiable on the other end.
        """
        if not self.secret:
            return
        import json as _json

        encoded = _json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
        headers["X-Baton-Signature"] = hmac.new(self.secret.encode(), encoded, "sha256").hexdigest()

    def _check(self, response_status: int, body: str) -> str | None:
        # A webhook receiver's contract is only "2xx means accepted".
        return None if response_status < 400 else f"HTTP {response_status}: {body[:200]}"

    def health(self) -> None:
        """Webhooks have no introspection endpoint; connectivity is the check."""
        response = http_request("GET", self.api_root, service="webhook", timeout=self.timeout)
        if response.status_code >= 500:
            raise UpstreamError(
                f"The webhook endpoint returned {response.status_code}.",
                service="webhook",
                status=response.status_code,
            )
