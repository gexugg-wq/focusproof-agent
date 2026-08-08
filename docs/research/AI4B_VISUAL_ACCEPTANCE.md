# AI4B Four-Viewport Visual Acceptance

Date: 2026-07-17
Baseline: `0ca1984`
Flow: general knowledge learning verification

## Scope and evidence source

The captured flow uses the production Next.js UI and BFF, the production
FastAPI `create_app`, and the existing OpenHands SDK integration through
`LocalConversation`, SDK `TestLLM`, the registered `ToolExecutor`, and native
Action/Observation events. It does not use a provider LLM or a second runtime.
Playwright builds the Next.js application and serves it with `next start`; the
capture helper asserts that no `nextjs-portal` development element exists and
takes the full-page PNG from the unmodified production DOM. No screenshot or
DOM post-processing is applied.

The production capture is isolated in
`frontend/playwright.visual.config.ts` and runs with:

`npx playwright test --config playwright.visual.config.ts`

The default Task 6 Playwright configuration remains on its existing
development-server path and does not write the AI4B visual evidence.

Successful Session creation, evidence submission, review, answer submission,
completion, Build Log persistence, and refresh recovery are not intercepted.
The 360 px failure state creates and loads a real Session, then uses Playwright
only to return one deterministic `503 backend_unavailable` response for the
Evidence POST. This exercises the existing UI recovery contract without adding
a public failure endpoint.

Test node:
`frontend/e2e/ai4b-real-flow.spec.ts` →
`captures four-viewport geometry through the real Next BFF flow`.

## Captured evidence

| File | Viewport | PNG dimensions | State | Deterministic fixture | Result |
| --- | --- | --- | --- | --- | --- |
| [1440x900-completed.png](assets/ai4b/1440x900-completed.png) | 1440×900 | 1440×1529 full page | completed | Long replay goal, text evidence, local-only URL evidence, follow-up answer, persisted review | Accepted |
| [1280x720-completed.png](assets/ai4b/1280x720-completed.png) | 1280×720 | 1280×1641 full page | completed | Same real completed flow at the narrower desktop grid | Accepted |
| [390x844-awaiting-user.png](assets/ai4b/390x844-awaiting-user.png) | 390×844 | 390×2406 full page | awaiting_user | Real review question after text and URL evidence | Accepted |
| [360x800-failed-input-preserved.png](assets/ai4b/360x800-failed-input-preserved.png) | 360×800 | 360×1367 full page | failed Evidence POST, Session remains running | Real Session plus one deterministic retryable 503; entered replay explanation remains visible | Accepted |

SHA-256:

- `1440x900-completed.png`: `ecc8bc99fd2fc0431101fc49c34e08de625837b3937cf9bbcb42af87d660dd57`
- `1280x720-completed.png`: `a0ecfb7d8c105e9be63fb596c4e1a16ff38bab0f17d46403b277207a1e9d3f15`
- `390x844-awaiting-user.png`: `3c723154f5795626ffeef28e72046465c6c296039a464216cbab6d35d88ff95b`
- `360x800-failed-input-preserved.png`: `636c944afb875eebd63f4dbcd57dd86eb7faec51b68c4e8e4dbf4649652ae74f`

## VIS-01 — Horizontal overflow

At all four viewports, `body.scrollWidth <= body.clientWidth + 1`. Long goal,
evidence, URL, question ID, finding, and displayed Build Log items do not
create page-level horizontal scrolling.

Conclusion: accepted.

## VIS-02 — Panel geometry and separation

The goal summary, Evidence, Agent review, and Build Log panels have positive
width and height, stay inside the page canvas and viewport width, and do not
intersect. State-relevant headings, messages, controls, and review status can
each be scrolled fully into the active viewport.

Conclusion: accepted.

## VIS-03 — Long-content wrapping

The long general-learning goal, independent evidence explanation, local-only
URL, review question, finding, and rendered Build Log entries were measured for
element-level horizontal overflow. The submitted URL wraps inside its evidence
card rather than expanding or clipping the page.

The production Build Log intentionally displays normalized event labels,
sequence, and actor rather than raw event payloads. Therefore this gate verifies
the rendered event representation, not arbitrary hidden payload length.

Conclusion: accepted with the noted representation limit.

## VIS-04 — Completed desktop states

Both desktop screenshots clearly show:

- Session status `reviewed`;
- score, `LikelyLearning`, confidence, dimensions, finding, summary, and next
  step;
- ordered Build Log entries through `Review completed`;
- text and URL evidence after refresh recovery;
- no wallet requirement;
- an explicit disabled Proof Recording section stating that on-chain proof is
  not enabled.

No overlap, clipped control, horizontal scrollbar, secret, provider key, raw
environment value, or misleading proof-success state was observed.
No Next.js development indicator or other framework chrome is present.

Conclusion: accepted.

## VIS-05 — Awaiting-user mobile state

At 390×844 the three-column desktop layout reflows into one readable column.
The `Awaiting user` state, question, answer field, and submit button are
explicit and reachable. Long goal, submitted evidence, URL, and generated
question ID wrap without widening the page. The Evidence input, tabs, submit
button, and status message are unobstructed.

Conclusion: accepted.

## VIS-06 — Failure recovery and domain neutrality

At 360×800 the retryable Runtime failure message is visible next to the intact
Evidence text. The Session remains running, no success fact is displayed, and
the Build Log contains only facts persisted before the rejected submission.
The textarea content, submit button, failure message, and review state are
unobstructed.

All four captures use the general deterministic replay topic. Web3 remains an
optional evidence tab; wallet metadata, transactions, contracts, and on-chain
proof are not prerequisites for the learning flow.

Conclusion: accepted.

## Remaining risks

- Screenshot review does not prove complete keyboard, screen-reader, contrast,
  zoom, or WCAG conformance; semantic state tests and linting remain separate
  gates.
- The header exposes the persisted token `awaiting_user` while the review panel
  presents the friendlier `Awaiting user`. This is understandable but remains a
  copy-polish opportunity.
- The completed screen retains the `End learning and verify` control even
  though the persisted result is already complete. Existing idempotency makes
  it safe, but the call to action could be clearer in a future UI task.
- The failure screenshot validates the existing browser recovery contract with
  one intercepted failure response; it does not claim a production provider
  outage was induced.
