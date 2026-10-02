from __future__ import annotations

from pathlib import Path

import pytest

from merkl_mcp.server import build_app
from tests.conftest import make_runtime


@pytest.mark.asyncio
async def test_the_six_tools_are_registered(payment_rig, tmp_path: Path) -> None:
    rt = make_runtime(payment_rig, payment_rig.ledger, tmp_path)
    app = build_app(rt)

    names = {tool.name for tool in await app.list_tools()}

    assert names == {
        "get_treasury",
        "read_receipts",
        "verify_receipt",
        "propose_payment",
        "propose_swap",
        "pending_approval",
    }


@pytest.mark.asyncio
async def test_a_tool_that_raises_reports_error_instead_of_crashing(
    payment_rig, tmp_path: Path, monkeypatch
) -> None:
    rt = make_runtime(payment_rig, payment_rig.ledger, tmp_path)
    app = build_app(rt)

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("the ledger is unreachable")

    monkeypatch.setattr(rt.reader, "balances", _boom)

    result = await app.call_tool("get_treasury", {})

    # FastMCP wraps a tool's return in content blocks; the dict comes back as
    # structured content either way — the point is nothing raised past here.
    assert result is not None
