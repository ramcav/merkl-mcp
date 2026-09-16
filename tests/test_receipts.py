from __future__ import annotations

from pathlib import Path

import pytest
from merkl.demo.rig import ATTACKER, ISSUER, SUPPLIER

from merkl_mcp import tools
from tests.conftest import make_runtime


@pytest.mark.asyncio
async def test_read_receipts_describes_outcome_what_and_when(payment_rig, tmp_path: Path) -> None:
    rt = make_runtime(payment_rig, payment_rig.ledger, tmp_path)
    await tools.propose_payment(
        rt,
        destination=SUPPLIER,
        amount="42.00",
        currency="RLUSD",
        issuer=ISSUER,
        why="a small settled payment",
    )
    await tools.propose_payment(
        rt,
        destination=ATTACKER,
        amount="1.00",
        currency="RLUSD",
        issuer=ISSUER,
        why="off the allowlist",
    )

    receipts = (await tools.read_receipts(rt, limit=5))["receipts"]

    assert len(receipts) == 2
    settled, refused = receipts
    assert settled["outcome"] == "settled"
    assert "42.00" in settled["what"] and SUPPLIER in settled["what"]
    assert settled["when"]
    assert settled["refused_by"] is None
    assert refused["outcome"] == "denied"
    assert refused["refused_by"]


@pytest.mark.asyncio
async def test_read_receipts_on_an_empty_treasury_is_an_empty_list(
    payment_rig, tmp_path: Path
) -> None:
    rt = make_runtime(payment_rig, payment_rig.ledger, tmp_path)

    assert await tools.read_receipts(rt) == {"receipts": []}
