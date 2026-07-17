# AI4C.4 Final Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Audit every AI4C production-readiness requirement with reproducible evidence, repair only evidence-proven defects in their owning phase, and publish an honest final staging-readiness classification.

**Architecture:** AI4C.4 adds no runtime architecture. It executes deterministic, identity, staging, recovery, accessibility and selectively authorized external gates against accepted AI4C.1-3, maps each requirement to exact evidence, and fails closed when a capability or external exercise is absent.

**Tech Stack:** Linux/WSL, Python 3.12, pytest, Ruff, Mypy, Node/npm, Next.js, Vitest, Playwright, OCI/Compose/PostgreSQL preflight, OpenHands SDK 1.31.0.

## Constraints and Ownership

- Begin only after AI0 accepts AI4C.3. End with a local commit/report and stop for AI0.
- Do not change production code unless a named acceptance red test proves a defect. Repair in its AI4C.1/.2/.3 ownership with a separate TDD commit.
- Create only `agent-server/tests/ai4c/test_final_acceptance.py`, `frontend/e2e/ai4c-production-readiness.spec.ts`, and `docs/research/AI4C_PRODUCTION_READINESS_REPORT.md`. If no existing accessibility checker exists, AI4C.4 may add `@axe-core/playwright` to frontend dev dependencies and lock.
- Modify `frontend/features/evidence/EvidencePanel.tsx`,
  `frontend/features/review/ReviewPanel.tsx`,
  `frontend/features/session/SessionWorkspace.tsx`,
  `frontend/features/build-log/BuildLog.tsx`, or `frontend/app/globals.css` only
  when a named accessibility/geometric red test proves that exact file owns
  the defect.
- Existing reports/deployment/security docs may receive evidence corrections only. Do not change scoring, contracts, OpenHands source, protocols or product scope.
- Default gates remove provider keys and exclude `real_llm`, `postgres`, and `staging_external` where applicable.
- Real provider requires fresh AI0 authorization. Real external OIDC/staging requires an identified non-public target and separate authorization.
- Never read `.env`; never print prompts, evidence, answers, completions, tokens, JWKS, secrets or environment values.
- No push, merge, public deployment, Web3, wallet, contract or multimodal work.

## Fixed Acceptance Types

```python
from dataclasses import dataclass
from typing import Literal

GateStatus = Literal["pass", "fail", "blocked", "not-authorized"]
ReleaseClassification = Literal[
    "failed",
    "blocked",
    "staging-ready with blockers",
    "production-readiness accepted for the tested staging profile",
]

@dataclass(frozen=True, slots=True)
class RequirementEvidence:
    requirement_id: str
    status: GateStatus
    evidence: tuple[str, ...]
    owning_phase: str
    residual_risk: str | None
```

There is no `public-launch-ready` state. If only the local issuer is used, or a
real managed/self-hosted OIDC provider and real external staging are not both
exercised, the maximum classification is `staging-ready with blockers`.

### Task 1: Requirement Inventory and Evidence-Lint Red Test

**Files:** final-acceptance test and report skeleton.

- [ ] Write a red evidence-lint test loading the report and requiring one unique row for every requirement in the accepted AI4C goal/spec and AI4C.1-3 reports. Every row needs an exact test node, command, document section, artifact digest or screenshot; prose alone is rejected.
- [ ] Require explicit matrices for OpenHands reuse, provider bounds/failures, 401/403/404, spoof resistance, anonymous isolation, SDK equivalence, PostgreSQL, clean stack, paired restore, redaction, accessibility and exclusions.
- [ ] Run red and observe absent report/evidence rows:

```bash
cd /home/holy/web3/focusproof-agent
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY \
  .venv/bin/python -m pytest agent-server/tests/ai4c/test_final_acceptance.py -q
```

- [ ] Create fixed report sections with factual `blocked` or `not-authorized` initial states. Do not pre-mark a gate passed.
- [ ] Run evidence-lint green for structural completeness only; it must not equate completeness with readiness.
- [ ] Commit `test: require auditable AI4C acceptance evidence`.

### Task 2: Full Deterministic Quality, Security and Dependency Gates

- [ ] Record Linux versions for Python, OpenHands SDK, pytest, Ruff, Mypy, Node, npm, Next, Vitest and Playwright without environment values.
- [ ] Run backend:

```bash
cd /home/holy/web3/focusproof-agent
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY \
  .venv/bin/python -m pytest agent-server/tests -q \
  -m "not real_llm and not postgres and not staging_external"
.venv/bin/ruff check agent-server scripts
.venv/bin/mypy agent-server scripts
```

- [ ] Run frontend:

```bash
cd /home/holy/web3/focusproof-agent/frontend
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY npm run lint
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY npm run typecheck
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY npm test
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY npm run build
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY npm run test:e2e
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY npm audit --omit=dev
```

- [ ] Restore any tracked AI3 baseline image rewritten by E2E. Reject `test-results`, traces, `.next`, temporary databases, `.env`, `var` or secrets. Scan text/artifacts for fixed test sentinels.
- [ ] If a gate fails, add a named red test in its owning phase, observe it fail, apply the smallest correction there, run focused green plus both full gates, and commit `fix(ai4c1):`, `fix(ai4c2):`, or `fix(ai4c3):` with the defect. Never hide failure in the report.
- [ ] Update evidence rows with exact counts, warnings, durations, advisories and commands; commit evidence-only report changes.

