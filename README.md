# acumulus-mcp

MCP server for [Acumulus](https://www.acumulus.nl/) — let an AI assistant
(Claude Desktop, Claude Code, Cursor, …) read and modify your Acumulus
administration through natural language, over the official Acumulus API.

Runs locally over stdio. Your Acumulus credentials stay on your machine;
there is no telemetry and no proxy.

## Install

```bash
pip install acumulus-mcp
```

Requires Python 3.10+.

## Configure credentials

The server authenticates with your Acumulus **contract code**, **username**
and an **API token** (generate one in Acumulus under your profile; it travels
in the API's `<password>` field). Provide them as environment variables:

```bash
export ACUMULUS_CONTRACT_CODE=123456
export ACUMULUS_USERNAME=your-username
export ACUMULUS_API_TOKEN=your-api-token
# optional: validate writes without persisting them
export ACUMULUS_TESTMODE=1
```

## Register in your MCP client

**Claude Code:**

```bash
claude mcp add acumulus \
  -e ACUMULUS_CONTRACT_CODE=123456 \
  -e ACUMULUS_USERNAME=your-username \
  -e ACUMULUS_API_TOKEN=your-api-token \
  -- acumulus-mcp
```

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "acumulus": {
      "command": "acumulus-mcp",
      "env": {
        "ACUMULUS_CONTRACT_CODE": "123456",
        "ACUMULUS_USERNAME": "your-username",
        "ACUMULUS_API_TOKEN": "your-api-token"
      }
    }
  }
}
```

Restart your client, then try: *"Use the acumulus `whoami` tool to test the connection."*

## Tools

| Tool | What it does |
|------|--------------|
| `whoami` | Sanity-check the connection (lists the account picklist). |
| `list_picklist(picklist)` | Fetch reference data: `accounts`, `contacts`, `products`, `vattypes`, `costcenters`, `costheadings`, `companytypes`. |
| `create_invoice(customer, lines, invoice=None)` | Add an invoice (`invoices/invoice_add.php`). |
| `acumulus_request(api_path, fields=None)` | Escape hatch — call any Acumulus endpoint directly (e.g. `invoices/invoice_set_payment_status.php`, `entry/entry_info.php`). Endpoint paths match the docs at [siel.nl/acumulus/API](https://www.siel.nl/acumulus/API/). |

The `acumulus_request` tool means every Acumulus function is reachable even
before it gets a dedicated wrapper — the credential/envelope is added for you;
you only pass the endpoint path and the function-specific fields.

## Safety

Set `ACUMULUS_TESTMODE=1` to have Acumulus validate write calls without
persisting them — do a dry run before pointing it at the live administration.

## License

AGPL-3.0-or-later. For a commercial license, contact nick@easeo.nl.
