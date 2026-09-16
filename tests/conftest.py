"""Test rig: a real signer, a real encrypted keystore and the SDK's in-memory
rail (``merkl.demo.rig``), wired into a :class:`~merkl_mcp.runtime.Runtime`
the same shape production builds — only the ledger reader and the
notary-escalation queue are faked, because neither a JSON-RPC node nor
merkl-api exists in memory.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

import pytest
from merkl.adapters.fake import FakeLedger
from merkl.core.canonical import JSONObject
from merkl.demo.rig import (
    AGENT,
    AGENT_ID,
    ISSUER,
    RLUSD,
    SUPPLIER,
    TREASURY,
    XRP,
    Rig,
    build_policy,
    build_rig,
)
from merkl.sdk.receipt_store import LocalReceiptStore
from merkl.sdk.receipts import ReceiptBuilder

from merkl_mcp import bundle
from merkl_mcp.book import FakeLedgerReader
from merkl_mcp.runtime import Runtime
from merkl_mcp.state import McpState

__all__ = [
    "AGENT",
    "AGENT_ID",
    "ISSUER",
    "RLUSD",
    "SUPPLIER",
    "TREASURY",
    "XRP",
    "FakeEscalations",
    "make_runtime",
]


class FakeEscalations:
    """Stands in for the notary's ``GET /v1/escalations/{challenge}``.

    ``answer`` is whatever the next call to :meth:`get` should return —
    ``None`` (the notary cannot say), a pending status, or a resolution
    carrying the signer's own ``signer_decision``. Tests set it directly.
    """

    def __init__(self, answer: JSONObject | None = None) -> None:
        self.answer = answer

    async def get(self, challenge: str) -> JSONObject | None:  # noqa: ARG002
        return self.answer

    async def aclose(self) -> None:
        return None


def _config(rig: Rig) -> bundle.Config:
    """A :class:`bundle.Config` that names the rig's own treasury and rail.

    Every path in it is nonexistent on purpose: nothing in the tools this
    backs ever reads a file off it, because the rig hands over an
    already-built signer and rail directly.
    """
    missing = Path("/nonexistent")
    return bundle.Config(
        agent_dir=missing,
        agent=bundle.AgentConfig(agent_id=AGENT_ID, key_file=missing),
        treasury=bundle.TreasuryConfig(
            address=rig.policy.treasury,
            policy_version=rig.policy.version,
            wallet_file=missing,
            wallet_name="agent",
        ),
        rail=bundle.RailConfig(
            name=rig.rail_name, json_rpc_url="http://fake.invalid", websocket_url=None
        ),
        signer=bundle.SignerConfig(url="http://fake.invalid", token_file=None),
        notary=bundle.NotaryConfig(url="http://fake.invalid", api_key_env="MERKL_TEST_API_KEY"),
    )


def make_runtime(
    rig: Rig,
    ledger: FakeLedger,
    tmp_path: Path,
    *,
    escalations: FakeEscalations | None = None,
    name: str = "default",
) -> Runtime:
    """A :class:`Runtime` around ``rig``'s real signer and rail.

    A fresh :class:`~merkl.sdk.receipts.ReceiptBuilder` is built here rather
    than reusing ``rig.builder`` because the demo rig's own builder carries no
    receipt store — every scenario in the SDK's own suite reads its result
    off the return value, not off disk. This tool server's ``read_receipts``
    needs the store, so it is added here, on the same signer and rail.
    """
    store = LocalReceiptStore(tmp_path / f"{name}-receipts")
    builder = ReceiptBuilder(
        signer=rig.signer,
        settlement=rig.rail,
        agent_id=AGENT_ID,
        agent_public_key=AGENT.public_key,
        agent_sign=AGENT.sign,
        clock=rig.clock,
        receipt_store=store,
    )
    state_file = tmp_path / f"{name}-state.json"
    return Runtime(
        config=_config(rig),
        builder=builder,
        agent_public_key=AGENT.public_key,
        reader=FakeLedgerReader(ledger),
        signer=rig.signer,
        store=store,
        escalations=escalations or FakeEscalations(),
        state=McpState.load(state_file),
        state_path=state_file,
        clock=rig.clock,
        lock=asyncio.Lock(),
    )


def reload_runtime(rt: Runtime) -> Runtime:
    """A new :class:`Runtime` reading the same state and receipt files back
    off disk — what a restarted process sees, without a real process restart."""
    return Runtime(
        config=rt.config,
        builder=rt.builder,
        agent_public_key=rt.agent_public_key,
        reader=rt.reader,
        signer=rt.signer,
        store=rt.store,
        escalations=rt.escalations,
        state=McpState.load(rt.state_path),
        state_path=rt.state_path,
        clock=rt.clock,
        lock=asyncio.Lock(),
    )


@pytest.fixture
def payment_rig(tmp_path: Path) -> Rig:
    """A treasury that may pay ``SUPPLIER`` in RLUSD; no invoice binding, since
    the shared contract's ``propose_payment`` carries no reference argument."""
    policy = build_policy(reference_required=False)
    return build_rig(tmp_path / "payment-home", policy=policy)


@pytest.fixture
def swap_rig(tmp_path: Path) -> Rig:
    """A treasury that may also swap RLUSD for XRP, at one flat rate."""
    policy = build_policy(
        reference_required=False, may_swap=True, other_asset=XRP, per_tx_cap="10000.00"
    )
    return build_rig(
        tmp_path / "swap-home",
        policy=policy,
        rates={
            (f"RLUSD.{ISSUER}", "XRP"): Decimal("1.5"),
            ("XRP", f"RLUSD.{ISSUER}"): Decimal("0.6"),
        },
    )
