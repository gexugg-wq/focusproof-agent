from __future__ import annotations

import os
import re
from collections.abc import Callable, Collection
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from openhands.sdk import Agent, Conversation, LLM
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.types import ConversationCallbackType
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import Tool
import httpx

from focusproof.config.profiles import RuntimeSettings, load_runtime_settings
from focusproof.openhands_adapter.llm_config import build_openhands_llm
from focusproof.openhands_runtime.capabilities import (
    VerificationCapabilityRegistry,
    build_builtin_capabilities,
)
from focusproof.openhands_runtime.handle import ConversationHandle, RuntimeMode
from focusproof.openhands_runtime.prompts import FOCUSPROOF_SYSTEM_PROMPT
from focusproof.openhands_runtime.tool_assembler import SessionToolAssembler
from focusproof.openhands_runtime.tool_registry import (
    configure_repository_provider,
    configure_url_execution_pool_provider,
    configure_url_fetcher_provider,
    ensure_focusproof_tools_registered,
)
from focusproof.openhands_runtime.tools import SessionEvidenceRepository
from focusproof.openhands_runtime.tools.url_evidence import UrlFetcher
from focusproof.openhands_runtime.tools.url_fetcher import BoundedUrlFetcher
from focusproof.openhands_runtime.tools.url_execution import BoundedUrlExecutionPool
from focusproof.openhands_runtime.tools.url_safety import UrlSafetyPolicy
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
        capability_registry: VerificationCapabilityRegistry | None = None,
        tool_assembler: SessionToolAssembler | None = None,
        url_fetcher: UrlFetcher | None = None,
        url_execution_pool: BoundedUrlExecutionPool | None = None,
        runtime_settings: RuntimeSettings | None = None,
    ) -> None:
        self._repository = repository
        configure_repository_provider(repository)
        registry = capability_registry or VerificationCapabilityRegistry(
            build_builtin_capabilities()
        )
        url_capability = registry.get("url")
        if url_fetcher is None:
            client = httpx.Client(
                follow_redirects=False,
                http2=False,
                limits=httpx.Limits(max_keepalive_connections=0),
                timeout=httpx.Timeout(15.0, connect=5.0),
            )
            url_fetcher = BoundedUrlFetcher(
                policy=UrlSafetyPolicy(allow_http=False),
                client=client,
                total_timeout_seconds=(
                    url_capability.timeout_seconds
                    if url_capability is not None
                    else 15.0
                ),
            )
            configure_url_fetcher_provider(url_fetcher, close=client.close)
        else:
            configure_url_fetcher_provider(url_fetcher)
        if url_execution_pool is None:
            url_execution_pool = BoundedUrlExecutionPool()
            configure_url_execution_pool_provider(
                url_execution_pool,
                close=url_execution_pool.close,
            )
        else:
            configure_url_execution_pool_provider(url_execution_pool)
        self._tool_assembler = tool_assembler or SessionToolAssembler(registry)
        self._project_root = project_root or Path(__file__).resolve().parents[3]
        self._data_dir = (data_dir or self._project_root / "var").resolve()
        self._llm_factory = llm_factory
        self._callback_factory = callback_factory
        self._runtime_settings = runtime_settings

    def create(
        self,
        session_id: str,
        goal: LearningGoal,
        *,
        conversation_id: UUID | None = None,
        user_id: str | None = None,
        evidence_types: Collection[str] | None = None,
    ) -> ConversationHandle:
        if not _SAFE_SESSION_ID_RE.fullmatch(session_id):
            raise ValueError("session_id contains unsafe path characters")
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
        conversation_store = Path(
            LocalConversation.get_persistence_dir(
                persistence_path,
                conversation_id,
            )
        ).resolve()
        if not conversation_store.is_relative_to(self._data_dir):
            raise ValueError(
                "conversation persistence path is outside FOCUSPROOF_DATA_DIR"
            )
        compatibility_restore = (
            conversation_store / "base_state.json"
        ).is_file()

        llm = (
            self._llm_factory(session_id)
            if self._llm_factory is not None
            else self._create_production_llm(session_id)
        )
        runtime_mode = self._runtime_mode_for(llm)
        toolset_version = self._tool_assembler.version(
            goal.domain,
            evidence_types,
            compatibility_restore=compatibility_restore,
        )
        agent = Agent(
            llm=llm,
            tools=self._session_tools(
                session_id,
                goal.domain,
                evidence_types,
                compatibility_restore=compatibility_restore,
            ),
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
                tags={
                    "application": "focusproof",
                    "sessionid": session_id,
                    "toolsetversion": toolset_version,
                },
                user_id=user_id,
            )
        except Exception as exc:
            raise RuntimeCreationError(
                "OpenHands LocalConversation creation failed"
            ) from exc
        if not isinstance(conversation, LocalConversation):
            conversation.close()
            raise RuntimeCreationError("SDK did not create a LocalConversation")
        persisted_toolset_version = conversation.state.tags.get("toolsetversion")
        return ConversationHandle(
            session_id=session_id,
            conversation=conversation,
            conversation_id=conversation_id,
            workspace_path=workspace_path,
            persistence_path=persistence_path,
            runtime_mode=runtime_mode,
            toolset_version=toolset_version,
            persisted_toolset_version=persisted_toolset_version,
            toolset_version_mismatch=(
                persisted_toolset_version is not None
                and persisted_toolset_version != toolset_version
            ),
            compatibility_restore=compatibility_restore,
        )

    def _runtime_mode_for(self, llm: LLM) -> RuntimeMode:
        if self._llm_factory is None:
            return "openhands-local-real"
        if isinstance(llm, TestLLM):
            return "openhands-local-scripted-test"
        raise ValueError("injected llm_factory must return the SDK TestLLM")

    def _create_production_llm(self, session_id: str) -> LLM:
        settings = self._runtime_settings or load_runtime_settings(os.environ)
        policy = settings.real_llm
        if policy is None:
            raise RuntimeUnavailableError("OpenHands LLM configuration is unavailable")
        return build_openhands_llm(
            policy,
            usage_id=f"focusproof-{session_id}",
        )

    def _session_tools(
        self,
        session_id: str,
        domain: str,
        evidence_types: Collection[str] | None,
        *,
        compatibility_restore: bool,
    ) -> list[Tool]:
        ensure_focusproof_tools_registered()
        return self._tool_assembler.assemble(
            session_id,
            domain,
            evidence_types,
            compatibility_restore=compatibility_restore,
        )
