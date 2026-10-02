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


@pytest.mark.asyncio
async def test_verify_receipt_says_nothing_was_contradicted(payment_rig, tmp_path: Path) -> None:
    rt = make_runtime(payment_rig, payment_rig.ledger, tmp_path)
    await tools.propose_payment(
        rt, destination=SUPPLIER, amount="42.00", currency="RLUSD", issuer=ISSUER, why="pay"
    )
    receipt_id = (await tools.read_receipts(rt))["receipts"][0]["receipt_id"]

    result = await tools.verify_receipt(rt, receipt_id)

    assert result["contradicted"] is False
    assert result["verdict"].startswith("Nothing was contradicted.")
    assert result["failed_checks"] == []


@pytest.mark.asyncio
async def test_verify_receipt_catches_a_tampered_receipt(payment_rig, tmp_path: Path) -> None:
    rt = make_runtime(payment_rig, payment_rig.ledger, tmp_path)
    await tools.propose_payment(
        rt, destination=SUPPLIER, amount="42.00", currency="RLUSD", issuer=ISSUER, why="pay"
    )
    receipt_id = (await tools.read_receipts(rt))["receipts"][0]["receipt_id"]
    path = rt.store.path_for(receipt_id)
    path.write_text(path.read_text().replace("42.00", "4200.00"))

    result = await tools.verify_receipt(rt, receipt_id)

    assert result["contradicted"] is True
    assert result["failed_checks"]


@pytest.mark.asyncio
async def test_verify_receipt_unknown_or_hostile_id_is_an_error(
    payment_rig, tmp_path: Path
) -> None:
    rt = make_runtime(payment_rig, payment_rig.ledger, tmp_path)

    assert "error" in await tools.verify_receipt(rt, "nope")
    assert "error" in await tools.verify_receipt(rt, "../../etc/passwd")


@pytest.mark.asyncio
async def test_read_receipts_finds_receipts_already_in_the_bundle_receipts_dir(
    payment_rig, tmp_path: Path
) -> None:
    """The agent's own earlier receipts live in ``<bundle>/receipts``; a fresh
    runtime pointed at that directory must see them."""
    rt = make_runtime(payment_rig, payment_rig.ledger, tmp_path, name="launch")
    await tools.propose_payment(
        rt, destination=SUPPLIER, amount="1.00", currency="RLUSD", issuer=ISSUER, why="launch"
    )
    later = make_runtime(payment_rig, payment_rig.ledger, tmp_path, name="restart")
    later.store = rt.store  # what receipt_store_for(config) yields for the same bundle dir

    assert len((await tools.read_receipts(later))["receipts"]) == 1
