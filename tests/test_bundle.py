from __future__ import annotations

from pathlib import Path

import pytest

from merkl_mcp import bundle

TOML = """
[agent]
agent_id = "agent-accounts-payable"
key_file = "agent-ed25519.pem"

[treasury]
address = "rTREASURY0000000000000000000000000"
policy_version = "2026.01.0"
wallet_file = "wallet.json"
wallet_name = "agent-accounts-payable"

[rail]
name = "xrpl"
json_rpc_url = "https://s.altnet.rippletest.net:51234"

[signer]
url = "http://127.0.0.1:8787"

[notary]
url = "https://api.merkl.ai"
api_key_env = "MERKL_API_KEY"
"""


def _bundle_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "agent"
    directory.mkdir()
    (directory / "trader.toml").write_text(TOML)
    (directory / "agent-ed25519.pem").write_text("not a real key\n")
    (directory / "wallet.json").write_text("{}\n")
    return directory


def test_loads_the_bundle_and_resolves_relative_paths(tmp_path: Path) -> None:
    directory = _bundle_dir(tmp_path)
    config = bundle.load(directory)

    assert config.agent.agent_id == "agent-accounts-payable"
    assert config.agent.key_file == directory / "agent-ed25519.pem"
    assert config.treasury.wallet_file == directory / "wallet.json"
    assert config.treasury.address == "rTREASURY0000000000000000000000000"
    assert config.rail.json_rpc_url == "https://s.altnet.rippletest.net:51234"
    assert config.notary.api_key_env == "MERKL_API_KEY"


def test_no_market_model_loop_or_bill_table_is_required(tmp_path: Path) -> None:
    """The whole point of this bundle reader: those four tables never come up."""
    _bundle_dir(tmp_path)  # the fixture TOML above has none of them, and still loads


def test_missing_table_is_a_config_error(tmp_path: Path) -> None:
    directory = tmp_path / "agent"
    directory.mkdir()
    (directory / "trader.toml").write_text('[agent]\nagent_id = "a"\nkey_file = "k.pem"\n')
    with pytest.raises(bundle.ConfigError, match=r"\[treasury\]"):
        bundle.load(directory)


def test_notary_needs_one_of_api_key_env_or_api_key_file(tmp_path: Path) -> None:
    directory = _bundle_dir(tmp_path)
    text = (directory / "trader.toml").read_text().replace('api_key_env = "MERKL_API_KEY"', "")
    (directory / "trader.toml").write_text(text)
    with pytest.raises(bundle.ConfigError, match="api_key_file.*api_key_env"):
        bundle.load(directory)


def test_market_table_is_optional_and_read_when_present(tmp_path: Path) -> None:
    assert bundle.load(_bundle_dir(tmp_path)).market is None

    config = bundle.parse(
        {
            **__import__("tomllib").loads(TOML),
            "market": {"base": "XRP", "quote_code": "RLUSD", "quote_issuer": "rISSUER"},
        },
        base_dir=tmp_path,
    )

    assert config.market == bundle.MarketConfig("XRP", "RLUSD", "rISSUER")


def test_receipts_live_under_the_bundle_directory(tmp_path: Path, monkeypatch) -> None:
    from merkl_mcp.runtime import receipt_store_for

    monkeypatch.delenv("MERKL_RECEIPT_DIR", raising=False)
    config = bundle.load(_bundle_dir(tmp_path))

    assert config.receipts_dir == tmp_path / "agent" / "receipts"
    assert receipt_store_for(config).directory == tmp_path / "agent" / "receipts"

    monkeypatch.setenv("MERKL_RECEIPT_DIR", str(tmp_path / "elsewhere"))
    assert receipt_store_for(config).directory == tmp_path / "elsewhere"
