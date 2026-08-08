# AI4C.2 Identity and Authorization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace implicit development identity with a fail-closed, provider-neutral OIDC bearer boundary and propagate only an opaque verified principal through FocusProof authorization decisions.

**Architecture:** An external standards-compliant OIDC provider issues access tokens. The Next BFF forwards an allowlisted Bearer header without interpreting it. FastAPI verifies signature/JWKS, issuer, audience, expiry and not-before, resolves `(issuer, subject)` to an opaque principal, and injects `VerifiedIdentity`. Repositories, the existing manager and server-bound tools consume that identity; they never parse tokens or infer identity from OpenHands events.

**Tech Stack:** Python 3.12, FastAPI dependencies, PyJWT cryptography/JWKS APIs, SQLAlchemy/Alembic, Next.js, `oidc-client-ts` Authorization Code + PKCE, pytest, Vitest, Playwright, OpenHands SDK 1.31.0.

## Constraints and Ownership

- Begin only after AI0 accepts AI4C.1; end with a local phase commit, report and stop for AI0.
- Use WSL/Linux. Never read `.env`; tests use local fake keys and `.env.example` names.
- Do not implement passwords, token issuance, custom cryptography, provider-specific OIDC clients or an authorization ledger.
- Only FastAPI creates `VerifiedIdentity`. Request body/query/cookie/proxy headers, `MessageEvent.sender`, tool arguments and model output are untrusted.
- `ownerUserId` remains the compatibility field and returns only opaque `principal_id`; issuer, subject, claims and token fingerprints are never public.
- Tokens/JWKS never enter Evidence, ReviewResult, native EventLog, Build Log or ordinary logs.
- Only stable `401`, `403` and existing non-enumerating `404` may change at the HTTP boundary. Event, Action, Observation, Tool and Review protocols stay frozen.
- Historical `dev-anonymous-user` data remains isolated and is never automatically claimed.
- Do not change scoring, contracts, OpenHands source or Conversation orchestration. Do not push, merge or begin AI4C.3.

**Create:**

- `agent-server/focusproof/config/identity.py`
- `agent-server/focusproof/api/oidc.py`
- `agent-server/migrations/versions/0002_verified_principals.py`
- `agent-server/tests/ai4c/oidc_fixture.py`
- `agent-server/tests/ai4c/test_identity_authorization.py`
- `agent-server/tests/ai4c/test_identity_persistence.py`
- `frontend/lib/auth/browser.ts`
- `frontend/lib/auth/server.ts`
- `frontend/tests/identity-boundary.test.ts`
- `docs/research/AI4C2_IDENTITY_AUTHORIZATION_REPORT.md`

**Modify only in named tasks:** `.env.example`, `pyproject.toml`,
`agent-server/focusproof/api/auth.py`, `agent-server/focusproof/api/app.py`,
`agent-server/focusproof/api/models.py`,
`agent-server/focusproof/persistence/models.py`,
`agent-server/focusproof/persistence/repositories.py`,
`agent-server/focusproof/persistence/unit_of_work.py`,
`agent-server/focusproof/persistence/providers.py`,
`agent-server/focusproof/openhands_runtime/factory.py`,
`agent-server/focusproof/openhands_runtime/manager.py`,
`agent-server/focusproof/openhands_runtime/tool_assembler.py`,
`frontend/app/api/focusproof/[...path]/route.ts`,
`frontend/app/providers.tsx`, frontend package/lock, and focused tests. AI4C.2
does not own provider policy, scoring, staging manifests or protocol documents.

## Fixed Interfaces

