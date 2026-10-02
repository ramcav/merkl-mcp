from __future__ import annotations

from pathlib import Path

import pytest

from merkl_mcp import tools
from tests.conftest import ISSUER, make_runtime


@pytest.mark.asyncio
async def test_get_treasury_reports_address_balances_and_policy(
    payment_rig, tmp_path: Path
) -> None:
    rt = make_runtime(payment_rig, payment_rig.ledger, tmp_path)

    result = await tools.get_treasury(rt)

    assert result["address"] == payment_rig.policy.treasury
    assert result["policy_version"] == payment_rig.policy.version
    assert result["balances"][f"RLUSD.{ISSUER}"] == "100000.00"
    assert result["pending_with_a_person"] is None
    assert "ok" in result["signer"]


@pytest.mark.asyncio
async def test_signer_health_line_names_the_policy_hash(payment_rig, tmp_path: Path) -> None:
    rt = make_runtime(payment_rig, payment_rig.ledger, tmp_path)

    result = await tools.get_treasury(rt)

    assert result["policy_version"] in result["signer"]
    assert "pending" in result["signer"]


@pytest.mark.asyncio
async def test_get_treasury_names_assets_and_the_market_pair_as_fields(
    payment_rig, tmp_path: Path
) -> None:
    import dataclasses

    from merkl_mcp.bundle import MarketConfig

    rt = make_runtime(payment_rig, payment_rig.ledger, tmp_path)
    rt.config = dataclasses.replace(
        rt.config, market=MarketConfig(base="XRP", quote_code="RLUSD", quote_issuer=ISSUER)
    )

    result = await tools.get_treasury(rt)

    rlusd = next(a for a in result["assets"] if a["code"] == "RLUSD")
    assert rlusd == {"code": "RLUSD", "issuer": ISSUER, "balance": "100000.00"}
    assert all("issuer" not in a for a in result["assets"] if a["code"] == "XRP")
    assert result["market"] == {"base": "XRP", "quote": {"code": "RLUSD", "issuer": ISSUER}}


@pytest.mark.asyncio
async def test_get_treasury_market_is_null_without_a_market_table(
    payment_rig, tmp_path: Path
) -> None:
    rt = make_runtime(payment_rig, payment_rig.ledger, tmp_path)

    assert (await tools.get_treasury(rt))["market"] is None
