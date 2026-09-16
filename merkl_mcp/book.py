"""Reading the book and the balances — outside the settlement port, on purpose.

``get_treasury`` and ``get_market`` are reads: nothing here proposes, signs or
submits, so neither tool goes anywhere near :class:`~merkl.sdk.receipts.ReceiptBuilder`
or the rail adapter it settles through. ``merkl_trader.market`` makes the same
choice for the same reason — a stranger could re-read this off a public node a
minute later, so it costs nothing to read it the same way ourselves, straight
off the ledger over JSON-RPC.

Two readers share one shape (:class:`LedgerReader`) because the tools are
tested against the SDK's in-memory rail (``merkl.adapters.fake``), which has
no JSON-RPC node to ask. ``XrplJsonRpcReader`` is what production talks to;
``FakeLedgerReader`` reads a :class:`~merkl.adapters.fake.FakeLedger` object
directly. The fake ledger prices a swap at one flat rate per asset pair, not a
depth curve (see its own docstring), so ``FakeLedgerReader``'s book has one
price and every requested size fills at it — honest about what the in-memory
rail can stand in for and what it cannot.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Protocol

import httpx
from merkl.adapters.xrpl import currency_code
from merkl.core.canonical import JSONObject, format_decimal

DROPS_PER_XRP: Final = Decimal(1_000_000)
PRICE_PLACES: Final = Decimal("0.000001")
BOOK_LIMIT: Final = 40
DEFAULT_TIMEOUT: Final = 20.0


class MarketError(Exception):
    """The ledger could not be read."""


class LedgerReader(Protocol):
    """What ``get_treasury`` and ``get_market`` need from a ledger."""

    async def balances(self, treasury: str) -> dict[str, str]: ...

    async def book(
        self, *, base: str, quote_code: str, quote_issuer: str, sizes: Sequence[Decimal]
    ) -> JSONObject: ...


# -- production: the real ledger, over JSON-RPC ------------------------------ #


class XrplJsonRpcReader:
    """One public node, asked directly — the same technique as ``merkl_trader.market``."""

    def __init__(
        self,
        json_rpc_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._url = json_rpc_url
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def balances(self, treasury: str) -> dict[str, str]:
        """XRP plus every trust line this account holds, by ``CODE.issuer``."""
        info = await self._call("account_info", {"account": treasury, "ledger_index": "validated"})
        data = info.get("account_data")
        drops = data.get("Balance") if isinstance(data, dict) else None
        balances = {"XRP": format_decimal(_drops(drops))}
        lines = await self._call(
            "account_lines", {"account": treasury, "ledger_index": "validated"}
        )
        for row in lines.get("lines") or []:
            if not isinstance(row, dict):
                continue
            code = _decoded_currency(str(row.get("currency", "")))
            issuer = str(row.get("account", ""))
            balances[f"{code}.{issuer}"] = format_decimal(_number(row.get("balance")))
        return balances

    async def book(
        self, *, base: str, quote_code: str, quote_issuer: str, sizes: Sequence[Decimal]
    ) -> JSONObject:
        """The book both ways: what ``sizes`` of ``base`` actually costs, and pays."""
        native = {"currency": base}
        issued = {"currency": currency_code(quote_code), "issuer": quote_issuer}
        asks = await self._offers(gets=native, pays=issued)  # give base, take quote
        bids = await self._offers(gets=issued, pays=native)  # give quote, take base
        best_bid, best_ask = _best(bids, side="bid"), _best(asks, side="ask")
        return {
            "as_of": _now_iso(),
            "best_bid": _opt(best_bid),
            "best_ask": _opt(best_ask),
            "mid": _opt(_mid(best_bid, best_ask)),
            "spread": _opt(_spread(best_bid, best_ask)),
            "cost_to_buy_base": [_walk(asks, size, side="ask") for size in sizes],
            "proceeds_selling_base": [_walk(bids, size, side="bid") for size in sizes],
        }

    async def _call(self, method: str, params: JSONObject) -> dict[str, Any]:
        try:
            response = await self._client.post(
                self._url, json={"method": method, "params": [params]}
            )
            response.raise_for_status()
            body = json.loads(response.text, parse_float=Decimal)
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise MarketError(f"{method} against {self._url} failed: {exc}") from exc
        result = body.get("result") if isinstance(body, dict) else None
        if not isinstance(result, dict):
            raise MarketError(f"{method} returned no result object")
        if result.get("status") == "error":
            raise MarketError(f"{method}: {result.get('error_message') or result.get('error')}")
        return result

    async def _offers(self, *, gets: JSONObject, pays: JSONObject) -> list[dict[str, Any]]:
        result = await self._call(
            "book_offers", {"taker_gets": gets, "taker_pays": pays, "limit": BOOK_LIMIT}
        )
        offers = result.get("offers")
        return (
            [entry for entry in offers if isinstance(entry, dict)]
            if isinstance(offers, list)
            else []
        )


# -- tests: the in-memory rail, read directly -------------------------------- #


class FakeLedgerReader:
    """Reads a ``merkl.adapters.fake.FakeLedger`` for the scenario suite's tools.

    There is no JSON-RPC node behind the in-memory rail, so this bypasses the
    wire format entirely and reads the ledger's own ``balances`` dict and
    per-pair ``rate``. A rate that is not configured reads as an empty book at
    that size, the same "could not fill any of it" a thin real book reports.
    """

    def __init__(self, ledger: Any) -> None:
        self._ledger = ledger

    async def balances(self, treasury: str) -> dict[str, str]:
        return {
            asset: format_decimal(value)
            for (account, asset), value in self._ledger.balances.items()
            if account == treasury
        }

    async def book(
        self, *, base: str, quote_code: str, quote_issuer: str, sizes: Sequence[Decimal]
    ) -> JSONObject:
        quote_asset = f"{quote_code}.{quote_issuer}"
        ask = self._ledger.rate(quote_asset, base)  # quote per base: cost of buying base
        bid = self._ledger.rate(base, quote_asset)  # base per quote: proceeds of selling base
        ask_r = None if ask is None else _round(ask)
        bid_r = None if bid is None else _round(bid)
        return {
            "as_of": _now_iso(),
            "best_bid": _opt(bid_r),
            "best_ask": _opt(ask_r),
            "mid": _opt(_mid(bid_r, ask_r)),
            "spread": _opt(_spread(bid_r, ask_r)),
            "cost_to_buy_base": [_flat_level(size, ask) for size in sizes],
            "proceeds_selling_base": [_flat_level(size, bid) for size in sizes],
        }


def _flat_level(size: Decimal, price: Decimal | None) -> JSONObject:
    filled = size if price is not None else Decimal(0)
    return {
        "size": format_decimal(size),
        "filled": format_decimal(filled),
        "price": None if price is None else format_decimal(_round(price)),
    }


# -- book arithmetic (shared shape with merkl_trader.market) ----------------- #


def _walk(offers: Sequence[dict[str, Any]], size: Decimal, *, side: str) -> JSONObject:
    remaining, base_filled, quote_moved = size, Decimal(0), Decimal(0)
    for offer in offers:
        base, quote = _sides(offer, side=side)
        if base <= 0 or quote <= 0:
            continue
        take = min(remaining, base)
        base_filled += take
        quote_moved += take * quote / base
        remaining -= take
        if remaining <= 0:
            break
    price = (quote_moved / base_filled) if base_filled > 0 else None
    return {
        "size": format_decimal(size),
        "filled": format_decimal(base_filled),
        "price": None if price is None else format_decimal(_round(price)),
    }


def _sides(offer: dict[str, Any], *, side: str) -> tuple[Decimal, Decimal]:
    gets, pays = offer.get("TakerGets"), offer.get("TakerPays")
    if side == "ask":  # the maker gives base and takes quote
        return _amount(gets), _amount(pays)
    return _amount(pays), _amount(gets)


def _best(offers: Sequence[dict[str, Any]], *, side: str) -> Decimal | None:
    for offer in offers:
        base, quote = _sides(offer, side=side)
        if base > 0 and quote > 0:
            return _round(quote / base)
    return None


def _mid(bid: Decimal | None, ask: Decimal | None) -> Decimal | None:
    return None if bid is None or ask is None else _round((bid + ask) / 2)


def _spread(bid: Decimal | None, ask: Decimal | None) -> Decimal | None:
    return None if bid is None or ask is None else _round(ask - bid)


def _amount(value: Any) -> Decimal:
    if isinstance(value, dict):
        return _number(value.get("value"))
    return _drops(value)


def _drops(value: Any) -> Decimal:
    return _number(value) / DROPS_PER_XRP


def _number(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or value is None:
        return Decimal(0)
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return Decimal(0)


def _round(value: Decimal) -> Decimal:
    return value.quantize(PRICE_PLACES) + Decimal(0)


def _opt(value: Decimal | None) -> str | None:
    return None if value is None else format_decimal(value)


def _decoded_currency(code: str) -> str:
    """The 3-letter code as-is, or the ASCII decoded out of the 40-hex form."""
    if len(code) == 3:
        return code
    try:
        return bytes.fromhex(code).rstrip(b"\x00").decode("ascii") or code
    except ValueError:
        return code


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "FakeLedgerReader",
    "LedgerReader",
    "MarketError",
    "XrplJsonRpcReader",
]
