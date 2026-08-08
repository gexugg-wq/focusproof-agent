from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
import re
from urllib.parse import urlsplit


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"", "0", "false", "no", "off"})
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


@dataclass(frozen=True, slots=True)
class MonadPluginSettings:
    enabled: bool
    rpc_url: str | None = None
    chain_id: int | None = None
    contract_address: str | None = None
    deployment_block: int | None = None
    explorer_tx_base_url: str | None = None

    @classmethod
    def from_environ(
        cls,
        environ: Mapping[str, str],
    ) -> MonadPluginSettings:
        raw_enabled = environ.get("FOCUSPROOF_PLUGIN_MONAD_ENABLED", "")
        normalized_enabled = raw_enabled.strip().lower()
        if normalized_enabled in _FALSE_VALUES:
            return cls(enabled=False)
        if normalized_enabled not in _TRUE_VALUES:
            raise ValueError("Monad plugin enabled flag must be a boolean")

        rpc_url = _required(environ, "FOCUSPROOF_MONAD_RPC_URL", "RPC URL")
        _validate_url(rpc_url, "RPC URL", allow_credentials=True)
        chain_id = _positive_integer(environ, "FOCUSPROOF_MONAD_CHAIN_ID", "chain ID")
        contract_address = _required(
            environ,
            "FOCUSPROOF_MONAD_CONTRACT_ADDRESS",
            "contract address",
        )
        address_body = contract_address[2:]
        if not _ADDRESS_RE.fullmatch(contract_address) or not (
            address_body.islower() or address_body.isupper()
        ):
            raise ValueError("Monad contract address must be checksummed")
        deployment_block = _non_negative_integer(
            environ,
            "FOCUSPROOF_MONAD_DEPLOYMENT_BLOCK",
            "deployment block",
        )
        explorer_url = _required(
            environ,
            "FOCUSPROOF_MONAD_EXPLORER_TX_BASE_URL",
            "explorer transaction base URL",
        )
        _validate_url(
            explorer_url,
            "explorer transaction base URL",
            allow_credentials=False,
        )
        return cls(
            enabled=True,
            rpc_url=rpc_url,
            chain_id=chain_id,
            contract_address=contract_address,
            deployment_block=deployment_block,
            explorer_tx_base_url=explorer_url,
        )


def _required(environ: Mapping[str, str], name: str, label: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Monad {label} is required")
    return value


def _positive_integer(environ: Mapping[str, str], name: str, label: str) -> int:
    value = _integer(environ, name, label)
    if value <= 0:
        raise ValueError(f"Monad {label} must be positive")
    return value


def _non_negative_integer(environ: Mapping[str, str], name: str, label: str) -> int:
    value = _integer(environ, name, label)
    if value < 0:
        raise ValueError(f"Monad {label} must not be negative")
    return value


def _integer(environ: Mapping[str, str], name: str, label: str) -> int:
    raw = _required(environ, name, label)
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Monad {label} must be an integer") from exc


def _validate_url(url: str, label: str, *, allow_credentials: bool) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"Monad {label} must be an absolute HTTPS URL")
    if not allow_credentials and (parsed.username is not None or parsed.password is not None):
        raise ValueError(f"Monad {label} must not contain credentials")
