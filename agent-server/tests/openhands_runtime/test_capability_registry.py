from dataclasses import replace

import pytest

from focusproof.openhands_runtime.capabilities import (
    VerificationCapability,
    VerificationCapabilityRegistry,
)


def capability(
    name: str = "text",
    *,
    evidence_types: frozenset[str] = frozenset({"text"}),
    domains: frozenset[str] = frozenset({"*"}),
    priority: int = 100,
    enabled: bool = True,
) -> VerificationCapability:
    return VerificationCapability(
        registry_name=name,
        tool_class_name=f"{name.title()}Tool",
        supported_evidence_types=evidence_types,
        supported_domains=domains,
        priority=priority,
        read_only=True,
        requires_network=False,
        timeout_seconds=5.0,
        enabled=enabled,
        version="1",
    )


def test_registry_rejects_conflicting_duplicate_name() -> None:
    registry = VerificationCapabilityRegistry([capability()])
    with pytest.raises(ValueError, match="text"):
        registry.register(replace(capability(), tool_class_name="OtherTool"))


def test_registry_selection_is_filtered_and_stable() -> None:
    registry = VerificationCapabilityRegistry(
        [
            capability("url", evidence_types=frozenset({"url"}), priority=20),
            capability("text", priority=10),
            capability("disabled", priority=1, enabled=False),
            capability("web3", domains=frozenset({"web3"}), priority=5),
        ]
    )
    selected = registry.select("general", {"text", "url"})
    assert [item.registry_name for item in selected] == ["text", "url"]


def test_idempotent_registration_returns_existing_value() -> None:
    item = capability()
    registry = VerificationCapabilityRegistry([item])
    assert registry.register(item) is item


def test_registry_exposes_capability_timeout_by_normalized_name() -> None:
    item = replace(capability("url"), timeout_seconds=2.5)
    registry = VerificationCapabilityRegistry([item])

    assert registry.get(" URL ") is item
    assert registry.get("missing") is None


def test_capability_metadata_is_normalized_at_the_model_boundary() -> None:
    item = capability(
        " Text ",
        evidence_types=frozenset({" Text "}),
        domains=frozenset({" GENERAL "}),
    )
    registry = VerificationCapabilityRegistry([item])
    assert item.registry_name == "text"
    assert item.supported_evidence_types == frozenset({"text"})
    assert item.supported_domains == frozenset({"general"})
    assert registry.select("general", {"text"}) == (item,)
