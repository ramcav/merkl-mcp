"""The six tools: ``get_treasury``, ``propose_payment``, ``propose_swap``,
``pending_approval``, ``read_receipts``, ``verify_receipt``. Market data is not
Merkl's business; the harness gets it from an XRPL or market MCP server.

Every function here takes a :class:`~merkl_mcp.runtime.Runtime` and returns a
plain JSON-able dict. None of them raises for a business outcome — a denial,
an escalation, a malformed request are all results, not exceptions — and
``server.py`` wraps every call in one more layer of ``except Exception`` so a
genuine bug (a network error the code below did not anticipate) still comes
back as ``{"error": ...}`` rather than tearing down the MCP session.

The order inside ``propose_payment``/``propose_swap``/``pending_approval``
mirrors ``merkl_trader.trader.Trader.cycle``'s first two steps for the same
reason: recover whatever crashed mid-flight before doing anything else, and
never start a new proposal while a person is still deciding the last one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from merkl.adapters.xrpl import currency_code
from merkl.core.canonical import ContentError, JSONObject, shift_instant
from merkl.core.intent import Amount, CurrencyRef, Intent, IssuedCurrency, SwapBuy, SwapSell
from merkl.core.receipt import Instruction, Reasoning
from merkl.core.verify.receipt import receipt_from_content
from merkl.core.verify.receipt import verify_receipt as verify_receipt_leaves
from merkl.shared.hashing import SHA256Hash

from merkl_mcp import compute_bill
from merkl_mcp.runtime import Runtime
from merkl_mcp.session import JoinedSession, joined
from merkl_mcp.state import InFlight, Pending

INTENT_TTL_SECONDS = 3600
"""Same reasoning as ``merkl_trader.trader.INTENT_TTL_SECONDS``: long enough
that a proposal escalated to a human survives them actually reading it."""

NOTE_LENGTH = 200
"""``why`` becomes leaf 6's note, truncated exactly where the receipt format
truncates it."""

RECEIPTS_SHOWN_MAX = 20

_RECEIPT_ID = re.compile(r"[A-Za-z0-9_.-]{1,128}")


# --------------------------------------------------------------------------- #
# get_treasury
# --------------------------------------------------------------------------- #


async def get_treasury(rt: Runtime) -> JSONObject:
    """Address, balances, policy version, signer health, any open escalation."""
    async with rt.lock:
        await _recover(rt)
        balances = await rt.reader.balances(rt.config.treasury.address)
        health = await rt.signer.health()
        pending = rt.state.pending
        return {
            "address": rt.config.treasury.address,
            "network": {"rail": rt.config.rail.name, "json_rpc_url": rt.config.rail.json_rpc_url},
            "balances": balances,
            "assets": _assets(balances),
            "market": _market(rt),
            "compute_bill": await _compute_bill(rt),
            "policy_version": rt.config.treasury.policy_version,
            "signer": _health_line(health),
            "pending_with_a_person": (
                None
                if pending is None
                else {"challenge": pending.challenge, "expires_at": pending.expires_at}
            ),
        }


async def _compute_bill(rt: Runtime) -> JSONObject | None:
    """What the agent owes its operator, from the harness's own journal costs."""
    bill = rt.config.bill
    if bill is None:
        return None
    home = rt.config.trader_home
    last_paid = await _last_paid_to(rt, bill.operator)
    since = None if last_paid is None else last_paid.isoformat()
    owed_usd = compute_bill.journal_costs(home / "journal.jsonl", since) if home else Decimal(0)
    price = await rt.prices.xrp_usd() if rt.prices is not None else None
    now = _when(rt.clock.now())
    return compute_bill.as_content(
        owed=compute_bill.owed_xrp(owed_usd, price),
        owed_usd=owed_usd,
        due_day=bill.bill_day,
        due=compute_bill.due_now(now, bill.bill_day, owed_usd, last_paid),
        operator=bill.operator,
    )


async def _last_paid_to(rt: Runtime, operator: str) -> datetime | None:
    """When the last settled payment to ``operator`` was filed (receipt file time)."""
    latest: datetime | None = None
    for envelope in await rt.store.list(rt.config.treasury.address):
        found = await rt.store.get(envelope.receipt_id)
        if found is None:  # pragma: no cover - listed a moment ago
            continue
        _, leaves = found
        intent, result = leaves.intent, leaves.result
        if intent is None or result is None or result.outcome != "settled":
            continue
        if intent.is_swap or intent.destination != operator:
            continue
        stamp = _mtime_iso(rt.store.path_for(envelope.receipt_id))
        moment = _when(stamp) if stamp else None
        if moment is not None and (latest is None or moment > latest):
            latest = moment
    return latest


