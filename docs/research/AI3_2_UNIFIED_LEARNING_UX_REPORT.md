# AI3.2 Unified Learning UX Report

Date: 2026-08-26
Status: READY_FOR_AI0_REVIEW

> Historical report note (2026-08-27): the optional Monad panel described
> below was subsequently removed with the entire active Monad plugin slice.
> The unified general evidence composer remains current.

## Scope and boundaries

The implementation changes only the Create Session and evidence input UX plus corresponding frontend tests and E2E. It reuses the existing BFF allowlist, API client, server-authoritative evidence list, OpenHands runtime, and AI5.8 image idempotency path. No backend protocol, runtime, scoring, owner isolation, media gate, or `runtime_unavailable` semantics changed. Monad remains capability-driven and off by default.

## Field mapping

The visible Create Session fields are `title` and `goal`. Submission fixes `domain` to `general`, `plannedMinutes` to `25`, and `expectedOutput` to `null`. The nullable value is supported by the existing `CreateSessionInput` contract; no invented public field or default copy was added. A synchronous ref and React Hook Form submitting state jointly prevent duplicate create requests.

## Unified composer

The product route renders one Submit evidence composer with one text area and one submit action. Text, URL, and Web3 tabs and the separate visible image form are absent. A whole trimmed HTTP/HTTPS URL becomes URL evidence; all other attachment-free content becomes text evidence. With images, text is only the image explanation and no duplicate text evidence is submitted.

Images can be selected, dropped, or pasted. Attachments use compact stable rows with filename, MIME, size, and an accessible remove action. Confirmed files are removed one by one. Retryable/unknown failures retain the active and unattempted files plus explanation. The original AI5.8 per-file fingerprint, session-storage record, bounded request key, and sequential upload loop remain the single implementation in `ImageEvidenceForm` and pass their original 18 tests.

The optional Monad panel remains outside the general composer and appears only from enabled provider metadata. No voice copy, control, permission, or API was added.

## RED / GREEN

- Create RED: 2 pass / 2 fail because hidden fields were visible and the frozen action did not exist.
- Create GREEN: 4/4, including exact `general` / `25` / `null` payload and synchronous duplicate suppression.
- Composer RED: 7/7 failed against tabs, duplicate image form, and missing unified attachment inputs.
- Composer GREEN: 7/7.
- Focused compatibility GREEN: 46/46 across Create, unified composer, Session/Review, and AI5.8 image idempotency.
- Full frontend unit/component GREEN: 131/131.

## Verification

- `npm test -- --run`: 9 files, 131 tests passed.
- `npm run lint`: passed.
- `npm run typecheck`: passed.
- `npm run build`: passed with Next.js 15.5.21 production output.
- `npx playwright test e2e/focusproof-flow.spec.ts e2e/image-evidence.spec.ts`: 20/20 across 1440x900, 1280x720, 390x844, and 360x800.
- The Playwright run covers create, empty composer, selected attachment, submitted server evidence, retryable 503 preservation, refresh recovery, Monad/Web3-specific tabs absent from the general composer, and desktop/mobile geometry.
- `git diff --check`: passed.

## Screenshots

Playwright produces disposable visual evidence under
`frontend/test-results/acceptance/`. These captures are intentionally ignored;
the assertions and test results are authoritative, while historical screenshots
remain available through Git history.

## Task changed files

- `frontend/features/session/CreateSessionForm.tsx`
- `frontend/features/evidence/EvidencePanel.tsx`
- `frontend/features/evidence/ImageEvidenceForm.tsx`
- `frontend/tests/create-session.test.tsx`
- `frontend/tests/security-and-recovery.test.tsx`
- `frontend/tests/session-review.test.tsx`
- `frontend/tests/unified-evidence-composer.test.tsx`
- `frontend/e2e/focusproof-flow.spec.ts`
- `frontend/e2e/image-evidence.spec.ts`
- `docs/research/AI3_2_UNIFIED_LEARNING_UX_REPORT.md`

## Residual risk

Browser security still requires reselecting file bytes after a full reload; AI5.8 safely recovers the same pending intent when the reselected bytes and explanation match. The focused real-server Playwright harness exercises the official BFF/session runtime while evidence edge branches use controlled API responses; no real LLM stage was entered.
