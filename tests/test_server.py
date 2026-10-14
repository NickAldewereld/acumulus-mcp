"""Smoke tests for the MCP tool layer (FastMCP routing + JSON serialisation)."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import pytest
import respx

from acumulus_mcp import server


def _unwrap(tool_obj):
    """FastMCP tools may be either raw callables or wrappers with .fn / .func."""
    for attr in ("fn", "func", "__wrapped__"):
        inner = getattr(tool_obj, attr, None)
        if callable(inner):
            return inner
    if callable(tool_obj):
        return tool_obj
    raise TypeError(f"Cannot unwrap tool: {tool_obj!r}")


def _sent_xml(route) -> str:
    body = route.calls.last.request.content.decode()
    return parse_qs(body)["xmlstring"][0]


@pytest.fixture(autouse=True)
def _reset_client():
    server._client = None
    yield
    server._client = None


@respx.mock
async def test_whoami_returns_summary(base_url: str) -> None:
    respx.post(base_url + "picklists/picklist_accounts.php").respond(
        200, json={"status": 0, "accounts": {"account": [{"accountid": "1"}, {"accountid": "2"}]}}
    )
    out = await _unwrap(server.whoami)()
    parsed = json.loads(out)
    assert parsed["status"] == 0
    assert parsed["accounts_found"] == 2
    assert parsed["testmode"] == 0


@respx.mock
async def test_list_picklist_builds_path_and_extracts(base_url: str) -> None:
    route = respx.post(base_url + "picklists/picklist_contacts.php").respond(
        200, json={"status": 0, "contacts": {"contact": [{"contactid": "7"}]}}
    )
    out = await _unwrap(server.list_picklist)("contacts")
    parsed = json.loads(out)
    assert parsed["status"] == "success"
    assert parsed["totalresults"] == 1
    assert parsed["items"][0]["contactid"] == "7"
    assert route.called


@respx.mock
async def test_create_invoice_nests_customer_invoice_lines(base_url: str) -> None:
    route = respx.post(base_url + "invoices/invoice_add.php").respond(
        200, json={"status": 0, "invoice": {"invoicenumber": "202601"}}
    )
    out = await _unwrap(server.create_invoice)(
        customer={"companyname1": "ACME", "countrycode": "NL"},
        lines=[{"product": "Advies", "unitprice": "100", "vatrate": "21"}],
        invoice={"concept": "1"},
    )
    parsed = json.loads(out)
    assert parsed["invoice"]["invoicenumber"] == "202601"

    xml = _sent_xml(route)
    assert "<customer><companyname1>ACME</companyname1>" in xml
    assert "<countrycode>NL</countrycode>" in xml
    assert "<invoice><concept>1</concept>" in xml
    assert "<line><product>Advies</product><unitprice>100</unitprice><vatrate>21</vatrate></line>" in xml


@respx.mock
async def test_tool_error_path_returns_status_error(base_url: str) -> None:
    respx.post(base_url + "picklists/picklist_accounts.php").respond(
        200, json={"status": 1, "errors": {"error": [{"message": "boom"}]}}
    )
    out = await _unwrap(server.whoami)()
    parsed = json.loads(out)
    assert parsed["status"] == "error"
    assert parsed["errors"] == ["boom"]
