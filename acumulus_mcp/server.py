"""
Acumulus MCP server.

Exposes the Acumulus API as MCP tools so any MCP-compatible client
(Claude Desktop, Claude Code, Cursor, etc.) can read and modify your
Acumulus administration via natural language.

Design notes:
  * The thin layer is `acumulus_request`, which lets you call any
    function endpoint with arbitrary fields — useful for endpoints we
    haven't wrapped yet, and as a safety valve when Acumulus ships new
    ones. Endpoint paths match the Acumulus docs verbatim (e.g.
    "invoices/invoice_add.php").
  * Higher-level tools wrap the most common operations (picklists,
    adding an invoice) with field names that match the Acumulus docs,
    so siel.nl/acumulus/API remains directly usable.
  * All write operations run against the live administration unless
    ACUMULUS_TESTMODE=1 is set, in which case Acumulus validates but
    does not persist — useful for a dry run before going live.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp.server.fastmcp import FastMCP

from .client import AcumulusClient, AcumulusError, extract_list

logger = logging.getLogger("acumulus-mcp")

# Single client instance for the lifetime of the server process. Created
# lazily on first use so importing this module (e.g. for tests) doesn't
# require the Acumulus credentials to be set.
_client: AcumulusClient | None = None


@asynccontextmanager
async def _lifespan(_server: FastMCP) -> AsyncIterator[None]:
    global _client
    try:
        yield
    finally:
        if _client is not None:
            await _client.close()
            _client = None


mcp = FastMCP(
    "acumulus",
    instructions=(
        "Tools for reading and modifying an Acumulus administration. "
        "Use `acumulus_request` for any endpoint not covered by a dedicated tool; "
        "endpoint paths match the docs at siel.nl/acumulus/API (e.g. "
        "'invoices/invoice_add.php', 'picklists/picklist_contacts.php'). "
        "All tools return parsed JSON from Acumulus; failing calls are raised with "
        "the messages Acumulus returned. Set ACUMULUS_TESTMODE=1 to validate writes "
        "without persisting them."
    ),
    lifespan=_lifespan,
)


def _get_client() -> AcumulusClient:
    global _client
    if _client is None:
        _client = AcumulusClient()
    return _client


def _result(data: Any) -> str:
    """Serialise tool output. MCP returns text; JSON keeps it round-trippable."""
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _error(e: AcumulusError) -> str:
    return _result({"status": "error", "errors": e.errors, "warnings": e.warnings})


# --------------------------------------------------------------------------
# Generic escape hatch
# --------------------------------------------------------------------------


@mcp.tool()
async def acumulus_request(api_path: str, fields: dict[str, Any] | None = None) -> str:
    """Call any Acumulus function endpoint directly.

    Use this for endpoints not covered by the dedicated tools below.

    Args:
        api_path: Path below the stable base, matching the Acumulus docs,
            e.g. "picklists/picklist_vattypes.php", "invoices/invoice_add.php",
            "invoices/invoice_set_payment_status.php", "entry/entry_info.php".
        fields: Extra fields merged into the request envelope. Nested dicts
            become nested elements; a list value repeats its element (e.g.
            {"customer": {"invoice": {"line": [{...}, {...}]}}}). Do NOT
            include the contract/credentials block — that is added for you.

    Returns:
        JSON-encoded response from Acumulus.
    """
    try:
        data = await _get_client().request(api_path, fields)
    except AcumulusError as e:
        return _result({"status": "error", "errors": e.errors, "warnings": e.warnings, "request": {"api_path": api_path}})
    return _result(data)


# --------------------------------------------------------------------------
# Picklists (reference data: accounts, contacts, products, vat types, ...)
# --------------------------------------------------------------------------


@mcp.tool()
async def list_picklist(picklist: str) -> str:
    """List an Acumulus picklist (reference data for dropdowns/validation).

    Args:
        picklist: The picklist name. Common values: "accounts" (bank/ledger
            accounts), "contacts", "products", "vattypes", "costcenters",
            "costheadings", "companytypes". The endpoint called is
            picklists/picklist_<picklist>.php.

    Returns the parsed response plus the extracted item list under `items`.
    """
    try:
        data = await _get_client().request(f"picklists/picklist_{picklist}.php")
    except AcumulusError as e:
        return _error(e)
    items = extract_list(data, picklist)
    return _result({"status": "success", "picklist": picklist, "totalresults": len(items), "items": items})


# --------------------------------------------------------------------------
# Invoices
# --------------------------------------------------------------------------


@mcp.tool()
async def create_invoice(
    customer: dict[str, Any],
    lines: list[dict[str, Any]],
    invoice: dict[str, Any] | None = None,
) -> str:
    """Add an invoice to Acumulus (invoices/invoice_add.php).

    Args:
        customer: Customer-block fields. Common ones: type, contactid,
            companyname1, fullname, address1, postalcode, city, countrycode,
            vatnumber, email, overwriteifexists.
        lines: One dict per invoice line. Common fields: product, itemnumber,
            unitprice (excl. VAT, 4 decimals), vatrate (defaults to 21),
            quantity (defaults to 1), nature.
        invoice: Optional invoice-block fields: concept, number, vattype,
            issuedate, costcenter, accountnumber, paymentstatus, paymentdate,
            description, template, invoicenotes.

    Set ACUMULUS_TESTMODE=1 to validate without persisting.
    """
    invoice_block: dict[str, Any] = dict(invoice or {})
    invoice_block["line"] = lines
    fields = {"customer": {**customer, "invoice": invoice_block}}
    try:
        data = await _get_client().request("invoices/invoice_add.php", fields)
    except AcumulusError as e:
        return _error(e)
    return _result(data)


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------


@mcp.tool()
async def whoami() -> str:
    """Sanity-check the Acumulus connection by listing the account picklist."""
    client = _get_client()
    try:
        data = await client.request("picklists/picklist_accounts.php")
    except AcumulusError as e:
        return _error(e)
    accounts = extract_list(data, "accounts")
    return _result(
        {
            "base_url": client.base_url,
            "testmode": client.testmode,
            "status": data.get("status"),
            "accounts_found": len(accounts),
        }
    )


def run() -> None:
    """Entrypoint: start the MCP server over stdio."""
    mcp.run()
