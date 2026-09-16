# merkl-mcp

Merkl as a tool server. An MCP server — the official `mcp` SDK, `FastMCP` —
exposing six tools any agent harness can mount: a treasury that trades
through a Merkl policy it cannot read, a market it can price, and receipts it
can read back, the same flow `merkl-trader`'s reference loop runs today,
offered here as tools instead of a loop. Merkl is not in the business of
building agents; it is the thing any agent's money goes through.

## The tools

| tool | input | returns |
|---|---|---|
| `get_treasury` | — | address, network, balances (XRP and each trust line), policy version, the signer's health line, whether a proposal is pending with a person |
| `get_market` | `{base, quote_code, quote_issuer, sizes: [..]}` | the book both ways at each size (what you would actually get), read off the ledger over JSON-RPC |
| `read_receipts` | `{limit}` | this agent's recent receipts in words: outcome, what was asked for, the rule that refused it if any, when |
| `propose_payment` | `{destination, amount, currency, issuer?, why}` | `settled` (with a tx hash), `waiting_for_a_person` (challenge, expiry), or `refused` (the rule, in words) |
| `propose_swap` | `{sell_amount, sell_currency, sell_issuer?, buy_amount, buy_currency, buy_issuer?, why}` | same three shapes as `propose_payment` |
| `pending_approval` | — | `none`, `waiting` (time left), or the resolution — it resumes the payment itself once a person has decided, and reports `settled`/`refused` |

`why` becomes the proposal's reasoning note (200 characters). A tool never
raises to the model: every failure is a result with `error` in words. Only
one proposal may be open at a time — a second `propose_*` call while one is
waiting returns `waiting_for_a_person` without touching the signer.

## Configuration

Everything this server needs is the five-file agent bundle `merkl treasury
init` writes (`trader.toml`, `agent-ed25519.pem`, `wallet.json`, and the
optional relay/notary secret files), read from `$MERKL_AGENT_DIR` (default
`/agent`), plus `trader.toml`'s `[agent]`, `[treasury]`, `[rail]`, `[signer]`
and `[notary]` tables. There is no `[market]`, `[model]`, `[loop]` or
`[bill]` section — this process has no trading loop, no model and no compute
bill of its own; every "which asset, which size, why" question arrives as a
tool argument instead.

The one proposal a person may be deciding on, and the local receipt store,
live under `$MERKL_MCP_STATE` (default `/var/lib/merkl-mcp`) — writable,
unlike the read-only bundle — so an escalation survives a restart without
re-preparing a transaction the policy key never signed.

## Install

```bash
uv venv .venv
uv pip install -p .venv/bin/python -e ".[dev]"
```

## Running it

```bash
merkl-mcp                  # stdio — what every mount below actually uses
merkl-mcp --http :8765     # streamable HTTP instead, on every interface
```

```bash
docker run --rm -i \
  -v "$PWD/merkl-agent:/agent:ro" -v merkl-mcp:/var/lib/merkl-mcp \
  ghcr.io/ramcav/merkl-mcp:0.1.0
```

## Mounting it

Four harnesses, one server. All four launch the same `merkl-mcp` process
over stdio and tell it where the agent bundle lives via `MERKL_AGENT_DIR`;
nothing else needs to cross the boundary; the bundle's secrets are for
`merkl-mcp` to read off disk itself.

**Claude Code**

```bash
claude mcp add merkl -e MERKL_AGENT_DIR=/path/to/merkl-agent -- merkl-mcp
```

**Claude Desktop** — `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "merkl": {
      "command": "merkl-mcp",
      "env": { "MERKL_AGENT_DIR": "/path/to/merkl-agent" }
    }
  }
}
```

**OpenAI Agents SDK** — the shape `merkl-trader`'s own harness
(`merkl_trader/harness/loop.py`) mounts it with:

```python
from agents.mcp import MCPServerStdio

server = MCPServerStdio(
    params={"command": "merkl-mcp", "env": {"MERKL_AGENT_DIR": "/path/to/merkl-agent"}},
    name="merkl-mcp",
    client_session_timeout_seconds=30,
)
```

**Hermes** — the same `mcpServers` convention as Claude Desktop:

```json
{
  "mcpServers": {
    "merkl": {
      "command": "merkl-mcp",
      "args": [],
      "env": { "MERKL_AGENT_DIR": "/path/to/merkl-agent" }
    }
  }
}
```

## The container's box

The image runs as uid 10002 and `/agent` arrives read-only. If a bundle file
was prepared with different ownership, this server reports a one-line error
naming the file and the fix:

```bash
chown -R 10002:10002 merkl-agent/
```

## Testing

```bash
pytest -q --timeout 60
ruff check merkl_mcp tests
ruff format --check merkl_mcp tests
```

Against `merkl.demo.rig`'s real signer, real encrypted keystore and the
SDK's in-memory rail — only the ledger reader is faked, because the
in-memory rail has no JSON-RPC node to ask (see `merkl_mcp/book.py`).

## See also

- [`merkl-sdk`](https://github.com/ramcav/merkl-sdk) — the policy layer this
  server is built on.
- [`merkl-trader`](https://github.com/ramcav/merkl-trader) — the reference
  trading agent; its `feat/harness` branch mounts this server over the OpenAI
  Agents SDK.
