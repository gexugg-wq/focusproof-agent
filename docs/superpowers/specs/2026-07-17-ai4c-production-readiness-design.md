# AI4C Production Readiness Design

Status: Draft for AI0 review
Date: 2026-07-17
Owner: AI0 architecture and acceptance, AI4C implementation
Baseline: `4f7378159f9ea7476c3c1f7fad6dbc31b9301af5`
Branch: `ai4c-production-readiness`
Accepted AI4B baseline: `bf5c9a8`
Observed OpenHands SDK: `1.31.0`

## 1. Purpose

AI4C turns the accepted text/URL FocusProof learning-verification MVP into a
controlled, reproducible staging-ready service. It introduces production-grade
real-provider policy, verified identity and authorization, reproducible
dependencies, PostgreSQL validation, operations evidence, and final acceptance.

FocusProof remains a domain-general learning-verification product. AI4C does
not add a learning domain, change what counts as learning, or replace the
official OpenHands runtime.

The selected delivery strategy is a strictly sequential vertical closure:

```text
AI4C.0 design and acceptance matrix
  -> AI4C.1 real-LLM operations
  -> AI4C.2 identity and authorization
  -> AI4C.3 reproducible staging
  -> AI4C.4 final production-readiness acceptance
```

Each numbered gate ends with local verification, a phase-specific commit and
an AI0 review. No later gate begins before AI0 accepts the current gate.

## 2. Approved Decisions

The design freezes these decisions:

- DashScope is the first real-provider acceptance instance, not an architecture
  dependency.
- DashScope is called through the OpenHands SDK `LLM`/LiteLLM public integration
  and an OpenAI-compatible endpoint. FocusProof does not create a DashScope or
  OpenAI HTTP client.
- The FastAPI OIDC bearer-token verifier is the only authoritative application
  identity boundary.
- The application remains OIDC-provider-neutral. Managed and self-hosted
  standards-compliant OIDC are compared, but FocusProof does not create a
  password system.
- Historical `dev-anonymous-user` data is isolated and retained. It is never
  claimed automatically by an authenticated user.
- The first reproducible staging target is a vendor-neutral OCI/Compose stack
  on one Linux host with one FastAPI worker.
- PostgreSQL product data and OpenHands native persistence are backed up and
  restored as one versioned recovery unit.
- Existing OpenHands public runtime behavior is reused directly. Product policy
  additions remain outside Conversation orchestration.
- The only allowed product HTTP-boundary changes are authentication-related
  `401`, `403` and existing non-enumerating `404` behavior.
- Event, Action, Observation, Tool and Review protocols do not change in AI4C.

## 3. Non-Goals

AI4C must not:

- add image, OCR, audio, ASR, video, PDF or other multimodal evidence;
- add Monad RPC, wallets, transactions, contracts or on-chain proof;
- rewrite scoring or claim universal semantic understanding;
- create a second Agent, Conversation, EventLog, View, Action, Observation,
  Tool runtime, scheduler or agent loop;
- enable OpenHands default programming tools;
- adopt a multi-region or multi-host runtime architecture;
- read or print the current `.env` file;
- run a real-provider smoke without explicit AI0 authorization;
- perform a public deployment, push or merge.

Default and CI tests must not read or consume a real provider key. Configuration
documentation and tests use `.env.example` names and explicit fake values only.

## 4. Alternatives and Selected Strategy

### 4.1 Program delivery

| Strategy | Benefit | Cost | Decision |
| --- | --- | --- | --- |
| Sequential vertical gates | Clear TDD ownership, small rollback surface, direct alignment with AI4C.1-4 | Full staging proof arrives in AI4C.3 | Selected |
| Infrastructure first | Exposes packaging and PostgreSQL issues early | Freezes deployment before provider and identity contracts | Rejected |
| One production-stack change | Short apparent schedule | Unreviewable failure attribution and unsafe rollback | Rejected |

### 4.2 Real-provider integration

| Approach | Benefit | Cost | Decision |
| --- | --- | --- | --- |
| SDK `LLM` with backend provider-neutral configuration | Smallest change; preserves current Conversation path; no duplicate provider client | One configured provider per runtime profile | Selected |
| SDK `LLMRegistry`/profile store/`RouterLLM` | Supports runtime routing and multiple profiles | Adds dynamic configuration and fallback surface not required by first acceptance | Valid future option, not selected |
| OpenAI-compatible model gateway called through SDK `LLM` | Central provider policy and quotas | Adds a service and trust boundary | Valid future option, not selected |

