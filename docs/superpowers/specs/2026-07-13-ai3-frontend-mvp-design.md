# AI3 Frontend MVP Design

## Goal
Build the FocusProof user-facing web MVP so a learner can create a learning session, submit text, URL, or Web3 evidence, request an OpenHands-backed review, answer agent follow-up questions, retry review, inspect the result, inspect the current Build Log, optionally connect an injected wallet for Web3 metadata, and recover the current session after refresh.

## Scope
AI3 changes are limited to frontend, frontend README, docs/research/AI3_FRONTEND_MVP_REPORT.md, docs/research/assets/ai3, and Superpowers spec and plan documentation. The frontend will not modify agent-server, contracts, protected architecture, protocol, or project-management docs, .env, var, or OpenHands SDK source. It will not call debug OpenHands routes, store LLM secrets, compute final scores, verify evidence truth, write a database, or send chain transactions.

## Architecture
The app will be a Next.js App Router project rooted at frontend. The browser will call only same-origin API routes under /api/focusproof. The route frontend/app/api/focusproof/[...path]/route.ts will act as a restricted BFF proxy to FOCUSPROOF_API_BASE_URL, defaulting to http://127.0.0.1:8000 on the server side. The proxy will allow only the formal FocusProof API paths: health, sessions, session detail, evidence submission, answer submission, review, events, and reviews.

TanStack Query will own server state, cache invalidation, and explicit retry behavior. React Hook Form and Zod will own form input and validation. The frontend API layer will define TypeScript contracts that mirror the accepted backend response shapes, including syncPending, reviewStatus, agentQuestions, reviewResult, event timeline records, and retryable error payloads. localStorage will store only recent session metadata: session id, title, domain, and last visited time.

## Pages and Layout
The root page will be the application first screen, not a marketing page. It will show a compact CreateSessionForm with domain, title, goal, expected output, and planned minutes. On success it will save recent-session metadata and route to the session workspace.

The session page will load session detail and render a working workspace. Desktop layout will use a restrained top bar, left goal/status panel, central evidence/review column, and right Build Log timeline. Mobile layout will become a single-column segmented workspace so content does not overlap or require horizontal scrolling. Cards are used only for actual panels and items, with radius at or below 8px, visible focus styles, labels, aria-live error regions, and icon buttons using lucide-react.

## Session Workflow
The session workspace will support three evidence modes: text, URL, and Web3. Text evidence submits notes, explanations, code, or error records. URL evidence submits sourceUrl plus the user explanation. Web3 evidence submits through the same evidence API with evidenceType web3, human explanation in textContent, optional explorer URL in sourceUrl, and metadata for tx hash, contract address, chain id or network, and optional wallet address. The UI will never state a transaction is verified unless backend review or event data explicitly says so.

Review is triggered by the session review endpoint. completed renders server-returned status, score, confidence, dimensions, findings, summary, and next step without recalculating anything. awaiting_user renders each agentQuestions item by questionId, lets the learner answer, calls answer submission, and then lets the learner request review again. The UI will allow multiple rounds and will not assume only one question. failed, 409 session_busy, 503 runtime unavailable, network failures, and 404 or 403 session access failures will preserve current form state and present neutral retry paths.

## Build Log
The Build Log will call session events, sort by sequence ascending in the UI, and render known events with recognizable labels: session created, goal submitted, evidence submitted, question asked, answer submitted, verification requested, verification completed, score calculated, review completed, and error occurred. Unknown event types will render with a generic event row rather than crashing. The Build Log defaults to the current session.

## Wallet UX
Wallet support is optional and limited to injected wallets through wagmi and viem. Wallet UI is prominent only when the session domain is Web3 or the evidence mode is Web3. It supports connect, display shortened address, display chain id, disconnect, and include the address as optional Web3 evidence metadata. It does not request private keys, auto-sign, switch networks, send transactions, or treat the address as an authenticated identity. Monad-related chain values will come from public frontend environment variables rather than hardcoded uncertain RPC data.

## Proof Recording Boundary
After a completed review, the UI will show an isolated ProofRecording component. The action is disabled by default, clearly labeled as not enabled for on-chain proof yet, and does not call any proof endpoint or contract. This leaves a clean AI4 integration point without coupling proof behavior into review rendering.

## Testing and Verification
Implementation will be test-first. Vitest and React Testing Library will cover API contracts and error mapping, create-session validation and routing, evidence payload construction for all three evidence types, syncPending behavior, awaiting-user answer and review loop, completed review rendering, 409 and 503 recovery, Build Log sorting and unknown event compatibility, refresh recovery, wallet optionality, and duplicate submit protection. Playwright will mock API responses for the full end-to-end journey, error retry paths, refresh recovery, and desktop and mobile viewport screenshots. Final validation will run npm install, lint, typecheck, unit tests, build, Playwright e2e, git diff check, git status, and git diff name-status from 85698cb. A single real backend session flow will be attempted without repeated paid LLM review calls.

## Deliverables
AI3 will deliver a complete frontend implementation, frontend/.env.example, updated frontend/README.md, screenshots under docs/research/assets/ai3, and docs/research/AI3_FRONTEND_MVP_REPORT.md. The report will cover page and component structure, BFF API boundary, backend endpoint mapping, session state flow, awaiting-user loop, syncPending behavior, wallet boundary, Proof Recording delay boundary, commands and outputs, Playwright viewport checks, known limitations, modified files, and whether any secrets were read or committed.
