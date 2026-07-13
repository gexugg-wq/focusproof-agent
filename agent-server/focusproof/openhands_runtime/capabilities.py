from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from threading import RLock


@dataclass(frozen=True, slots=True)
class VerificationCapability:
    registry_name: str
    tool_class_name: str
    supported_evidence_types: frozenset[str]
    supported_domains: frozenset[str]
    priority: int
    read_only: bool
    requires_network: bool
    timeout_seconds: float
    enabled: bool
    version: str

    def __post_init__(self) -> None:
        if not self.registry_name.strip() or not self.tool_class_name.strip():
            raise ValueError("capability names must not be empty")
        if not self.supported_evidence_types:
            raise ValueError("supported_evidence_types must not be empty")
        if not self.supported_domains:
            raise ValueError("supported_domains must not be empty")
        if not self.read_only:
            raise ValueError("AI4A verification capabilities must be read-only")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


class VerificationCapabilityRegistry:
    def __init__(self, capabilities: Iterable[VerificationCapability] = ()) -> None:
        self._lock = RLock()
        self._items: dict[str, VerificationCapability] = {}
        for item in capabilities:
            self.register(item)

    def register(self, item: VerificationCapability) -> VerificationCapability:
        with self._lock:
            existing = self._items.get(item.registry_name)
            if existing is not None and existing != item:
                raise ValueError(f"conflicting capability: {item.registry_name}")
            if existing is None:
                self._items[item.registry_name] = item
                return item
            return existing

    def select(
        self,
        domain: str,
        evidence_types: Collection[str] | None,
    ) -> tuple[VerificationCapability, ...]:
        normalized_domain = domain.strip().lower()
        normalized_types = (
            {value.strip().lower() for value in evidence_types}
            if evidence_types is not None
            else None
        )
        with self._lock:
            items = tuple(self._items.values())
        selected = (
            item
            for item in items
            if item.enabled
            and ("*" in item.supported_domains or normalized_domain in item.supported_domains)
            and (
                normalized_types is None
                or bool(item.supported_evidence_types & normalized_types)
            )
        )
        return tuple(sorted(selected, key=lambda item: (item.priority, item.registry_name)))

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


def build_builtin_capabilities() -> tuple[VerificationCapability, ...]:
    return (
        VerificationCapability(
            registry_name="text",
            tool_class_name="FocusProofTextEvidenceVerificationTool",
            supported_evidence_types=frozenset({"text"}),
            supported_domains=frozenset({"*"}),
            priority=10,
            read_only=True,
            requires_network=False,
            timeout_seconds=5.0,
            enabled=True,
            version="1",
        ),
        VerificationCapability(
            registry_name="url",
            tool_class_name="FocusProofUrlEvidenceVerificationTool",
            supported_evidence_types=frozenset({"url"}),
            supported_domains=frozenset({"*"}),
            priority=20,
            read_only=True,
            requires_network=True,
            timeout_seconds=15.0,
            enabled=True,
            version="1",
        ),
    )