A FocusProof-owned provider HTTP client is prohibited rather than treated as a
fourth approach.

### 4.3 Identity provider

| Approach | Benefit | Cost | Decision |
| --- | --- | --- | --- |
| Managed standards-compliant OIDC | Mature signing-key rotation, MFA, recovery and security operations | External dependency and cost | Recommended first real staging candidate |
| Self-hosted standards-compliant OIDC | Greater policy and data control | FocusProof operators own upgrades, keys, MFA, backup and incident response | Supported alternative |
| Custom password or token issuer | No external identity dependency | Creates an unsafe identity product | Prohibited |

Deterministic and isolated staging tests may use a reproducible local OIDC test
issuer. That fixture is not evidence of a real managed or self-hosted identity
deployment.

### 4.4 Anonymous data

| Approach | Security | Decision |
| --- | --- | --- |
| Separate local-dev data domain; retain but do not claim | No unprovable ownership transfer | Selected |
| Explicit offline operator mapping with an audited manifest | Can support a future controlled import | Deferred and requires separate AI0 approval |
| First-login automatic claim | Any user could claim data without proof | Prohibited |

### 4.5 Staging carrier

| Approach | Benefit | Cost | Decision |
| --- | --- | --- | --- |
| OCI images plus vendor-neutral Compose | Reproducible clean-host build and simple recovery drill | Explicitly single-host | Selected |
| Hand-built venv plus systemd | Similar to current WSL workflow | Depends on host state and is hard to reproduce | Rejected for acceptance |
| Kubernetes | Supports distributed operations | Adds scheduling and network scope without a product need | Out of scope |

## 5. Target Architecture and Trust Boundaries

```text
Browser
  -> external OIDC provider obtains access token
  -> same-origin Next BFF forwards an allowlisted Bearer header
  -> FastAPI validates token and produces VerifiedIdentity
  -> API / repositories / ConversationManager enforce ownership
  -> OpenHands Agent + LocalConversation
  -> OpenHands LLM/LiteLLM -> configured provider endpoint
  -> native ActionEvent
  -> SDK ToolDefinition / ToolExecutor with server-bound identity
  -> native ObservationEvent
  -> native EventLog/View
  -> FocusProof scoring and ReviewResult
  -> product persistence and audit projection
```

Trust boundaries are:

1. The browser and external identity provider. The browser may possess an
   access token but never an LLM credential.
2. The BFF and FastAPI boundary. The BFF may forward an `Authorization` header
   but may not issue, rewrite or interpret identity.
3. The FastAPI verifier. Only it converts token claims into
   `VerifiedIdentity`.
4. FocusProof product policy. Repositories, Manager and tools enforce ownership
   from the verified identity.
5. The OpenHands runtime. The SDK owns Agent steps, Conversation state,
   EventLog/View, lifecycle and native tool events.
6. The external LLM provider. Model content is untrusted until validated by
   native tool schemas and deterministic product boundaries.
7. PostgreSQL and the OpenHands persistence volume. They form one recovery
   unit but retain distinct product and runtime ownership.

OpenHands EventLog remains runtime truth. FocusProof database events remain an
idempotent product/audit projection rather than a parallel agent ledger.

## 6. AI4C.1 Real-LLM Operations

### 6.1 Provider-neutral configuration

Backend-only validated configuration defines:

- runtime profile;
- provider alias;
- model ID;
- OpenAI-compatible base URL;
- API credential;
- request timeout;
- retry count and bounded backoff;
- maximum input and output tokens;
- maximum Conversation iterations;
- maximum review duration;
- maximum concurrent real-provider reviews;
- maximum calls and cost for a review and smoke test;
- explicit input and output token prices.

DashScope supplies the first values for this contract. Later providers may
change configuration only. They do not change Conversation, native events,
ReviewResult or product API shapes.

Staging and production profiles read controlled process environment or mounted
secret sources. They do not load project `.env`. Provider, base URL, model and
credential are never sent to browser code or ordinary API responses.

### 6.2 SDK execution path

AI4C.1 directly configures these OpenHands SDK 1.31.0 capabilities:

