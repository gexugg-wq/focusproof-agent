# AI4B General Quality and Security Release Report

Date: 2026-07-17
Baseline: `8c04372`
Branch: `ai4b-general-quality-security-release`
Pre-report HEAD: `90dfe00e7f417388b0cd72fd3140845b1b8cf724`

## Decision

AI4B's deterministic local and isolated-staging quality, security, recovery,
deployment-documentation, and visual gates pass. Public deployment is blocked.
The application still uses the fixed development identity
`dev-anonymous-user`; no production authentication provider, authorization
lifecycle, revocation design, or operator ownership model has been selected.
This report is therefore not a production-launch approval.

No real LLM key was read and the `real_llm` test was not executed. AI4B did not
perform a deployment, Web3 transaction, contract operation, wallet write,
on-chain proof, multimodal flow, AI4C work, push, or merge.

## Baseline and commit chain

The audited chain from `8c04372` to the pre-report HEAD is:

```text
99de9aa docs: freeze AI4B quality acceptance design
c3ea003 docs: plan AI4B quality and release work
9590a21 fix: reject copied goals as learning evidence
3048615 fix: require independent copied-goal support
fa11900 fix: bind evidence specificity to alignment
8c416cd docs: record Q-03 semantic association risk
b5304b1 fix: bound and deduplicate session submissions
81b3692 fix:
04eb01d fix: harden general security boundaries
a7b9a84 fix: preserve finalized session error semantics
d711ad1 test: close AI4B recovery and shutdown gaps
994d50a docs: add AI4B security and deployment runbooks
f4ea096 fix: validate AI4B database path before migration
7f678a0 test: add real BFF browser acceptance
32c82a3 fix: stabilize AI4B Next web server startup
0ca1984 fix: stabilize cold real BFF browser flow
2108481 test: capture AI4B visual acceptance
90dfe00 fix: capture AI4B visuals from production Next
```

Every implementation task was committed independently. No commit was amended,
compressed, pushed, merged, or deployed.

## Direct OpenHands SDK reuse

The verified environment contains `openhands-sdk 1.31.0`. FocusProof directly
constructs the SDK `Agent` with `include_default_tools=[]`, constructs and
persists the SDK `LocalConversation`, and asks `LocalConversation.arun()` to
perform the native orchestration (including the SDK Agent step behavior). The
existing SDK EventLog is read through `conversation.state.events`. FocusProof
tools subclass SDK `ToolDefinition` and `ToolExecutor`; runtime facts are native
`ActionEvent` and `ObservationEvent` instances joined by `tool_call_id`.

Authoritative executable evidence:

- `test_manager_run_uses_native_action_tool_and_observation_flow`
- `test_action_and_observation_projection_preserves_order_and_tool_call_id`
- `test_manager_reuses_same_conversation`
- `test_verification_contract_uses_native_openhands_types`
- `test_focusproof_tool_models_are_native_openhands_types`
- `test_forbidden_default_tools_are_never_assembled`
- `test_completed_review_score_is_owned_by_focusproof`

No second Runtime, Agent loop, EventLog, scheduler, Action/Observation model,
Tool protocol, or programming-tool environment was introduced. FocusProof's
manager, projector, repositories, and scoring remain adapters and product-domain
boundaries around the SDK, not replacements for its orchestration.

## Full gate results

All commands ran in WSL Ubuntu from the repository root. Provider-key variables
were removed from backend and browser-test child environments.

### Backend

| Command | Exact result | Duration |
| --- | --- | --- |
| `.venv/bin/python -m pytest agent-server/tests -q -m "not real_llm"` | `333 passed, 1 deselected, 8 warnings` | pytest `34.11s`; wall `36.5s` |
| `.venv/bin/ruff check agent-server` | `All checks passed!` | `0.02s` |
| `.venv/bin/mypy agent-server` | `Success: no issues found in 126 source files` | `1.40s` |

The deselected node is the explicitly marked real-provider LLM integration.
Warnings were one Starlette `httpx` TestClient deprecation and seven Python 3.12
SQLite datetime-adapter deprecations from SQLAlchemy. They do not hide a failed
assertion, but remain dependency-maintenance work.

Versions: Python 3.12.3; OpenHands SDK 1.31.0; pytest 9.1.1; FastAPI 0.139.0;
Ruff 0.15.21; Mypy 2.2.0 (compiled).

After the Task 8 security-map correction,
`pytest agent-server/tests/ai4b/test_release_artifacts.py -q` was rerun:
`13 passed, 1 warning in 0.96s`.

### Frontend

