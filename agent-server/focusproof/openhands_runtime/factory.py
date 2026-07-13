from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from openhands.sdk import Agent, Conversation, LLM
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.types import ConversationCallbackType
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import Tool

from focusproof.openhands_adapter.llm_config import build_openhands_llm_config
from focusproof.openhands_runtime.handle import ConversationHandle, RuntimeMode
from focusproof.openhands_runtime.prompts import FOCUSPROOF_SYSTEM_PROMPT
from focusproof.openhands_runtime.tool_registry import (
    configure_repository_provider,
    ensure_focusproof_tools_registered,
)
from focusproof.openhands_runtime.tools import SessionEvidenceRepository
from focusproof.runtime.evidence import LearningGoal

LLMFactory = Callable[[str], LLM]
CallbackFactory = Callable[[str, UUID], ConversationCallbackType]

_SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class RuntimeUnavailableError(RuntimeError):
    """Raised when production LLM configuration is unavailable."""


class RuntimeCreationError(RuntimeError):
    """Raised when the SDK cannot create the required local runtime."""


class ConversationFactory:
    def __init__(
        self,
        *,
        repository: SessionEvidenceRepository,
        project_root: Path | None = None,
        data_dir: Path | None = None,
        llm_factory: LLMFactory | None = None,
        callback_factory: CallbackFactory | None = None,
    ) -> None:
        self._repository = repository
        configure_repository_provider(repository)
        self._project_root = project_root or Path(__file__).resolve().parents[3]
        self._data_dir = (data_dir or self._project_root / "var").resolve()
        self._llm_factory = llm_factory
        self._callback_factory = callback_factory

    def create(
        self,
        session_id: str,
        goal: LearningGoal,
        *,
        conversation_id: UUID | None = None,
        user_id: str | None = None,
    ) -> ConversationHandle:
        if not _SAFE_SESSION_ID_RE.fullmatch(session_id):
            raise ValueError("session_id contains unsafe path characters")
        del goal

        conversation_id = conversation_id or uuid5(
            NAMESPACE_URL, f"focusproof:{session_id}"
        )
        runtime_root = (self._data_dir / "conversations" / session_id).resolve()
        if not runtime_root.is_relative_to(self._data_dir):
            raise ValueError("conversation path is outside FOCUSPROOF_DATA_DIR")
        workspace_path = runtime_root / "workspace"
        persistence_path = runtime_root / "persistence"
        workspace_path.mkdir(parents=True, exist_ok=True)
        persistence_path.mkdir(parents=True, exist_ok=True)

        llm = (
            self._llm_factory(session_id)
            if self._llm_factory is not None
            else self._create_production_llm(session_id)
        )
        runtime_mode = self._runtime_mode_for(llm)
        agent = Agent(
            llm=llm,
            tools=self._session_tools(session_id),
            include_default_tools=[],
            system_prompt=FOCUSPROOF_SYSTEM_PROMPT,
        )
        callbacks = (
            [self._callback_factory(session_id, conversation_id)]
            if self._callback_factory is not None
            else None
        )
        try:
            conversation = Conversation(
                agent=agent,
                workspace=workspace_path,
                persistence_dir=persistence_path,
                conversation_id=conversation_id,
                callbacks=callbacks,
                max_iteration_per_run=6,
                visualizer=None,
                delete_on_close=False,
                tags={"application": "focusproof", "sessionid": session_id},
                user_id=user_id,
            )
        except Exception as exc:
            raise RuntimeCreationError(
                "OpenHands LocalConversation creation failed"
            ) from exc
        if not isinstance(conversation, LocalConversation):
            conversation.close()
            raise RuntimeCreationError("SDK did not create a LocalConversation")
        return ConversationHandle(
            session_id=session_id,
            conversation=conversation,
            conversation_id=conversation_id,
            workspace_path=workspace_path,
            persistence_path=persistence_path,
            runtime_mode=runtime_mode,
        )

    def _runtime_mode_for(self, llm: LLM) -> RuntimeMode:
        if self._llm_factory is None:
            return "openhands-local-real"
        if isinstance(llm, TestLLM):
            return "openhands-local-scripted-test"
        raise ValueError("injected llm_factory must return the SDK TestLLM")

    def _create_production_llm(self, session_id: str) -> LLM:
        config = build_openhands_llm_config(self._project_root)
        if config is None:
            raise RuntimeUnavailableError("OpenHands LLM configuration is unavailable")
        return LLM(
            usage_id=f"focusproof-{session_id}",
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url,
        )

    def _session_tools(self, session_id: str) -> list[Tool]:
        ensure_focusproof_tools_registered()
        params = {"session_id": session_id}
        return [
            Tool(name="FocusProofEvidenceVerificationTool", params=params),
            Tool(name="FocusProofLearnerInputTool", params=params),
            Tool(name="FocusProofReviewDraftTool", params=params),
        ]
