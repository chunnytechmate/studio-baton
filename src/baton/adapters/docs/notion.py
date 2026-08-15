"""Session documents in Notion.

Property names and status values are read from configuration, never hardcoded:
the original scattered ``"Status"``, ``"Titles"`` and ``"Date (Flexible)"``
across twenty call sites, which is precisely why adopting someone else's
database meant editing code.

Two API facts shape this module. Notion accepts at most 100 children per
request, so appends are chunked. And a property can be one of several types
(``status``, ``select``, ``rich_text``, …) depending on how the database was
built, so reads accept whichever shape arrives rather than assuming one.
"""

from __future__ import annotations

from typing import Any

from ...core.config import Config
from ...core.retry import http_request
from ...errors import ConfigError, UpstreamError
from .base import Block, DocStatus, PreservePolicy

API_ROOT = "https://api.notion.com/v1"

#: Notion's documented ceiling on children per append request.
MAX_CHILDREN_PER_REQUEST = 100

#: Block types whose text lives under a ``rich_text`` array.
_RICH_TEXT_TYPES = frozenset(
    {
        "paragraph",
        "heading_1",
        "heading_2",
        "heading_3",
        "bulleted_list_item",
        "numbered_list_item",
        "to_do",
        "toggle",
        "quote",
        "callout",
        "code",
    }
)