- `LLM` and its LiteLLM integration;
- `LLM` timeout, retry/backoff, token bounds and retry listener;
- `LLM.metrics` and Conversation statistics for calls, tokens, cost and latency;
- `Agent` with its native tool-concurrency limit;
- `Conversation`/`LocalConversation` iteration, persistence and lifecycle;
- native callbacks and observability metadata;
- SDK tool-call schema validation;
- `TestLLM` for deterministic tests.

Default programming tools stay disabled.

LiteLLM must use a local, approved model-cost map or explicitly configured
prices. Import or runtime startup must not fetch a mutable remote price map.
Completion logging remains disabled.

### 6.3 Bounded execution

Bounds are cumulative rather than interchangeable:

- token limits bound the maximum first-call cost;
- iteration and call limits bound a Conversation run;
- SDK retries handle only named transient provider errors;
- the existing review timeout interrupts the native Conversation;
- a product admission policy bounds concurrent paid work;
- the public `LocalConversation` budget guard reads SDK Conversation statistics
  to stop additional calls at the approved ceiling, while SDK metrics report
  calls, tokens, cost and latency.

Product admission runs before entering the Conversation. It is a policy guard,
not a scheduler, second event loop or Conversation replacement.

Provider fallback is disabled for the first acceptance. A DashScope failure
must not silently use another provider or a deterministic model.

### 6.4 Failure semantics

- Missing or invalid real-provider configuration makes staging/production
  unready or prevents startup.
- A provider outage, timeout or malformed response never creates a completed
  ReviewResult or a successful tool Observation.
- A model statement claiming tool success remains untrusted message content.
- Invalid tool arguments are rejected through the native SDK schema.
- Safe retry resumes through the existing Session lock and persisted
  Conversation; it does not append duplicate answers, events or reviews.
- Provider errors expose stable safe codes and retryability, never response
  bodies, credentials, endpoint details or prompt content.

### 6.5 Real-provider acceptance

The real-provider test is marked explicitly and refuses to run unless its exact
marker/command is selected. It records bounded calls, tokens, cost, duration and
native event counts without printing prompts, evidence, answers, completions or
environment values.

Only AI0 may authorize the command. Default and CI commands remove provider
credentials from child environments.

## 7. AI4C.2 Identity and Authorization

### 7.1 Authoritative verifier

The FastAPI OIDC dependency or middleware:

1. accepts only `Authorization: Bearer`;
2. uses configured HTTPS issuer, audience, JWKS URL and allowed algorithms;
3. retrieves JWKS with a bounded timeout and cache;
4. validates signature, issuer, audience, expiry and not-before;
5. maps the verified `(issuer, subject)` pair to an internal opaque principal;
6. checks local disable/revocation state;
7. emits an immutable `VerifiedIdentity`.

Request body, query, cookie, ordinary reverse-proxy identity headers, OpenHands
`MessageEvent.sender`, LLM output and client-supplied user IDs are never
authorization facts.

Repositories, Manager, Conversation creation and tool executors receive the
server-created identity. They do not parse tokens. OpenHands `user_id` and
message sender preserve verified attribution for auditing, but cannot grant
access.

### 7.2 Identity data model

An identity mapping record contains:

- opaque internal `principal_id`;
- normalized issuer;
- provider subject;
- active or disabled state;
- creation and state-change timestamps.

The `(issuer, subject)` pair is unique. Raw access tokens and token claims are
not persisted.

`ownerUserId` is an existing API field. It remains for compatibility but may
contain only the opaque internal principal ID. It must never expose issuer,
subject, access token or token claims. AI4C does not introduce a second public
identity field.

Historical anonymous data stays in a separate local-dev database and
Conversation directory. Staging and production never share those paths. No
authenticated user can claim the historical data through a public API.

### 7.3 BFF role

The browser uses the OIDC Authorization Code flow with PKCE. Access tokens are
kept in memory by the approved OIDC client and are not written to local storage,
recent-session storage or a client-readable cookie. Reauthentication or the
OIDC client's standards-compliant renewal behavior restores a browser session.

The BFF adds `Authorization` to its explicit forwarding allowlist. It must not:

- sign or refresh identity assertions for FastAPI;
- derive a principal;
- forward cookies as authorization;
- forward arbitrary identity or provider headers;
- return token content in errors or logs.

### 7.4 HTTP semantics

The only allowed product HTTP-boundary changes are:

- missing, malformed or invalid authentication: `401` with
  `{"code":"invalid_token","retryable":false}` and
  `WWW-Authenticate: Bearer`;
