# AI4C.2 Identity Authorization Report

## Baseline, Branch, and Commits

- Branch: `ai4c-production-readiness`.
- Task4 accepted baseline: `75a5027f0c34d9ab0e518ba9bf3b05c14ad861c6`.
- Runtime: WSL/Linux, Python 3.12.3, OpenHands SDK 1.31.0, Node.js v22.17.0 from `/tmp/node-v22.17.0-linux-x64/bin`.
- AI4C.2 Task5 implementation commit:
  - `dcf381bd4fc49c57aec9016fe409e5f50c7180a2` - `feat: add minimized security audit retention`.

This report closes the local AI4C.2 identity and authorization gate. It does
not claim public deployment readiness or completion of AI4C.3/AI4C.4.

## Changed Files in Task5

- `.env.example`
- `agent-server/focusproof/api/app.py`
- `agent-server/focusproof/api/auth.py`
- `agent-server/focusproof/api/oidc.py`
- `agent-server/focusproof/config/identity.py`
- `agent-server/focusproof/persistence/models.py`
- `agent-server/focusproof/persistence/repositories.py`
- `agent-server/focusproof/persistence/security_audit.py`
- `agent-server/focusproof/persistence/unit_of_work.py`
- `agent-server/focusproof/runtime/security_audit.py`
- `agent-server/migrations/versions/0003_security_audit_events.py`
- `agent-server/tests/ai4c/test_security_audit.py`
- `agent-server/tests/ai4c/test_identity_authorization.py`
- `agent-server/tests/ai4c/test_identity_end_to_end.py`
- `agent-server/tests/persistence/test_migrations.py`
- `docs/research/AI4C2_IDENTITY_AUTHORIZATION_REPORT.md`

No frontend business code, contracts, scoring, chain logic, OpenHands SDK
source, `.env`, or `var/` path was modified.

## Security Audit Design Evidence

Task5 adds `security_audit_events`, a FocusProof product security query record.
It is not an OpenHands EventLog and cannot schedule Agent steps, execute tools,
restore Conversation state, or replace native OpenHands facts.

Allowed columns are only:

- `id` as the necessary primary key;
- server-generated `request_id`;
- opaque `principal_id`, nullable for authentication-before-success failures;
- HMAC token fingerprint, nullable when no Bearer token exists;
- `outcome`;
- coarse `reason_category`;
- `occurred_at`.

There is no JSON payload, message, body, headers, issuer, subject, claims,
Authorization value, JWKS body, Evidence text, ReviewResult text, Build Log
text, or model output column.

The closed reason categories are:

- `success`
- `missing_credentials`
- `invalid_credentials`
- `forbidden`
- `not_found`
- `dependency_unavailable`
- `internal_failure`

The fingerprint is exactly `hmac.new(key, bearer_token_bytes, sha256).hexdigest()`.
Missing credentials produce `NULL`, not a fingerprint of an empty string.

Staging and production require an exact non-blank HMAC key of at least 32 bytes
and a retention interval from 60 seconds through 7,776,000 seconds. Invalid
configuration fails closed before protected product requests can write product
facts or enter the Conversation runtime.

## Endpoint and Auth Matrix

The Task5 tests cover the protected FocusProof product routes:

- `POST /sessions`
- `POST /sessions/{session_id}/evidence`
- `POST /sessions/{session_id}/answer`
- `POST /sessions/{session_id}/review`
- `GET /sessions/{session_id}`
- `GET /sessions/{session_id}/events`
- `GET /sessions/{session_id}/reviews`

Observed outcomes:

- valid token A success: one security audit row per protected request;
- missing Authorization: `401 {"code":"invalid_token","retryable":false}`,
  `WWW-Authenticate: Bearer`, principal and fingerprint `NULL`;
- malformed/invalid Bearer token: `401`, principal `NULL`, fingerprint only
  when token bytes exist;
- disabled principal: `403 {"code":"forbidden","retryable":false}`, no internal
  `principal_disabled` reason in HTTP or audit;
- owner B against owner A resource and nonexistent resource: identical `404`;
- audit table unavailable: clean `503 {"code":"database_unavailable","retryable":true}`
  before Evidence, Answer, Build Log, Review, or Conversation side effects.

`/health` and `/openhands/capabilities` remain unprotected readiness/capability
routes and do not become audit DB write paths.

## Red/Green TDD Evidence

RED:

```bash
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY \
  LITELLM_LOCAL_MODEL_COST_MAP=true \
  .venv/bin/python -m pytest agent-server/tests/ai4c/test_security_audit.py \
  agent-server/tests/persistence/test_migrations.py -q
```

Initial result: collection failed with
`ModuleNotFoundError: No module named 'focusproof.persistence.security_audit'`.

GREEN:

```bash
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY \
  LITELLM_LOCAL_MODEL_COST_MAP=true \
  .venv/bin/python -m pytest agent-server/tests/ai4c/test_security_audit.py \
  agent-server/tests/persistence/test_migrations.py -q
```

Result: 22 passed, 8 warnings in 8.66s.

Identity/API/OpenHands reuse regression:

```bash
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY \
  LITELLM_LOCAL_MODEL_COST_MAP=true \
  .venv/bin/python -m pytest \
  agent-server/tests/ai4c/test_identity_authorization.py \
  agent-server/tests/ai4c/test_identity_persistence.py \
  agent-server/tests/ai4c/test_identity_end_to_end.py \
  agent-server/tests/ai4c/test_openhands_reuse_boundary.py \
  agent-server/tests/ai4c/test_safe_import_bootstrap.py \
  agent-server/tests/api/test_identity.py \
  agent-server/tests/api/test_api_sessions.py -q
```

Result: 148 passed, 3 warnings in 146.48s.

## Redaction and Leakage Evidence

`test_security_audit.py` uses high-entropy sentinels for token material, JWKS
material, private claims, issuer, subject, HMAC key, Evidence body, and
ReviewResult/model output. The test scans:

- security audit rows;
- product database rows, with `verified_principals` as the only allowed
  issuer/subject mapping boundary;
- captured logs;
- HTTP responses;
- Evidence and ReviewResult product surfaces for identity sentinels;
- Build Log projection;
- OpenHands native state/base_state/tool serialization.

The scan proves raw token, JWKS sentinel, issuer, subject, claims, and HMAC key
do not enter those surfaces. Evidence and ReviewResult text remain legitimate
product data, but are not written to `security_audit_events`.

## Migration and Retention Evidence

Migration `0003_security_audit_events` creates only the minimized audit table
with indexes for retention and lookup. Existing `audit_events`, product tables,
verified principals, and OpenHands native persistence are not rewritten.

SQLite migration tests cover upgrade, downgrade, and re-upgrade. PostgreSQL DDL
compilation includes `security_audit_events`.

Retention is portable across SQLite and PostgreSQL: the repository first
selects up to 64 expired IDs ordered by `(occurred_at, id)`, then deletes those
IDs. It never relies on non-portable `DELETE LIMIT`. Boundary behavior is
tested: rows with `occurred_at < cutoff` delete; rows exactly at cutoff remain.
Restart and concurrent append tests passed.

## OpenHands APIs Reused

Task5 did not modify OpenHands SDK source or add a second runtime/protocol.
The official SDK remains the source for:

- `Agent`;
- `Conversation` and `LocalConversation`;
- native Conversation state and EventLog;
- native Message, Action, and Observation events;
- `ToolDefinition` and `ToolExecutor`;
- SDK `TestLLM` in deterministic tests.

FocusProof-owned additions are limited to OIDC/product authorization, security
audit minimization, HMAC fingerprinting, and retention policy.

## Fifth Independent Review Finding and Repair

The fifth independent review found one remaining P1 gap in the AI4C.2 security
audit boundary: protected requests rejected before route handler execution were
not guaranteed to write a `security_audit_events` row. Specifically,
`RequestBodyLimitMiddleware` could return 413 before identity resolution, and
FastAPI request validation could return 422 before the handler-local success
audit call.

Repair commit `fix: audit protected pre-route failures` closes this as an auth
boundary issue, not a business-success issue. Protected route detection now uses
actual FastAPI `APIRoute.matches(scope)` plus recursive dependency metadata for
the existing `get_verified_identity` dependency; `/health` and
`/openhands/capabilities` remain public and unaudited. The FastAPI dependency,
body-limit middleware, and validation handler all call the same explicit core
identity boundary, which continues to reuse `OidcTokenVerifier`,
`UowPrincipalResolver`, and `SecurityAuditSink` without copying OIDC/JWT logic.

New focused regression evidence:

```bash
.venv/bin/python -m pytest agent-server/tests/ai4c/test_security_audit.py -q
```

