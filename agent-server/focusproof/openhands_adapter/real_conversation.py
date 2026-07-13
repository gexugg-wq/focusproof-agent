from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from focusproof.openhands_adapter.llm_config import build_openhands_llm_config
from focusproof.openhands_adapter.safe_debug_tools import list_disabled_openhands_tools
from focusproof.openhands_runtime.factory import (
    RuntimeCreationError,
    RuntimeUnavailableError,
)
from focusproof.openhands_runtime.manager import ConversationManager
from focusproof.runtime.event_log import InMemoryEventLog
from focusproof.runtime.evidence import Evidence, LearningGoal, hash_evidence_content

_ALLOWED_ACTIONS = {"ask_question", "request_evidence", "tentative_review"}
_ACTION_ALIASES = {
    "ask question": "ask_question",
    "request evidence": "request_evidence",
    "tentative review": "tentative_review",
}
_MARKDOWN_FIELD_RE = re.compile(
    r"(?:^|[\n|])\s*(?:[-*]\s*)?(?:\*\*)?(?P<label>Action|RecommendedAction|Question|Reason)(?:\*\*)?\s*:\s*(?P<value>.*?)(?=(?:\s*[|\n]\s*(?:[-*]\s*)?(?:\*\*)?(?:Action|RecommendedAction|Question|Reason|Next Step|Weak Evidence Signals)(?:\*\*)?\s*:)|$)",
    re.IGNORECASE | re.DOTALL,
)


def _prompt(goal: str, evidence: str, domain: str) -> str:
    return (
        "You are a FocusProof learning evidence review agent.\n"
        "You only judge evidence credibility, not personal worth.\n"
        "You must not execute shell commands.\n"
        "You must not edit files.\n"
        "You must not browse the web.\n"
        "Based only on the provided domain, goal, and evidence, return compact JSON with keys: "
        "recommendedAction, question, reason, weakEvidenceSignals, nextStep.\n"
        "recommendedAction must be one of: ask_question, request_evidence, tentative_review.\n\n"
        f"domain: {domain}\n"
        f"goal: {goal}\n"
        f"evidence: {evidence}\n"
    )