def _assets(balances: dict[str, str]) -> list[JSONObject]:
    """``"RLUSD.rIssuer": "0"`` as ``{code, currency_hex, issuer, balance}`` — the keys of
    ``balances`` are for people, these fields are for the next tool call."""
    assets: list[JSONObject] = []
    for key, balance in balances.items():
        code, _, issuer = key.partition(".")
        asset: JSONObject = {"code": code, "currency_hex": currency_code(code), "balance": balance}
        if issuer:
            asset["issuer"] = issuer
        assets.append(asset)
    return assets


def _market(rt: Runtime) -> JSONObject | None:
    market = rt.config.market
    if market is None:
        return None
    return {
        "base": market.base,
        "quote": {
            "code": market.quote_code,
            "currency_hex": currency_code(market.quote_code),
            "issuer": market.quote_issuer,
        },
    }


def _health_line(health: JSONObject) -> str:
    attested = bool(health.get("attested"))
    trust = "attested" if attested else str(health.get("warning") or "unattested")
    policy_hash = str(health.get("policy_hash", ""))[:12]
    status, version = health.get("status", "unknown"), health.get("policy_version")
    pending = health.get("pending_escalations", 0)
    return f"{status}; policy {version} ({policy_hash}…); {pending} pending; {trust}"


# --------------------------------------------------------------------------- #
# read_receipts
# --------------------------------------------------------------------------- #


async def read_receipts(rt: Runtime, limit: int = 10) -> JSONObject:
    """This agent's recent receipts, in words, oldest of the window first.

    ``LocalReceiptStore.list()`` sorts by receipt id, not by time — its own
    ``since`` parameter is a no-op for the same reason (a receipt has no
    timestamp the store may trust). "Recent" only means something if this
    tool sorts by the one time-like signal available, the file's mtime,
    before applying ``limit`` — so that is done here rather than trusting
    the store's own order.
    """
    envelopes = await rt.store.list(rt.config.treasury.address)
    shown = max(1, min(int(limit), RECEIPTS_SHOWN_MAX))
    found_all: list[tuple[str, Any, Any]] = []
    for envelope in envelopes:
        found = await rt.store.get(envelope.receipt_id)
        if found is None:  # pragma: no cover - listed a moment ago
            continue
        _, leaves = found
        found_all.append((envelope.receipt_id, leaves, rt.store.path_for(envelope.receipt_id)))
    found_all.sort(key=lambda row: _mtime(row[2]))
    rows = [
        _summarize(receipt_id, leaves, path) for receipt_id, leaves, path in found_all[-shown:]
    ]
    return {"receipts": rows}


async def verify_receipt(rt: Runtime, receipt_id: str) -> JSONObject:
    """Run the SDK verifier, locally, on a receipt in this agent's store.

    No network and no trusted party: the checks are the ones ``merkl verify``
    runs. Trust anchors this process was not given (validator keys, PCRs, the
    policy document) come back as named unchecked lines, never as passes.
    """
    if not _RECEIPT_ID.fullmatch(receipt_id):
        return {"error": "receipt_id must be a receipt id from read_receipts"}
    path = rt.store.path_for(receipt_id)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"error": f"no receipt {receipt_id} in this agent's store"}
    envelope, leaves = receipt_from_content(record)
    verdict = verify_receipt_leaves(
        envelope,
        leaves,
        settlement_proof=record.get("settlement_proof"),
        policy_document=record.get("policy_document"),
    )
    failed = [f"{check.name}: {check.detail}" for check in verdict.result.failures]
    summary = verdict.summary
    return {
        "receipt_id": receipt_id,
        "verdict": verdict.verdict_line,
        "contradicted": not verdict.ok,
        "complete": verdict.complete,
        "failed_checks": failed,
        "authorization": verdict.transaction_authorization,
        "ledger_inclusion": verdict.ledger_inclusion,
        "plain": [
            line
            for line in (summary.instructed, summary.rule, summary.approved, summary.settled)
            if line
        ],
    }


def _mtime(path: Any) -> float:
    try:
        return path.stat().st_mtime
    except OSError:  # pragma: no cover - the file was just read successfully
        return 0.0