- authenticated but revoked or explicitly forbidden identity: `403` with
  `{"code":"forbidden","retryable":false}`;
- access to another principal's Session or derived resource: the existing
  non-enumerating `404` response, unchanged.

The BFF preserves these statuses and does not turn permanent authentication
failure into a retryable network error.

### 7.5 Token and audit exclusion

OIDC tokens, JWKS documents and token claims must never enter:

- OpenHands EventLog;
- FocusProof Build Log;
- learning Evidence;
- ReviewResult;
- prompt or tool arguments;
- ordinary structured logs.

Security audit records retain only the minimum necessary opaque principal,
request identifier, decision, reason category and a non-reversible token
fingerprint when replay investigation requires it. The fingerprint is a keyed
HMAC made with a separately managed, rotatable audit key and has a bounded
retention period; it is not a plain token hash. Audit storage never retains the
raw token.

## 8. AI4C.3 Reproducible Staging

### 8.1 Runtime topology

The accepted first topology is:

```text
TLS/reverse proxy
  -> Next production container
  -> FastAPI container, exactly one worker
  -> PostgreSQL
  -> mounted OpenHands persistence volume
  -> external OIDC provider or approved test issuer
  -> DashScope or deterministic TestLLM according to the explicit profile
```

Single worker is a declared limitation. Existing file-backed Conversation
persistence and Session file locks do not prove multi-host correctness.

### 8.2 Reproducible OpenHands dependency

AI4C.3 first performs a controlled equivalence experiment:

1. install the official `openhands-sdk==1.31.0` release package on a clean Linux
   Python 3.12 host;
2. compare imported public classes, signatures and behavior used by FocusProof
   against the accepted source/API;
3. run the OpenHands reuse, Conversation, native event, tool, recovery and full
   deterministic FocusProof suites;
4. record package provenance and hashes.

If the official package is equivalent, it becomes the exact hash-locked
dependency. If it cannot reproduce the accepted behavior, AI4C stops and asks
AI0 to approve a fixed source commit. Only after that approval may a wheel be
built from that commit and stored as a SHA-256-addressed artifact.

Mutable branches, mutable tags, developer-local absolute paths and unverified
wheel files are prohibited. The design does not assume in advance that a
custom wheel is necessary.

### 8.3 PostgreSQL validation

SQLite remains a local deterministic test backend. Staging uses PostgreSQL and
must prove:

- Alembic upgrade from an empty database to head;
- application startup checks the exact head and never migrates implicitly;
- JSON, timezone datetime, foreign-key cascade and unique constraints behave
  as required;
- duplicate Evidence, Answer, Review and audit projection constraints remain
  effective;
- transaction rollback after OperationalError produces no false success;
- identity migration is reversible under the documented rollback boundary;
- downgrade and re-upgrade operate on a disposable restored copy;
- restart and recovery preserve the same Conversation, native event IDs,
  projection sequences and Review IDs.

AI4C does not claim multi-host review concurrency from PostgreSQL compatibility.

### 8.4 Configuration and secrets

Profiles are explicit: deterministic-test, local-dev, staging and production.

- deterministic-test uses SDK `TestLLM`, isolated temporary data and no real
  identity or provider credential;
- local-dev may explicitly enable `dev-anonymous-user`;
- staging and production require valid OIDC, provider, PostgreSQL and persistent
  data configuration and fail closed when any required value is missing;
- no staging/production profile falls back to anonymous or TestLLM;
- secrets are injected at runtime and never baked into images, lock files,
  reports, screenshots or client bundles.

Documentation refers only to `.env.example` placeholder names. Implementation
and tests must not inspect or report the existing `.env` contents.

### 8.5 Health, logging and metrics

- Liveness reports only that the process can serve.
- Readiness validates schema revision, database connection, required profile
  configuration and writable native persistence.
- Metrics and detailed readiness are restricted to an operator boundary.
- Structured logs use request ID, opaque principal ID, Session ID, status,
  latency and safe failure categories.
- LLM metrics include aggregate call, retry, token, cost and latency values.
- Logs exclude tokens, claims, credentials, provider response bodies, prompts,
  evidence, answers, raw URLs and environment values.

FocusProof logging is product policy. It does not wrap or replace Conversation
observability.

### 8.6 Joint backup, restore and rollback

Before backup or migration:

1. reject new reviews;
2. interrupt or wait for admitted runs according to shutdown policy;
3. record application commit and Alembic revision;
4. back up PostgreSQL;
5. snapshot the OpenHands persistence volume;
6. write a manifest of Conversation IDs and file hashes.

Restore uses the matching pair. The application verifies the manifest, starts
against the recorded compatible revision, restores native Conversations and
reconciles projections idempotently. Acceptance proves no duplicate native
event, Evidence, Answer or Review.

Rollback restores the previous compatible application image and, when schema
compatibility cannot be maintained, the paired pre-migration database and
Conversation snapshot.

## 9. AI4C.4 Final Acceptance and Readiness Language

AI4C.4 combines:

- complete deterministic backend, frontend and real-BFF browser regression;
- explicitly authorized DashScope acceptance with call, token, cost and secret
  accounting;
- OIDC abuse, cross-owner, replay and revocation acceptance;
- clean-host OCI/Compose staging deployment;
- PostgreSQL migration and joint recovery drill;
- keyboard, focus, zoom and automated accessibility baseline;
- dependency, secret, log, artifact and repository hygiene audits.

The final report uses pass, fail and blocked status without inference.

If acceptance uses only a local test issuer, does not connect a real managed or
self-hosted OIDC deployment, or does not exercise actual staging external
services, the strongest permitted result is **staging-ready with blockers**.
It must not claim **public-launch-ready**. Public deployment remains a separate
AI0 authorization even if all AI4C gates pass.

## 10. OpenHands Direct-Reuse Audit

### 10.1 APIs reused

Each phase report names the inspected and used public APIs. The expected set is:

- SDK `LLM`, LiteLLM integration, retry, timeout, metrics and token controls;
- SDK `Agent` and native step behavior;
- `Conversation` and `LocalConversation`;
- `ConversationState`, native EventLog and View;
- Message, Action, Observation and lifecycle events;
- `Tool`, `ToolDefinition`, `ToolExecutor` and registration;
- callbacks, observability metadata, pause, interrupt, close and restore;
- SDK `TestLLM`.

FocusProof must not copy SDK source, mutate private state or add equivalent
runtime semantics.

### 10.2 FocusProof-owned gaps and policy boundaries

| Boundary | Why it is local | Minimum implementation | Removal condition |
| --- | --- | --- | --- |
| Conversation cost configuration | SDK 1.31.0 `LocalConversation` exposes `max_budget_per_run`, but its `Conversation` factory does not forward it | Use the public `LocalConversation` capability through the smallest constructor/configuration adapter; do not copy run logic | Return to the factory when it exposes the public option |
| Provider admission | SDK bounds an LLM/run but does not enforce FocusProof-wide or per-principal admission | A policy guard before `Conversation.arun`, using existing request/run lifecycle | Remove if the SDK provides an equivalent public admission API |
| Bounded blocking URL execution | Already accepted application gap for process-wide blocking I/O | Retain the existing bounded pool only for URL work | Remove when an SDK public executor supplies the same bound |
| Identity and product-log redaction | OpenHands does not own FocusProof authorization or product data classification | OIDC verifier, ownership checks and minimal structured redaction at product boundaries | Keep as product policy; delete only overlapping helpers if SDK gains equivalent public utilities |

Provider admission, identity and logging are policy components. They may not
schedule Agent steps, own Conversation state or create a second runtime.

PostgreSQL projections, OIDC mappings and dependency packaging are product or
deployment responsibilities rather than SDK gaps.

## 11. Public Interface and Protocol Freeze

AI4C keeps unchanged:

- successful Session, Evidence, Answer and Review request/response shapes;
- ReviewResult and scoring semantics;
- FocusProof Event types and Build Log ordering;
- OpenHands Action and Observation types;
- Tool action and observation schemas;
- Conversation and native event identity.

The permitted HTTP changes are exactly those in section 7.4. `ownerUserId`
continues as the only public owner field and contains only an opaque principal
ID. No identity data is added to Event, Action, Observation, Tool or Review
protocols.

Any other public interface change requires a separate AI0 decision before code
or protocol documentation changes.

## 12. Threat Model

