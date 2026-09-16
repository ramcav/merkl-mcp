"""What survives this process being killed: a proposal a person is deciding
on, and the pointer that stops a crash mid-``propose()`` from ever looking
like nothing happened.

Two records, same shape ``merkl_trader.ledger`` uses for the identical
problem, because it is the identical problem: an MCP server is just as
restartable as the trading loop, and the fix is the same — write the nonce
down *before* the call leaves this process.

``InFlight`` covers the seconds between "we reserved a nonce" and "the signer
answered"; on the next call, :func:`merkl_mcp.tools.recover` looks the receipt
up in the local store rather than re-proposing, because the nonce is spent
either way.

``Pending`` covers the much longer window an escalation opens: a person has to
read the proposal and decide, which can outlive this process many times over.
It carries ``prepared_tx`` for the same reason ``merkl_trader`` keeps one —
preparing the transaction again, later, asks the ledger for a fresh sequence
and fee, producing bytes the policy key never signed. It is also what makes
the tool server's one-proposal-at-a-time rule enforceable across a restart:
a second ``propose_*`` call finds this record on disk and never touches the
signer.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

JSONObject = dict[str, Any]

MERKL_MCP_STATE_ENV = "MERKL_MCP_STATE"
DEFAULT_STATE_DIR = "/var/lib/merkl-mcp"
STATE_FILE_NAME = "state.json"


def state_dir() -> Path:
    """``$MERKL_MCP_STATE``, or ``/var/lib/merkl-mcp`` — the writable volume."""
    import os

    override = os.environ.get(MERKL_MCP_STATE_ENV, "").strip()
    return Path(override).expanduser() if override else Path(DEFAULT_STATE_DIR)


def state_path(directory: Path | None = None) -> Path:
    return (directory or state_dir()) / STATE_FILE_NAME


@dataclasses.dataclass
class InFlight:
    """A propose/resume call that left this process and has not come back."""

    receipt_id: str
    nonce: str
    kind: str
    at: str

    def to_content(self) -> JSONObject:
        return {
            "receipt_id": self.receipt_id,
            "nonce": self.nonce,
            "kind": self.kind,
            "at": self.at,
        }

    @classmethod
    def from_content(cls, data: dict[str, Any]) -> InFlight:
        return cls(
            receipt_id=str(data["receipt_id"]),
            nonce=str(data["nonce"]),
            kind=str(data.get("kind", "payment")),
            at=str(data.get("at", "")),
        )


@dataclasses.dataclass
class Pending:
    """An escalation the signer opened, and everything needed to finish it."""

    challenge: str
    expires_at: str
    quorum: int
    receipt_id: str
    kind: str
    """``"payment"`` or ``"swap"`` — which tool opened this escalation."""
    intent: JSONObject
    instruction: JSONObject
    reasoning: JSONObject | None
    prepared_tx: JSONObject | None

    def to_content(self) -> JSONObject:
        return {
            "challenge": self.challenge,
            "expires_at": self.expires_at,
            "quorum": self.quorum,
            "receipt_id": self.receipt_id,
            "kind": self.kind,
            "intent": self.intent,
            "instruction": self.instruction,
            "reasoning": self.reasoning,
            "prepared_tx": self.prepared_tx,
        }

    @classmethod
    def from_content(cls, data: dict[str, Any]) -> Pending:
        return cls(
            challenge=str(data["challenge"]),
            expires_at=str(data["expires_at"]),
            quorum=int(data["quorum"]),
            receipt_id=str(data["receipt_id"]),
            kind=str(data.get("kind", "payment")),
            intent=dict(data["intent"]),
            instruction=dict(data["instruction"]),
            reasoning=dict(data["reasoning"]) if data.get("reasoning") else None,
            prepared_tx=dict(data["prepared_tx"]) if data.get("prepared_tx") else None,
        )


@dataclasses.dataclass
class McpState:
    """The whole of what this process remembers between calls."""

    counter: int = 0
    """Ticks once per accepted ``propose_*`` attempt — this server's analogue
    of the trading loop's cycle number, and the seed for each receipt id."""

    pending: Pending | None = None
    in_flight: InFlight | None = None

    def to_content(self) -> JSONObject:
        return {
            "counter": self.counter,
            "pending": self.pending.to_content() if self.pending else None,
            "in_flight": self.in_flight.to_content() if self.in_flight else None,
        }

    @classmethod
    def from_content(cls, data: dict[str, Any]) -> McpState:
        return cls(
            counter=int(data.get("counter", 0)),
            pending=Pending.from_content(data["pending"]) if data.get("pending") else None,
            in_flight=InFlight.from_content(data["in_flight"]) if data.get("in_flight") else None,
        )

    @classmethod
    def load(cls, path: Path) -> McpState:
        """Read the file, or start fresh. A corrupt file is an error, not a reset."""
        if not path.exists():
            return cls()
        return cls.from_content(json.loads(path.read_text()))

    def save(self, path: Path) -> None:
        """Write atomically: a half-written state file is worse than none."""
        path.parent.mkdir(parents=True, exist_ok=True)
        scratch = path.with_suffix(path.suffix + ".tmp")
        scratch.write_text(json.dumps(self.to_content(), indent=2) + "\n")
        scratch.replace(path)


__all__ = [
    "DEFAULT_STATE_DIR",
    "MERKL_MCP_STATE_ENV",
    "InFlight",
    "McpState",
    "Pending",
    "state_dir",
    "state_path",
]
