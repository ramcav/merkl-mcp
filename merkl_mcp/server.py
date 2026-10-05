"""The MCP surface: six tools, one runtime, no path that raises to the model.

Every tool below is a thin wrapper: it declares the shape a harness sees
(names, types, docstring-as-description) and immediately hands off to
``merkl_mcp.tools``, which holds the actual logic and is what the test suite
calls directly. ``_safe`` is the one place that turns "this code has a bug"
into a result instead of a dropped connection — the shared contract's rule
that a tool never raises to the model, enforced once rather than six times.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from merkl_mcp import tools
from merkl_mcp.runtime import Runtime

INSTRUCTIONS = (
    "Merkl as a tool server: every payment and swap goes through a Merkl policy "
    "this process cannot read. get_treasury, read_receipts and verify_receipt are free reads; "
    "market data comes from other servers. "
    "propose_payment and propose_swap either settle, wait for a person, or are "
    "refused with the rule in words — call pending_approval to check on one that "
    "is waiting. Only one proposal may be open at a time."
)


async def _safe(
    fn: Callable[..., Awaitable[dict[str, Any]]], *args: Any, **kwargs: Any
) -> dict[str, Any]:
    try:
        return await fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - the shared contract: never raise to the model
        return {"error": str(exc)}


def build_app(rt: Runtime) -> FastMCP:
    """Register the six tools against one runtime and return the server."""
    app = FastMCP("merkl-mcp", instructions=INSTRUCTIONS)

    @app.tool()
    async def get_treasury() -> dict[str, Any]:
        """Address, balances (XRP and each trust line), policy version, signer
        health, and whether a proposal is waiting on a person."""
        return await _safe(tools.get_treasury, rt)

    @app.tool()
    async def read_receipts(limit: int = 10) -> dict[str, Any]:
        """This agent's recent receipts in words: outcome, what was asked for,
        the rule that refused it if any, and when."""
        return await _safe(tools.read_receipts, rt, limit)

    @app.tool()
    async def propose_payment(
        destination: str,
        amount: str,
        currency: str,
        why: str,
        issuer: str | None = None,
        session_id: str | None = None,
        session_action_count: int | None = None,
        depends_on: str | None = None,
    ) -> dict[str, Any]:
        """Propose a payment of ``amount`` ``currency`` to ``destination``.
        ``amount`` is a decimal string, never a float. ``issuer`` is required
        for an issued currency (e.g. RLUSD) and omitted for XRP. ``why``
        becomes this proposal's reasoning note. Returns settled (with a tx
        hash), waiting_for_a_person (with a challenge and expiry), or refused
        (with the rule, in words). ``session_id``, ``session_action_count`` and
        ``depends_on`` are filled in by the harness, not the model."""
        return await _safe(
            tools.propose_payment,
            rt,
            destination=destination,
            amount=amount,
            currency=currency,
            issuer=issuer,
            why=why,
            session_id=session_id,
            session_action_count=session_action_count,
            depends_on=depends_on,
        )

    @app.tool()
    async def propose_swap(
        sell_amount: str,
        sell_currency: str,
        buy_amount: str,
        buy_currency: str,
        why: str,
        sell_issuer: str | None = None,
        buy_issuer: str | None = None,
        session_id: str | None = None,
        session_action_count: int | None = None,
        depends_on: str | None = None,
    ) -> dict[str, Any]:
        """Propose a swap: sell at most ``sell_amount`` ``sell_currency`` for
        exactly ``buy_amount`` ``buy_currency``. Same amount, issuer and
        return-shape rules as ``propose_payment``."""
        return await _safe(
            tools.propose_swap,
            rt,
            sell_amount=sell_amount,
            sell_currency=sell_currency,
            buy_amount=buy_amount,
            buy_currency=buy_currency,
            sell_issuer=sell_issuer,
            buy_issuer=buy_issuer,
            why=why,
            session_id=session_id,
            session_action_count=session_action_count,
            depends_on=depends_on,
        )

    @app.tool()
    async def pending_approval() -> dict[str, Any]:
        """Is a proposal still waiting on a person? Returns status "none",
        "waiting" (with the time left), or the resolution — it resumes and
        settles or refuses the payment itself once a human has decided."""
        return await _safe(tools.pending_approval, rt)

    @app.tool()
    async def verify_receipt(receipt_id: str) -> dict[str, Any]:
        """Verify one of this agent's receipts, locally, with the SDK verifier
        (no network, no trusted party). Returns the verdict in words: whether
        anything was contradicted, whether every check ran, and what was not
        checked. ``receipt_id`` comes from ``read_receipts``."""
        return await _safe(tools.verify_receipt, rt, receipt_id)

    return app


__all__ = ["build_app"]
