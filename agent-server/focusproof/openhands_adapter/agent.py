from __future__ import annotations

from focusproof.runtime.actions import Action
from focusproof.runtime.view import AgentView


class OpenHandsLearningAgentAdapter:
    def __init__(self, runtime_source: str) -> None:
        self.runtime_source = runtime_source

    def build_review_prompt(self, view: AgentView) -> str:
        evidence_count = len(view.evidence)
        return (
            "FocusProof learning review\n"
            f"domain: {view.goal.domain}\n"
            f"goal: {view.goal.goal}\n"
            f"evidenceCount: {evidence_count}\n"
            "Return a FocusProof action projection."
        )

    def parse_openhands_output(self, raw_output: str | None, view: AgentView) -> Action:
        del raw_output
        return DeterministicLearningAgentFallback(self.runtime_source).step(view)


class DeterministicLearningAgentFallback(OpenHandsLearningAgentAdapter):
    def step(self, view: AgentView) -> Action:
        if not view.evidence:
            return Action(
                type="request_evidence",
                evidenceType="text",
                reason="No learning evidence has been submitted.",
            )
        unverified = [
            evidence
            for evidence in view.evidence
            if not any(evidence.evidenceId in obs.sourceRefs for obs in view.verificationResults)
        ]
        if unverified:
            evidence = unverified[0]
            tool_name = "FakeWeb3TxTool" if evidence.evidenceType == "transaction" else "FakeTextEvidenceTool"
            input_payload = (
                {"hash": evidence.textContent}
                if evidence.evidenceType == "transaction"
                else {"text": evidence.textContent or ""}
            )
            return Action(
                type="verify_evidence",
                toolName=tool_name,
                input=input_payload,
                evidenceIds=[evidence.evidenceId],
            )
        return Action(type="calculate_score")


class OpenHandsAgentAdapter(DeterministicLearningAgentFallback):
    def __init__(self) -> None:
        super().__init__(runtime_source="projection-fallback")
