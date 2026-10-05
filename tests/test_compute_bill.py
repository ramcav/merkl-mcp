from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from merkl.demo.rig import ISSUER, SUPPLIER

from merkl_mcp import tools
from merkl_mcp.bundle import BillConfig
from merkl_mcp.compute_bill import CoinGeckoPrice
from tests.conftest import make_runtime


class FakePrices:
    def __init__(self, price: str | None) -> None:
        self.price = None if price is None else Decimal(price)

    async def xrp_usd(self) -> Decimal | None:
        return self.price


def _journal(home: Path, rows: list[tuple[str, str]]) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "journal.jsonl").write_text(
        "".join(json.dumps({"at": at, "cost_usd": cost}) + "\n" for at, cost in rows)
    )


def _runtime(rig, tmp_path: Path, *, price: str | None, day: str | None = None):
    rt = make_runtime(rig, rig.ledger, tmp_path)
    today = datetime.fromisoformat(rt.clock.now().replace("Z", "+00:00")).strftime("%A").lower()
    rt.config = dataclasses.replace(
        rt.config,
        bill=BillConfig(operator=SUPPLIER, bill_day=day or today),
        trader_home=tmp_path / "home",
    )
    rt.prices = FakePrices(price)
    return rt


@pytest.mark.asyncio
async def test_the_bill_is_the_journal_cost_in_xrp_at_the_reference_price(
    payment_rig, tmp_path: Path
) -> None:
    rt = _runtime(payment_rig, tmp_path, price="0.50")
    _journal(
        tmp_path / "home", [("2026-09-14T09:00:00Z", "0.30"), ("2026-09-15T09:00:00Z", "0.20")]
    )

    bill = (await tools.get_treasury(rt))["compute_bill"]

    assert bill["owed"] == "1"  # $0.50 at $0.50 per XRP
    assert bill["currency"] == "XRP"
    assert bill["operator"] == SUPPLIER
    assert bill["due_now"] is True
    assert bill["due_day"]


@pytest.mark.asyncio
async def test_no_price_means_no_amount_and_not_a_guess(payment_rig, tmp_path: Path) -> None:
    rt = _runtime(payment_rig, tmp_path, price=None)
    _journal(tmp_path / "home", [("2026-09-14T09:00:00Z", "0.30")])

    bill = (await tools.get_treasury(rt))["compute_bill"]

    assert bill["owed"] is None
    assert bill["owed_usd"] == "0.3"


@pytest.mark.asyncio
async def test_a_settled_payment_to_the_operator_resets_the_tab(
    payment_rig, tmp_path: Path
) -> None:
    rt = _runtime(payment_rig, tmp_path, price="0.50")
    _journal(
        tmp_path / "home",
        [("2000-01-01T00:00:00Z", "9.00"), ("2999-01-01T00:00:00Z", "0.10")],
    )
    await tools.propose_payment(
        rt, destination=SUPPLIER, amount="1.00", currency="RLUSD", issuer=ISSUER, why="bill"
    )

    bill = (await tools.get_treasury(rt))["compute_bill"]

    assert bill["owed_usd"] == "0.1", "only what was spent after the payment is owed"


@pytest.mark.asyncio
async def test_not_due_off_the_bill_day_and_null_without_a_bill_table(
    payment_rig, tmp_path: Path
) -> None:
    rt = _runtime(payment_rig, tmp_path, price="0.50", day="nonday")
    _journal(tmp_path / "home", [("2026-09-14T09:00:00Z", "0.30")])
    assert (await tools.get_treasury(rt))["compute_bill"]["due_now"] is False

    rt.config = dataclasses.replace(rt.config, bill=None)
    assert (await tools.get_treasury(rt))["compute_bill"] is None


@pytest.mark.asyncio
async def test_coingecko_price_is_cached_and_failure_is_none() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"ripple": {"usd": 0.53}})

    source = CoinGeckoPrice(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await source.xrp_usd() == Decimal("0.53")
    assert await source.xrp_usd() == Decimal("0.53")
    assert calls == 1

    down = CoinGeckoPrice(
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    )
    assert await down.xrp_usd() is None
