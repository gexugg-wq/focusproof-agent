from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from pydantic import BaseModel, ConfigDict, Field

from focusproof.domain.review import ReviewResult

RuntimeMode = Literal[
    "openhands-local-real",
    "openhands-local-scripted-test",
    "unavailable",
    "failed",
]
RuntimeReviewStatus = Literal["completed", "awaiting_user", "failed"]


class ConversationHandle(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    session_id: str
    conversation: LocalConversation
    conversation_id: UUID
    workspace_path: Path
    persistence_path: Path
    runtime_mode: RuntimeMode
    toolset_version: str
    persisted_toolset_version: str | None = None
    toolset_version_mismatch: bool = False
    projected_event_ids: set[str] = Field(default_factory=set)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RuntimeReviewResult(BaseModel):
    sessionId: str
    conversationMode: RuntimeMode
    usedOpenHandsConversation: bool
    conversationId: str | None = None
    nativeEventCount: int = 0
    messageEventsCount: int = 0
    actionEventsCount: int = 0
    observationEventsCount: int = 0
    projectedEventsCount: int = 0
    reviewStatus: RuntimeReviewStatus
    agentQuestions: list[dict[str, str]] = Field(default_factory=list)
    reviewResult: ReviewResult | None = None
    error: str | None = None