class NotionDocStore:
    """A :class:`~baton.adapters.docs.base.DocStore` backed by Notion."""

    driver = "notion"

    def __init__(
        self,
        token: str,
        *,
        api_version: str = "2022-06-28",
        properties: dict[str, str] | None = None,
        statuses: dict[str, str] | None = None,
        preserve: PreservePolicy | None = None,
        timeout: float = 30.0,
        api_root: str = API_ROOT,
    ) -> None:
        self.token = token
        self.api_version = api_version
        self.properties = properties or {}
        self.statuses = statuses or {}
        self.preserve = preserve or PreservePolicy(rules=())
        self.timeout = timeout
        self.api_root = api_root.rstrip("/")

    @classmethod
    def from_config(cls, config: Config) -> NotionDocStore:
        return cls(
            token=str(config.secret("docs.notion.token_env")),
            api_version=str(config.get("docs.notion.api_version", "2022-06-28")),
            properties={k: str(v) for k, v in config.section("docs.properties").items()},
            statuses={k: str(v) for k, v in config.section("docs.statuses").items()},
            preserve=PreservePolicy.from_config(config.get("docs.preserve", [])),
        )

    # -- transport ---------------------------------------------------------

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": self.api_version,
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, json_body: Any = None) -> dict[str, Any]:
        headers = self._headers
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        response = http_request(
            method,
            f"{self.api_root}{path}",
            service="notion",
            headers=headers,
            json=json_body,
            timeout=self.timeout,
        )

        if response.status_code == 401:
            raise ConfigError(
                "Notion rejected the integration token.",
                remedy="Check the token named by docs.notion.token_env.",
            )
        if response.status_code == 404:
            raise ConfigError(
                "Notion returned 404 for this page.",
                remedy="A 404 here usually means the page exists but is not "
                "shared with the integration. Share it, then retry.",
            )
        if response.status_code >= 400:
            detail = response.text[:300]
            raise UpstreamError(
                f"Notion rejected the request: {detail}",
                service="notion",
                status=response.status_code,
            )

        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamError(
                "Notion returned a response that is not JSON.",
                service="notion",
                status=response.status_code,
            ) from exc

    # -- properties --------------------------------------------------------

    def _property_name(self, key: str) -> str:
        try:
            return self.properties[key]
        except KeyError:
            raise ConfigError(
                f"No Notion property is configured for `{key}`.",
                remedy=f"Add docs.properties.{key} to baton.yaml.",
            ) from None

    @staticmethod
    def _read_property(prop: dict[str, Any]) -> str:
        """Flatten whichever property shape Notion returned into text."""
        kind = prop.get("type", "")
        value = prop.get(kind)
        if value is None:
            return ""
        if kind in ("status", "select"):
            return str(value.get("name", "")) if isinstance(value, dict) else ""
        if kind == "date":
            return str(value.get("start", "")) if isinstance(value, dict) else ""
        if kind in ("rich_text", "title"):
            return "".join(part.get("plain_text", "") for part in value)
        if kind == "multi_select":
            return ", ".join(item.get("name", "") for item in value)
        if kind in ("number", "checkbox", "url", "email", "phone_number"):
            return str(value)
        return ""

    def get_status(self, doc_id: str) -> DocStatus:
        page = self._request("GET", f"/pages/{doc_id}")
        properties = page.get("properties", {}) or {}

        def read(key: str) -> str:
            name = self.properties.get(key)
            if not name or name not in properties:
                return ""
            return self._read_property(properties[name])

        blocks = self.list_blocks(doc_id)
        return DocStatus(
            doc_id=doc_id,
            status=read("status"),
            date=read("date"),
            titles=read("titles"),
            block_count=len(blocks),
            url=str(page.get("url", "")),
        )

    def set_status(self, doc_id: str, status: str) -> None:
        """Set the status property, resolving a configured key to its value.

        Accepts either a key from ``docs.statuses`` (``done``) or the literal
        value (``Done``), so callers can be written against Baton's vocabulary
        while the profile decides the wording.
        """
        value = self.statuses.get(status, status)
        name = self._property_name("status")
        # `status` and `select` take the same body shape, so one payload covers
        # both of the property types a studio is likely to have used.
        self._request(
            "PATCH",
            f"/pages/{doc_id}",
            {"properties": {name: {"select": {"name": value}}}},
        )

    # -- blocks ------------------------------------------------------------

    @classmethod
    def _block(cls, raw: dict[str, Any]) -> Block:
        kind = str(raw.get("type", ""))
        body = raw.get(kind, {}) or {}

        text = ""
        if kind in _RICH_TEXT_TYPES:
            text = "".join(part.get("plain_text", "") for part in body.get("rich_text", []))

        icon = ""
        icon_body = body.get("icon") or {}
        if isinstance(icon_body, dict):
            icon = str(icon_body.get("emoji", ""))

        url = ""
        if kind == "video":
            source = body.get("external") or body.get("file") or {}
            url = str(source.get("url", "")) if isinstance(source, dict) else ""
        elif kind in ("embed", "bookmark", "link_preview"):
            url = str(body.get("url", ""))

        return Block(id=str(raw.get("id", "")), type=kind, text=text, icon=icon, url=url, raw=raw)

    def list_blocks(self, doc_id: str) -> list[Block]:
        """Every top-level block, following pagination to the end."""
        blocks: list[Block] = []
        cursor: str | None = None
        while True:
            path = f"/blocks/{doc_id}/children?page_size=100"
            if cursor:
                path = f"{path}&start_cursor={cursor}"
            payload = self._request("GET", path)
            blocks.extend(self._block(raw) for raw in payload.get("results", []))
            if not payload.get("has_more"):
                return blocks
            cursor = payload.get("next_cursor")
            if not cursor:
                return blocks

    def append_blocks(self, doc_id: str, blocks: list[dict[str, Any]]) -> None:
        """Append blocks in chunks of at most 100.

        Chunking is not an optimisation: Notion rejects a larger request
        outright, and the original system worked around it by asking the model
        to split the payload itself.
        """
        for start in range(0, len(blocks), MAX_CHILDREN_PER_REQUEST):
            chunk = blocks[start : start + MAX_CHILDREN_PER_REQUEST]
            self._request("PATCH", f"/blocks/{doc_id}/children", {"children": chunk})

    def delete_blocks(self, block_ids: list[str]) -> int:
        deleted = 0
        for block_id in block_ids:
            self._request("DELETE", f"/blocks/{block_id}")
            deleted += 1
        return deleted

    # -- lifecycle ---------------------------------------------------------

    def health(self) -> None:
        """Confirm the token is valid by identifying the integration."""
        self._request("GET", "/users/me")
