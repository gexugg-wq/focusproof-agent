from __future__ import annotations

from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel

from focusproof.domain.review import ReviewResult
from focusproof.domain.scoring import score_learning_session
from focusproof.openhands_adapter.agent import DeterministicLearningAgentFallback
from focusproof.openhands_adapter.event_projection import (
    project_action_to_focusproof_event,
    project_answer_to_message_event,
    project_evidence_to_message_event,
    project_observation_to_focusproof_event,
    project_user_goal_to_message_event,
)
from focusproof.openhands_adapter.safe_debug_tools import list_disabled_openhands_tools
from focusproof.openhands_adapter.tools import execute_focusproof_tool
from focusproof.runtime.actions import Action
from focusproof.runtime.event_log import InMemoryEventLog
from focusproof.runtime.evidence import Evidence, LearningGoal
from focusproof.runtime.events import Event
from focusproof.runtime.observations import Observation
from focusproof.runtime.view import AgentView, SessionView

ConversationMode = Literal["projection-fallback", "fallback"]


class ConversationReviewResult(BaseModel):
    sessionId: str
    conversationMode: ConversationMode
    usedOpenHandsConversation: bool
    focusproofEvents: list[Event]
    reviewResult: ReviewResult
    actionEventsCount: int
    observationEventsCount: int
    unsafeToolsBlocked: list[str]
    error: str | None = None


class FocusProofLearningConversation:
    def __init__(
        self,
        *,
        session_id: str,
        goal: LearningGoal,
        conversation_mode: ConversationMode,
        used_openhands_conversation: bool,
        project_root: Path | None = None,
        error: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.goal = goal
        self.conversation_mode = conversation_mode
        self.used_openhands_conversation = used_openhands_conversation
        self.project_root = project_root
        self.error = error
        self._event_log = InMemoryEventLog()
        self._source_index = 0
        self._evidence_ids: set[str] = set()
        self._answers: dict[str, str] = {}
        self._observations: list[Observation] = []
        self._actions: list[Action] = []
        self._agent = DeterministicLearningAgentFallback(runtime_source=conversation_mode)
        self._append_system_created()
        self._append(project_user_goal_to_message_event(
            session_id=session_id,
            goal=goal,
            runtime_source=conversation_mode,
            source_index=self._next_source_index(),
        ))

    @classmethod
    def create(
        cls,
        session_id: str,
        goal: LearningGoal,
        *,
        use_real_llm: bool = False,
        project_root: Path | None = None,
    ) -> "FocusProofLearningConversation":
        return cls(
            session_id=session_id,
            goal=goal,
            conversation_mode="projection-fallback",
            used_openhands_conversation=False,
            project_root=project_root,
            error=(
                "legacy projection fallback cannot run a real OpenHands conversation"
                if use_real_llm
                else None
            ),
        )

    def submit_evidence(self, evidence: Evidence) -> list[Event]:
        if evidence.evidenceId in self._evidence_ids:
            return []
        self._evidence_ids.add(evidence.evidenceId)
        event = project_evidence_to_message_event(
            session_id=self.session_id,
            evidence=evidence,
            runtime_source=self.conversation_mode,
            source_index=self._next_source_index(),
        )
        self._append(event)
        return [event]

    def submit_answer(self, question_id: str, answer: str) -> list[Event]:
        if self._answers.get(question_id) == answer:
            return []
        self._answers[question_id] = answer
        event = project_answer_to_message_event(
            session_id=self.session_id,
            question_id=question_id,
            answer=answer,
            runtime_source=self.conversation_mode,
            source_index=self._next_source_index(),
        )
        self._append(event)
        return [event]

    def run_review(
        self,
        evidence: list[Evidence],
        answers: list[str],
        max_steps: int = 3,
    ) -> ConversationReviewResult:
        for item in evidence:
            self.submit_evidence(item)
        for index, answer in enumerate(answers, start=1):
            self.submit_answer(f"answer_{index}", answer)

        action_count = 0
        observation_count = 0
        for _ in range(max_steps):
            action = self._agent.step(self._view(evidence))
            self._actions.append(action)
            if action.type == "calculate_score":
                break
            action_event = project_action_to_focusproof_event(
                session_id=self.session_id,
                action=action,
                runtime_source=self.conversation_mode,
                source_index=self._next_source_index(),
            )
            self._append(action_event)
            action_count += 1
            if action.type == "verify_evidence":
                observation = execute_focusproof_tool(action)
                self._observations.append(observation)
                observation_event = project_observation_to_focusproof_event(
                    session_id=self.session_id,
                    observation=observation,
                    runtime_source=self.conversation_mode,
                    source_index=self._next_source_index(),
                )
                self._append(observation_event)
                observation_count += 1
                continue
            break

        review = score_learning_session(
            goal=self.goal,
            evidence=evidence,
            answers=answers,
            observations=self._observations,
        )
        score_event = self._event_log.append(
            self.session_id,
            "score.calculated",
            "agent",
            self._review_payload(review, "ReviewProjection"),
        )
        self._event_log.append(
            self.session_id,
            "review.completed",
            "agent",
            {
                "reviewId": f"rev_{uuid4().hex}",
                "summary": review.summary,
                "nextStep": review.nextStep,
                "scoreEventId": score_event.id,
                "runtimeSource": self.conversation_mode,
                "sourceRuntime": self.conversation_mode,
                "openhandsEventKind": "ReviewProjection",
                "sourceIndex": self._next_source_index(),
                "sessionId": self.session_id,
            },
        )
        return ConversationReviewResult(
            sessionId=self.session_id,
            conversationMode=self.conversation_mode,
            usedOpenHandsConversation=self.used_openhands_conversation,
            focusproofEvents=self.get_focusproof_events(),
            reviewResult=review,
            actionEventsCount=action_count,
            observationEventsCount=observation_count,
            unsafeToolsBlocked=list_disabled_openhands_tools(),
            error=self.error,
        )

    def get_focusproof_events(self) -> list[Event]:
        return self._event_log.list(self.session_id)

    def _append_system_created(self) -> None:
        self._event_log.append(
            self.session_id,
            "session.created",
            "system",
            {
                "sessionId": self.session_id,
                "runtimeSource": self.conversation_mode,
                "sourceRuntime": self.conversation_mode,
                "openhandsEventKind": "ConversationState",
                "sourceIndex": self._next_source_index(),
            },
        )

    def _append(self, event: Event) -> None:
        self._event_log.append_event(event)

    def _next_source_index(self) -> int:
        self._source_index += 1
        return self._source_index

    def _view(self, evidence: list[Evidence]) -> AgentView:
        return AgentView(
            session=SessionView(id=self.session_id, status="running"),
            goal=self.goal,
            evidence=evidence,
            verificationResults=self._observations,
            findings=[],
            unansweredQuestions=[],
            availableTools=[],
            previousActions=self._actions,
        )

    def _review_payload(self, review: ReviewResult, kind: str) -> dict[str, object]:
        payload = review.model_dump()
        payload.update(
            {
                "runtimeSource": self.conversation_mode,
                "sourceRuntime": self.conversation_mode,
                "openhandsEventKind": kind,
                "sourceIndex": self._next_source_index(),
                "sessionId": self.session_id,
            }
        )
        return payload
