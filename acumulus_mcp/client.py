"""
Thin async HTTP client for the Acumulus API.

The Acumulus API is not RESTful: every call is a POST to a per-function
endpoint under

    https://api.sielsystems.nl/acumulus/stable/<group>/<function>.php

The request is an XML document submitted as a single form field,
`xmlstring`. Every request carries the same envelope:

    <?xml version="1.0" encoding="UTF-8"?>
    <myxml>
        <format>json</format>
        <testmode>0</testmode>
        <lang>nl</lang>
        <contract>
            <contractcode>123456</contractcode>
            <username>...</username>
            <password>...</password>
        </contract>
        ...function-specific fields...
    </myxml>

With `<format>json</format>` Acumulus answers in JSON, so the happy path
needs no XML parser. A small stdlib XML fallback covers the rare case
where Acumulus answers in XML anyway (e.g. some hard auth failures).

The response carries a `status` field: 0 = success, 1 = failed,
2 = success with warnings, 3 = exception. This client raises
`AcumulusError` on 1 and 3, and returns the parsed response on 0 and 2.

This client is intentionally low-level. It does not model individual
resources — that is left to the higher-level MCP tool layer (and to
`acumulus_request` for ad-hoc calls).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import xml.etree.ElementTree as ET
from typing import Any
from xml.sax.saxutils import escape as _xml_escape

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.sielsystems.nl/acumulus/stable/"
DEFAULT_TIMEOUT = 30.0
DEFAULT_USER_AGENT = "acumulus-mcp/0.1.0 (+https://github.com/NickAldewereld/acumulus-mcp)"

# status values Acumulus returns in the response envelope.
_STATUS_SUCCESS = {0, 2, "0", "2"}


class AcumulusError(Exception):
    """Raised when Acumulus returns a failing status (1 or 3)."""

    def __init__(
        self,
        message: str,
        *,
        errors: list[str] | None = None,
        warnings: list[str] | None = None,
        payload: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.errors = errors or []
        self.warnings = warnings or []
        self.payload = payload or {}


class AcumulusClient:
    """Async client for the Acumulus XML-over-POST API."""

    def __init__(
        self,
        contract_code: str | None = None,
        username: str | None = None,
        api_token: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str = DEFAULT_USER_AGENT,
        max_retries: int = 2,
        testmode: int | None = None,
        lang: str = "nl",
    ) -> None:
        self.contract_code = contract_code or os.environ.get("ACUMULUS_CONTRACT_CODE")
        self.username = username or os.environ.get("ACUMULUS_USERNAME")
        # The third credential is an API token you generate in Acumulus; it
        # travels in the <password> element. Accept either env name.
        self.api_token = (
            api_token
            or os.environ.get("ACUMULUS_API_TOKEN")
            or os.environ.get("ACUMULUS_PASSWORD")
        )
        missing = [
            name
            for name, val in (
                ("ACUMULUS_CONTRACT_CODE", self.contract_code),
                ("ACUMULUS_USERNAME", self.username),
                ("ACUMULUS_API_TOKEN", self.api_token),
            )
            if not val
        ]
        if missing:
            raise ValueError(
                "Missing Acumulus credentials: "
                + ", ".join(missing)
                + ". Set the env vars or pass them to AcumulusClient."
            )

        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout
        self.user_agent = user_agent
        self.max_retries = max_retries
        if testmode is None:
            self.testmode = 1 if os.environ.get("ACUMULUS_TESTMODE") in ("1", "true", "yes") else 0
        else:
            self.testmode = int(testmode)
        self.lang = lang
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "AcumulusClient":
        self._ensure_client()
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers={"User-Agent": self.user_agent},
            )
        return self._client

    def _contract_block(self) -> dict[str, Any]:
        return {
            "contractcode": self.contract_code,
            "username": self.username,
            "password": self.api_token,
        }

    def _build_envelope(self, fields: dict[str, Any] | None) -> str:
        body: dict[str, Any] = {
            "format": "json",
            "testmode": self.testmode,
            "lang": self.lang,
            "contract": self._contract_block(),
        }
        if fields:
            body.update(fields)
        return '<?xml version="1.0" encoding="UTF-8"?>\n<myxml>' + _dict_to_xml(body) + "</myxml>"

    async def request(
        self,
        api_path: str,
        fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make a single POST call to an Acumulus function endpoint.

        Args:
            api_path: Path below the stable base, e.g.
                "picklists/picklist_accounts.php" or "invoices/invoice_add.php".
            fields: Function-specific fields merged into the request envelope.
                Nested dicts become nested elements; a list value repeats its
                element (how Acumulus expects multiple <line> entries).

        Returns the parsed response on success (status 0 or 2). Raises
        AcumulusError on a failing status (1 or 3).
        """
        client = self._ensure_client()
        xml = self._build_envelope(fields)
        url = self.base_url + api_path.lstrip("/")

        attempt = 0
        while True:
            try:
                resp = await client.post(url, data={"xmlstring": xml})
                resp.raise_for_status()
                data = _parse_response(resp.text)
                break
            except httpx.HTTPError as e:
                attempt += 1
                if attempt > self.max_retries:
                    raise
                logger.warning("Acumulus transient error on %s (attempt %d): %s", api_path, attempt, e)
                await asyncio.sleep(0.5 * (2 ** (attempt - 1)))

        status = data.get("status")
        if status in _STATUS_SUCCESS:
            return data

        errors = _collect_messages(data.get("errors"))
        warnings = _collect_messages(data.get("warnings"))
        msg = "; ".join(errors) if errors else f"Acumulus error (status {status}): {data}"
        logger.warning("Acumulus error: api_path=%s status=%s errors=%s", api_path, status, errors)
        raise AcumulusError(
            msg,
            errors=errors,
            warnings=warnings,
            # Never log credentials: the contract block is omitted here.
            payload={"request": {"api_path": api_path, "fields": fields}, "response": data},
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dict_to_xml(d: dict[str, Any]) -> str:
    """Render a dict as Acumulus request XML (no wrapping root element).

    - dict value  -> nested element
    - list value  -> the element is repeated once per item (e.g. <line>)
    - scalar      -> escaped text
    """
    return "".join(_render_element(key, val) for key, val in d.items())


def _render_element(key: str, val: Any) -> str:
    if isinstance(val, dict):
        return f"<{key}>{_dict_to_xml(val)}</{key}>"
    if isinstance(val, (list, tuple)):
        return "".join(_render_element(key, item) for item in val)
    return f"<{key}>{_xml_escape(_scalar(val))}</{key}>"


def _scalar(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)


def _parse_response(text: str) -> dict[str, Any]:
    """Parse an Acumulus response. JSON when format=json; XML as a fallback."""
    text = (text or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"result": parsed}
    except ValueError:
        pass
    # XML fallback. Acumulus error responses are not always wrapped in a
    # single root element, so wrap defensively before parsing.
    for candidate in (text, f"<myxml>{text}</myxml>"):
        try:
            return _etree_to_dict(ET.fromstring(candidate))
        except ET.ParseError:
            continue
    return {"status": 3, "errors": {"error": [{"message": "Unparseable Acumulus response"}]}}


def _etree_to_dict(elem: ET.Element) -> dict[str, Any]:
    """Minimal ElementTree -> dict. Repeated tags collapse into a list."""
    result: dict[str, Any] = {}
    for child in elem:
        value: Any = _etree_to_dict(child) if len(child) else (child.text or "")
        if child.tag in result:
            existing = result[child.tag]
            if isinstance(existing, list):
                existing.append(value)
            else:
                result[child.tag] = [existing, value]
        else:
            result[child.tag] = value
    return result


def _collect_messages(block: Any) -> list[str]:
    """Pull human-readable messages out of an errors/warnings block."""
    if not block:
        return []
    items = block.get("error") or block.get("warning") if isinstance(block, dict) else block
    if items is None:
        return []
    if isinstance(items, dict):
        items = [items]
    out: list[str] = []
    for item in items:
        if isinstance(item, dict):
            out.append(str(item.get("message") or item))
        else:
            out.append(str(item))
    return out


def extract_list(data: dict[str, Any], plural: str, singular: str | None = None) -> list[dict[str, Any]]:
    """Pull the item list out of an Acumulus response.

    Acumulus nests lists as {plural: {singular: [...]}} (mirroring the XML).
    A single item may arrive as a dict rather than a list.
    """
    singular = singular or (plural[:-1] if plural.endswith("s") else plural)
    container = data.get(plural)
    if container is None:
        return []
    items = container.get(singular) if isinstance(container, dict) else container
    if items is None:
        return []
    if isinstance(items, dict):
        return [items]
    return list(items)