```python
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Protocol

from fastapi import Depends, Header

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, SecretStr

class OidcSettings(BaseModel):
    model_config = ConfigDict(frozen=True)
    enabled: bool
    issuer: AnyHttpUrl | None
    audience: str | None
    jwks_uri: AnyHttpUrl | None
    allowed_algorithms: tuple[str, ...] = ("RS256",)
    clock_skew_seconds: int = 30
    jwks_cache_seconds: int = 300
    principal_fingerprint_key: SecretStr | None

@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    principal_id: str
    token_fingerprint: str

class TokenVerifier(Protocol):
    async def verify(self, encoded_token: str) -> VerifiedIdentity: ...

class PrincipalResolver(Protocol):
    def resolve(self, *, issuer: str, subject: str) -> str: ...

class SecurityAuditSink(Protocol):
    def record(self, *, principal_id: str | None, request_id: str,
               token_fingerprint: str | None, outcome: str,
               reason_category: str,
               occurred_at: datetime) -> None: ...

def load_oidc_settings(environ: Mapping[str, str], *, profile: str) -> OidcSettings: ...
def get_token_verifier() -> TokenVerifier: ...
async def require_verified_identity(
    authorization: Annotated[str | None, Header(alias="Authorization")],
    verifier: Annotated[TokenVerifier, Depends(get_token_verifier)],
) -> VerifiedIdentity: ...
```

After resolution, `VerifiedIdentity` excludes issuer/subject. The server-side principal table owns that mapping. Fingerprints are HMAC-SHA-256 audit correlation only and never authorize. Missing/malformed/expired/not-yet-valid/wrong-issuer/wrong-audience/unverifiable tokens return `401 {"code":"invalid_token","retryable":false}` plus `WWW-Authenticate: Bearer`; disabled principals return `403 {"code":"forbidden","retryable":false}`; cross-owner resources return `404`.

`agent-server/tests/ai4c/oidc_fixture.py` must define, before any test uses them:

```python
@dataclass(frozen=True, slots=True)
class LocalOidcFixture:
    issuer: str
    audience: str
    private_key_pem: bytes
    public_jwk: dict[str, object]
    kid: str
    def token(self, *, subject: str = "subject-a",
              expires_delta_seconds: int = 300,
              not_before_delta_seconds: int = -1,
              issuer: str | None = None,
              audience: str | None = None,
              algorithm: str = "RS256") -> str: ...

def local_oidc_fixture() -> LocalOidcFixture: ...
def oidc_test_app(tmp_path: Path, fixture: LocalOidcFixture) -> FastAPI: ...
```

The fixture creates ephemeral RSA keys in memory, exposes an in-process JWKS response, implements no passwords and redacts tokens from failures.

### Task 1: Configuration, Verifier and Fail-Closed Startup

**Files:** create `agent-server/focusproof/config/identity.py`,
`agent-server/focusproof/api/oidc.py`, the two named test files and fixture;
modify `.env.example`, `pyproject.toml`, `agent-server/focusproof/api/auth.py`
and `agent-server/focusproof/api/app.py`.

- [ ] Write red tests for signature, issuer, audience, `exp`, `nbf`, `sub`, algorithm allowlist, `kid`, JWKS cache/rotation/outage, malformed Authorization, safe bodies and no token logging. Test explicit local-dev anonymous mode and staging/production startup rejection without complete OIDC configuration.
- [ ] Run red and capture the missing-module failure:

```bash
cd /home/holy/web3/focusproof-agent
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY \
  .venv/bin/python -m pytest agent-server/tests/ai4c/test_identity_authorization.py -q
```

- [ ] Implement the fixture, pin the selected compatible `PyJWT[crypto]`
  release in root `pyproject.toml`, and implement `load_oidc_settings`,
  `OidcTokenVerifier`, and `require_verified_identity` with PyJWT public
  verification APIs. Never log encoded tokens, JWKS bodies or claims.
