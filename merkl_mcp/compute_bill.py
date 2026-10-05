"""What this agent owes its operator for compute, in XRP.

The harness writes ``cost_usd`` for every cycle into ``journal.jsonl`` under
``$MERKL_TRADER_HOME`` (or ``[loop].home``). The bill is the sum of those costs
since the last settled payment to the operator, converted at a reference price
read from CoinGecko's public API (cached ten minutes). With no price the
amount is ``None``: the agent is told to hold the bill, not to guess.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from decimal import ROUND_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final, Protocol

import httpx

COINGECKO_PRICE_URL: Final = "https://api.coingecko.com/api/v3/simple/price"
PRICE_TTL_SECONDS: Final = 600
DROP: Final = Decimal("0.000001")
WEEKDAYS: Final = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class PriceSource(Protocol):
    async def xrp_usd(self) -> Decimal | None: ...


class CoinGeckoPrice:
    """XRP in dollars, one public GET, cached; ``None`` whenever it cannot be read."""

    def __init__(self, *, client: httpx.AsyncClient | None = None, ttl: float = PRICE_TTL_SECONDS):
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._owns_client = client is None
        self._ttl = ttl
        self._cached: tuple[float, Decimal] | None = None

    async def xrp_usd(self) -> Decimal | None:
        now = time.monotonic()
        if self._cached is not None and now - self._cached[0] < self._ttl:
            return self._cached[1]
        try:
            response = await self._client.get(
                COINGECKO_PRICE_URL, params={"ids": "ripple", "vs_currencies": "usd"}
            )
            response.raise_for_status()
            price = Decimal(str(response.json()["ripple"]["usd"]))
        except (httpx.HTTPError, ValueError, KeyError, TypeError, InvalidOperation):
            return None
        if price <= 0:
            return None
        self._cached = (now, price)
        return price

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def journal_costs(journal: Path, since: str | None) -> Decimal:
    """Sum of ``cost_usd`` over journal rows later than ``since`` (an ISO instant)."""
    total = Decimal(0)
    try:
        lines = journal.read_text(encoding="utf-8").splitlines()
    except OSError:
        return total
    for line in lines:
        try:
            row = json.loads(line)
            if since is not None and _when(str(row["at"])) <= _when(since):
                continue
            total += Decimal(str(row.get("cost_usd", "0")))
        except (ValueError, KeyError, InvalidOperation):
            continue
    return total


def _when(instant: str) -> datetime:
    return datetime.fromisoformat(instant.replace("Z", "+00:00")).astimezone(UTC)


def owed_xrp(owed_usd: Decimal, price: Decimal | None) -> Decimal | None:
    """Rounded up to the drop, like the trading loop: the rounding belongs to who is owed."""
    if price is None or price <= 0:
        return None
    return (owed_usd / price).quantize(DROP, rounding=ROUND_UP)


def due_now(now: datetime, due_day: str, owed_usd: Decimal, last_paid: datetime | None) -> bool:
    if owed_usd <= 0 or WEEKDAYS[now.weekday()] != due_day:
        return False
    return last_paid is None or last_paid.date() != now.date()


def as_content(
    *,
    owed: Decimal | None,
    owed_usd: Decimal,
    due_day: str,
    due: bool,
    operator: str,
) -> dict[str, Any]:
    return {
        "owed": None if owed is None else format(owed.normalize(), "f"),
        "owed_usd": format(owed_usd.normalize(), "f"),
        "currency": "XRP",
        "due_day": due_day,
        "due_now": due,
        "operator": operator,
    }
