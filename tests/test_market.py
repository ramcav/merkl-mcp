from __future__ import annotations

from pathlib import Path

import pytest

from merkl_mcp import tools
from tests.conftest import ISSUER, make_runtime


@pytest.mark.asyncio
async def test_get_market_reads_the_flat_rate_both_ways(swap_rig, tmp_path: Path) -> None:
    rt = make_runtime(swap_rig, swap_rig.ledger, tmp_path)

    result = await tools.get_market(
        rt, base="XRP", quote_code="RLUSD", quote_issuer=ISSUER, sizes=["10", "500"]
    )

    assert result["pair"] == "XRP/RLUSD"
    assert result["best_ask"] == "1.500000"  # RLUSD per XRP, buying XRP
    assert result["best_bid"] == "0.600000"  # RLUSD per XRP, selling XRP
    assert [level["filled"] for level in result["cost_to_buy_base"]] == ["10", "500"]
    assert result["cost_to_buy_base"][0]["price"] == "1.500000"


@pytest.mark.asyncio
async def test_get_market_rejects_a_non_native_base(swap_rig, tmp_path: Path) -> None:
    rt = make_runtime(swap_rig, swap_rig.ledger, tmp_path)

    result = await tools.get_market(
        rt, base="RLUSD", quote_code="XRP", quote_issuer=ISSUER, sizes=["10"]
    )

    assert "error" in result


@pytest.mark.asyncio
async def test_get_market_rejects_non_decimal_sizes(swap_rig, tmp_path: Path) -> None:
    rt = make_runtime(swap_rig, swap_rig.ledger, tmp_path)

    result = await tools.get_market(
        rt, base="XRP", quote_code="RLUSD", quote_issuer=ISSUER, sizes=["ten"]
    )

    assert "error" in result


@pytest.mark.asyncio
async def test_get_market_with_no_configured_rate_reports_no_fill(
    payment_rig, tmp_path: Path
) -> None:
    rt = make_runtime(payment_rig, payment_rig.ledger, tmp_path)

    result = await tools.get_market(
        rt, base="XRP", quote_code="RLUSD", quote_issuer=ISSUER, sizes=["10"]
    )

    assert result["best_ask"] is None
    assert result["cost_to_buy_base"][0]["price"] is None
    assert result["cost_to_buy_base"][0]["filled"] == "0"