- [ ] Wire profiles: explicit `local-dev` may inject legacy anonymous identity; deterministic tests inject the fixture; staging/production fail startup or reject business requests when configuration is invalid. `/health` remains secret-free.
- [ ] Add only these names to `.env.example`: `FOCUSPROOF_OIDC_ISSUER`, `FOCUSPROOF_OIDC_AUDIENCE`, `FOCUSPROOF_OIDC_JWKS_URI`, `FOCUSPROOF_OIDC_ALLOWED_ALGORITHMS`, `FOCUSPROOF_OIDC_FINGERPRINT_KEY`, `NEXT_PUBLIC_OIDC_ISSUER`, `NEXT_PUBLIC_OIDC_CLIENT_ID`, `NEXT_PUBLIC_OIDC_AUDIENCE`, `NEXT_PUBLIC_OIDC_REDIRECT_URI`. No secret may use `NEXT_PUBLIC_`.
- [ ] Run green/regression, Ruff, Mypy and diff check, then commit all files listed by `git diff --name-only` that belong to this task:

```bash
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY \
  .venv/bin/python -m pytest agent-server/tests/ai4c/test_identity_authorization.py \
  agent-server/tests/ai4b/test_api_security.py -q
.venv/bin/ruff check agent-server
.venv/bin/mypy agent-server
git diff --check
git commit -m "feat: verify OIDC identity at the API boundary"
```

### Task 2: Opaque Principal Persistence and Anonymous Isolation

**Files:** `agent-server/migrations/versions/0002_verified_principals.py`,
`agent-server/focusproof/persistence/models.py`,
`agent-server/focusproof/persistence/repositories.py`,
`agent-server/focusproof/persistence/unit_of_work.py`,
`agent-server/focusproof/persistence/providers.py`, and
`agent-server/tests/ai4c/test_identity_persistence.py`.

- [ ] Write red tests proving stable `(issuer, subject) -> principal_id`, separate
  issuers with equal subjects, concurrent uniqueness, disabled principals,
  opaque `ownerUserId`, and historical anonymous Sessions visible only when the
  explicit local-dev profile uses a database and Conversation directory that
  are different from staging/production paths.
- [ ] Run `test_identity_persistence.py`; observe failures for the absent model/repository/migration.
- [ ] Implement migration `0002_verified_principals.py` with
  application-generated opaque ID, normalized issuer, subject, active/disabled
  state, creation/state-change timestamps and unique `(issuer, subject)`. Do
  not rewrite Session owners or create claim mappings.
- [ ] Resolve within one UoW; convert uniqueness races into rereads of the winning row. Return only `principal_id` to the verifier.
- [ ] Run green plus restart persistence regression, Ruff/Mypy/diff check; commit `feat: persist opaque verified principals`.

```bash
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY \
  .venv/bin/python -m pytest agent-server/tests/ai4c/test_identity_persistence.py \
  agent-server/tests/api/test_restart_persistence.py -q
```

### Task 3: Complete Owner Isolation and Server-Bound Tools

**Files:** `agent-server/focusproof/api/app.py`,
`agent-server/focusproof/api/models.py`, the four named persistence modules,
`agent-server/focusproof/openhands_runtime/factory.py`,
`agent-server/focusproof/openhands_runtime/manager.py`,
`agent-server/focusproof/openhands_runtime/tool_assembler.py`, and focused API,
runtime and tool tests.

- [ ] Write a parameterized red matrix for create/read/Evidence/Answer/review/result/Build Log and every Session-derived endpoint with owner A, owner B, missing token, disabled principal and spoofed body/query/cookie/proxy header. Assert `401/403/404` and unchanged Evidence/Answer/Event/Review counts after denial.
- [ ] Add runtime/tool red tests proving sender/model text/Action arguments/user-supplied `user_id` cannot change owner, tools receive the server-bound principal, and no token/claim/fingerprint enters native or projected events.
- [ ] Run the named matrix and capture exact legacy-string or unbound-tool failures.
- [ ] Minimally propagate `VerifiedIdentity` through existing route/service calls; pass `principal_id` into repositories and bind it when assembling existing ToolDefinition handlers. Repositories never parse tokens and tool schemas never accept identity from the model.
- [ ] Run full API/persistence/runtime/tool security regression plus Ruff/Mypy/diff check; commit `fix: enforce verified principal ownership`.

