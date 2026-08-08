# AI4C Production Readiness Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drive the accepted FocusProof text/URL product through four separately accepted production-readiness gates without changing its learning protocol or OpenHands runtime.

**Architecture:** AI4C executes real-LLM operations, OIDC identity, reproducible staging and final acceptance in that order. OpenHands SDK 1.31.0 remains the runtime owner; FocusProof adds only provider admission, authorization, product persistence, logging policy and deployment controls at product boundaries.

**Tech Stack:** WSL Ubuntu, Python 3.12, FastAPI, OpenHands SDK 1.31.0, LiteLLM through SDK `LLM`, SQLAlchemy 2, Alembic, PostgreSQL, Next.js 15, TypeScript, Playwright, OCI/Compose.

## Global Constraints

- Execute only in `/home/holy/web3/focusproof-agent` under WSL/Linux.
- Baseline branch is `ai4c-production-readiness`; do not amend accepted commits.
- Do not read or print `.env`; configuration examples use `.env.example` names and fake values.
- Default and CI commands remove `DASHSCOPE_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `GEMINI_API_KEY`, `AZURE_OPENAI_API_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `LLM_API_KEY`.
- Default tests use SDK `TestLLM` and run with `-m "not real_llm and not postgres and not staging_external"` when the named markers exist.
- A real-provider command runs only after a written AI0 authorization for that invocation.
- Do not create a FocusProof provider HTTP client, Agent loop, Conversation, EventLog, Action, Observation, Tool runtime, scheduler or event loop.
- Do not modify scoring or `docs/protocol/EVENTS.md`.
- The only product HTTP-boundary changes are `401 invalid_token`, `403 forbidden` and existing non-enumerating `404`.
- `ownerUserId` remains the sole public owner field and contains only an opaque internal principal ID.
- Do not add multimodal, Web3, wallet, contract, Monad or on-chain behavior.
- Do not push, merge or deploy publicly.

## Authoritative Inputs

- Design: `docs/superpowers/specs/2026-07-17-ai4c-production-readiness-design.md`
- Goal: `docs/project-management/goals/AI4C_PRODUCTION_READINESS_CODEX_GOAL.md`
- Architecture: `docs/architecture/ARCHITECTURE.md`
- Reuse gate: `docs/architecture/OPENHANDS_REUSE_STRATEGY.md`
- Accepted baseline report: `docs/research/AI4B_GENERAL_QUALITY_SECURITY_RELEASE_REPORT.md`

## Public Interface Freeze

The following remain unchanged through AI4C:

- successful Session, Evidence, Answer and Review request/response shapes;
- ReviewResult and scoring semantics;
- FocusProof Event names and Build Log order;
- OpenHands Message, Action and Observation types;
- Tool action/observation schemas;
- native Conversation and event identifiers.

Allowed HTTP changes are exactly:

```json
{"code":"invalid_token","retryable":false}
```

with status 401 and `WWW-Authenticate: Bearer`, and:

```json
{"code":"forbidden","retryable":false}
```

with status 403. Cross-owner Session resources keep the current non-enumerating
404. No other public change may be implemented without a new AI0 decision.

## OpenHands Direct-Reuse Gate

Before every runtime-affecting task:

- [ ] Record `importlib.metadata.version("openhands-sdk")` and require `1.31.0`.
- [ ] Inspect the installed public API and accepted source for the named behavior.
- [ ] Cite the exact public class/method in the red test or phase report.
- [ ] Use SDK `LLM`, `Agent`, `Conversation`/`LocalConversation`, native state/EventLog/View, native events, Tool APIs and lifecycle directly.
- [ ] If a capability is absent, record version, inspected APIs, exact gap, minimal local policy, tests and deletion condition before implementation.
- [ ] Reject any change that schedules Agent steps or owns native runtime state outside OpenHands.

Approved local boundaries are limited to:

1. passing the public `LocalConversation.max_budget_per_run` option while the
   SDK `Conversation` factory omits it;
2. FocusProof-wide/per-principal provider admission before `arun()`;
3. the accepted bounded blocking URL execution pool;
4. OIDC authorization and FocusProof data/log redaction.

## Stage Dependency and Rollback Index

| Gate | Requires | Deliverable | Rollback point | Hard stop |
| --- | --- | --- | --- | --- |
| AI4C.1 | Accepted AI4C.0 documents | SDK-native bounded real-provider path and report | Pre-AI4C.1 commit; no schema change | AI0 accepts AI4C.1 |
| AI4C.2 | AI0-accepted AI4C.1 | OIDC/VerifiedIdentity, identity migration and report | Pre-identity database/native-store backup plus previous app commit | AI0 accepts AI4C.2 |
| AI4C.3 | AI0-accepted AI4C.2 | Reproducible OCI/PostgreSQL staging and recovery report | Previous compatible image plus paired PostgreSQL/native snapshot | AI0 accepts AI4C.3 |
| AI4C.4 | AI0-accepted AI4C.3 | Final evidence report | No release mutation; earlier-gate repair commit if a red gate proves a defect | AI0 accepts AI4C.4 |

## Phase Plans

