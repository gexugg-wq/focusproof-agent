# AI5 Multimodal Image Foundation Codex Goal

## Mission

Implement design v7: four images, two-stage linear quota, complete quarantine/staging lifecycle, existing synchronous UoW, conditional runtime contribution, safe repository facts, modality-neutral narratives, stable native ImageContent, and recoverable local media. ArtifactResolvingLLM is a conditional Task5 design only: it may be implemented only after an official public extension point proves the required composition, recovery identity, accounting, and replacement-Agent behavior.

## Locked outcomes

Initial lease reserves one count under DB lock; final transaction rechecks owner/Session/lease and actual distinct normalized bytes. At three committed images, concurrent attempts yield one completion; competing normalized bytes that jointly exceed 20 MiB yield one final commit. EventLog restores four images; larger history requires a separate future protocol.

Runtime contributions merge built-ins then ordered optional capability/tool mappings with conflict rejection. Disabled clean process imports/instantiates no FocusProof media module/provider. Media Tool returns safe repository facts only. General narrative projection, not modality branching in scoring, supplies verified explanations; fake future audio projection changes no scoring code.

Task1 contract-tests the proven OpenHands 1.31.0 public Message/TextContent/ImageContent, Message model_dump/model_validate roundtrip, TestLLM completion/acompletion, public metrics mutation/read, LocalConversation, Agent identity, required public ToolDefinition model fields and concrete registration behavior, process-isolated tool/model registration, and dependency versions. A child process may inherit FocusProof registrations already present in the parent; isolation means only that registrations created in the child do not leak back to the parent. Public inner composition, wrapper identity across recovery, stats/budget/call accounting, and the LocalConversation replacement-Agent negative case remain unproved. Task5 is a hard STOP GATE: without an official public extension point, stop and do not implement an OpenHands-style wrapper/facade or touch private state. VisionInspectTool stays disabled. Existing locked dependencies are regenerated rather than split into a parallel lock.

## Completion and prohibitions

All eight review gates pass, including migration commands, PostgreSQL concurrency, core/media Docker builds, routing/BFF streams, stable recovery, backup restore, regressions, and secret scan. Do not claim that a real visual LLM has been accepted. Never touch dirty ignore files or stage/commit/push/merge/amend without later AI0 authorization. Stop on image branches in Manager/Agent loop, domain scoring, text/URL tools, or Monad; second UoW/runtime/tool protocol; static media imports; private SDK access; unscoped resolution; or public media URLs. Task2-4 remain independently actionable if Task5 stops.

## 2026-08-25 Authoritative final status

Status: `AI5_ENGINEERING_RUNTIME_ACCEPTED`.

This section supersedes earlier AI5.7-pending, external-Clamd-blocked, and
whole-AI5-incomplete wording without deleting history. AI5.7 Task6 is complete:
OpenHands SDK 1.31.0 `Conversation`, native `EventLog`, `MessageEvent`,
`ActionEvent`, `ObservationEvent`, and `ToolDefinition` are directly reused;
two live Clamd matrices passed; no parallel runtime/protocol was added.

AI5.8 independent full-system audit was initially `REJECTED`. Fix Rounds 1/2/3
completed and final Round 3 independent re-verification was `ACCEPTED`.
PostgreSQL revision `0006_media_scan_receipts`, shared `FileSessionRunLock`,
cancellation gate, permanent RED bypass oracle, two real delegate barriers,
and restart reconstruction are accepted. Round 2 default evidence is
`1900 passed, 1 skipped, 19 deselected`; Round 3 focused evidence is `85/94
passed`, with strict mypy, Ruff, diff, and cached-empty gates passing.

Pinned PNG V6 remains a controlled local gate. Real visual-provider use is
default-off and the official SDK hard stop cannot be bypassed. Public
production deployment, managed OIDC, and external long-term operations/SLOs
remain unauthorized. Monad is detachable/default-off; audio/PDF/OCR/ASR are
not implemented. AI6 multimodal expansion requires separate AI0 approval.

## 2026-08-14 Acceptance status

Status: `APPROVED`.

Independent foundation review returned `VERDICT: APPROVED`. The later real
visual provider gate also passed with a real PNG, official OpenHands LLM, and
`openai/qwen3.7-plus`, including concrete pixel facts and two follow-up rounds.
Its corrected nested Review-result state machine was independently accepted as
`APPROVED_CONTRACT_GATE` with 19 deterministic tests.

Key verification:

- focused architecture/product/SDK: `43 passed, 1 skipped`
- media API/core/adapters/message content: `171 passed`
- runtime contribution/tool/scoring: `21 passed`
- default API/general core/Monad default-disabled: `76 passed`
- persistence/migrations/restart recovery: `114 passed`
- Alembic upgrade `0005` -> downgrade `0004` -> upgrade head: PASS
- image gate: `BLOCKED_BY_OFFICIAL_SDK_GATE`, `provider_executed:false`, `env_file_read:false`
- real-image test default: `1 passed, 1 deselected`
- `git diff --check` and Ruff: PASS

Historical broader acceptance evidence remains valid context:

- backend: `1238 passed, 1 skipped, 19 deselected`
- PostgreSQL: `5 passed`
- Docker core/media build: PASS
- frontend lint/typecheck/`114` Vitest/build: PASS
- default E2E: `28/28`
- Monad E2E: `4/4`
- staging restore: `1 passed`

Task status:

- Task1, Task2, Task3, Task4, Task6, and Task7 are complete.
- Task5 is correctly hard-stopped by the official OpenHands SDK 1.31.0 public extension gate. `BLOCKED_BY_OFFICIAL_SDK_GATE` is the passing compliance result for that gate, not a project failure.
- Task8 engineering/report/diff gate is complete.
- Real visual-provider acceptance is complete for the local/staging gate.
- Historical code-gate claim (superseded/non-production): deterministic scanner
  tests passed, but they established no production implementation or acceptance.
  Only `fake-clean` isolation is verified with `productionMalwareScanningVerified=false`;
  AI5.7 owns `ScannerPort`, `ScanResult`, the replaceable production adapter, and fail-closed production boundary.

The accepted implementation directly reuses OpenHands `Message`, `ImageContent`, `Conversation`, and `ToolDefinition`; it does not introduce a second Runtime, Conversation, EventLog, or Tool protocol. All six disabled fresh-import combinations for `focusproof.api.app`, `focusproof.openhands_runtime.manager`, and `focusproof.openhands_runtime.synchronizer`, with media unset or `false`, reported `leaked=[]`.

Acceptance is layered: the image foundation and real visual interpretation
gate are complete. Production scanning implementation and acceptance remain
incomplete; current evidence is limited to `fake-clean` isolation with
`productionMalwareScanningVerified=false`. AI5.7 owns the future production boundary, and public production remains unauthorized.

## 2026-08-20 authoritative status correction

This correction supersedes the 2026-08-14 statements above that describe
production malware-scanning implementation or code gates as complete. The
whole AI5 image phase must not be reported as complete.

The accepted state has four separate layers:

1. **Foundation complete:** deterministic image handling plus the native
   OpenHands SDK 1.31.0 `ImageContent` -> `MessageEvent` -> `Conversation`
   event chain are complete.
2. **Pinned real PNG complete:**
   `docs/research/assets/ai5/task7/chromium-success.png` has SHA-256
   `7ee186d8b0efa5ca62039ab97655e811e748c86696fee1752f8c0fc7ef3f468e`.
   V6 used `openai/qwen3.7-plus` with exactly one visual provider completion,
   zero agent-decision completions, no retry, and eight structured visual
   facts. It recorded `parseStage=complete`, `errorCategory=none`, review
   `completed`, runner `PASS`, and independent decision
   `V6_REAL_IMAGE_GATE_FINAL_ACCEPTED`. V6 report SHA-256:
   `80305ffa837cf42bb79ab3a10f2e14c7ffd83ff426ed95fab01d1037f750afc3`;
   sidecar SHA-256:
   `80d76c711bb3c168cb0bbc2b992c1734e6201a69e527770bb0f473fca079ae17`.
3. **Production scanning pending:** production malicious-media/virus scanning
   is incomplete. Fake-clean is local isolation only, and
   `productionMalwareScanningVerified=false`.
4. **Broader acceptance pending:** broader image sets, formats, sizes,
   concurrency, recovery, and cost still require acceptance.

## Next goal: AI5.7 Production Media Safety Boundary

Deliver a `ScannerPort`, structured `ScanResult`, fail-closed policy, and a
replaceable production scanner adapter. Acceptance must cover `clean`,
`malicious`, `timeout`, `unavailable`, and `oversize`. `timeout` and
`unavailable` fail closed. Raw media remains quarantined and cannot enter the
LLM or OpenHands events until the scan result is clean. The adapter remains
replaceable, logs are redacted, and general workflows regress successfully.

Non-goals and invariants:

- do not change the Agent loop, general scoring, evidence model, or Monad;
- do not hard-code a scanner vendor or claim production safety complete;
- keep FocusProof a general knowledge-learning verification product, with
  Monad default-off and detachable;
- directly reuse the official OpenHands SDK; do not implement imitation
  Runtime, Conversation, EventLog, ImageContent, or Tool abstractions.