| Command | Exact result | Duration |
| --- | --- | --- |
| `npm run lint` | ESLint passed | `2.10s` |
| `npm run typecheck` | TypeScript passed | `2.57s` |
| `npm run test` | 5 files, 39 tests passed | Vitest `2.96s`; wall `3.42s` |
| `npm run build` | Next production build passed; 4 routes generated | `22.76s` |
| `npm run test:e2e` | 4 projects, 16 tests passed, 1 worker | Playwright `1.1m`; wall `64.02s` |
| `npm audit --omit=dev` | 0 vulnerabilities | `1.42s` |

Vitest emitted the upstream Vite CJS Node API deprecation notice. Versions:
Linux Node v22.17.0; npm 10.9.2; Next 15.5.18; Vitest 2.1.9; Playwright and
`@playwright/test` 1.61.1. The E2E run used Linux Chromium and no real provider
LLM. AI3 baseline screenshots rewritten by the legacy E2E projects were restored
before the audit.

## Frozen requirement audit

Evidence below names an exact pytest/Vitest/Playwright node, a documented
section, or an accepted screenshot. A passing full gate is the execution record
for each named node.

### End-to-end flow

| ID | Result | Authoritative evidence |
| --- | --- | --- |
| E2E-01 | Pass | Playwright `captures four-viewport geometry through the real Next BFF flow`; `test_general_domain_uses_real_fastapi_and_native_openhands_flow` |
| E2E-02 | Pass | The same real Playwright node submits text plus local-only URL evidence through Next BFF; no success API route is mocked |
| E2E-03 | Pass | `test_manager_run_uses_native_action_tool_and_observation_flow`; real-flow Build Log asserts `Verification requested` |
| E2E-04 | Pass | `test_action_and_observation_projection_preserves_order_and_tool_call_id`; real-flow Build Log asserts requested before completed |
| E2E-05 | Pass | General-domain integration asserts `awaiting_user`, persists the answer, then completes; real browser node exercises the same transition |
| E2E-06 | Pass | General-domain integration asserts persisted ReviewResult and sorted sequence; browser asserts unique sorted sequence through `Review completed` |
| E2E-07 | Pass | Real browser node reloads and reasserts goal, text/URL evidence, completed result, and Build Log |
| E2E-08 | Pass | `test_fastapi_restart_preserves_session_events_and_reviews`; `test_completed_review_restart_preserves_all_persisted_identities` |
| E2E-09 | Pass | Duplicate Evidence/Answer/review nodes in `test_api_security.py`; `test_concurrent_identical_answer_allows_retryable_503_and_safe_retry` |
| E2E-10 | Pass | Parameterized `test_general_domain_uses_real_fastapi_and_native_openhands_flow[programming|mathematics|language|reading]` uses one app/runtime/tool framework |

### Quality behavior

| ID | Result | Authoritative evidence |
| --- | --- | --- |
| Q-01 | Pass | `test_vague_notes_never_receive_high_confidence` |
| Q-02 | Pass, closed | `test_goal_copy_is_not_independent_evidence` plus copied-goal boundary tests in `test_scoring.py`; accepted commits `9590a21`, `3048615`, `fa11900` |
| Q-03 | Pass with residual | `test_goal_evidence_mismatch_is_reported`; see semantic-association limitation below |
| Q-04 | Pass | `test_correct_reflection_can_support_an_error_record` |
| Q-05 | Pass | `test_strong_follow_up_can_improve_support` |
| Q-06 | Pass | `test_elapsed_time_alone_never_proves_learning` |

Q-03 is not evidence of universal semantic understanding. English word overlap
and Chinese character overlap are low-confidence heuristics and cannot prove
semantic alignment. AI4B did not add stop-word lists, character thresholds,
embeddings, or LLM similarity calls. Until real Agent/LLM semantic assessment
is integrated with deterministic scoring boundaries, FocusProof must not claim
reliable detection of every detailed-but-semantically-unrelated false-learning
submission.

### Security

