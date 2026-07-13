# AI3 Frontend MVP Report

## Summary

AI3 implemented the FocusProof user-facing web MVP in `frontend/` on branch `ai3-frontend-mvp` from baseline `85698cb`. The app is an operational learning verification workspace, not a marketing site.

## Page and Component Structure

- `frontend/app/page.tsx`: first-screen create Session form.
- `frontend/app/sessions/[sessionId]/page.tsx`: Session workspace route.
- `frontend/app/api/focusproof/[...path]/route.ts`: restricted BFF proxy.
- `frontend/features/session`: create form and workspace shell.
- `frontend/features/evidence`: text, URL, and Web3 evidence submission.
- `frontend/features/review`: review trigger, awaiting-user answer loop, completed result display, disabled Proof Recording.
- `frontend/features/build-log`: event timeline sorted by sequence.
- `frontend/features/wallet`: optional injected-wallet metadata UX.
- `frontend/lib/api`: TypeScript contracts, API client, proxy allowlist, error mapping.
- `frontend/lib/storage`: recent-session metadata only.
- `frontend/lib/wallet`: viem-only wallet address helpers. No wagmi provider is in the core app tree.

## BFF API Boundary

The browser calls only same-origin `/api/focusproof/...`. The BFF allowlist accepts:

- `GET /health`
- `POST /sessions`
- `GET /sessions/{sessionId}`
- `POST /sessions/{sessionId}/evidence`
- `POST /sessions/{sessionId}/answer`
- `POST /sessions/{sessionId}/review`
- `GET /sessions/{sessionId}/events`
- `GET /sessions/{sessionId}/reviews`

Debug OpenHands routes and arbitrary forwarding are blocked.

## Backend Interface Mapping

- Create form maps to `CreateSessionRequest`.
- Evidence forms map to `SubmitEvidenceRequest`.
- Agent answers map to `SubmitAnswerRequest`.
- Review panel consumes `RuntimeReviewResult`.
- Build Log consumes the event list from `/events`.
- Refresh recovery calls `GET /sessions/{sessionId}` and recent-session localStorage only stores id, title, domain, and timestamp.

## Session State Flow

The normal flow is create session, load session detail, submit evidence, request review, answer follow-up questions if needed, request review again, display completed review, and inspect Build Log. The UI preserves form state across retryable failures and does not invent success states.

## awaiting_user Loop

`awaiting_user` renders all returned `agentQuestions` by `questionId`. The learner can answer each question through `/answer`, then request review again. The UI supports multiple rounds and does not assume only one question.

## syncPending Behavior

Evidence and answer submissions display normal success when `syncPending=false`. They display `Evidence saved, waiting for Agent sync.` or `Answer saved, waiting for Agent sync.` when `syncPending=true`.

## Wallet Boundary

Wallet UX appears for Web3 sessions and Web3 evidence contexts. It supports injected-wallet connect, shortened address display, chain id display, local disconnect, and optional wallet metadata in Web3 evidence. It does not request private keys, auto-sign, switch networks, send transactions, or treat the wallet address as identity.

## Proof Recording Boundary

After a completed review, the UI renders a separate Proof Recording panel. The action is disabled and says on-chain proof is not enabled yet. No proof API or contract call is made.

## Test Commands and Outputs

Executed in `/home/holy/web3/focusproof-agent/frontend` with local Linux Node on PATH:

- `npm install`: completed, produced package-lock. npm reported dependency audit warnings.
- `npm run lint`: passed.
- `npm run typecheck`: passed.
- `npm run test`: 4 files passed, 27 tests passed.
- `npm run build`: passed with Next.js 15.5.18 production build.
- `npm run test:e2e`: 12 tests passed using mocked API.

Playwright required local WSL browser dependencies. Because sudo is unavailable, `libnspr4`, `libnss3`, and `libasound2t64` were downloaded with `apt-get download` and extracted under `/home/holy/.cache/focusproof-playwright-libs`; tests were run with `LD_LIBRARY_PATH` pointing to that cache.

## Playwright Viewports and Screenshots

Mocked Playwright flow covered:

- 1440x900: `docs/research/assets/ai3/01-create-session-desktop.png`
- 1440x900: `docs/research/assets/ai3/02-general-evidence-desktop.png`
- 1440x900: `docs/research/assets/ai3/03-awaiting-user-desktop.png`
- 1440x900: `docs/research/assets/ai3/04-review-completed-desktop.png`
- 1440x900: `docs/research/assets/ai3/05-session-busy-desktop.png`
- 1440x900: `docs/research/assets/ai3/06-runtime-unavailable-desktop.png`
- 390x844: `docs/research/assets/ai3/07-awaiting-user-mobile.png`
- 390x844: `docs/research/assets/ai3/08-review-completed-mobile.png`
- 1280x720: `docs/research/assets/ai3/09-web3-evidence-desktop.png`

The mocked flow covered a general programming learning session, text evidence, first review returning `awaiting_user`, answer submission, second review returning `completed`, score/result/dimensions/findings/summary/next-step display, Build Log display, refresh recovery, precise retryable 409/503 behavior, and one isolated Web3 evidence screenshot.