```bash
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY \
  .venv/bin/python -m pytest agent-server/tests/api agent-server/tests/persistence \
  agent-server/tests/openhands_runtime agent-server/tests/tools \
  agent-server/tests/ai4c/test_identity_authorization.py -q -m "not real_llm"
```

### Task 4: BFF Forwarding and Browser OIDC Client

**Files:** create `frontend/lib/auth/browser.ts`, `frontend/lib/auth/server.ts`,
`frontend/tests/identity-boundary.test.ts`; modify
`frontend/app/api/focusproof/[...path]/route.ts`, `frontend/app/providers.tsx`,
`frontend/package.json`, and `frontend/package-lock.json`.

- [ ] Write red Vitest tests proving exactly one valid Bearer header is forwarded unchanged, identity cannot come from body/query/cookie/proxy headers, tokens are never logged, backend `401/403/404` survive, and provider/base URL/model/key are not exposed.
- [ ] Write red browser-client tests for Authorization Code + PKCE state/nonce, memory-only token storage, expiry and logout. Use the fake issuer; do not build passwords.
- [ ] Run red frontend tests and observe absent auth modules/forwarding.
- [ ] Implement `oidc-client-ts` provider-neutral integration. BFF syntax-allowlists and forwards Authorization but never verifies, issues, refreshes, decodes or rewrites identity. Browser attaches in-memory access tokens only to same-origin BFF requests.
- [ ] Run lint, typecheck, Vitest and production build, then commit `feat: forward verified OIDC bearer identity`.

```bash
cd frontend
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY npm run lint
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY npm run typecheck
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY npm test
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY npm run build
cd ..
git diff --check
```

### Task 5: Audit Minimization, Phase Gate and Stop

- [ ] Write red sentinel tests exercising success and all auth failures, then
  scan database rows, captured logs, HTTP bodies, Build Log, Evidence,
  ReviewResult and native EventLog serialization. Raw token, JWKS, issuer,
  subject and claims must be absent; security audit may contain only opaque
  principal/request/fingerprint/outcome/reason-category/time and must delete
  expired records according to one configured bounded retention interval.
- [ ] Extend the existing audit/logging sink minimally to implement `SecurityAuditSink`. Fingerprint with a server-secret HMAC; missing staging/production key fails closed. Do not store request bodies.
- [ ] Run the complete credential-free phase gate:

```bash
cd /home/holy/web3/focusproof-agent
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY \
  .venv/bin/python -m pytest agent-server/tests -q \
  -m "not real_llm and not postgres and not staging_external"
.venv/bin/ruff check agent-server
.venv/bin/mypy agent-server
cd frontend
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY npm run lint
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY npm run typecheck
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY npm test
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY npm run build
cd ..
git diff --check
```

- [ ] Write `AI4C2_IDENTITY_AUTHORIZATION_REPORT.md` with counts, endpoint matrix, anonymous isolation, redaction evidence, dependency audit and managed-vs-self-hosted OIDC residual decision. State that the local issuer is test evidence, not real deployment evidence.
- [ ] Commit report/test evidence with `test: close AI4C identity authorization gate`; report commit range, files, red-green results, migration, security findings and rollback point; stop for AI0.

## Rollback and Deletion Conditions

- Roll back all AI4C.2 commits together to the accepted AI4C.1 SHA; do not mutate migrated data manually.
- OIDC verification, principal mapping and audit policy are FocusProof product boundaries, not SDK gaps.
- Delete any FocusProof tool identity argument when an SDK public immutable server context can carry the principal without model-authored arguments; prove deletion with the spoof matrix and ToolDefinition/event diff.
- Never replace FastAPI verification with `Conversation.user_id` or `MessageEvent.sender`; those remain audit attribution only.