### Task 3: Authenticated Real-BFF Accessibility and Recovery

**Files:** Playwright spec; optional accessibility dev dependency only if inspection proves absent.

- [ ] Write red Playwright cases against production Next plus deterministic FastAPI/OpenHands and local OIDC fixture. Cover authenticated text/URL learning, awaiting-user answer, completed review, refresh recovery, expired token `401`, disabled identity `403`, cross-owner `404`, retained input, keyboard-only flow, visible focus, accessible names/live status, 200% zoom, four AI4B viewports and no overflow/overlap.
- [ ] Prohibit `page.route`, mocks or intercepts for successful `/api/focusproof/**`. Browser traffic must traverse Next BFF, FastAPI verifier, existing manager, OpenHands `LocalConversation`/`TestLLM`, ToolExecutor/Observation and persistence.
- [ ] Run targeted red. Make only evidence-proven accessibility corrections in owning components; do not redesign UI or add public test endpoints.
- [ ] Run targeted green twice from cold production builds and full E2E. Inspect temporary images at original detail; do not replace accepted AI4B screenshots without new AI0 approval.
- [ ] Commit tests as `test: exercise authenticated AI4C browser acceptance`. Commit any UI correction separately with the failing node in its message body.

### Task 4: External Capability, Recovery and Cost Gates

- [ ] Run `scripts/check_ai4c_capabilities.py` first. If container, Compose or PostgreSQL is blocked, record it and do not claim the corresponding gate.
- [ ] When available, run official SDK equivalence, PostgreSQL tests, two clean stack builds, production BFF flow, restart recovery and destructive paired backup/restore. Record digests and persistent IDs, never user content.
- [ ] Real DashScope is forbidden until AI0 authorizes the exact command. If authorized, enforce concurrency 1, retries 1, at most 4 calls, 8192 input tokens/call, 1024 output tokens/call, total cost at most USD 0.10, provider timeout 30 seconds and review timeout 60 seconds. Abort at any bound and print aggregates only.
- [ ] External OIDC/staging runs only against an AI0-approved managed or standards-compliant self-hosted issuer and identified non-public target. Validate real JWKS rotation/outage, issuer/audience, BFF forwarding, backup and rollback without storing tokens. A local fixture cannot pass this row.
- [ ] Classify absent authorization honestly: real smoke is `not-authorized`;
  absent real OIDC/external staging is `blocked` evidence and caps the release
  classification at `staging-ready with blockers`.
- [ ] Route any failure to its owning phase red test/minimal fix/green/regression/separate commit before repeating this task.

### Task 5: OpenHands Reuse and Protocol Freeze Audit

- [ ] Record exact imports/construction sites proving direct SDK 1.31.0 use of `Agent`, `LLM`, `LocalConversation`, `EventLog`, `View`, `ToolDefinition`, `ToolExecutor`, `ActionEvent`, `ObservationEvent`, metrics, lifecycle `interrupt`/`close`, and `TestLLM`.
- [ ] Scan for duplicate Conversation/agent loops, EventLogs/event schemas, tool protocols, provider HTTP clients, schedulers and default programming tools. Any duplicate fails the gate.
- [ ] Compare serialized API/protocol fixtures to accepted AI4B. Only auth `401/403/404` differences are allowed; `ownerUserId` remains opaque and no issuer/subject field appears.
- [ ] Audit provider admission, VerifiedIdentity/authorization, audit/operations projection and paired backup coordination. Cite each SDK-gap check, minimum surface and deletion condition.
- [ ] Run protocol/reuse tests and add exact nodes/diffs to the report.

### Task 6: Final Report, Hygiene, Commit and Stop

- [ ] Complete `AI4C_PRODUCTION_READINESS_REPORT.md`: baseline/branch/commits, architecture, requirement matrix, red-green history, exact gates/versions/audits, SDK equivalence, external authorization, threat model, migrations, restore, accessibility, changed paths, rollback and residual risks.
- [ ] State explicitly: anonymous identity is local-dev only; production auth provider selection/integration status; no real key was read unless an authorized smoke is cited; no Web3/contracts/wallet/on-chain/multimodal/public deploy occurred; no second runtime/EventLog/agent loop/Action/Observation/Tool protocol was created; SDK 1.31.0 public APIs were reused.
- [ ] Assign the honest `ReleaseClassification`; without real OIDC plus external
  staging, use `staging-ready with blockers`, never public-launch-ready.
- [ ] Run hygiene:

```bash
cd /home/holy/web3/focusproof-agent
git diff --check
git status --short
git diff --name-only d93416e58298d75e64416e35d9a5b080cc7260fa...HEAD
git log --oneline --decorate d93416e58298d75e64416e35d9a5b080cc7260fa..HEAD
git worktree list
```

- [ ] Verify only main worktree and no Next/FastAPI/Playwright/staging process or disposable volume remains. Scan tracked text/screenshots for fixed fake sentinels without reading `.env`.
- [ ] Run evidence-lint and diff check, commit `docs: report AI4C production readiness`, then prove clean status.
- [ ] Stop and report HEAD, chain, exact counts/durations, blockers, classification, files and rollback revision to AI0. Do not push, merge, deploy or start another program.

## Rollback

AI4C.4 evidence-only commits roll back to the accepted AI4C.3 SHA. Defect repairs roll back at their owning phase commit and require the complete later-phase matrix again. Data rollback always uses the paired PostgreSQL/OpenHands unit.
