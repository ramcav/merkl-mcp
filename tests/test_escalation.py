"""Escalation, the one-at-a-time rule, and resuming after a restart.

``payment_rig``'s policy sends anything at or above 500.00 RLUSD to a human
(``human_threshold`` in ``merkl.demo.rig.build_policy``) with a quorum of 2,
and its approvers are ``ALICE`` (Ed25519) and ``BOB`` (a passkey) — the same
two ``merkl.demo.rig.approvals_for`` signs with. That function stands in for
"a person decided, and the co-signer relayed it to the notary before it ever
reached this process" — which is exactly the shape ``pending_approval`` reads
back as ``signer_decision``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from merkl.demo.rig import ISSUER, SUPPLIER, approvals_for

from merkl_mcp import tools
from tests.conftest import FakeEscalations, make_runtime, reload_runtime


async def _escalate(rt, *, amount: str = "600.00"):
    return await tools.propose_payment(
        rt,
        destination=SUPPLIER,
        amount=amount,
        currency="RLUSD",
        issuer=ISSUER,
        why="above the human threshold",
    )


@pytest.mark.asyncio
async def test_a_large_payment_escalates_with_a_challenge_and_expiry(
    payment_rig, tmp_path: Path
) -> None:
    rt = make_runtime(payment_rig, payment_rig.ledger, tmp_path)

    result = await _escalate(rt)

    assert result["outcome"] == "waiting_for_a_person"
    assert result["challenge"]
    assert result["expires_at"]
    assert rt.state.pending is not None
    assert rt.state.pending.prepared_tx is not None


@pytest.mark.asyncio
async def test_a_second_proposal_while_one_waits_is_not_proposed(
    payment_rig, tmp_path: Path
) -> None:
    rt = make_runtime(payment_rig, payment_rig.ledger, tmp_path)
    first = await _escalate(rt)
    counter_after_first = rt.state.counter

    second = await _escalate(rt, amount="700.00")

    assert second == {
        "outcome": "waiting_for_a_person",
        "challenge": first["challenge"],
        "expires_at": first["expires_at"],
    }
    assert rt.state.counter == counter_after_first  # the signer was never touched a second time


@pytest.mark.asyncio
async def test_pending_approval_reports_none_then_waiting(payment_rig, tmp_path: Path) -> None:
    rt = make_runtime(payment_rig, payment_rig.ledger, tmp_path)
    assert await tools.pending_approval(rt) == {"status": "none"}

    await _escalate(rt)
    result = await tools.pending_approval(rt)

    assert result["status"] == "waiting"
    assert result["time_left_seconds"] > 0


@pytest.mark.asyncio
async def test_pending_approval_resumes_and_settles_once_a_person_decides(
    payment_rig, tmp_path: Path
) -> None:
    escalations = FakeEscalations()
    rt = make_runtime(payment_rig, payment_rig.ledger, tmp_path, escalations=escalations)
    escalated = await _escalate(rt)
    challenge = escalated["challenge"]

    assertions = approvals_for(challenge, at=payment_rig.clock.now())
    decision = await payment_rig.signer.approve(
        challenge, assertions, rt.state.pending.prepared_tx
    )
    escalations.answer = {"status": "approved", "signer_decision": decision}

    result = await tools.pending_approval(rt)

    assert result["status"] == "settled"
    assert result["tx_hash"]
    assert rt.state.pending is None
    assert await tools.pending_approval(rt) == {"status": "none"}


@pytest.mark.asyncio
async def test_resume_survives_a_restart(payment_rig, tmp_path: Path) -> None:
    """The prepared transaction is read back off disk, not rebuilt — rebuilding
    it now would ask the ledger for a sequence and fee the policy key never
    signed (see ``Pending.prepared_tx``'s own docstring)."""
    escalations = FakeEscalations()
    rt = make_runtime(payment_rig, payment_rig.ledger, tmp_path, escalations=escalations)
    escalated = await _escalate(rt)
    challenge = escalated["challenge"]
    assertions = approvals_for(challenge, at=payment_rig.clock.now())
    decision = await payment_rig.signer.approve(
        challenge, assertions, rt.state.pending.prepared_tx
    )

    # A brand new Runtime, reading only what was saved to disk — the restart.
    restarted = reload_runtime(rt)
    assert restarted.state.pending is not None
    assert restarted.state.pending.challenge == challenge
    restarted.escalations = escalations
    escalations.answer = {"status": "approved", "signer_decision": decision}

    result = await tools.pending_approval(restarted)

    assert result["status"] == "settled"
    assert restarted.state.pending is None
