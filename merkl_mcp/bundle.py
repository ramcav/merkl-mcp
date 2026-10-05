"""What this process reads before it can answer a single tool call.

The five-file agent bundle merkl-sdk's ``merkl treasury init`` writes
(``trader.toml``, ``agent-ed25519.pem``, ``wallet.json``, and the two optional
relay/notary secret files — see ``merkl.cli.bundle``), plus four tables out of
``trader.toml`` itself: ``[treasury]``, ``[rail]``, ``[signer]``, ``[notary]``, and the optional
``[market]`` pair (named back to the agent, never read).

This is deliberately a *subset* of what ``merkl_trader.config`` reads. There is
no ``[model]``, ``[loop]`` or ``[bill]`` table here, because this
process has no trading loop, no model and no compute bill of its own — every
"which asset, which size, why" question arrives as a tool argument instead of
a standing config, and whatever calls this server owns the loop and the bill.

``[agent]`` is read too, for ``agent_id`` and ``key_file`` — the request key
that authenticates every ``propose()`` call and the identity every receipt
names. The shared contract only names the four tables above, but a signer call
with no agent id and no key to sign with cannot be made; reading `[agent]`
is the one addition this module makes to the letter of that contract, and
``agent.mandate`` (a standing instruction this process has none of) is never
read.
"""

from __future__ import annotations

import dataclasses
import os
import tomllib
from pathlib import Path
from typing import Any

MERKL_AGENT_DIR_ENV = "MERKL_AGENT_DIR"
DEFAULT_AGENT_DIR = "/agent"
TRADER_CONFIG_NAME = "trader.toml"


class ConfigError(Exception):
    """The bundle is missing something, or ``trader.toml`` says something impossible."""


@dataclasses.dataclass(frozen=True)
class AgentConfig:
    agent_id: str
    key_file: Path


@dataclasses.dataclass(frozen=True)
class TreasuryConfig:
    address: str
    policy_version: str
    wallet_file: Path
    wallet_name: str


@dataclasses.dataclass(frozen=True)
class RailConfig:
    name: str
    json_rpc_url: str
    websocket_url: str | None


@dataclasses.dataclass(frozen=True)
class SignerConfig:
    url: str
    token_file: Path | None


@dataclasses.dataclass(frozen=True)
class NotaryConfig:
    url: str
    api_key_env: str | None = None
    api_key_file: Path | None = None

    def api_key(self) -> str:
        """The key itself. Never logged, never echoed."""
        if self.api_key_file is not None:
            return read_secret_file(self.api_key_file)
        if self.api_key_env:
            return read_secret_env(self.api_key_env)
        raise ConfigError("notary needs either api_key_file or api_key_env")


@dataclasses.dataclass(frozen=True)
class MarketConfig:
    """The pair this agent trades, from the optional ``[market]`` table. Only
    named back to the agent (``get_treasury``); this server never reads a book."""

    base: str
    quote_code: str
    quote_issuer: str


@dataclasses.dataclass(frozen=True)
class BillConfig:
    """The optional ``[bill]`` table: who is owed for compute and on which day."""

    operator: str
    bill_day: str


TRADER_HOME_ENV = "MERKL_TRADER_HOME"


@dataclasses.dataclass(frozen=True)
class Config:
    agent_dir: Path
    agent: AgentConfig
    treasury: TreasuryConfig
    rail: RailConfig
    signer: SignerConfig
    notary: NotaryConfig
    market: MarketConfig | None = None
    bill: BillConfig | None = None
    trader_home: Path | None = None
    """Where the harness writes ``journal.jsonl``: ``$MERKL_TRADER_HOME`` or ``[loop].home``."""

    @property
    def receipts_dir(self) -> Path:
        """The agent's receipt store: ``receipts/`` under the bundle directory."""
        return self.agent_dir / "receipts"


# -- reading ------------------------------------------------------------- #


def agent_dir() -> Path:
    """``$MERKL_AGENT_DIR``, or ``/agent`` — the bundle's own default mount."""
    override = os.environ.get(MERKL_AGENT_DIR_ENV, "").strip()
    return Path(override).expanduser() if override else Path(DEFAULT_AGENT_DIR)


def load(directory: Path | None = None) -> Config:
    """Read ``trader.toml`` out of the bundle directory and validate it."""
    base = (directory or agent_dir()).expanduser()
    path = base / TRADER_CONFIG_NAME
    try:
        raw = tomllib.loads(path.read_text())
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc
    return parse(raw, base_dir=base)


