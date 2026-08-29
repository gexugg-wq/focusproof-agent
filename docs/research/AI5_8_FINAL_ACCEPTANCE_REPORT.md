# AI5.8 Final Acceptance Report

Date: 2026-08-25
Status: `AI5_FINAL_INDEPENDENT_ACCEPTED`
Scope: final documentation sync and minimum runtime acceptance only

## Independent final review

Independent review task `01a038d3-cabd-7312-a7c6-e6e4c97f20f3` returned
`AI5_FINAL_INDEPENDENT_ACCEPTED`. Its fresh backend selection reported
`173 passed, 4 skipped`; frontend lint, typecheck, and production build passed;
the minimal Playwright selection reported `3 passed`; OpenHands SDK 1.31.0 and
final repository/resource hygiene were verified. This review did not rerun the
five PostgreSQL nodes, real Clamd gate, or HTTP nine-event flow; those conclusions
depend on the existing fresh runtime evidence recorded below. Residual risks and
unauthorized boundaries are unchanged.

## Evidence chain and fix rounds

The independent AI5.8 full-system audit first returned `REJECTED`. Fix Rounds
1, 2, and 3 completed; final Round 3 independent re-verification returned
`ACCEPTED`. Current source, migrations, existing reports, and fresh runtime
evidence were cross-checked before this status sync.

- Round 2 default gate: `1900 passed, 1 skipped, 19 deselected`.
- Round 3 focused evidence: `85/94 passed`; targeted/production strict mypy,
  Ruff, diff, and cached-empty checks passed.
- Fresh final focused backend: `236 passed, 1 skipped`.
- Fresh PostgreSQL media migration/concurrency: `5 passed`.
- Frontend lint, typecheck, production build: PASS; minimal Playwright: `3/3`.

The current code uses one `FileSessionRunLock` for image final publish and
Review. Expensive read, scan, validation, normalization, and staging stays
outside it. The cancellation gate covers stage through publish;
finalize/reference/confirm is inside the critical section. The permanent
oracle deliberately bypasses the Review lock and proves RED; normal and
restart-reconstructed publish/review delegate barriers are GREEN. Restart
rebuilds the app, manager, and repositories.

PostgreSQL revision `0006_media_scan_receipts` was exercised through
0005 -> head -> repeated head -> downgrade -> head. Fresh PostgreSQL concurrency
tests passed all five nodes.

## OpenHands APIs Reused

- OpenHands SDK 1.31.0 `Conversation` / `LocalConversation` and native EventLog.
- Native `Message`, `TextContent`, `ImageContent`, `MessageEvent`,
  `ActionEvent`, and `ObservationEvent`.
- Native `ToolDefinition` and official tool execution/registration surfaces.
- Official `TestLLM` for deterministic acceptance; it is not a real free-form
  production provider.

No second Runtime, Conversation, EventLog, Message/Action/Observation, or Tool
protocol exists. Suitable official OpenHands capabilities must continue to be
used directly; OpenHands-style imitations are prohibited.

## FocusProof-Owned SDK Gaps

FocusProof owns product semantics OpenHands cannot define: learning evidence,
authorization and capability policy, modality-neutral scoring, safe audit/query
projections and Build Log presentation, media quarantine/scan receipts,
file/object-store policy, session-level provider admission, and the file-backed
cross-process session lock. These additions do not schedule Agent steps or
replace native Conversation/EventLog restoration. The official SDK hard stop
remains binding where no public extension point exists.

## Runtime acceptance

The existing deterministic server ran at `http://127.0.0.1:58765`. Existing
HTTP smoke returned health `ok/ready`, created general session
`sess_f9f5fe1f4b3d4b03877946eac229da63`, stored text evidence, returned
`awaiting_user`, accepted an answer, then returned `completed`. `GET /events`
returned nine ordered projections with native Message/Action/Observation
lineage, `score.calculated`, and `review.completed`; Build Log is derived from
this official events endpoint. The server was stopped; the URL is reproduction
evidence only.

Fresh live Clamd gate used a real local daemon and returned `PASS` with
`liveClamdExecuted=true`, `productionMalwareScanningVerified=true`,
`visualProviderEnabled=false`, and `productionLlmEnabled=false`. The existing
live cases are benign PNG, EICAR, timeout, unavailable, and daemon error.
Oversize is separately enforced before daemon streaming by the adapter contract;
it is not mislabeled as a sixth live-daemon case. Non-clean admission tests
prove raw media cannot reach LLM/OpenHands events, and reports remain redacted.

Disabled-process contracts prove media modules are not loaded when media is
off. Product capability tests and Playwright prove the image entry is
capability-controlled. Monad is absent from default tools/UI, and generic
scoring has no Web3/image branch.

## Fresh commands and results

```text
run_real_image_evidence_gate.py --clamd-endpoint tcp://127.0.0.1:53310
PASS: clean, malicious/EICAR, timeout, unavailable, daemon error

pytest test_media_postgres_concurrency.py
5 passed

pytest focused Clamd/import/scoring/SDK/restart selection
236 passed, 1 skipped

pytest publish/review barrier + bypass oracle + restart barrier + shared locks
PASS

npm run lint; npm run typecheck; npm run build
PASS / PASS / PASS

Playwright image capability-off, capability/recovery, Monad disabled
3 passed

ai4b_smoke.py --base-url http://127.0.0.1:58765 --scripted-review
health ok/ready; session created; evidence synced; awaiting_user; completed
```

## Residual risk and approval gate

- Pinned PNG V6 is controlled local acceptance, not a public visual service.
- Real visual-provider execution remains default-off; none was called here.
- Clamd engineering acceptance does not authorize public deployment.
- Managed OIDC, public deployment, external long-term operations, monitoring,
  and SLO ownership remain unapproved.
- The former Monad plugin has since been removed; FocusProof remains
  general-purpose.
- Audio/PDF/OCR/ASR are not implemented.
- Historical 114 frontend-format debt was not mechanically reformatted.
- AI6 multimodal expansion requires separate AI0 approval.

## Cleanup evidence required at handoff

Final hygiene must show both exact acceptance containers removed, the loopback
backend stopped, no pytest/uvicorn/node/Clamd test process left, cached diff
empty, no secret/.env change from this task, and modifications limited to root
README plus approved docs.

AI5_FINAL_INDEPENDENT_ACCEPTED

## 2026-08-27 Real-Provider Browser Addendum

This addendum supersedes only the earlier statement that no real visual
provider was called. It does not authorize public deployment.

A visible-browser local acceptance used the production FastAPI and BFF paths,
official OpenHands `Conversation`, and `openai/qwen3.7-plus` in
`demo-real-vision` mode. One non-Web3 Python-learning session submitted one PNG
plus a meaningful explanation through the unified composer. The product stored
one `image/png` evidence carrying the explanation in `textContent`; it did not
create a duplicate text evidence row. Refresh recovery preserved the session
and evidence.

The real provider completed the review in one round with:

- status: `LikelyLearning`;
- score: `65`;
- confidence: `0.72`;
- native provider calls: `3`;
- Build Log sequence: `session.created`, `goal.submitted`,
  `evidence.submitted`, `verification.requested`, `verification.completed`,
  `score.calculated`, `review.completed`.

No `TestLLM`, fallback runtime, `runtime_unavailable`, HTTP 503, data loss, or
Monad UI appeared. The media scanner was deliberately `fake-clean` for this
local browser test and therefore did not certify production malware scanning;
the independent live-Clamd acceptance above remains the production-scanning
evidence. Both services were stopped after acceptance and the repository was
clean at `b18e14fbfe579667d970ea5c8a9248ec4aa233aa`.
