from __future__ import annotations

from pathlib import Path

import pytest
from merkl.demo.rig import ISSUER

from merkl_mcp import tools
from tests.conftest import make_runtime


@pytest.mark.asyncio
async def test_propose_swap_settles_at_the_configured_rate(swap_rig, tmp_path: Path) -> None:
    rt = make_runtime(swap_rig, swap_rig.ledger, tmp_path)

    result = await tools.propose_swap(
        rt,
        sell_amount="200.00",
        sell_currency="RLUSD",
        sell_issuer=ISSUER,
        buy_amount="100",
        buy_currency="XRP",
        why="rebalance into XRP",
    )

    assert result["outcome"] == "settled"
    assert result["tx_hash"]


@pytest.mark.asyncio
async def test_propose_swap_above_the_ceiling_is_refused(swap_rig, tmp_path: Path) -> None:
    rt = make_runtime(swap_rig, swap_rig.ledger, tmp_path)

    # 100 XRP costs 150 RLUSD at the configured 1.5 rate; a 50 RLUSD ceiling can't cover it.
    result = await tools.propose_swap(
        rt,
        sell_amount="50.00",
        sell_currency="RLUSD",
        sell_issuer=ISSUER,
        buy_amount="100",
        buy_currency="XRP",
        why="too tight a ceiling",
    )

    assert result["outcome"] == "refused"
    assert result["reason"]