- AI4C.1: `docs/superpowers/plans/2026-07-17-ai4c1-real-llm-operations.md`
- AI4C.2: `docs/superpowers/plans/2026-07-17-ai4c2-identity-authorization.md`
- AI4C.3: `docs/superpowers/plans/2026-07-17-ai4c3-reproducible-staging.md`
- AI4C.4: `docs/superpowers/plans/2026-07-17-ai4c4-final-acceptance.md`

### Gate 1: AI4C.1 Real-LLM Operations

- [ ] Verify AI0 accepted this master plan and the AI4C.1 plan.
- [ ] Execute every checkbox in the AI4C.1 plan using TDD.
- [ ] Run the AI4C.1 deterministic gates with provider variables removed.
- [ ] Run a real-provider smoke only when AI0 separately authorizes the exact command.
- [ ] Commit AI4C.1 and its report locally.
- [ ] Stop and send AI0 exact files, commands, counts, usage/cost and residual risks.
- [ ] Do not open or execute the AI4C.2 plan until AI0 accepts AI4C.1.

### Gate 2: AI4C.2 Identity and Authorization

- [ ] Verify AI0 accepted the AI4C.1 commit and explicitly released AI4C.2.
- [ ] Execute every checkbox in the AI4C.2 plan using local issuer/fake key material.
- [ ] Run identity, BFF, runtime attribution and full deterministic gates.
- [ ] Commit AI4C.2, its reversible migration and report locally.
- [ ] Stop and send AI0 exact files, commands, migration evidence and risks.
- [ ] Do not open or execute the AI4C.3 plan until AI0 accepts AI4C.2.

### Gate 3: AI4C.3 Reproducible Staging

- [ ] Verify AI0 accepted the AI4C.2 commit and explicitly released AI4C.3.
- [ ] Run the capability preflight before any Docker or PostgreSQL command.
- [ ] If a required capability is absent, record the blocker and stop; do not mark a skipped environment as passing.
- [ ] Execute the official SDK 1.31.0 equivalence experiment before choosing dependency provenance.
- [ ] If the official package is not equivalent, stop for AI0 approval before building a fixed-commit wheel.
- [ ] Execute every remaining checkbox in the AI4C.3 plan using TDD.
- [ ] Commit AI4C.3 and its report locally.
- [ ] Stop and send AI0 exact clean-host, PostgreSQL, OCI, recovery and rollback evidence.
- [ ] Do not open or execute the AI4C.4 plan until AI0 accepts AI4C.3.

### Gate 4: AI4C.4 Final Acceptance

- [ ] Verify AI0 accepted the AI4C.3 commit and explicitly released AI4C.4.
- [ ] Execute every checkbox in the AI4C.4 plan.
- [ ] Attribute each red acceptance defect to AI4C.1, AI4C.2 or AI4C.3 before changing production code.
- [ ] Run a real-provider command only with separate AI0 authorization.
- [ ] Classify missing real OIDC or external staging evidence as a blocker.
- [ ] Commit the final report locally.
- [ ] Stop for AI0 final acceptance; do not push, merge, deploy or begin another phase.

## Universal Phase Report Template

Every phase report must include these exact headings:

```markdown
## Baseline, Branch, and Commits
## Changed Files
## Red/Green TDD Evidence
## Exact Commands and Results
## OpenHands APIs Reused
## FocusProof-Owned SDK Gaps
## Security and Secret Audit
## Migration and Rollback Evidence
## Remaining Risks and Blockers
## Stop Confirmation
```

`Stop Confirmation` states that no later gate, push, merge, public deployment,
multimodal work or Web3 work occurred.

## Universal Verification Commands

Run from the repository root with provider keys removed:

```bash
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY \
  -u ANTHROPIC_API_KEY \
  -u GOOGLE_API_KEY -u GEMINI_API_KEY -u AZURE_OPENAI_API_KEY \
  -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u LLM_API_KEY \
  .venv/bin/python -m pytest agent-server/tests -q \
  -m "not real_llm and not postgres and not staging_external"
.venv/bin/ruff check agent-server
.venv/bin/mypy agent-server
PATH=/home/holy/.cache/focusproof-node/node-v22.17.0-linux-x64/bin:$PATH \
  npm --prefix frontend run lint
PATH=/home/holy/.cache/focusproof-node/node-v22.17.0-linux-x64/bin:$PATH \
  npm --prefix frontend run typecheck
PATH=/home/holy/.cache/focusproof-node/node-v22.17.0-linux-x64/bin:$PATH \
  npm --prefix frontend run test
git diff --check
```

The marker expression may reference a marker before its phase adds it; the
phase plan first registers the marker, then uses this command.

## Master Completion Check

- [ ] Confirm all five plans contain checkbox steps and exact file ownership.
- [ ] Confirm every behavior-changing task orders red test, observed failure,
  minimal implementation, green test, regression and commit.
- [ ] Confirm all phase transitions stop for AI0.
- [ ] Confirm the repository contains no plan instruction to read `.env`.
- [ ] Confirm no plan authorizes push, merge, public deployment, multimodal or Web3 work.