| Threat | Required control | Failure result |
| --- | --- | --- |
| Forged, expired or confused token | Signature, issuer, audience, expiry, not-before and algorithm validation | `401`, no product or runtime mutation |
| JWKS substitution, SSRF or outage | Fixed HTTPS configuration, bounded fetch, cache and fail-closed refresh | `401` or unready, no anonymous fallback |
| BFF identity forgery | Bearer-only allowlist; discard cookie and identity headers | Safe authentication failure |
| Cross-owner access | API, repository, Manager and tool ownership checks | Non-enumerating `404` |
| Revoked identity | Principal state check on every request | `403`, no runtime entry |
| Token leakage | Token exclusion and non-reversible audit fingerprint | Redacted log and security alert |
| Prompt injection | Native tool allowlist, authoritative Evidence lookup and scoring boundary | No privileged event or verdict |
| LLM tool-fact forgery | Only matching native Observation is authoritative | Claim remains untrusted text |
| Provider outage or malformed output | SDK schema, timeout, bounded retry and no fallback | Failed/uncompleted Review |
| Cost or concurrency abuse | Token, iteration, call, retry, cost and admission limits | Safe rate/budget failure |
| Duplicate work after retry | Session lock, persisted idempotency and native recovery | One logical fact/result |
| Migration partial failure | Pre-migration paired backup and transactional migration | Roll back; no false readiness |
| Database/native-store divergence | Quiesced joint snapshot, manifest and reconcile | Readiness failure until repaired |
| Anonymous profile in staging | Explicit profile validation | Startup/readiness failure |

## 13. Phase Ownership and Acceptance Matrix

### 13.0 Exact file ownership map

The paths below are the complete permitted ownership envelope for each gate.
Existing files are modified only when a red test proves the need. New files use
the listed directory and purpose. Because gates are sequential, ownership may
transfer between gates but no two gates edit a file concurrently.

| Gate | Existing files permitted | New files/directories permitted |
| --- | --- | --- |
| AI4C.1 | `.env.example`; `agent-server/focusproof/config/env.py`; `agent-server/focusproof/openhands_adapter/llm_config.py`; `agent-server/focusproof/openhands_runtime/factory.py`; narrowly `agent-server/focusproof/openhands_runtime/manager.py` and `agent-server/focusproof/api/app.py`; existing LLM/runtime tests | `agent-server/focusproof/config/profiles.py`; `agent-server/tests/ai4c/test_llm_operations.py`; `agent-server/tests/ai4c/test_real_provider.py`; `docs/research/AI4C1_REAL_LLM_OPERATIONS_REPORT.md` |
| AI4C.2 | `.env.example`; `pyproject.toml`; `frontend/package.json`; `frontend/package-lock.json`; `agent-server/focusproof/api/auth.py`; `agent-server/focusproof/api/app.py`; persistence models, repositories, providers and UoW; Manager, factory, tool assembler and evidence-tool repository interfaces; `frontend/app/api/focusproof/[...path]/route.ts`; narrowly affected frontend providers/layout and existing security tests | `agent-server/focusproof/api/oidc.py`; `agent-server/focusproof/config/identity.py`; one `0002` identity Alembic revision; `agent-server/tests/ai4c/test_identity_authorization.py`; `frontend/lib/auth/`; focused frontend identity tests; `docs/research/AI4C2_IDENTITY_AUTHORIZATION_REPORT.md` |
| AI4C.3 | `.env.example`; `pyproject.toml`; `alembic.ini`; database/schema-check/config/app modules; migration and persistence tests; `scripts/README.md`; `docs/deployment/`; `docs/security/` | `requirements/production.lock`; `deploy/agent-server.Dockerfile`; `deploy/frontend.Dockerfile`; `deploy/compose.staging.yml`; safe AI4C staging/check/backup/restore scripts under `scripts/`; PostgreSQL, clean-host and recovery tests under `agent-server/tests/ai4c/`; `docs/research/AI4C3_REPRODUCIBLE_STAGING_REPORT.md` |
| AI4C.4 | Existing backend/frontend/E2E acceptance tests; an earlier-gate production file only for a named red acceptance defect | AI4C final backend/security tests under `agent-server/tests/ai4c/`; frontend accessibility/E2E tests; `docs/research/AI4C_PRODUCTION_READINESS_REPORT.md`; accessibility evidence under `docs/research/assets/ai4c/` |

AI4C.1-4 do not own `contracts/`, `.env`, `var/`, OpenHands SDK source,
multimodal modules, wallet/chain modules, the task board, AI0 goal files or
public protocol documents.

### 13.1 AI4C.1 ownership