def _summarize(receipt_id: str, leaves: Any, path: Any) -> JSONObject:
    intent, decision, result = leaves.intent, leaves.policy_decision, leaves.result
    failed = [
        {"rule": rule.name, "detail": rule.detail}
        for rule in (decision.rules if decision else ())
        if rule.outcome == "fail"
    ]
    return {
        "receipt_id": receipt_id,
        "when": _mtime_iso(path),
        "outcome": result.outcome if result else None,
        "what": _describe(intent),
        "refused_by": failed or None,
        "why_it_was_asked": leaves.reasoning.note if leaves.reasoning else None,
    }


def _describe(intent: Intent | None) -> str | None:
    if intent is None:  # pragma: no cover - every receipt has leaf 1
        return None
    if intent.is_swap:
        return (
            f"swap: sell up to {intent.outflow.value} {_code(intent.outflow.currency)} "
            f"for {intent.deliver_amount.value} {_code(intent.deliver_amount.currency)}"
        )
    return f"pay {intent.outflow.value} {_code(intent.outflow.currency)} to {intent.destination}"


def _mtime_iso(path: Any) -> str | None:
    try:
        return (
            datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat().replace("+00:00", "Z")
        )
    except OSError:  # pragma: no cover - the file was just read successfully
        return None


# --------------------------------------------------------------------------- #
# propose_payment / propose_swap
# --------------------------------------------------------------------------- #


async def propose_payment(
    rt: Runtime,
    *,
    destination: str,
    amount: str,
    currency: str,
    issuer: str | None = None,
    why: str = "",
    session_id: str | None = None,
    session_action_count: int | None = None,
    depends_on: str | None = None,
) -> JSONObject:
    """Propose a payment. Amounts are decimal strings — never a float.

    ``session_id`` (with ``session_action_count``, how many actions that session
    already holds, and ``depends_on``, an action id) joins the receipt to a
    session the caller opened; the harness supplies them, the model never does."""

    def build(nonce: str, expires_at: str) -> Intent:
        return Intent(
            rail=rt.config.rail.name,
            treasury=rt.config.treasury.address,
            destination=destination,
            amount=Amount(value=amount, currency=_currency(currency, issuer)),
            policy_version=rt.config.treasury.policy_version,
            agent_public_key=rt.agent_public_key,
            nonce=nonce,
            expires_at=expires_at,
        )

    return await _propose(
        rt,
        kind="payment",
        build_intent=build,
        why=why,
        session=_session(rt, session_id, session_action_count),
        depends_on=depends_on,
    )


async def propose_swap(
    rt: Runtime,
    *,
    sell_amount: str,
    sell_currency: str,
    buy_amount: str,
    buy_currency: str,
    sell_issuer: str | None = None,
    buy_issuer: str | None = None,
    why: str = "",
    session_id: str | None = None,
    session_action_count: int | None = None,
    depends_on: str | None = None,
) -> JSONObject:
    """Propose a swap: sell at most ``sell_amount``, buy exactly ``buy_amount``.

    The ``session_*`` and ``depends_on`` arguments are as for ``propose_payment``."""

    def build(nonce: str, expires_at: str) -> Intent:
        treasury = rt.config.treasury.address
        return Intent(
            type="swap",
            rail=rt.config.rail.name,
            treasury=treasury,
            destination=treasury,
            sell=SwapSell(currency=_currency(sell_currency, sell_issuer), max_amount=sell_amount),
            buy=SwapBuy(currency=_currency(buy_currency, buy_issuer), amount=buy_amount),
            policy_version=rt.config.treasury.policy_version,
            agent_public_key=rt.agent_public_key,
            nonce=nonce,
            expires_at=expires_at,
        )

    return await _propose(
        rt,
        kind="swap",
        build_intent=build,
        why=why,
        session=_session(rt, session_id, session_action_count),
        depends_on=depends_on,
    )


def _session(
    rt: Runtime, session_id: str | None, action_count: int | None
) -> JoinedSession | None:
    if not session_id or rt.session_transport is None:
        return None
    return JoinedSession(
        rt.session_transport, rt.config.agent.agent_id, session_id, action_count or 0
    )


def _currency(code: str, issuer: str | None) -> CurrencyRef:
    return code if issuer is None else IssuedCurrency(code=code, issuer=issuer)


