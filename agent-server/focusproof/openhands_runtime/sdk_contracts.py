from __future__ import annotations

import inspect
from collections.abc import Callable

from openhands.sdk import Agent, Conversation
from openhands.sdk.conversation import LocalConversation
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.sdk.tool import ToolExecutor


class OpenHandsContractUnavailable(RuntimeError):
    pass


def _require_parameters(target: Callable[..., object], required: frozenset[str]) -> None:
    if not required.issubset(inspect.signature(target).parameters):
        raise OpenHandsContractUnavailable("runtime_contract_unavailable")


def preflight_openhands_sdk_contract() -> None:
    """Check public SDK surfaces without constructing an LLM or running an Agent."""
    try:
        _require_parameters(Agent.step, frozenset({"self", "conversation", "on_event"}))
        if not callable(Conversation):
            raise OpenHandsContractUnavailable("runtime_contract_unavailable")
        _require_parameters(LocalConversation.arun, frozenset({"self"}))
        if not inspect.isclass(ToolExecutor):
            raise OpenHandsContractUnavailable("runtime_contract_unavailable")
        _require_parameters(
            ActionEvent,
            frozenset(
                {"thought", "action", "tool_name", "tool_call_id", "tool_call", "llm_response_id"}
            ),
        )
        _require_parameters(
            ObservationEvent,
            frozenset({"tool_name", "tool_call_id", "observation", "action_id"}),
        )
    except OpenHandsContractUnavailable:
        raise
    except (AttributeError, TypeError, ValueError):
        raise OpenHandsContractUnavailable("runtime_contract_unavailable") from None
