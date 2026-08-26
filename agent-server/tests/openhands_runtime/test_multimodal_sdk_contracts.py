from __future__ import annotations

import asyncio
import copy
import importlib.metadata as metadata
import inspect
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Protocol, cast
from uuid import UUID

import pytest
from openhands.sdk import Agent, Conversation, LLM
from openhands.sdk.conversation import LocalConversation
from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import ImageContent, Message, TextContent
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import ToolDefinition, register_tool
from packaging.requirements import Requirement
from packaging.version import Version
from focusproof.openhands_runtime.factory import ConversationFactory
from focusproof.runtime.evidence import Evidence, LearningGoal


class _EvidenceRepository(Protocol):
    def get_evidence(self, session_id: str, evidence_id: str) -> "Evidence": ...


class _EmptyRepository:
    def get_evidence(self, session_id: str, evidence_id: str) -> "Evidence":
        raise KeyError((session_id, evidence_id))


class _ConversationConstructor(Protocol):
    def __call__(
        self,
        *,
        agent: Agent,
        workspace: Path,
        persistence_dir: Path,
        conversation_id: UUID,
        callbacks: object,
        max_iteration_per_run: int,
        visualizer: object,
        delete_on_close: bool,
        tags: dict[str, str],
        user_id: str | None,
    ) -> LocalConversation: ...


class SupportsLearnerMessage(Protocol):
    def send_message(self, message: str | Message, sender: str | None = None) -> object: ...


def _learning_goal() -> LearningGoal:
    return LearningGoal(domain="general", title="SDK contract", goal="Verify SDK behavior")


def _factory(tmp_path: Path) -> ConversationFactory:
    return ConversationFactory(
        project_root=tmp_path,
        repository=_EmptyRepository(),
        compatibility_mode=True,
        llm_factory=lambda _session_id: TestLLM.from_messages([]),
    )


def _message_text(message: Message) -> str:
    text_parts: list[str] = []
    for item in message.content:
        if isinstance(item, TextContent):
            text_parts.append(item.text)
    return "\n".join(text_parts)