Result before the final full gate: 33 passed, 1 warning in 13.89s. The added
cases cover all three body-model protected POST endpoints returning 422,
Content-Length and chunked 413, missing/invalid/disabled credentials taking
401/403 precedence over 422/413, audit-unavailable 503 fail-closed behavior,
protected route metadata coverage, public route non-audit behavior, spoofed
client request IDs, and no product/runtime side effects for denied pre-handler
requests.

## Full Credential-Free Gate

Backend:

```bash
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY \
  LITELLM_LOCAL_MODEL_COST_MAP=true \
  .venv/bin/python -m pytest agent-server/tests -q \
  -m 'not real_llm and not postgres and not staging_external'
```

Result after fifth-review repair: 562 passed, 1 deselected, 16 warnings in
196.91s.

Real-provider contract without live provider call:

```bash
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY \
  LITELLM_LOCAL_MODEL_COST_MAP=true \
  .venv/bin/python -m pytest agent-server/tests/ai4c/test_real_provider.py -q \
  -m 'not real_llm'
```

Result: 3 passed, 1 deselected in 4.41s.

Static checks:

```bash
.venv/bin/ruff check agent-server
.venv/bin/mypy --cache-dir=/tmp/focusproof-mypy-repair-p1 agent-server
.venv/bin/mypy --cache-dir=/tmp/focusproof-mypy-repair-p1 agent-server
git diff --check
```

Results after fifth-review repair: Ruff passed; cold Mypy passed for 139
source files; warm Mypy passed for 139 source files; migration focused tests
passed; diff check passed.

Frontend used Linux Node only:

```bash
export PATH=/tmp/node-v22.17.0-linux-x64/bin:/usr/bin:/bin
npm run lint
npm run typecheck
npm test
npm run build
npm run test:e2e
```

Results after fifth-review repair: lint passed; typecheck passed; Vitest 67
passed across 6 files; Next production build passed; Playwright 16 passed in
1.0m.

Playwright regenerated eight historical AI3 PNG files again; those generated
out-of-scope artifacts were restored exactly to HEAD and were not committed.

## Dependency Audit

Production frontend dependency audit:

```bash
npm audit --omit=dev
```

Result: found 0 vulnerabilities.

Full frontend audit:

```bash
npm audit
```

Result: 5 existing dev-tooling vulnerabilities through `esbuild`, `vite`,
`vite-node`, `@vitest/mocker`, and `vitest` (3 moderate, 1 high, 1 critical).
The suggested remediation requires `npm audit fix --force`, upgrading to
Vitest 4.1.10 as a breaking change. No force fix was applied because this is
unrelated dev tooling churn outside AI4C.2 Task5.

## Anonymous Isolation

Historical `dev-anonymous-user` data is still isolated by Task2 storage path
selection and is not migrated or claimed. Task5 does not add a fake anonymous
security principal. For authentication-before-success failures, `principal_id`
is SQL `NULL`. Opaque principal IDs appear only after successful verified OIDC
resolution.

## Managed vs Self-Hosted OIDC Residual Decision

All Task5 identity evidence used local deterministic RSA/JWKS fixtures and the
real FastAPI verifier/resolver/SQLite path. The local issuer is test evidence,
not managed or self-hosted deployment evidence. A real managed/self-hosted OIDC
deployment decision and external staging proof remain future AI0-authorized
work. This report therefore supports the local AI4C.2 gate only, not
public-launch-ready language.

## Rollback

Rollback for AI4C.2 remains an ordered rollback of the AI4C.2 commits to the
accepted AI4C.1 SHA, with paired database/native persistence handling as
documented in the plan. The Task5 migration downgrade drops only
`security_audit_events`. No migrated learning sessions, Evidence, Reviews,
Build Log projections, verified principal mappings, or OpenHands native events
are manually rewritten.

## Residual Risks

- The strongest OIDC claim remains local deterministic verification; no live
  managed or self-hosted OIDC issuer was exercised.
- The production audit table is intentionally minimized; it supports incident
  correlation, not a full forensic logging platform.
- The dev dependency audit retains known Vitest/Vite/esbuild issues until a
  separately authorized frontend dependency upgrade.
- AI4C.3 staging, PostgreSQL deployment evidence, OCI reproducibility, and
  joint backup/restore remain future gates.

## Stop Confirmation

AI4C.2 Task5 completed local implementation and evidence generation only. No
AI4C.3/AI4C.4 work, push, merge, public deploy, real provider call, contract
change, wallet work, chain work, frontend business change, OpenHands SDK source
change, `.env` read/write, or `var/` mutation was performed.