Allowed files are provider configuration, the existing LLM/Conversation
construction boundary, narrowly affected Manager/API readiness code,
`.env.example` placeholder names, necessary dependency declarations, AI4C.1
tests and its report. Frontend, persistence schema and public protocols are not
owned by AI4C.1.

Required red/green coverage:

- invalid and missing configuration;
- deterministic child environments with provider keys removed;
- SDK `LLM` construction for a provider-neutral DashScope fixture;
- timeout, retry exhaustion, malformed tool call and outage;
- no false Observation or completed Review;
- call, token, cost, duration and concurrency limits;
- log and error redaction;
- explicitly authorized real-provider native flow.

Stop after the phase commit, report and AI0 review. Rollback removes provider
configuration/runtime policy changes; AI4C.1 has no schema migration.

### 13.2 AI4C.2 ownership

Allowed files are FastAPI auth/OIDC modules, narrowly affected API,
repositories, identity models and migration, VerifiedIdentity propagation into
Manager/Conversation/tools, the BFF Authorization allowlist, necessary frontend
OIDC integration, identity tests and its report.

Required red/green coverage:

- valid token and every required claim validation;
- invalid signature, algorithm, issuer, audience, expiry and not-before;
- JWKS cache, rotation and failure;
- request/body/query/cookie/header/sender/LLM identity forgery;
- BFF allowlist and safe errors;
- every cross-owner Session-derived endpoint;
- revoked identity and replay;
- server-bound tool identity and authorized Evidence lookup;
- local-dev isolation and staging/production fail-closed behavior;
- no automatic anonymous-data claim;
- token/JWKS/claim exclusion from runtime and product artifacts.

Stop after the reversible identity migration, phase commit, report and AI0
review. Rollback uses the pre-migration backup and never converts authenticated
records to anonymous ownership.

### 13.3 AI4C.3 ownership

Allowed files are reproducible dependency declarations and lock artifacts,
OCI/Compose deployment files, PostgreSQL and Alembic validation, configuration,
readiness, metrics and structured logging, deployment/operations/security docs,
safe scripts, staging tests and its report.

Required red/green coverage:

- official SDK package equivalence experiment and provenance decision;
- no mutable or developer-local dependency;
- clean Linux build and installation;
- no secret in image, bundle, log or report;
- real PostgreSQL migration and constraints;
- configuration fail-closed behavior;
- liveness/readiness and restricted metrics;
- real BFF-to-FastAPI-to-OpenHands staging smoke;
- paired PostgreSQL/native persistence backup and restore;
- application/schema rollback drill;
- no duplicate facts after recovery.

Stop after the staging evidence, phase commit, report and AI0 review. Rollback
uses the previous compatible image and paired snapshot.

### 13.4 AI4C.4 ownership

AI4C.4 owns acceptance tests, accessibility evidence and the final report. It
changes production files only when a named failing acceptance test proves a
defect in an earlier phase, and that repair remains attributed to that phase.

Required evidence is the full deterministic regression, authorized real
provider evidence, identity/security matrix, clean staging and recovery drill,
accessibility baseline and repository/secret audit.

Stop after the final local report commit and wait for AI0. Do not push, merge,
deploy publicly or begin multimodal/Web3 work.

## 14. Gate Reporting Rules

Every phase report includes:

- branch and full HEAD;
- commits and exact changed files;
- commands, versions, test counts, warnings and durations;
- red/green TDD evidence;
- `OpenHands APIs Reused`;
- `FocusProof-Owned SDK Gaps`;
- migration and rollback evidence;
- security findings and residual risks;
- provider call/token/cost evidence when authorized;
- explicit confirmation that no later phase, push, merge or public deployment
  occurred.

An empty SDK-gap section is valid. An undocumented local runtime addition is a
gate failure.

## 15. Readiness Decision

AI4C acceptance is evidence-based and does not imply public launch. The final
state is one of:

- `failed`: a required deterministic or security invariant is false;
- `blocked`: required external identity, provider or staging evidence is absent;
- `staging-ready with blockers`: deterministic and isolated staging gates pass,
  but real identity or real external staging evidence remains incomplete;
- `production-readiness accepted for the tested staging profile`: all approved
  AI4C evidence passes, while public launch still requires a separate AI0
  authorization.

`public-launch-ready` is not an AI4C outcome unless AI0 creates and accepts a
separate public-deployment gate.