async def _propose(
    rt: Runtime,
    *,
    kind: str,
    build_intent: Callable[[str, str], Intent],
    why: str,
    session: JoinedSession | None = None,
    depends_on: str | None = None,
) -> JSONObject:
    async with rt.lock:
        await _recover(rt)

        if rt.state.pending is not None:
            # One proposal at a time: never touch the signer for a second one.
            return _waiting_result(rt.state.pending)

        now = rt.clock.now()
        rt.state.counter += 1
        receipt_id = _receipt_id(rt, f"{kind}-{rt.state.counter}")
        nonce = SHA256Hash.from_bytes(receipt_id.encode()).hex()[:32]

        try:
            intent = build_intent(nonce, shift_instant(now, INTENT_TTL_SECONDS, "now"))
        except (ContentError, ValueError) as exc:
            return {
                "error": (
                    f"could not build a proposal: {exc}. Amounts must be plain decimal strings "
                    "and currencies must name a code this rail understands."
                )
            }

        already = _refused_within_the_hour(rt, intent, now)
        if already is not None:
            rt.state.counter -= 1
            return {
                "outcome": "refused",
                "reason": (
                    f"the same intent was refused at {already}; not filing it again within "
                    "the hour. Wait, or change what you ask for."
                ),
            }

        rt.state.in_flight = InFlight(receipt_id=receipt_id, nonce=nonce, kind=kind, at=now)
        rt.save()

        instruction = Instruction(
            source="mandate",
            content_hash=SHA256Hash.from_bytes((why or kind).encode()).hex(),
            ref=f"tool:{kind}",
        )
        reasoning = _reasoning(why)

        try:
            with joined(session):
                outcome = await rt.builder.execute(
                    instruction=instruction,
                    intent=intent,
                    reasoning=reasoning,
                    receipt_id=receipt_id,
                    depends_on=depends_on,
                )
        except Exception as exc:  # never leaves this process unresolved
            rt.state.in_flight = None
            rt.save()
            return {"error": str(exc)}

        rt.state.in_flight = None
        return _finish(rt, outcome, kind=kind, intent=intent)


def _finish(rt: Runtime, outcome: Any, *, kind: str, intent: Intent) -> JSONObject:
    if outcome.settled:
        rt.save()
        return {
            "outcome": "settled",
            "receipt_id": outcome.envelope.receipt_id,
            "tx_hash": outcome.settlement.tx_hash,
        }
    if outcome.pending_escalation is not None:
        rt.state.pending = Pending(
            challenge=str(outcome.pending_escalation["challenge"]),
            expires_at=str(outcome.pending_escalation["expires_at"]),
            quorum=int(str(outcome.pending_escalation["quorum"])),
            receipt_id=outcome.envelope.receipt_id,
            kind=kind,
            intent=intent.to_content(),
            instruction=(
                outcome.receipt.leaves.instruction.to_content()
                if outcome.receipt.leaves.instruction
                else {}
            ),
            reasoning=(
                outcome.receipt.leaves.reasoning.to_content()
                if outcome.receipt.leaves.reasoning
                else None
            ),
            prepared_tx=outcome.prepared_tx,
        )
        rt.save()
        return _waiting_result(rt.state.pending)
    _remember_refusal(rt, intent)
    rt.save()
    return {
        "outcome": "refused",
        "reason": _reason(outcome),
        "receipt_id": outcome.envelope.receipt_id,
    }


REFILE_WINDOW = timedelta(hours=1)
SAME_INTENT_TOLERANCE = Decimal("0.01")


def _when(instant: str) -> datetime:
    return datetime.fromisoformat(instant.replace("Z", "+00:00"))


def _refused_within_the_hour(rt: Runtime, intent: Intent, now: str) -> str | None:
    """When the same intent (destination, amount within 1%) was last refused, if
    that was inside the hour. At most one refused receipt per intent per hour."""
    amount = Decimal(intent.outflow.value)
    for item in rt.state.refused:
        if item["destination"] != intent.destination:
            continue
        if _when(now) - _when(item["at"]) >= REFILE_WINDOW:
            continue
        earlier = Decimal(item["amount"])
        if abs(amount - earlier) <= earlier * SAME_INTENT_TOLERANCE:
            return item["at"]
    return None


def _remember_refusal(rt: Runtime, intent: Intent) -> None:
    now = rt.clock.now()
    kept = [
        item for item in rt.state.refused if _when(now) - _when(item["at"]) < 2 * REFILE_WINDOW
    ]
    kept.append({"destination": intent.destination, "amount": intent.outflow.value, "at": now})
    rt.state.refused = kept


def _waiting_result(pending: Pending) -> JSONObject:
    return {
        "outcome": "waiting_for_a_person",
        "challenge": pending.challenge,
        "expires_at": pending.expires_at,
    }


