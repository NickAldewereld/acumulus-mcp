"""Tests for AcumulusClient against a respx-mocked Acumulus API."""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest
import respx

from acumulus_mcp.client import (
    AcumulusClient,
    AcumulusError,
    _dict_to_xml,
    extract_list,
)


def _sent_xml(route) -> str:
    """Decode the xmlstring form field from the last request."""
    body = route.calls.last.request.content.decode()
    return parse_qs(body)["xmlstring"][0]


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_init_uses_env() -> None:
    c = AcumulusClient()
    assert c.contract_code == "123456"
    assert c.username == "test-user"
    assert c.api_token == "test-token"
    assert c.base_url.endswith("/stable/")


def test_init_explicit_overrides_env() -> None:
    c = AcumulusClient(contract_code="999", username="u", api_token="t")
    assert c.contract_code == "999"


def test_init_missing_credentials_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACUMULUS_USERNAME", raising=False)
    with pytest.raises(ValueError, match="ACUMULUS_USERNAME"):
        AcumulusClient()


def test_testmode_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACUMULUS_TESTMODE", "1")
    assert AcumulusClient().testmode == 1


# ---------------------------------------------------------------------------
# Request envelope
# ---------------------------------------------------------------------------


@respx.mock
async def test_request_builds_envelope_with_contract(base_url: str) -> None:
    route = respx.post(base_url + "picklists/picklist_accounts.php").respond(
        200, json={"status": 0, "accounts": {"account": []}}
    )
    async with AcumulusClient() as c:
        await c.request("picklists/picklist_accounts.php")

    xml = _sent_xml(route)
    assert "<format>json</format>" in xml
    assert "<testmode>0</testmode>" in xml
    assert "<contract><contractcode>123456</contractcode>" in xml
    assert "<username>test-user</username>" in xml
    assert "<password>test-token</password>" in xml


@respx.mock
async def test_request_merges_fields(base_url: str) -> None:
    route = respx.post(base_url + "invoices/invoice_add.php").respond(
        200, json={"status": 0, "invoice": {"invoicenumber": "1"}}
    )
    fields = {"customer": {"companyname1": "ACME", "invoice": {"line": [{"product": "A"}, {"product": "B"}]}}}
    async with AcumulusClient() as c:
        await c.request("invoices/invoice_add.php", fields)

    xml = _sent_xml(route)
    assert "<customer><companyname1>ACME</companyname1>" in xml
    assert "<invoice><line><product>A</product></line><line><product>B</product></line></invoice>" in xml


@respx.mock
async def test_request_success_returns_parsed(base_url: str) -> None:
    respx.post(base_url + "picklists/picklist_accounts.php").respond(
        200, json={"status": 0, "accounts": {"account": [{"accountid": "1"}]}}
    )
    async with AcumulusClient() as c:
        data = await c.request("picklists/picklist_accounts.php")
    assert data["status"] == 0
    assert data["accounts"]["account"][0]["accountid"] == "1"


@respx.mock
async def test_request_status_two_is_success(base_url: str) -> None:
    """Status 2 = success with warnings; must not raise."""
    respx.post(base_url + "x.php").respond(
        200, json={"status": 2, "warnings": {"warning": [{"message": "heads up"}]}, "ok": True}
    )
    async with AcumulusClient() as c:
        data = await c.request("x.php")
    assert data["ok"] is True


@respx.mock
async def test_request_error_status_raises(base_url: str) -> None:
    respx.post(base_url + "x.php").respond(
        200,
        json={"status": 1, "errors": {"error": [{"code": "403", "message": "Invalid credentials"}], "count_errors": 1}},
    )
    async with AcumulusClient() as c:
        with pytest.raises(AcumulusError) as ei:
            await c.request("x.php")
    assert "Invalid credentials" in str(ei.value)
    assert ei.value.errors == ["Invalid credentials"]
    # Credentials must never leak into the error payload.
    assert "contract" not in ei.value.payload.get("request", {})


@respx.mock
async def test_request_parses_xml_fallback(base_url: str) -> None:
    """When Acumulus answers in XML (e.g. hard auth failure), still parse it."""
    xml_body = (
        "<myxmlresponse><status>1</status>"
        "<errors><error><code>401</code><message>No contract</message></error>"
        "<count_errors>1</count_errors></errors></myxmlresponse>"
    )
    respx.post(base_url + "x.php").respond(200, text=xml_body)
    async with AcumulusClient() as c:
        with pytest.raises(AcumulusError) as ei:
            await c.request("x.php")
    assert "No contract" in str(ei.value)


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------


@respx.mock
async def test_retries_on_transient_then_succeeds(base_url: str) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, text="upstream down")
        return httpx.Response(200, json={"status": 0, "accounts": {"account": []}})

    respx.post(base_url + "picklists/picklist_accounts.php").mock(side_effect=handler)
    async with AcumulusClient() as c:
        data = await c.request("picklists/picklist_accounts.php")
    assert data["status"] == 0
    assert calls["n"] == 3


@respx.mock
async def test_exhausts_retries_then_raises(base_url: str) -> None:
    respx.post(base_url + "x.php").respond(503, text="still down")
    async with AcumulusClient() as c:
        with pytest.raises(httpx.HTTPError):
            await c.request("x.php")


# ---------------------------------------------------------------------------
# Helpers (pure)
# ---------------------------------------------------------------------------


def test_dict_to_xml_repeats_lists_and_escapes() -> None:
    xml = _dict_to_xml({"line": [{"d": "A & B"}, {"d": "<C>"}]})
    assert xml == "<line><d>A &amp; B</d></line><line><d>&lt;C&gt;</d></line>"


def test_dict_to_xml_bool_and_none() -> None:
    assert _dict_to_xml({"a": True, "b": False, "c": None}) == "<a>1</a><b>0</b><c></c>"


def test_extract_list_handles_single_dict() -> None:
    data = {"accounts": {"account": {"accountid": "1"}}}
    assert extract_list(data, "accounts") == [{"accountid": "1"}]


def test_extract_list_missing_returns_empty() -> None:
    assert extract_list({"status": 0}, "accounts") == []
