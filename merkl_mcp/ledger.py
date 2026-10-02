"""Reading the treasury's balances — outside the settlement port, on purpose.

``get_treasury`` is a read: nothing here proposes, signs or submits. Market
data (order books, prices) is deliberately not served here; the harness gets it
from an XRPL or market MCP server.

Two readers share one shape (:class:`LedgerReader`) because the tools are
tested against the SDK's in-memory rail (``merkl.adapters.fake``), which has no
JSON-RPC node to ask. ``XrplJsonRpcReader`` is what production talks to;
``FakeLedgerReader`` reads a :class:`~merkl.adapters.fake.FakeLedger` directly.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Protocol

import httpx
from merkl.core.canonical import JSONObject, format_decimal

DROPS_PER_XRP: Final = Decimal(1_000_000)
DEFAULT_TIMEOUT: Final = 20.0


class LedgerError(Exception):
    """The ledger could not be read."""


class LedgerReader(Protocol):
    """What ``get_treasury`` needs from a ledger."""

    async def balances(self, treasury: str) -> dict[str, str]: ...


class XrplJsonRpcReader:
    """One public node, asked directly over JSON-RPC."""

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
        balances = {"XRP": format_decimal(_number(drops) / DROPS_PER_XRP)}
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

    async def _call(self, method: str, params: JSONObject) -> dict[str, Any]:
        try:
            response = await self._client.post(
                self._url, json={"method": method, "params": [params]}
            )
            response.raise_for_status()
            body = json.loads(response.text, parse_float=Decimal)
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise LedgerError(f"{method} against {self._url} failed: {exc}") from exc
        result = body.get("result") if isinstance(body, dict) else None
        if not isinstance(result, dict):
            raise LedgerError(f"{method} returned no result object")
        if result.get("status") == "error":
            raise LedgerError(f"{method}: {result.get('error_message') or result.get('error')}")
        return result


class FakeLedgerReader:
    """Reads a ``merkl.adapters.fake.FakeLedger``'s balances for the scenario suite."""

    def __init__(self, ledger: Any) -> None:
        self._ledger = ledger

    async def balances(self, treasury: str) -> dict[str, str]:
        return {
            asset: format_decimal(value)
            for (account, asset), value in self._ledger.balances.items()
            if account == treasury
        }


def _number(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or value is None:
        return Decimal(0)
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return Decimal(0)


def _decoded_currency(code: str) -> str:
    """The 3-letter code as-is, or the ASCII decoded out of the 40-hex form."""
    if len(code) == 3:
        return code
    try:
        return bytes.fromhex(code).rstrip(b"\x00").decode("ascii") or code
    except ValueError:
        return code