## Real Backend Smoke

FastAPI was started locally from `.venv` and checked at `http://127.0.0.1:8000/health`. A real API smoke flow used only official endpoints:

- `POST /sessions`: created `sess_05b90bbdb78a4d01aac416479320d458`.
- `POST /sessions/{id}/evidence`: returned `syncPending=false`.
- First `POST /sessions/{id}/review`: returned `awaiting_user`.
- `POST /sessions/{id}/answer`: returned `syncPending=false`.
- Second `POST /sessions/{id}/review`: returned `awaiting_user` again.
- `GET /sessions/{id}/events`: returned 8 events including `session.created`, `goal.submitted`, `evidence.submitted`, `verification.requested`, `verification.completed`, `question.asked`, `answer.submitted`, and another `question.asked`.

Known limitation: the live backend chose another follow-up question instead of a completed score within two review attempts. The frontend handles this multi-round state as required; additional real LLM/review cycles were not forced.

## Known Limitations

- There is no backend Session list endpoint, so refresh recovery is limited to current route and recent session metadata.
- Wallet connect uses injected browser providers only.
- On-chain proof recording is intentionally disabled for AI3.
- Playwright in this WSL environment requires a user-local shared-library path because system browser dependencies are not installed globally.

## Modified File Areas

- `frontend/`
- `docs/research/AI3_FRONTEND_MVP_REPORT.md`
- `docs/research/assets/ai3/`
- `docs/superpowers/specs/2026-07-13-ai3-frontend-mvp-design.md`
- `docs/superpowers/plans/2026-07-13-ai3-frontend-mvp.md`

Protected backend, contracts, architecture docs, protocol docs, project-management docs, `.env`, `var/`, and OpenHands SDK source were not intentionally modified.

## Secret Handling

No browser LLM key was added. No real secret was committed. The frontend `.env.example` contains only placeholder/public configuration.

## AI3.1 Acceptance Corrections

Baseline for this correction pass was accepted AI3 commit `bb643be` on branch `ai3-frontend-mvp`. No protected backend, contracts, architecture, protocol, project-management, `.env`, `var/`, or OpenHands SDK files were modified.

- Structured API failures now throw real `ApiError extends Error` instances. `409 session_busy`, `503 backend_unavailable/runtime_unavailable`, access errors, network errors, invalid JSON, and non-JSON upstream failures are mapped to safe user messages without `SyntaxError` leakage.
- The BFF proxy now has timeout/abort handling, returns structured `503 backend_unavailable` when FastAPI is down, returns structured `upstream_non_json` for non-JSON upstream failures, and still blocks debug or arbitrary proxy paths.
- Create Session, Evidence, Review, and Answer flows catch failures locally, preserve form/page state, avoid false success copy, and keep answer text when submission fails. Answer submission has in-flight duplicate prevention.
- `wagmi` was removed from production dependencies and the app provider tree. Optional wallet UX now uses injected `window.ethereum` only for metadata, with viem address validation. Monad RPC env vars were removed from frontend examples/docs because Monad RPC is not required for AI3 core browsing, evidence, review, or Build Log behavior.
- The main E2E path is now a general programming learning case: `Understanding event sourcing`. Web3 is covered by one isolated optional evidence screenshot only. AI3 does not write proof on-chain, does not call proof APIs, and does not enter AI4.
- Nested panels were reduced for wallet/proof subsections, and the Build Log desktop column is constrained with `h-fit`.

Final validation commands run in `/home/holy/web3/focusproof-agent/frontend`:

- `npm run lint`: passed.
- `npm run typecheck`: passed.
- `npm run test`: 4 files passed, 27 tests passed.
- `npm run build`: passed on Next.js 15.5.18.
- `npm run test:e2e`: 12 tests passed across 1440x900, 1280x720, 390x844, and 360x800 projects.
- `npm audit --omit=dev`: 2 moderate vulnerabilities reported, 0 high, 0 critical. The remaining report is Next's internal `postcss <8.5.10` advisory; moving to Next 16 requires the ESLint 9/React 19 toolchain jump and was not taken in this AI3.1 correction pass.

AI3.1 screenshot artifacts:

- `docs/research/assets/ai3/01-create-session-desktop.png`
- `docs/research/assets/ai3/02-general-evidence-desktop.png`
- `docs/research/assets/ai3/03-awaiting-user-desktop.png`
- `docs/research/assets/ai3/04-review-completed-desktop.png`
- `docs/research/assets/ai3/05-session-busy-desktop.png`
- `docs/research/assets/ai3/06-runtime-unavailable-desktop.png`
- `docs/research/assets/ai3/07-awaiting-user-mobile.png`
- `docs/research/assets/ai3/08-review-completed-mobile.png`
- `docs/research/assets/ai3/09-web3-evidence-desktop.png`

Remaining limitations are unchanged: no backend Session list endpoint, wallet connect is injected-provider metadata only, and proof recording remains intentionally disabled for AI3.
