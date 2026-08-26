from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar, Self

from openhands.sdk.tool import Action, Observation, ToolDefinition, ToolExecutor
from openhands.sdk.tool import list_registered_tools
from pydantic import Field

from focusproof.domain.plugins.base import EvidencePluginProvider
from focusproof.openhands_runtime.capabilities import (
    VerificationCapability,
    VerificationCapabilityRegistry,
    build_builtin_capabilities,
)
from focusproof.openhands_runtime.tool_assembler import SessionToolAssembler


class PluginAction(Action):
    value: str = Field(description="Value to echo.")


class PluginObservation(Observation):
    value: str


class PluginExecutor(ToolExecutor[PluginAction, PluginObservation]):
    def __call__(self, action: PluginAction, conversation: Any | None = None) -> PluginObservation:
        del conversation
        return PluginObservation.from_text(action.value, value=action.value)


class FocusProofBoundaryTestTool(ToolDefinition[PluginAction, PluginObservation]):
    name: ClassVar[str] = "focusproof_boundary_test"

    @classmethod
    def create(cls, conv_state: Any | None = None, **_: object) -> Sequence[Self]:
        del conv_state
        return [
            cls(
                description="Exercise the plugin boundary.",
                action_type=PluginAction,
                observation_type=PluginObservation,
                executor=PluginExecutor(),
            )
        ]


class BoundaryProvider:
    plugin_id = "boundary-test"

    def tool_definitions(
        self,
    ) -> Mapping[str, type[ToolDefinition[Any, Any]]]:
        return {"FocusProofBoundaryTestTool": FocusProofBoundaryTestTool}

    def capability_definitions(self) -> Sequence[VerificationCapability]:
        return (
            VerificationCapability(
                registry_name="boundary-test",
                tool_class_name="FocusProofBoundaryTestTool",
                supported_evidence_types=frozenset({"boundary-test"}),
                supported_domains=frozenset({"boundary-test"}),
                priority=100,
                read_only=True,
                requires_network=False,
                timeout_seconds=1.0,
                enabled=True,
                version="1",
            ),
        )


def _assembler(*providers: EvidencePluginProvider) -> SessionToolAssembler:
    return SessionToolAssembler(
        VerificationCapabilityRegistry(build_builtin_capabilities()),
        plugin_providers=providers,
    )


def test_provider_protocol_has_no_parallel_executor_registry() -> None:
    assert "executors" not in EvidencePluginProvider.__annotations__
    assert not hasattr(EvidencePluginProvider, "executors")


def test_empty_provider_sequence_preserves_baseline_tools_and_version() -> None:
    baseline = SessionToolAssembler(VerificationCapabilityRegistry(build_builtin_capabilities()))
    empty = _assembler()

    assert [
        tool.name for tool in empty.assemble("sess_1", "general", None, compatibility_mode=True)
    ] == [
        tool.name for tool in baseline.assemble("sess_1", "general", None, compatibility_mode=True)
    ]
    assert empty.version("general", None) == baseline.version("general", None)


def test_enabled_provider_registers_official_tool_and_selects_capability() -> None:
    assembler = _assembler(BoundaryProvider())
    tools = assembler.assemble(
        "sess_1",
        "boundary-test",
        {"boundary-test"},
        compatibility_mode=True,
    )

    assert "FocusProofBoundaryTestTool" in list_registered_tools()
    assert [tool.name for tool in tools][-1] == "FocusProofBoundaryTestTool"
    definition = FocusProofBoundaryTestTool.create()[0]
    assert isinstance(definition.executor, PluginExecutor)
    assert definition.executor(PluginAction(value="bound")).value == "bound"