def _reason(outcome: Any) -> str:
    failed = [
        f"{rule.name}: {rule.detail or rule.outcome}"
        for rule in outcome.decision.rules
        if rule.outcome == "fail"
    ]
    return "; ".join(failed) or outcome.reason or outcome.outcome


def _reasoning(why: str) -> Reasoning | None:
    if not why:
        return None
    flat = " ".join(why.split())
    return Reasoning(
        content_hash=SHA256Hash.from_bytes(why.encode()).hex(),
        source="mcp_tool",
        note=flat[:NOTE_LENGTH],
    )


def _receipt_id(rt: Runtime, label: str) -> str:
    seed = f"{rt.config.agent.agent_id}:{rt.config.treasury.address}:{label}"
    return SHA256Hash.from_bytes(seed.encode()).hex()[:32]


def _code(currency: CurrencyRef) -> str:
    return currency if isinstance(currency, str) else currency.code


# --------------------------------------------------------------------------- #
# pending_approval
# --------------------------------------------------------------------------- #


async def pending_approval(rt: Runtime) -> JSONObject:
    """``none``, ``waiting`` (time left), or the resolution of an escalation."""
    async with rt.lock:
        await _recover(rt)

        pending = rt.state.pending
        if pending is None:
            return {"status": "none"}

        answer = await rt.escalations.get(pending.challenge)
        if answer is None:
            return {"status": "waiting", "time_left_seconds": _time_left(rt, pending)}

        status = str(answer.get("status", "pending"))
        if status == "pending":
            return {"status": "waiting", "time_left_seconds": _time_left(rt, pending)}

        decision = answer.get("signer_decision")
        if isinstance(decision, dict):
            return await _resume(rt, pending, decision)

        rt.state.pending = None
        rt.save()
        if status == "approved":
            return {
                "status": "refused",
                "reason": (
                    "approved by a human, but the co-signer handed the settlement material to "
                    "the notary that relayed it, not to this server, so nothing was submitted "
                    "from here. Propose it again if it is still the right move."
                ),
            }
        return {"status": "refused", "reason": f"escalated to a human and was {status}."}


async def _resume(rt: Runtime, pending: Pending, decision: JSONObject) -> JSONObject:
    receipt_id = SHA256Hash.from_bytes(f"resume:{pending.challenge}".encode()).hex()[:32]
    rt.state.in_flight = InFlight(
        receipt_id=receipt_id,
        nonce=str(pending.intent.get("nonce", "")),
        kind=f"resume-{pending.kind}",
        at=rt.clock.now(),
    )
    rt.state.pending = None
    rt.save()
    try:
        outcome = await rt.builder.resume(
            instruction=Instruction.from_content(pending.instruction),
            intent=Intent.from_content(pending.intent),
            decision=decision,
            receipt_id=receipt_id,
            reasoning=Reasoning.from_content(pending.reasoning) if pending.reasoning else None,
            prepared_tx=pending.prepared_tx,
        )
    except Exception as exc:
        rt.state.in_flight = None
        rt.save()
        return {"error": str(exc)}
    rt.state.in_flight = None
    if outcome.settled:
        rt.save()
        return {
            "status": "settled",
            "receipt_id": outcome.envelope.receipt_id,
            "tx_hash": outcome.settlement.tx_hash,
        }
    rt.save()
    return {
        "status": "refused",
        "reason": _reason(outcome),
        "receipt_id": outcome.envelope.receipt_id,
    }


def _time_left(rt: Runtime, pending: Pending) -> float:
    expires = datetime.fromisoformat(pending.expires_at.replace("Z", "+00:00")).astimezone(UTC)
    now = datetime.fromisoformat(rt.clock.now().replace("Z", "+00:00")).astimezone(UTC)
    return max(0.0, (expires - now).total_seconds())


# --------------------------------------------------------------------------- #
# recovery — shared by every tool that touches state
# --------------------------------------------------------------------------- #


async def _recover(rt: Runtime) -> None:
    """Look up an action started before the process died. Never re-propose."""
    in_flight = rt.state.in_flight
    if in_flight is None:
        return
    await rt.store.get(
        in_flight.receipt_id
    )  # only to settle whether it happened; nonce is spent either way
    rt.state.in_flight = None
    rt.save()


__all__ = [
    "get_treasury",
    "pending_approval",
    "propose_payment",
    "propose_swap",
    "read_receipts",
    "verify_receipt",
]