def _normalize_action(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip().strip("`*_ ").replace("-", "_").lower()
    cleaned = re.sub(r"\s+", "_", cleaned)
    if cleaned in _ALLOWED_ACTIONS:
        return cleaned
    alias_key = cleaned.replace("_", " ")
    return _ACTION_ALIASES.get(alias_key)


def _extract_markdown_fields(raw_text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in _MARKDOWN_FIELD_RE.finditer(raw_text):
        label = match.group("label").lower()
        value = match.group("value").strip().strip("`*_ ")
        if label == "recommendedaction":
            label = "action"
        if value:
            fields[label] = value
    return fields


def _event_text(event: object) -> str | None:
    for attr in ("content", "message", "text"):
        value = getattr(event, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    llm_message = getattr(event, "llm_message", None)
    if llm_message is not None:
        content = getattr(llm_message, "content", None)
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            parts = [getattr(item, "text", None) for item in content]
            text = "\n".join(part for part in parts if isinstance(part, str))
            if text.strip():
                return text
    rendered = str(event)
    return rendered if rendered.strip() else None


def _extract_text(conversation: Any) -> str | None:
    state = getattr(conversation, "state", None)
    events = getattr(state, "events", []) if state is not None else []
    for event in reversed(list(events)):
        text = _event_text(event)
        if text and "recommendedAction" in text:
            return text[:4000]
    ask_agent = getattr(conversation, "ask_agent", None)
    if callable(ask_agent):
        try:
            response = ask_agent("Summarize your FocusProof review JSON in one compact response.")
            return str(response) if response is not None else None
        except Exception:
            return None
    if events:
        return "\n".join(str(event) for event in events[-3:])[:4000]
    return None


def _parse_review(raw_text: str | None) -> dict[str, str | None]:
    parsed: dict[str, str | None] = {
        "recommendedAction": None,
        "question": None,
        "reason": None,
    }
    if not raw_text:
        return parsed
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(raw_text[start : end + 1])
        except json.JSONDecodeError:
            data = {}
        action = data.get("recommendedAction")
        if isinstance(action, str):
            parsed["recommendedAction"] = _normalize_action(action)
        for key in ("question", "reason"):
            value = data.get(key)
            if isinstance(value, str):
                parsed[key] = value.strip() or None
    markdown_fields = _extract_markdown_fields(raw_text)
    if parsed["recommendedAction"] is None:
        parsed["recommendedAction"] = _normalize_action(markdown_fields.get("action"))
    for key in ("question", "reason"):
        if parsed[key] is None:
            parsed[key] = markdown_fields.get(key)
    if parsed["recommendedAction"] is None:
        lowered = raw_text.lower()
        for action in _ALLOWED_ACTIONS:
            if action in lowered:
                parsed["recommendedAction"] = action
                break
        if parsed["recommendedAction"] is None:
            for alias, action in _ACTION_ALIASES.items():
                if alias in lowered:
                    parsed["recommendedAction"] = action
                    break
    return parsed


def run_real_learning_review_spike(
    goal: str,
    evidence: str,
    domain: str,
    project_root: Path | None = None,
) -> dict[str, Any]:
    disabled_tools = list_disabled_openhands_tools()
    config = build_openhands_llm_config(project_root)
    if config is None:
        return {
            "mode": "unavailable",
            "model": None,
            "domain": domain,
            "recommendedAction": None,
            "question": None,
            "reason": None,
            "rawText": None,
            "disabledTools": disabled_tools,
            "error": "missing credential or invalid dotenv configuration",
        }

    session_id = f"debug_{uuid4().hex}"
    submitted_evidence = Evidence(
        evidenceId=f"ev_{uuid4().hex}",
        evidenceType="text",
        contentHash=hash_evidence_content(evidence, None),
        textContent=evidence,
    )
    repository = _DebugEvidenceRepository(session_id, submitted_evidence)
    manager = ConversationManager(
        repository=repository,
        audit_log=InMemoryEventLog(),
        project_root=project_root,
    )
    created = False
    try:
        manager.create(
            session_id,
            LearningGoal(
                domain=domain,
                title="FocusProof debug review",
                goal=goal,
            ),
        )
        created = True
        manager.send_evidence(session_id, submitted_evidence)
        result = manager.run_review(session_id)
    except RuntimeUnavailableError:
        return {
            "mode": "unavailable",
            "model": config.model,
            "domain": domain,
            "recommendedAction": None,
            "question": None,
            "reason": None,
            "rawText": None,
            "disabledTools": disabled_tools,
            "error": "missing credential or invalid dotenv configuration",
        }
    except (RuntimeCreationError, ValueError) as exc:
        return {
            "mode": "failed",
            "model": config.model,
            "domain": domain,
            "recommendedAction": None,
            "question": None,
            "reason": None,
            "rawText": None,
            "disabledTools": disabled_tools,
            "error": f"{type(exc).__name__}: OpenHands LocalConversation creation failed",
        }
    finally:
        if created:
            manager.close(session_id)

    question = result.agentQuestions[0] if result.agentQuestions else None
    return {
        "mode": "real" if result.usedOpenHandsConversation else "failed",
        "model": config.model,
        "domain": domain,
        "recommendedAction": (
            "ask_question"
            if result.reviewStatus == "awaiting_user"
            else "tentative_review"
            if result.reviewStatus == "completed"
            else None
        ),
        "question": question["question"] if question else None,
        "reason": question["reason"] if question else None,
        "rawText": None,
        "disabledTools": disabled_tools,
        "error": result.error,
    }


class _DebugEvidenceRepository:
    def __init__(self, session_id: str, evidence: Evidence) -> None:
        self._session_id = session_id
        self._evidence = evidence

    def get_evidence(self, session_id: str, evidence_id: str) -> Evidence:
        if session_id != self._session_id or evidence_id != self._evidence.evidenceId:
            raise KeyError((session_id, evidence_id))
        return self._evidence.model_copy(deep=True)
