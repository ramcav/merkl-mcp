from __future__ import annotations

from pathlib import Path

import pytest
from merkl.demo.rig import ATTACKER, ISSUER, SUPPLIER

from merkl_mcp import tools
from tests.conftest import make_runtime


@pytest.mark.asyncio
async def test_propose_payment_settles_within_policy(payment_rig, tmp_path: Path) -> None:
    rt = make_runtime(payment_rig, payment_rig.ledger, tmp_path)

    result = await tools.propose_payment(
        rt,
        destination=SUPPLIER,
        amount="100.00",
        currency="RLUSD",
        issuer=ISSUER,
        why="pay the supplier",
    )

    assert result["outcome"] == "settled"
    assert result["tx_hash"]
    assert rt.state.pending is None
    assert rt.state.in_flight is None


@pytest.mark.asyncio
async def test_propose_payment_off_the_allowlist_is_refused_in_words(
    payment_rig, tmp_path: Path
) -> None:
    rt = make_runtime(payment_rig, payment_rig.ledger, tmp_path)

    result = await tools.propose_payment(
        rt,
        destination=ATTACKER,
        amount="10.00",
        currency="RLUSD",
        issuer=ISSUER,
        why="not the supplier",
    )

    assert result["outcome"] == "refused"
    assert result["reason"]  # the rule, in words — never just "denied"


@pytest.mark.asyncio
async def test_a_malformed_amount_is_an_error_not_a_crash(payment_rig, tmp_path: Path) -> None:
    rt = make_runtime(payment_rig, payment_rig.ledger, tmp_path)

    result = await tools.propose_payment(
        rt,
        destination=SUPPLIER,
        amount="not-a-number",
        currency="RLUSD",
        issuer=ISSUER,
        why="oops",
    )

    assert "error" in result
    assert "decimal" in result["error"]


@pytest.mark.asyncio
async def test_why_becomes_the_reasoning_note(payment_rig, tmp_path: Path) -> None:
    rt = make_runtime(payment_rig, payment_rig.ledger, tmp_path)

    await tools.propose_payment(
        rt,
        destination=SUPPLIER,
        amount="50.00",
        currency="RLUSD",
        issuer=ISSUER,
        why="restocking invoice",
    )

    receipts = (await tools.read_receipts(rt))["receipts"]
    assert receipts[-1]["why_it_was_asked"] == "restocking invoice"


@pytest.mark.asyncio
async def test_the_same_refused_intent_is_filed_once_an_hour(payment_rig, tmp_path: Path) -> None:
    rt = make_runtime(payment_rig, payment_rig.ledger, tmp_path)

    async def ask(amount: str) -> dict:
        return await tools.propose_payment(
            rt, destination=ATTACKER, amount=amount, currency="RLUSD", issuer=ISSUER, why="again"
        )

    first = await ask("1.00")
    again = await ask("1.005")  # within 1%
    different = await ask("5.00")

    assert first["outcome"] == "refused" and "receipt_id" in first
    assert again["outcome"] == "refused" and "receipt_id" not in again
    assert "not filing it again within the hour" in again["reason"]
    assert "receipt_id" in different, "a different amount is a different intent"
    assert len(await rt.store.list(rt.config.treasury.address)) == 2

    payment_rig.clock.advance(3601)  # the hour passes; the same intent may be tried again
    third = await ask("1.00")
    assert "receipt_id" in third


@pytest.mark.asyncio
async def test_the_refusal_guard_survives_a_restart(payment_rig, tmp_path: Path) -> None:
    from merkl_mcp.state import McpState

    rt = make_runtime(payment_rig, payment_rig.ledger, tmp_path)
    await tools.propose_payment(
        rt, destination=ATTACKER, amount="1.00", currency="RLUSD", issuer=ISSUER, why="x"
    )
    rt.state = McpState.load(rt.state_path)

    again = await tools.propose_payment(
        rt, destination=ATTACKER, amount="1.00", currency="RLUSD", issuer=ISSUER, why="x"
    )

    assert "receipt_id" not in again