def _isolated(code: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    env["LITELLM_LOG"] = "ERROR"
    env["OPENHANDS_SUPPRESS_BANNER"] = "1"
    env["PYTHONPATH"] = str(Path(__file__).parents[2])
    for name in tuple(env):
        if name.upper().endswith("_API_KEY"):
            env.pop(name, None)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        env.pop(name, None)
        env.pop(name.lower(), None)
    return subprocess.run(
        [sys.executable, "-c", code],
        text=True,
        capture_output=True,
        check=False,
        env=env,
        timeout=20,
    )


def test_testllm_public_completion_and_acompletion_contract() -> None:
    sync = inspect.signature(LLM.completion)
    async_ = inspect.signature(LLM.acompletion)
    assert list(sync.parameters)[:2] == ["self", "messages"]
    assert list(async_.parameters)[:2] == ["self", "messages"]
    assert inspect.iscoroutinefunction(LLM.acompletion)

    response = Message(role="assistant", content=[TextContent(text="official text")])
    llm = TestLLM.from_messages([response, response], usage_id="sdk-contract")
    prompt = [Message(role="user", content=[TextContent(text="hello")])]

    sync_result = llm.completion(prompt)
    assert _message_text(sync_result.message) == "official text"
    assert sync_result.message.role == "assistant"
    assert llm.call_count == 1
    assert llm.remaining_responses == 1

    async_result = asyncio.run(llm.acompletion(prompt))
    assert _message_text(async_result.message) == "official text"
    assert async_result.message.role == "assistant"
    assert llm.call_count == 2
    assert llm.remaining_responses == 0


def test_message_content_deep_copy_preserves_public_contract() -> None:
    original = Message(
        role="user",
        content=[TextContent(text="explain"), ImageContent(image_urls=["focusproof-artifact://a"])],
    )
    copied = copy.deepcopy(original)
    assert isinstance(copied.content[0], TextContent)
    assert isinstance(copied.content[1], ImageContent)
    copied.content[0].text = "changed"
    copied.content[1].image_urls[0] = "data:image/png;base64,AA=="
    assert isinstance(original.content[0], TextContent)
    assert isinstance(original.content[1], ImageContent)
    assert original.content[0].text == "explain"
    assert original.content[1].image_urls == ["focusproof-artifact://a"]


def test_local_conversation_sends_string_and_message_to_event_log(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    goal = _learning_goal()
    try:
        handle = factory.create("multimodal_send", goal)
        try:
            conversation = cast(SupportsLearnerMessage, handle.conversation)
            conversation.send_message("plain")
            structured = Message(role="user", content=[TextContent(text="structured")])
            conversation.send_message(structured)
            events = [event for event in handle.conversation.state.events if isinstance(event, MessageEvent)]
            assert isinstance(events[-2].llm_message.content[0], TextContent)
            assert events[-2].llm_message.content[0].text == "plain"
            assert events[-1].llm_message == structured
        finally:
            handle.conversation.close()
    finally:
        from focusproof.openhands_runtime.tool_registry import release_repository_provider

        release_repository_provider()


def test_public_sdk_symbols_remain_available() -> None:
    assert {
        "description",
        "action_type",
        "observation_type",
        "executor",
    } <= ToolDefinition.model_fields.keys()
    assert callable(register_tool)
    assert LocalConversation is not None
    assert Conversation is not None
    assert Agent is not None
    assert TextContent.model_fields.keys() >= {"text"}
    assert ImageContent.model_fields.keys() >= {"image_urls"}

    original = Message(role="user", content=[TextContent(text="roundtrip")])
    assert Message.model_validate(original.model_dump()) == original

    response = Message(role="assistant", content=[TextContent(text="ok")])
    llm = TestLLM.from_messages([response], usage_id="metrics-contract")
    metrics = llm.metrics
    metrics.add_token_usage(3, 2, 0, 0, 16_384, "response-1")
    metrics.add_cost(0.004)
    metrics.add_response_latency(0.25, "response-1")
    usage = metrics.accumulated_token_usage
    assert usage is not None
    assert usage.prompt_tokens == 3
    assert usage.completion_tokens == 2
    assert usage.per_turn_token == 5
    assert metrics.accumulated_cost == pytest.approx(0.004)
    assert metrics.response_latencies[0].latency == pytest.approx(0.25)


def test_focusproof_factory_preserves_bound_agent_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from focusproof.openhands_runtime import factory as factory_module
    factory = _factory(tmp_path)
    goal = _learning_goal()
    captured_agents: list[Agent] = []
    original_constructor = cast(
        _ConversationConstructor,
        factory_module.__dict__["Conversation"],
    )

    def recording_constructor(
        *,
        agent: Agent,
        workspace: Path,
        persistence_dir: Path,
        conversation_id: UUID,
        callbacks: object,
        max_iteration_per_run: int,
        visualizer: object,
        delete_on_close: bool,
        tags: dict[str, str],
        user_id: str | None,
    ) -> LocalConversation:
        captured_agents.append(agent)
        return original_constructor(
            agent=agent,
            workspace=workspace,
            persistence_dir=persistence_dir,
            conversation_id=conversation_id,
            callbacks=callbacks,
            max_iteration_per_run=max_iteration_per_run,
            visualizer=visualizer,
            delete_on_close=delete_on_close,
            tags=tags,
            user_id=user_id,
        )

    monkeypatch.setitem(factory_module.__dict__, "Conversation", recording_constructor)

    try:
        created = factory.create("identity_create", goal)
        try:
            assert created.conversation.agent is captured_agents[0]
            assert created.conversation.state.agent is captured_agents[0]
            conversation_id = created.conversation_id
        finally:
            created.conversation.close()

        restored = factory.create("identity_create", goal, conversation_id=conversation_id)
        try:
            assert restored.conversation.agent is captured_agents[1]
            assert restored.conversation.state.agent is captured_agents[1]
        finally:
            restored.conversation.close()

        assert restored.conversation_id == conversation_id
    finally:
        from focusproof.openhands_runtime.tool_registry import release_repository_provider

        release_repository_provider()


def test_isolated_registry_probe_does_not_leak_to_parent_process() -> None:
    from openhands.sdk.tool import list_registered_tools
    import litellm

    def summary() -> tuple[tuple[str, ...], tuple[str, ...]]:
        return (
            tuple(sorted(str(name) for name in list_registered_tools())),
            tuple(sorted(str(name) for name in litellm.model_cost)),
        )

    before = summary()
    result = _isolated(
        """
import socket
import http
import ssl


class _BlockedSocket(socket.socket):
    def connect(self, *args, **kwargs):
        raise AssertionError("network access is forbidden in SDK contract probes")

    def connect_ex(self, *args, **kwargs):
        raise AssertionError("network access is forbidden in SDK contract probes")

socket.socket = _BlockedSocket
socket.create_connection = lambda *args, **kwargs: (_ for _ in ()).throw(
    AssertionError("network access is forbidden in SDK contract probes")
)
assert socket.socket is _BlockedSocket
assert isinstance(socket.socket, type)

from openhands.sdk.tool import list_registered_tools, register_tool
import litellm
from focusproof.openhands_runtime.factory import ConversationFactory
from focusproof.openhands_runtime.tools.text_evidence import (
    FocusProofTextEvidenceVerificationTool,
)

assert ConversationFactory is not None

probe_tool_name = "sdk_contract_probe_tool"
before_tools = tuple(sorted(str(name) for name in list_registered_tools()))
before_models = tuple(sorted(str(name) for name in litellm.model_cost))
register_tool(probe_tool_name, FocusProofTextEvidenceVerificationTool)
assert probe_tool_name in list_registered_tools()

probe_model_name = "sdk-contract-probe-model"
litellm.register_model(
    {
        probe_model_name: {
            "max_tokens": 17,
            "max_input_tokens": 11,
            "max_output_tokens": 6,
            "input_cost_per_token": 0.0,
            "output_cost_per_token": 0.0,
            "litellm_provider": "openai",
            "mode": "chat",
        }
    }
)
assert litellm.model_cost[probe_model_name]["max_tokens"] == 17
assert tuple(sorted(str(name) for name in list_registered_tools())) != before_tools
assert tuple(sorted(str(name) for name in litellm.model_cost)) != before_models
print('probe-ok')
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "probe-ok"
    assert summary() == before


def test_profile_llm_public_hook_contract_or_stop_gate() -> None:
    hook = LocalConversation.get_or_create_profile_llm
    assert list(inspect.signature(hook).parameters) == ["self", "profile_name", "usage_id"]
    return_annotation = inspect.signature(hook).return_annotation
    assert return_annotation in {"LLM", LLM}


def test_installed_media_dependency_contracts_and_lock_fact() -> None:
    assert metadata.version("openhands-sdk") == "1.31.0"
    requirements = [Requirement(value) for value in metadata.requires("openhands-sdk") or ()]
    pillow = next(item for item in requirements if item.name.lower() == "pillow")
    assert Version(metadata.version("pillow")) in pillow.specifier
    assert Version(metadata.version("pillow")) >= Version("12.1.1")
    lock = Path(__file__).parents[3] / "requirements/production.lock"
    match = re.search(r"(?m)^python-multipart==([^ \\\n]+)", lock.read_text(encoding="utf-8"))
    assert match is not None
    locked = Version(match.group(1))
    assert Version(metadata.version("python-multipart")) == locked
    assert locked >= Version("0.0.20")