def parse(raw: dict[str, Any], *, base_dir: Path) -> Config:
    """Build a :class:`Config` from an already-parsed ``trader.toml``.

    Every relative path resolves against ``base_dir`` — the bundle directory
    — never the process's working directory, the same reasoning
    ``merkl_trader.config`` gives for the identical rule: the bundle is a
    folder that describes itself from the inside, and a container started
    with ``--agent-dir /agent`` has no reason to share a working directory
    with it.
    """
    agent = _table(raw, "agent")
    treasury = _table(raw, "treasury")
    rail = _table(raw, "rail")
    signer = _table(raw, "signer")
    notary = _table(raw, "notary")

    return Config(
        agent_dir=base_dir,
        agent=AgentConfig(
            agent_id=_string(agent, "agent_id", "agent"),
            key_file=_path(agent, "key_file", "agent", base_dir),
        ),
        treasury=TreasuryConfig(
            address=_string(treasury, "address", "treasury"),
            policy_version=_string(treasury, "policy_version", "treasury"),
            wallet_file=_path(treasury, "wallet_file", "treasury", base_dir),
            wallet_name=_string(treasury, "wallet_name", "treasury"),
        ),
        rail=RailConfig(
            name=_optional_string(rail, "name") or "xrpl",
            json_rpc_url=_string(rail, "json_rpc_url", "rail"),
            websocket_url=_optional_string(rail, "websocket_url"),
        ),
        signer=SignerConfig(
            url=_string(signer, "url", "signer"),
            token_file=_optional_path(signer, "token_file", base_dir),
        ),
        notary=_notary(notary, base_dir),
        market=_market(raw.get("market")),
        bill=_bill(raw.get("bill")),
        trader_home=_trader_home(raw.get("loop"), base_dir),
    )


def _bill(table: Any) -> BillConfig | None:
    if not isinstance(table, dict):
        return None
    return BillConfig(
        operator=_string(table, "operator", "bill"),
        bill_day=_string(table, "bill_day", "bill").lower(),
    )


def _trader_home(table: Any, base_dir: Path) -> Path | None:
    override = os.environ.get(TRADER_HOME_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    if isinstance(table, dict) and table.get("home"):
        return _path(table, "home", "loop", base_dir)
    return None


def _market(table: Any) -> MarketConfig | None:
    if not isinstance(table, dict):
        return None
    return MarketConfig(
        base=_string(table, "base", "market"),
        quote_code=_string(table, "quote_code", "market"),
        quote_issuer=_string(table, "quote_issuer", "market"),
    )


def _notary(table: dict[str, Any], base_dir: Path) -> NotaryConfig:
    api_key_file = _optional_path(table, "api_key_file", base_dir)
    api_key_env = _optional_string(table, "api_key_env")
    if api_key_file is None and api_key_env is None:
        raise ConfigError(
            "notary needs api_key_file (a 0600 file, what `merkl treasury init` writes) "
            "or api_key_env (the name of an environment variable)"
        )
    return NotaryConfig(
        url=_string(table, "url", "notary"), api_key_env=api_key_env, api_key_file=api_key_file
    )


# -- secrets --------------------------------------------------------------- #


def read_secret_file(path: Path) -> str:
    """One line out of a ``0600`` file. Never logged, never echoed."""
    try:
        return path.expanduser().read_text().strip()
    except OSError as exc:
        raise ConfigError(f"cannot read the secret at {path}: {exc}") from exc


def read_secret_env(name: str) -> str:
    """The value of an environment variable, by name. Never logged, never echoed."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"${name} is not set")
    return value


# -- primitives -------------------------------------------------------------- #


def _table(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name)
    if not isinstance(value, dict):
        raise ConfigError(f"{TRADER_CONFIG_NAME} needs a [{name}] table")
    return value


def _string(table: dict[str, Any], key: str, owner: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{owner}.{key} must be a non-empty string")
    return value


def _optional_string(table: dict[str, Any], key: str) -> str | None:
    value = table.get(key)
    return value if isinstance(value, str) and value else None


def _path(table: dict[str, Any], key: str, owner: str, base_dir: Path) -> Path:
    return _resolve(Path(_string(table, key, owner)), base_dir)


def _optional_path(table: dict[str, Any], key: str, base_dir: Path) -> Path | None:
    value = _optional_string(table, key)
    return None if value is None else _resolve(Path(value), base_dir)


def _resolve(path: Path, base_dir: Path) -> Path:
    expanded = path.expanduser()
    return expanded if expanded.is_absolute() else base_dir / expanded


__all__ = [
    "DEFAULT_AGENT_DIR",
    "MERKL_AGENT_DIR_ENV",
    "AgentConfig",
    "Config",
    "ConfigError",
    "NotaryConfig",
    "RailConfig",
    "SignerConfig",
    "TreasuryConfig",
    "agent_dir",
    "load",
    "parse",
    "read_secret_env",
    "read_secret_file",
]
