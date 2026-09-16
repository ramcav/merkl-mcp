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
