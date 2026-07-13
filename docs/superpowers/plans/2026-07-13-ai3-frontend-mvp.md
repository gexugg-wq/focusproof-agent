# AI3 Frontend MVP Implementation Plan

For agentic workers: REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

Goal: Build the FocusProof AI3 frontend MVP with a restricted Next.js BFF, complete learning session and review loop, Build Log, optional wallet UX, tests, screenshots, report, and local commits only.

Architecture: The frontend is a standalone Next.js App Router app in frontend. Browser traffic goes through a same-origin restricted BFF route and never directly reaches FastAPI. Client features are split by workflow: session creation, evidence, review, build log, wallet, and proof recording.

Tech Stack: Next.js App Router, TypeScript strict mode, React, Tailwind CSS, TanStack Query, React Hook Form, Zod, wagmi, viem, lucide-react, Vitest, React Testing Library, Playwright.

## Global Constraints

- Work only in /home/holy/web3/focusproof-agent.
- Branch must be ai3-frontend-mvp created from 85698cb.
- Do not modify agent-server, contracts, docs/architecture, docs/protocol, docs/project-management, .env, var, or OpenHands SDK source.
- Do not call debug OpenHands routes from the frontend.
- Do not store LLM secrets, compute final scores, verify evidence truth in the browser, write database records, or send chain transactions.
- Do not push.

---

## File Structure

- frontend/package.json: frontend scripts and dependencies.
- frontend/app/api/focusproof/[...path]/route.ts: restricted BFF proxy.
- frontend/app/layout.tsx, frontend/app/page.tsx, frontend/app/sessions/[sessionId]/page.tsx, frontend/app/providers.tsx, frontend/app/globals.css: app shell and routes.
- frontend/features/session: create form, workspace, session status panels.
- frontend/features/evidence: text, URL, and Web3 evidence forms and payload builder.
- frontend/features/review: review trigger, agent question answers, review result, proof recording panel.
- frontend/features/build-log: event timeline sorting and rendering.
- frontend/features/wallet: optional injected wallet controls.
- frontend/lib/api: contracts, client, error mapping.
- frontend/lib/storage: recent session persistence.
- frontend/tests: Vitest and RTL tests.
- frontend/e2e: Playwright mocked journey tests.
- frontend/README.md and frontend/.env.example: operation docs and safe env examples.
- docs/research/AI3_FRONTEND_MVP_REPORT.md and docs/research/assets/ai3: final report and screenshots.

## Task 1: Frontend Scaffold and API Boundary

Files: frontend package/config files, app providers, globals, lib/api contracts, BFF route, API tests.

Steps:
- Write failing tests for allowed BFF path matching, blocked debug route behavior, API error mapping, and event sorting.
- Run targeted Vitest command and confirm failures are from missing files or functions.
- Add Next.js, TypeScript, Tailwind, Vitest, RTL, Playwright config, and the BFF route.
- Implement FocusProof contracts and API client helpers.
- Run lint, typecheck, and targeted tests until green.
- Commit as chore(frontend): scaffold FocusProof web app.

## Task 2: Create Session Flow

Files: app/page.tsx, features/session/CreateSessionForm.tsx, lib/storage/recent-sessions.ts, create-session tests.

Steps:
- Write failing tests for required form validation, domain options, successful POST /sessions payload, route navigation, and recent session metadata storage.
- Run targeted tests and confirm red state.
- Implement the form with React Hook Form and Zod, neutral loading and error states, and first-screen operational layout.
- Run targeted tests, lint, and typecheck until green.
- Commit as feat(frontend): add learning session workflow.

## Task 3: Session Workspace, Evidence, Review, and Build Log

Files: session page, workspace components, evidence forms, review components, build-log components, workspace tests.

Steps:
- Write failing tests for session refresh load, three evidence payload shapes, syncPending display, duplicate-submit prevention, awaiting_user answer loop, completed review result display, 409 and 503 recovery, Build Log sequence sorting, and unknown event rendering.
- Run targeted tests and confirm red state.
- Implement session workspace layout, evidence modes, review mutation flow, question-answer forms, review result, disabled ProofRecording component, and Build Log timeline.
- Run targeted tests, lint, typecheck, and build until green.
- Commit as feat(frontend): add review and build log.

## Task 4: Optional Wallet UX

Files: features/wallet, lib/wallet/config.ts, evidence Web3 integration tests.

Steps:
- Write failing tests proving general sessions do not require wallet, Web3 evidence can submit without wallet address, connected wallet metadata is optional, and wallet panel appears for Web3 contexts.
- Run targeted tests and confirm red state.
- Implement wagmi and viem config for injected wallets, connect/disconnect controls, short address and chain id display, and optional metadata injection into Web3 evidence.
- Run targeted tests, lint, typecheck, and build until green.
- Commit as feat(frontend): add optional wallet evidence flow.

## Task 5: E2E, Screenshots, Docs, and Final Verification

Files: frontend/e2e, docs/research/AI3_FRONTEND_MVP_REPORT.md, docs/research/assets/ai3, frontend/README.md.

Steps:
- Write Playwright mocked tests for create session, submit evidence, first review awaiting_user, answer question, second review completed, score and Build Log display, refresh recovery, and 409/503 retry paths.
- Add viewport screenshot checks for 1440x900, 1280x720, 390x844, and 360x800.
- Run Playwright and fix frontend-only issues.
- Write README and AI3 report with commands, outputs, boundaries, known limitations, modified files, and secret handling statement.
- Run npm install, npm run lint, npm run typecheck, npm run test, npm run build, npm run test:e2e, git diff --check, git status --short, and git diff --name-status 85698cb..HEAD.
- Attempt one real backend session flow if the local FastAPI service and non-secret runtime configuration are available; otherwise document the blocker in the AI3 report.
- Commit as test(frontend): cover FocusProof user journey and docs: report AI3 frontend MVP.
