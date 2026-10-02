# merkl-mcp

Merkl as a tool server. Standalone repository, phase 23 of the Merkl build,
built on the *released* `merkl-sdk` — a change to the SDK reaches this repo
only through a version bump and release, same relationship `merkl-trader` and
`merkl-api` have with it.

## What this is

An MCP server (the official `mcp` SDK, `FastMCP`) exposing six tools —
`get_treasury`, `propose_payment`, `propose_swap`, `pending_approval`,
`read_receipts`, `verify_receipt` (market data is not ours: the harness mounts
an XRPL/market MCP server) — over stdio (default) or streamable HTTP
(`--http :PORT`). Any harness that speaks MCP can mount it and get a treasury
that trades through a Merkl policy it cannot read, exactly the way
`merkl-trader` does today — this is that same flow, as tools instead of a
loop. See `README.md` for the full story and the mount snippets.

```
merkl_mcp/
  bundle.py     the five-file agent bundle plus trader.toml's [agent],
                [treasury], [rail], [signer], [notary] — no [market],
                [model], [loop] or [bill]: this process has no trading loop
  state.py      $MERKL_MCP_STATE/state.json — the one open proposal (with its
                prepared_tx, so an escalation survives a restart) and the
                in-flight pointer a crash mid-call leaves behind
  ledger.py     balances only: XrplJsonRpcReader (production, JSON-RPC) and
                FakeLedgerReader (tests, reads merkl.adapters.fake.FakeLedger)
  escalations.py  GET /v1/escalations/{challenge} — has a person decided?
  runtime.py    wires a Runtime together: build_runtime() for production
                (DevSignerClient, XrplSettlementAdapter, HttpNotary,
                LocalReceiptStore)
  tools.py      the six tools' actual logic — server.py is a thin wrapper
  server.py     FastMCP app: registers the six tools, never lets one raise
  __main__.py   merkl-mcp / python -m merkl_mcp — stdio or --http [HOST]:PORT
tests/          against merkl.demo.rig's real signer + in-memory rail
Dockerfile      ghcr.io/ramcav/merkl-mcp, uid 10002, read-only /agent
```

## Install

```bash
uv venv .venv
uv pip install -p .venv/bin/python -e ".[dev]"
```

## Testing

```bash
pytest -q --timeout 60
ruff check merkl_mcp tests
ruff format --check merkl_mcp tests
```

`tests/conftest.py` builds a `Runtime` around `merkl.demo.rig.build_rig()`:
a real `SignerEngine`, a real encrypted keystore, real receipts on disk,
against `merkl.adapters.fake`'s in-memory rail — the same rig the SDK's own
scenario suite uses. Only the ledger reader is faked (`FakeLedgerReader`,
`ledger.py`), because the in-memory rail has no JSON-RPC node to ask. `--timeout 60`
(`pytest-timeout`) fails a hung test instead of a hung CI run; nothing here
should ever approach that limit, since nothing touches a real network.

## The image

`Dockerfile` builds `ghcr.io/ramcav/merkl-mcp`. Non-root from the start
(uid 10002, matching `merkl-trader`'s image, so a bundle prepared for one
runs unmodified under the other), read-only `/agent`, the one pending
proposal and the receipt store on `/var/lib/merkl-mcp`
(`$MERKL_MCP_STATE`). `.github/workflows/release.yml` builds and pushes it on
every `v*` tag; `ci.yml` runs tests and ruff on every push and PR.

## Guidelines

- This repo imports `merkl.*` from the installed `merkl-sdk` package only —
  never a relative path into an SDK checkout. A change to the SDK reaches
  here through a release, not a shared filesystem.
- No section for the policy's rules, ever, and no standing mandate: this
  process has no loop of its own, so it does not read `[market]`, `[model]`,
  `[loop]` or `[bill]` — every "which asset, which size, why" question
  arrives as a tool argument, and whatever harness mounts this server owns
  the loop, the model and the mandate.
- `get_treasury`, `read_receipts` and `verify_receipt` never go near `ReceiptBuilder` or the rail
  adapter — they are reads (the verifier runs locally on the stored
  receipt, no network).
- Only one proposal may be open at a time, enforced by `state.py`'s
  `Pending` record on disk, not by a prompt. A second `propose_*` call while
  one is open returns `waiting_for_a_person` without touching the signer.
- A tool never raises to the model (`server.py`'s `_safe`): a policy denial,
  an escalation, a malformed request are all results with words in them, not
  exceptions. A genuine bug still comes back as `{"error": ...}`.
- No `mandate` field is read from `[agent]` — this process has no standing
  instruction of its own. `[agent].agent_id` and `.key_file` are read despite
  the shared contract naming only `[treasury]`/`[rail]`/`[signer]`/`[notary]`,
  because a signer call needs an identity and a key to sign with; see
  `merkl_mcp/bundle.py`'s module docstring.
- Amounts are decimal strings on every tool, never a float — the same rule
  every other Merkl repo enforces at the boundary.