| ID | Result | Authoritative evidence |
| --- | --- | --- |
| SEC-01 | Pass | `test_every_session_derived_endpoint_denies_non_owner_without_state_change` |
| SEC-02 | Pass | `test_user_text_and_llm_claims_cannot_forge_authoritative_runtime_facts` |
| SEC-03 | Pass | The same forgery test plus `test_manager_run_uses_native_action_tool_and_observation_flow`: a claim is not an Observation |
| SEC-04 | Pass | `test_prompt_like_text_stays_user_content_and_sdk_secrets_are_redacted`; forbidden-default-tools and FocusProof-owned-score tests |
| SEC-05 | Pass | `test_policy_blocks_unsafe_targets`, DNS pin/rebinding, redirect revalidation, total-deadline/body-limit tests, and URL-observation redaction nodes |
| SEC-06 | Pass | Vitest `renders a malicious learning goal as text`, malicious review findings/questions, and malicious Build Log labels |
| SEC-07 | Pass | Session/Evidence/Answer bounds plus fixed/chunked oversized-request tests in `test_api_security.py` |
| SEC-08 | Pass | Replay/freeze tests; `test_two_concurrent_reviews_enter_conversation_run_once`; safe concurrent-answer retry test |
| SEC-09 | Pass | `test_schema_out_of_date_is_sanitized`, `test_sqlite_locked_is_sanitized`, runtime creation failure, safe BFF 503/non-JSON tests |
| SEC-10 | Pass | Vitest `forwards only content-type and never returns environment or fetch errors`; `test_check_uses_argument_arrays_and_removes_provider_keys` |
| SEC-11 | Pass | Release-artifact text scanner, placeholder test, smoke-output test, binary screenshot sentinel scan, and original-detail visual inspection |

The corrected detailed mapping is in `docs/security/SECURITY_ACCEPTANCE.md`.

### Reliability and recovery

| ID | Result | Authoritative evidence |
| --- | --- | --- |
| REL-01 | Pass | `test_upgrade_downgrade_and_reupgrade`; Operations sections `SQLite backup`, `Restore`, and `Rollback` |
| REL-02 | Pass | `test_restart_restores_native_history_without_duplicate_product_rows`; completed identity-preservation and FastAPI-restart nodes |
| REL-03 | Pass | Timeout, cancelled-request, HTTP-disconnect, cancellation-before-restore, and retry nodes in `test_reliability.py` |
| REL-04 | Pass | `test_two_concurrent_reviews_enter_conversation_run_once`; lock-timeout and shared-lock write tests |
| REL-05 | Pass | `test_reviews_for_different_sessions_enter_native_runs_concurrently` |
| REL-06 | Pass | OperationalError rollback/retry, schema-out-of-date, and SQLite-locked sanitization tests |
| REL-07 | Pass | `test_llm_exception_before_tool_call_can_retry_without_false_completion` |
| REL-08 | Pass | `test_structured_verification_failure_never_completes_review`; URL failure Observation tests |
| REL-09 | Pass | Vitest rejected Evidence/Answer input-preservation tests, Build Log recovery test, and safe BFF network-failure tests |
| REL-10 | Pass | Shutdown rejection/interrupt/close nodes and provider-registry/engine exactly-once release nodes |

### Deployment deliverables

| ID | Result | Authoritative evidence |
| --- | --- | --- |
| DEP-01 | Pass | `docs/deployment/LOCAL_WSL.md`: Prerequisites, migrations, deterministic server, frontend, health/shutdown, verification |
| DEP-02 | Pass | `docs/deployment/STAGING.md`: topology, preconditions, environment, proxy boundary, smoke, rollout/rollback |
| DEP-03 | Pass | `docs/deployment/OPERATIONS.md`: health, redacted logs, backup/restore/rollback, diagnosis, monitoring, incidents |
| DEP-04 | Pass | `docs/security/THREAT_MODEL.md`: assets, boundaries, actors, controls, abuse cases, residual risks, public gate |
| DEP-05 | Pass | Corrected `docs/security/SECURITY_ACCEPTANCE.md` SEC-01..11 executable/manual mapping |
| DEP-06 | Pass | `test_test_server_is_loopback_only_and_reuses_production_runtime`, pre-migration path rejection, loopback smoke, argument-array/key-removal tests |
| DEP-07 | Pass | `test_env_example_contains_only_placeholders_for_sensitive_names` |
| DEP-08 | Blocked as required | `test_docs_preserve_public_release_identity_blocker`; no production identity/provider design exists |

### Visual acceptance

| ID | Result | Authoritative evidence |
| --- | --- | --- |
| VIS-01 | Pass | Four-project real-flow geometry asserts body width, positive/in-viewport panel bounds, sibling separation, and long-content wrapping |
| VIS-02 | Pass | 360x800 deterministic failed Evidence POST keeps the exact textarea value; Vitest Evidence/Answer rejection tests cover both forms |
| VIS-03 | Pass | Accessible `awaiting_user`, `completed`, and `failed` Vitest nodes; accepted awaiting/completed/failure captures |
| VIS-04 | Pass | Browser asserts unique sorted Build Log sequences; native projection test proves matching Action precedes Observation |
| VIS-05 | Pass | Real completion asserts wallet controls absent; Proof Recording remains disabled and is not a prerequisite |
| VIS-06 | Pass | Tracked-text scan, PNG binary sentinel scan, and original-detail inspection found no credential, environment value, or raw secret fixture |

