from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from openhands.sdk.conversation import LocalConversation
from pydantic import BaseModel, ConfigDict, Field

from focusproof.domain.review import ReviewResult

RuntimeMode = Literal[
    "openhands-local-real",
    "openhands-local-scripted-test",
    "unavailable",
    "failed",
]
RuntimeReviewStatus = Literal["completed", "awaiting_user", "failed"]


class ProviderUsageSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    call_count: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0)
    latency_seconds: float = Field(ge=0)


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
    compatibility_restore: bool = False
    projected_event_ids: set[str] = Field(default_factory=set)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def provider_usage_snapshot(self) -> ProviderUsageSnapshot:
        metrics = self.conversation.state.stats.get_combined_metrics()
        token_usage = metrics.accumulated_token_usage
        return ProviderUsageSnapshot(
            call_count=len(metrics.token_usages),
            input_tokens=token_usage.prompt_tokens if token_usage else 0,
            output_tokens=token_usage.completion_tokens if token_usage else 0,
            cost_usd=metrics.accumulated_cost,
            latency_seconds=sum(item.latency for item in metrics.response_latencies),
        )


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
