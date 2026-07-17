# AI4C Production Readiness Codex Goal

## Mission

Take the accepted FocusProof text/URL learning-verification MVP at commit
`bf5c9a8` through a controlled production-readiness program. Preserve the
domain-general product and the official OpenHands SDK runtime. Do not begin
multimodal or Web3-specialized work.

Project root: `/home/holy/web3/focusproof-agent`

Execution environment: WSL/Linux. Use Windows only when a required tool has no
Linux path, and record the exception.

## Mandatory Clarification Gate

Before changing production code:

1. Inspect the repository, AI4B final report, architecture, protocol and task board.
2. Restate your understanding of the objective, current baseline, non-goals,
   architectural boundaries, likely files and acceptance criteria.
3. Ask AI0 questions one at a time. Never bundle multiple questions.
4. After each answer, update your stated assumptions and confidence.
5. Continue until you can honestly report at least 90% confidence.
6. Stop and request AI0 approval of the understanding gate.
7. Produce the AI4C.0 design and implementation plan only after that approval.
8. Do not implement AI4C.1 until AI0 accepts both documents.

Questions should resolve material ambiguity, not seek permission for ordinary
engineering choices already fixed by repository conventions.

## Authoritative Baseline

- Branch: `ai4b-general-quality-security-release`.
- Accepted commit: `bf5c9a8`.
- AI4B report:
  `docs/research/AI4B_GENERAL_QUALITY_SECURITY_RELEASE_REPORT.md`.
- OpenHands SDK version observed by AI4B: `1.31.0`.
- Agent runtime and API remain Python/FastAPI.
- Frontend remains Next.js/TypeScript.
- OpenHands Conversation, Agent, native events and Tool protocol remain the
  official runtime path.

## Product Boundary

FocusProof verifies learning in any knowledge domain. AI4C must improve the
operational trustworthiness of that product; it must not turn the project into
a Web3 application.

In scope:

- production identity and authorization,
- real-LLM provider behavior and operational controls,
- reproducible dependencies and staging deployment,
- PostgreSQL compatibility and migration validation,
- observability, redaction, backup/restore and rollback,
- end-to-end production-readiness acceptance.

Out of scope:

- images, OCR, audio, ASR, PDF and other multimodal evidence,
- Monad RPC, wallets, contracts and on-chain proof recording,
- unrelated scoring rewrites,
- horizontal multi-region architecture,
- public launch, push or merge without explicit authorization.

## OpenHands Direct-Reuse Gate

Before creating or changing any runtime abstraction:

1. Search the installed OpenHands SDK public API and the pinned source.
2. Reuse the public SDK implementation directly when it supplies the behavior.
3. Do not implement an "OpenHands-inspired" equivalent.
4. If the SDK lacks the capability, document the exact gap, call site, minimal
   FocusProof-owned addition, tests and future removal condition.
5. Every phase report must contain `OpenHands APIs Reused` and
   `FocusProof-Owned SDK Gaps` sections.

FocusProof continues to own product identity, authorization, learning evidence,
review semantics, scoring, persistence projections and provider policy. It does
not own duplicate Agent/Conversation/EventLog/Action/Observation/Tool runtime
semantics.

## Sequential Delivery Gates

### AI4C.0 Design

Deliver:

- `docs/superpowers/specs/2026-07-17-ai4c-production-readiness-design.md`,
- `docs/superpowers/plans/2026-07-17-ai4c-production-readiness.md`,
- threat model and trust boundaries,
- exact phase/file ownership map,
- migration and rollback strategy,
- deterministic, real-provider and staging acceptance matrix.

The design must compare at least two viable approaches for identity and LLM
provider integration, state the selected approach and explain the trade-offs.
No production behavior changes are allowed in AI4C.0.

### AI4C.1 Real LLM Operations

Required outcomes:

- real provider runs through the official Conversation-backed review path,
- strict structured-output validation and safe failure behavior,
- bounded deadline, retry, concurrency, rate and cost policy,
- secret and prompt/output redaction,
- deterministic tests never consume a real key,
- explicitly marked real-provider smoke and failure-mode tests,
- provider outage cannot forge observations or silently fall back to success.

### AI4C.2 Identity and Authorization

Required outcomes:

- verified identity is injected by an approved boundary, never trusted from a
  request body or model output,
- Session, Evidence, Answer, Review and audit ownership are enforced,
- cross-user reads/writes, sender forgery, replay and revoked identity fail,
- development anonymous identity is explicitly isolated and cannot be enabled
  accidentally in a production profile,
- OpenHands message sender and Conversation user identity preserve verified
  attribution without becoming the authorization source of truth.

### AI4C.3 Reproducible Staging

Required outcomes:

- approved reproducible OpenHands SDK/dependency source and lock strategy,
- clean-host installation succeeds without developer-local absolute paths,
- PostgreSQL migration, persistence and recovery verification,
- configuration/secrets validation fails closed,
- structured redacted logs, health/readiness, metrics and operator runbooks,
- staging deploy, smoke, backup/restore and rollback evidence.

### AI4C.4 Final Acceptance

Required outcomes:

- complete deterministic backend/frontend/E2E regression,
- authorized real-provider acceptance with cost and secret report,
- identity isolation and abuse/security matrix,
- clean staging deployment and recovery drill,
- accessibility baseline covering keyboard, focus, zoom and automated checks,
- final report listing pass/fail/block status without overstating readiness.

## Working Rules

- Use TDD for every behavior change.
- Keep commits small and phase-specific.
- Do not amend accepted AI4B commits.
- Do not modify OpenHands SDK source.
- Do not read, print or commit `.env` secrets.
- Do not weaken tests to make a gate pass.
- Run Linux commands from `/home/holy/web3/focusproof-agent`.
- Stop after every numbered AI4C gate and wait for AI0 acceptance.
- AI0 may issue a narrow repair instruction; complete and reverify it before
  requesting the same gate again.

## Completion Evidence

Every gate report must include:

- branch and full HEAD,
- commits added during the gate,
- exact changed files,
- exact commands and summarized results,
- red/green TDD evidence,
- OpenHands APIs reused,
- FocusProof-owned SDK gaps,
- security and migration findings,
- remaining risks,
- explicit statement that no later gate, push, merge or public deployment was
  performed.

AI4C is complete only after AI0 independently verifies and accepts AI4C.4.
