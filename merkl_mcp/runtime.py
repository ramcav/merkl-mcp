"""Everything a tool call needs, already wired together.

``build_runtime`` assembles the real thing: a signer over HTTPS, XRPL, a
notary, a local receipt store — the same pieces ``merkl_trader.trader.build``
wires, minus the trading loop. Tests build a :class:`Runtime` by hand around
``merkl.demo.rig.build_rig``'s pieces instead, so every tool runs against a
real signer, a real encrypted keystore and the SDK's in-memory rail, with
only the ``FakeLedgerReader`` standing in for a JSON-RPC node that does not
exist in memory.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from merkl.adapters.notary import HttpNotary
from merkl.adapters.signer_dev import DevSignerClient
from merkl.adapters.xrpl import XrplSettlementAdapter, load_wallets
from merkl.sdk.receipt_store import LocalReceiptStore
from merkl.sdk.receipts import ReceiptBuilder, SystemClock

from merkl_mcp import bundle
from merkl_mcp.escalations import EscalationQueue
from merkl_mcp.ledger import LedgerReader, XrplJsonRpcReader
from merkl_mcp.state import McpState, state_dir, state_path


class WiringError(Exception):
    """Raised only while building the runtime — never once a tool is live."""


@dataclasses.dataclass
class Runtime:
    """Bound to one treasury, one signer, one state file."""

    config: bundle.Config
    builder: ReceiptBuilder
    agent_public_key: str
    reader: LedgerReader
    signer: Any
    """Whatever answers ``health()`` — ``DevSignerClient`` or ``LocalSignerClient``."""
    store: LocalReceiptStore
    escalations: EscalationQueue
    state: McpState
    state_path: Path
    clock: Any = dataclasses.field(default_factory=SystemClock)
    lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock)

    def save(self) -> None:
        self.state.save(self.state_path)

    async def aclose(self) -> None:
        for closer in (self.reader, self.escalations, self.signer):
            close = getattr(closer, "aclose", None)
            if close is None:
                continue
            try:
                await close()
            except (
                RuntimeError
            ) as exc:  # a client whose loop already ended has nothing left to close
                if "closed" not in str(exc).lower():
                    raise


async def build_runtime(
    agent_dir: Path | None = None, *, state_dir_override: Path | None = None
) -> Runtime:
    """Assemble the production runtime from the agent bundle and ``trader.toml``."""
    config = bundle.load(agent_dir)

    material = config.agent.key_file.read_bytes()
    loaded = serialization.load_pem_private_key(material, password=None)
    if not isinstance(loaded, Ed25519PrivateKey):
        raise WiringError(f"{config.agent.key_file} is not an Ed25519 private key")
    agent_public_key = (
        loaded.public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        .hex()
    )

    token = bundle.read_secret_file(config.signer.token_file) if config.signer.token_file else None
    signer = DevSignerClient(base_url=config.signer.url, bearer_token=token)
    policy_public_key = await signer.public_key()

    wallets = load_wallets(config.treasury.wallet_file)
    if config.treasury.wallet_name not in wallets:
        raise WiringError(
            f"{config.treasury.wallet_file} has no wallet called {config.treasury.wallet_name!r}"
        )
    rail = XrplSettlementAdapter(
        treasury=config.treasury.address,
        agent_wallet=wallets[config.treasury.wallet_name],
        policy_public_key=policy_public_key,
        json_rpc_url=config.rail.json_rpc_url,
        websocket_url=config.rail.websocket_url,
    )

    home = state_dir_override or state_dir()
    import os

    receipts_dir = None if os.environ.get("MERKL_RECEIPT_DIR") else home / "receipts"
    store = LocalReceiptStore(receipts_dir)
    api_key = config.notary.api_key()
    builder = ReceiptBuilder(
        signer=signer,
        settlement=rail,
        agent_id=config.agent.agent_id,
        agent_public_key=agent_public_key,
        agent_sign=lambda message: loaded.sign(message).hex(),
        receipt_store=store,
        notary=HttpNotary(config.notary.url, api_key=api_key),
        rail=config.rail.name,
    )

    return Runtime(
        config=config,
        builder=builder,
        agent_public_key=agent_public_key,
        reader=XrplJsonRpcReader(config.rail.json_rpc_url),
        signer=signer,
        store=store,
        escalations=EscalationQueue(config.notary.url, api_key),
        state=McpState.load(state_path(home)),
        state_path=state_path(home),
    )


__all__ = ["Runtime", "WiringError", "build_runtime"]