Accepted full-page production captures:

- [1440x900 completed](assets/ai4b/1440x900-completed.png), PNG 1440x1529
- [1280x720 completed](assets/ai4b/1280x720-completed.png), PNG 1280x1641
- [390x844 awaiting user](assets/ai4b/390x844-awaiting-user.png), PNG 390x2406
- [360x800 failed input preserved](assets/ai4b/360x800-failed-input-preserved.png), PNG 360x1367

The images come from `next build` plus `next start` and the real deterministic
Next BFF -> FastAPI -> OpenHands flow. They contain no Next development portal,
cropping, DOM removal, or image post-processing. Original-detail inspection
found no overlap, clipped required control, horizontal overflow, framework
overlay, or secret. The failure capture alone uses the already accepted
single-response failure interception; no public debug endpoint exists.

## Red/green repair record

- Q-02 first exposed copied-goal false positives; three bounded TDD commits
  added normalization, independent-information safeguards, per-evidence
  specificity/alignment, and conservative caps without an LLM similarity call.
- Task 2 exposed unbounded and duplicate facts, then reviewed-session mutation;
  persistence-backed replay and the finalized fact-freeze contract closed them.
- Task 3 exposed owner, forgery, SSRF, XSS, BFF, and recovery gaps; Task 3.1
  preserved permanent `session_finalized` semantics instead of mislabeling it
  retryable busy.
- Task 4 added failure, cancellation, concurrency, restart, and shutdown barriers;
  production changes were limited to failures demonstrated by those tests.
- Task 5 added operational artifacts and corrected database-path validation to
  run before migrations could create an out-of-directory file.
- Task 6 established the real BFF browser harness; cold Next startup and bounded
  navigation waits were repaired without fixed sleeps.
- Task 7 added four-viewport geometry and visual evidence; production `next
  start` capture removed development chrome without DOM/image post-processing.
- Task 8 found no runtime defect. It corrected only the SEC-03..11 evidence-ID
  mapping in the security acceptance document.

## Changed scope

`git diff --name-status 8c04372...90dfe00` was reviewed. Changes are confined to:

- bounded API/persistence/scoring/runtime adapters and their tests under
  `agent-server/`;
- security, deployment, AI4B planning/reporting, and accepted AI4B visual files
  under `docs/`;
- BFF recovery, accessible states, the real/visual Playwright harness, tests,
  and the minimal Next security override under `frontend/`;
- deterministic loopback server, smoke, check helper, and script documentation
  under `scripts/`.

There are no changed files under `contracts/`, `var/`, OpenHands SDK source,
wallet/contract code, `.env`, or the AI3 screenshot baseline. No `test-results`,
trace, temporary database, `.next`, provider response, or secret is tracked.

## Dependency audit

`npm audit --omit=dev` reports 0 vulnerabilities after the accepted minimal
Next override to 15.5.18; no `--force` or Next downgrade was used. Python static
gates pass. The two deprecation groups reported above are maintenance risks,
not ignored failures.

The repository's pre-existing Python dependency declaration references a local
OpenHands SDK source path even though the verified environment contains SDK
1.31.0. A clean deployment host must receive a reproducible approved SDK
artifact/source location before deployment packaging; AI4B did not alter that
baseline dependency mechanism.

## Remaining risks and deployment blockers

1. Public deployment is blocked by the development-only anonymous identity.
   Production authentication, authorization, revocation, audit ownership, and
   a provider choice require a separate AI0-approved design.
2. Q-03 semantic association remains low confidence. The system must not claim
   that it reliably rejects every detailed but semantically unrelated input.
3. Real-provider behavior, cost, latency, rate limiting, and provider-specific
   failure modes were not exercised because no real LLM test or key was used.
4. SQLite/file locking is suitable for the accepted local boundary, not proof
   of horizontally scaled multi-host concurrency.
5. Screenshot review is not full WCAG, keyboard, screen-reader, contrast, or
   zoom certification.
6. Dependency deprecations and the local SDK source declaration require
   maintenance before a reproducible production package can be claimed.

AI4B stops here for AI0 final acceptance. This report does not authorize Task
AI4C, Web3 specialization, multimodal work, deployment, merge, or push.
