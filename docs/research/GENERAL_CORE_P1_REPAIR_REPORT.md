# General Core P1 Repair Report

Status: **READY_FOR_AI0_REVIEW**
Date: 2026-08-26
Scope: P1.1–P1.6 and directly related low-risk P2 repairs. No real provider was run and no key or secret was read.

## Architectural result

The repair preserves one official OpenHands SDK 1.31.0 runtime, Conversation,
agent loop, tool protocol, and production manager. General scoring, public
protocols, identity/owner, Unit of Work, and media admission semantics remain
shared. Monad remains default-off.

## P1.1 canonical real-vision fixture

RED: the gate expected 93,296 bytes / `7ee186...` while the mutable Playwright
output contained a valid reviewed 66,594-byte PNG / SHA-256
`9a2fc6ac6864101e14e933e503840705392f5153fd2a4b2b7b9da246aeac4e67`.

GREEN: the reviewed bytes were copied, not generated, to
`agent-server/tests/fixtures/real-vision/focusproof-general-session.png` (0444).
Gate code and research report use that immutable path, size, and digest as one
contract. Non-canonical paths, symlinks, and byte changes fail closed. Contract:
6 passed.

## P1.2 active Playwright and BFF retry

RED: active specs referenced removed Text/URL tabs, and the canonical image test
was mixed into a deterministic review sequence. Fixed ports also allowed stale
services to contaminate runs. In the AI0 review round, an unconditional return
left the awaiting-user/completed/reload half of the nominal real-flow test dead,
and project-level `testIgnore` accidentally replaced the historical-spec ignore
list (39 tests were collected instead of the intended active gate).

GREEN: active general UI uses Create topic/goal with protocol defaults
`general/25/null` and one composer. Historical `focusproof-flow.spec.ts` is
excluded from the default gate. Every active matrix asserts `tablist/tab = 0`.
The image proof is isolated and exercises real Next BFF → FastAPI: the server
returns an unknown 503 on the first image intent and only accepts the retry when
the idempotency key is identical. The UI retains unknown input and removes only
confirmed attachments. Monad UI is absent without capability. Runtime/data
directories and API/web ports are run-specific. Results: general 4/4 passed;
BFF retry 4/4 passed. Four viewports retain the evidence-submit geometry check;
one separate active Chromium test executes awaiting_user → answer → completed →
ordered Build Log → same-conversation reload. The default gate lists and passes
exactly 21 active tests; historical AI4C/focusproof specs remain excluded.

The real Uvicorn RED also exposed `/review` polling before the request body was
consumed: TestClient passed, while a real ASGI response remained open. The route
now consumes the body behind the existing global 256 KiB body limiter before
disconnect polling. Real Uvicorn contracts prove declared and chunked overflow
both return 413, legal `{}` returns `200 awaiting_user`, and the existing
disconnect contract still interrupts the review worker (2 passed).

## P1.3 SDK readiness

RED: readiness only established that imports existed.

GREEN: side-effect-free preflight checks official public `Agent.step`,
`Conversation`/`LocalConversation.arun`, `ToolExecutor`, `ActionEvent`, and
`ObservationEvent` construction/signature compatibility. It creates no provider,
user content, or agent run. Drift yields structured
`runtime_contract_unavailable` without exception text, paths, URLs, or secrets.
Focused readiness: 2 passed.

## P1.4 Clamd absolute deadline

RED: DNS and arbitrary `source.stream.read` could outlive the scan budget and
cancelled threads were not a bounded cleanup guarantee.

GREEN: startup DNS is bounded and cached using a restored process timer; scan
input must already be a trusted bounded `BytesIO` or regular spool. One absolute
deadline covers admission, connect, send, and receive. Unknown/blocking stream
objects fail closed without invoking `read`; semaphore and socket ownership use
`finally`/context boundaries. Clamd suite: 44 passed.

## P1.5 capability/tool projection

RED: the API maintained an independent hard-coded available-tools list.

GREEN: API descriptors are the read-only projection of the same registry and
assembler used for `agent.tools_map`, without provider execution or dangerous
tool instantiation. Registry name and SDK tool identity are both unique.
Default, URL/media, and Monad enabled/disabled focused suites passed.

## P1.6 log confidentiality

RED: SQLAlchemy bind values and exception-derived Monad/RPC text could reach
logs or public observations.

GREEN: application and Alembic engines use `hide_parameters=True`; SQLAlchemy
engine/ORM loggers are forced to WARNING before model mapping, after migration
configuration, and after runtime construction. Monad exceptions expose only an
allowlisted code and map to fixed observation text. Startup and
create/evidence/answer/review canaries contain no goal, evidence, answer,
Authorization, provider query, or raw exception. Focused P1.3–P1.6 run:
96 passed, 3 skipped.

## Related P2

- Image idempotency keys accept safe `[A-Za-z0-9._:-]` values through length 255;
  length 256 and controls are stable 422; the 68-character `img_` key remains valid.
- Monad deadline retains fixed `deadline_exhausted`; all other RPC failures map
  to fixed `rpc_unavailable`, never `str(exception)`.
- `openhands_adapter/real_conversation.py` is marked gate/debug-only and has no
  production import edge.
- Current architecture is indexed as v0.9; earlier fake/local runtime reports
  are prominently Historical / Superseded and retained.

## Verification evidence

- Canonical vision contract: 6 passed.
- P1.3–P1.6 concentrated suite: 96 passed, 3 skipped.
- Full frontend Vitest: 9 files, 131 passed.
- Clean-state TypeScript: after removing `.next` and `tsconfig.tsbuildinfo`,
  `next typegen && tsc --noEmit` passed without a preceding build.
- ESLint: passed. Next production build: passed.
- Ruff: passed. Strict mypy: 119 source files passed.
- Playwright general four viewports: 4 passed.
- Playwright real BFF retry four viewports: 4 passed.
- Playwright complete Chromium review/reload closure: 1 passed.
- Default Playwright general/image/Monad-off matrix: 21 passed.
- Affected reliability/request-limit suites: 27 passed; real Uvicorn/disconnect
  focus: 2 passed.
- Deterministic HTTP restore/retry: 4 passed.
- Full Python non-real-LLM: 1,921 passed, 9 skipped, 14 deselected, 0 failed.
- `git diff --check`: passed; staged diff: empty.

## Residual risk

No real LLM/provider stage was authorized or executed. External PostgreSQL,
staging identity, Clamd daemon, and real-provider markers remain separately
gated. The first parallel Playwright attempt exposed a shared Next `.next` cache
race; final matrices were serialized on fresh ports. This is orchestration
evidence, not a production runtime exception.
