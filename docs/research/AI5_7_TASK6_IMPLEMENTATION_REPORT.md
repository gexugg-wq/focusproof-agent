# AI5.7 Task6 Implementation Report

## Scope and design

Task6 reuses `ClamdMalwareScanner` through `ReadOnlyMediaSource`; it does not add a scanner protocol, runtime, event loop, or OpenHands type. `scripts/run_image_evidence_gate.py` remains unchanged. `scripts/run_real_image_evidence_gate.py` is now a Clamd-only, fail-closed certification step with visual provider and production LLM explicitly disabled.

The gate accepts an explicit CLI endpoint only and does not read project environment files. Reports omit endpoints, filesystem paths, payloads, SQL, credentials, and signature text. EICAR is assembled only in memory from test-fixture fragments and is never persisted or logged.

## RED / GREEN

- Initial Task6 contract: 11 expected failures proved the old runner was locked to `fake-clean`, read environment configuration, required a visual provider/model, and lacked live Clamd certification state.
- Scanner contract plus new Task6 contract: 53 passed, confirming the existing adapter already provided typed clean/malicious/timeout/unavailable/error outcomes and bounded cleanup.
- First live matrix: benign PNG, EICAR, timeout, and unavailable passed; the protocol-error harness incorrectly timed out. The harness was fixed to consume bounded INSTREAM frames before returning a daemon error; scanner production code was not changed.
- Compose regression exposed one obsolete test requiring an external Clamd endpoint. It was updated to require the pinned private sidecar, health dependency, and no host port.

## Live Clamd evidence

Pinned reference: `clamav/clamav:1.4.3@sha256:75fb5fd95fcbe1d7e6d240c369c1572b686ee2c95949d1042b5148de8eddebb4`.

Two independent runs completed with exit 0. Each produced the same sanitized matrix:

- `benign_png`: clean, passed, finalized
- `eicar`: malicious, rejected, not finalized
- `timeout`: timeout, fail closed
- `unavailable`: unavailable, fail closed
- `error`: error, fail closed

Both reports had `liveClamdExecuted=true`, `visualProviderEnabled=false`, `productionLlmEnabled=false`, and `productionMalwareScanningVerified=true`. Each run removed its container and temporary report in an exit trap. No Task6 container remains.

## Verification

- Task6/adapter/Compose focused: 94 passed.
- Media, ingestion, Task5/OpenHands regression: 528 passed, 1 skipped, with one obsolete external-endpoint assertion subsequently corrected.
- Earlier Task6 plus scanner run: 54 passed.
- No real LLM or visual provider was invoked.
- Staged files remained empty; no commit, push, merge, or amend was performed.

## Visual-provider contract recovery

### Incident and forensic evidence

Before AI0's correction, the untracked 2,560-line visual gate contract `agent-server/tests/ai5/test_real_image_gate_contract.py` was mistakenly deleted while its shared runner was being converted to the Task6 Clamd-only gate. The Task6 Clamd files were then frozen and a separate recovery task performed read-only forensics before editing.

The preserved cache is `/tmp/task6-old-visual-contract.pyc`, SHA-256 `5a6934451397212513218d7668b5d95393223bc2667ac96b7d23a52680b1ffe9`. Its header records a 92,045-byte CPython 3.12 source file and it contains pytest 9.1.1 assertion-rewrite globals. Recursive code-object inspection yields exactly 71 ordered, unique `test_*` functions.

No complete source copy was found in HEAD, Git diffs, `/tmp`, ext4 deleted-inode inspection, VS Code history, Codex cache, or archived task outputs. A genuine 435-line earlier visual runner source survived at `/mnt/d/web3/_patches/review2/work/scripts/run_real_image_evidence_gate.py`, SHA-256 `28cc1bae16feb86e028d20b2f05178d3281748f9a9b18452acabdcee04de9375`. Three later runner pycs preserved the evolved diagnostics, completion observer, report safety, EventLog, and product-chain API inventory. Recovery therefore is explicitly a **semantic-equivalence reconstruction**, not exact source recovery.

### Responsibility split

- `scripts/run_real_image_evidence_gate.py` remains the frozen Clamd-only Task6 certification gate. It alone may emit `productionMalwareScanningVerified=true` after the five-case live matrix.
- `scripts/run_real_visual_provider_gate.py` is the independent visual-provider gate, restored from the genuine earlier source and hardened using the preserved final pyc contracts. It always reports `mediaScanner=fake-clean`, `scope=local-test-only`, and `productionMalwareScanningVerified=false`.
- Real visual execution requires both the `real_llm` pytest marker and the explicit `--execute-real-provider` CLI switch. Default collection and the recovery verification never call a provider.
- The runner continues to construct the production app and use the official OpenHands SDK conversation/runtime path; it introduces no replacement Runtime, Conversation, EventLog, Action, Observation, or Tool type.

### 71-to-71 mapping and case-count ruling

`agent-server/tests/ai5/test_real_visual_provider_gate_contract.py` preserves the old function names in the same source order. A machine comparison between the pyc code-object walk and the new AST reports:

```text
old 71 new 71 unique 71
missing []
extra []
order_equal True
```

The mapping is therefore an identity mapping: every old function name maps to the same function name in the new independent contract. The covered groups are review-state validation; locked CLI/provider configuration; canonical path/symlink/hash/size/PNG identity; official SDK/TestLLM provenance and completion observation; environment restoration; report schema/redaction/size/depth limits; atomic report/sidecar rollback; failure diagnostics; safe EventLog summaries; v3 observation/fact/projection/scoring/review lineage and explicit consumed fact sets; transport outcome; product-chain completion; and provider attribution.

The historical handoff claimed 92 collected/passed. That number could not be reproduced. Reading the surviving pytest markers produces 146 cases, including the preserved 7-by-5 Cartesian strict-ID matrix. AI0 ruled that the reproducible pyc evidence takes precedence: coverage must not be deleted to manufacture 92. Fresh collection reports exactly `146 tests collected`.

No skip, xfail, empty assertion, test-function alias, or duplicate parameter padding is used. PASS and FAIL atomic-publication contracts exercise distinct schemas and rollback states.

### Recovery verification

- Independent visual contract: 146 passed; no real provider invoked.
- Exact mapping: 71 old functions, 71 new functions, no missing/extra names, identical order.
- Task6 Clamd/Compose/security plus Task5 OpenHands finite regression: 290 passed with `-m "not real_llm"`.
- Ruff check and format check passed for the visual runner and both AI5 visual test files.
- Strict mypy passed for `scripts/run_real_visual_provider_gate.py`.
- The existing Task6 evidence remains two independent live five-case Clamd matrices and the prior focused 94 passed; live Clamd was not rerun or modified during recovery.

## Fix Round 1 - independent-review findings

This round changes only the independent visual-provider runner, its product-path integration contract, and this report. The Clamd runner, Compose configuration, malware verification, and official OpenHands SDK remain unchanged.

Finding 1 is closed by a deterministic integration test that drives the real `_run_product_chain` through the production app, official SDK `Conversation`, the existing FocusProof media tool, native `ActionEvent`/`ObservationEvent`, and final report publication. RED evidence progressed through the previously unexercised boundaries: the SDK response was incorrectly rejected as a non-dict completion; real visual facts were incorrectly read from a missing review-response field; safe publication rejected the safe `messageCount` aggregate; and transport outcome was inferred before `providerAttempted` was present. GREEN now observes the official SDK completion object, derives at least three unique facts from actual observation payloads, requires a completed review and successful transport via `_require_product_success`, validates v3 lineage/consumed facts, publishes a redacted EventLog summary, and includes provider/model attribution.

Finding 2 is closed on the same real product path. The integration uses an official `TestLLM` subclass and asserts the runner's final published `checks.productionLlmUsed` is false. The production path calls `_production_llm_used(llm)`, whose `isinstance(llm, openhands.sdk.testing.TestLLM)` boundary makes only non-TestLLM instances true; the explicitly authorized real-provider contract retains the production-true assertion without invoking it in default verification.

Fresh default verification invokes no real visual provider or production LLM. The restored contract remains 71 ordered function names and 146 cases; the product-path integration is additive.

## Fix Round 2 - real audit lineage and final publication gate

### Scope and reviewer verification

This round changes only `scripts/run_real_visual_provider_gate.py`, the existing generic product extraction boundary in `agent-server/focusproof/openhands_runtime/result_extractor.py`, the two independent visual-provider tests, and this report. The reviewer findings were checked against the current code before editing:

- `_safe_eventlog_summary` re-hashed Observation text and minted `projection_*`, `score_*`, and `review_*` identities unrelated to the persisted product audit.
- `_require_product_success` ran before transport/provider diagnostics and audit summaries existed, and it checked only review status, completion success, and a three-fact count.

The findings therefore matched the repository state and no scope expansion was needed.

### TDD RED / GREEN

The first RED drove the real `_run_product_chain` with an official OpenHands SDK `TestLLM` subclass, captured the same run's `PersistentAuditProjectionStore`, published the runner result, and compared projection ID, source Observation ID, score event ID, review event ID, fact IDs, and consumed fact IDs. The old implementation failed the composite comparison: the real and reported consumed counts were both three, but the consumed IDs and projection/score/review IDs differed.

The second RED exercised `main` and final report publication with `completionSucceeded=true` but `attempts=0`; the derived diagnostics were `providerAttempted=false` and `transportOutcome=unknown`. The old implementation printed PASS, published PASS, and returned exit 0.

After switching to product audit lineage, the lineage integration became GREEN while the zero-attempt test remained RED. Additional publication mutations then recorded three missing decisions: source-lineage mismatch, wrong review event ID, and invalid safe EventLog counts still published PASS. Duplicate consumed IDs and a wrong score event ID were already rejected by the v3 validator. The final GREEN is 153 passed: the preserved 146-case contract plus seven product-path cases.

### Single lineage source and safe projection

`RuntimeResultExtractor` remains the owner that emits `score.calculated` and `review.completed`. The runner now reads those persisted product events from the existing `PersistentAuditProjectionStore.list(session_id)` query boundary. The generic `project_safe_completed_review_lineage` projection in `result_extractor.py` verifies and returns only:

- product score/review event IDs and event types;
- narrative projection IDs and source Observation event IDs;
- fact IDs, fact types, counts, and consumed-fact aggregation;
- score-to-review and projection-to-review references.

The runner no longer derives lineage identity from Observation text and no longer mints score, review, or projection IDs. Raw messages, image content, fact text, credentials, and secrets are excluded. The safe EventLog side keeps only official SDK event ID/type and bounded status/error-code fields plus counts and a digest of that safe list.

### Final fail-closed order

The product path now executes in this order:

1. complete the official SDK conversation and collect native events;
2. generate completion, provider, transport, visual-fact, and attribution diagnostics;
3. read the real product audit projection and build the safe v3 lineage/EventLog summary;
4. call `_require_product_success` with the complete publication candidate;
5. only after that call returns, construct `status="PASS"`.

The final gate requires completed review, successful response/completion, at least one attempt, `providerAttempted=true`, `transportOutcome=received`, at least three visual facts, valid/bound v3 lineage, valid safe EventLog counts/digest, provider/model attribution equality, and production-LLM attribution equality. `isinstance(llm, openhands.sdk.testing.TestLLM)` remains authoritative: `TestLLM` and subclasses report `productionLlmUsed=false`; non-`TestLLM` instances report true.

### Verification and evidence limits

- RED: 2 failed product-path tests, including an explicit old exit-0/PASS publication for the zero-attempt mutation.
- Intermediate mutation RED: 3 failed and 2 passed; the failures were source mismatch, wrong review ID, and invalid safe EventLog count.
- Preserved recovery contract: 71 old/new ordered unique `test_*` functions, no missing/extra names, order equal; exactly 146 cases collected and 146 passed.
- Visual runner contract plus product path: 153 passed.
- Task6 runner/scanner/Compose/malware/security/architecture focused set: 208 passed, 1 guarded-real-Clamd case skipped.
- Task5/OpenHands adapter/runtime/media-tool finite regression: 296 passed with `-m "not real_llm and not staging_external and not external and not postgres"`.
- Strict mypy passed for the visual runner and result extractor. Ruff, format, diff, staged-empty, `.orig`, and final Clamd hash/container evidence are recorded by the handoff verification after this report edit.

Exact verification commands were:

```text
.venv/bin/python -m pytest -q agent-server/tests/ai5/test_real_visual_provider_gate_contract.py agent-server/tests/ai5/test_real_visual_provider_product_path.py
.venv/bin/python -m pytest -q agent-server/tests/ai5/test_real_media_gate_contract.py agent-server/tests/media_adapters/test_clamd_malware_scanner.py agent-server/tests/media_core/test_malware_scanner_contract.py agent-server/tests/integration/test_media_malware_admission.py agent-server/tests/media_adapters/test_media_composition.py agent-server/tests/media_adapters/test_media_security_policy.py agent-server/tests/architecture/test_media_import_boundaries.py -m "not real_llm and not staging_external and not external and not postgres"
.venv/bin/python -m pytest -q agent-server/tests/openhands_adapter agent-server/tests/openhands_runtime agent-server/tests/integration/test_image_review.py agent-server/tests/domain/test_media_scoring.py -m "not real_llm and not staging_external and not external and not postgres"
.venv/bin/python -m ruff check scripts/run_real_visual_provider_gate.py agent-server/focusproof/openhands_runtime/result_extractor.py agent-server/tests/ai5/test_real_visual_provider_gate_contract.py agent-server/tests/ai5/test_real_visual_provider_product_path.py
.venv/bin/python -m ruff format --check scripts/run_real_visual_provider_gate.py agent-server/focusproof/openhands_runtime/result_extractor.py agent-server/tests/ai5/test_real_visual_provider_gate_contract.py agent-server/tests/ai5/test_real_visual_provider_product_path.py
.venv/bin/python -m mypy --strict scripts/run_real_visual_provider_gate.py agent-server/focusproof/openhands_runtime/result_extractor.py
git diff --check
git diff --cached --name-only
```

No real visual provider, production LLM, live Clamd endpoint, or container was invoked by Fix Round 2. The working tree was already cumulative and dirty; the visual runner, both visual tests, and this report were pre-existing untracked paths. Git cannot timestamp ownership within those untracked files, so the RED logs, explicit path-scoped diffs, and before/after Clamd hashes define this round's evidence boundary. This report does not claim independent acceptance.


## Fix Round 3 - official Observation provenance and audit event identity

### Scope and review verification

This round is limited to the remaining lineage identity finding. It changes the generic completed-review extraction/projection boundary, the independent visual runner's call into that boundary, focused product/extractor tests, and this report. It does not change the Clamd runner, scanner, composition, Compose file, malware admission logic, official OpenHands SDK, final product-success decision order, or any AI5.8 work.

The independent review was reproduced against the cumulative working tree before production edits. The previous safe projection accepted any strict unique narrative `sourceObservationEventId` without checking the same official SDK EventLog, and returned the stored `review.completed` ID without independently deriving it. The existing creation boundary already used deterministic `evt_score_<accepted-review-draft-observation-id>` and `evt_review_<accepted-review-draft-observation-id>` identities, so no new self-reference, audit subsystem, or migration was needed.

### TDD RED / GREEN

The pre-edit visual baseline was 153 passed. New product-path tests use the production app, official SDK `TestLLM`, `Conversation`, and native EventLog, then mutate cloned real audit events immediately before the safe product projection. They do not mutate the generated summary and do not call an isolated helper as proof.

The first RED was five failures: the normal path lacked persisted score/review source anchors, while a missing Observation source, an ActionEvent ID used as source, a review-draft Observation used as narrative source, and a changed `review.completed` audit event ID all still published PASS with exit 0. A fifth real-audit identity mutation changed the narrative projection ID and matching review projection link together; it also published PASS with exit 0. Focused extractor RED independently showed the missing score/review source anchor.

The minimal GREEN keeps `RuntimeResultExtractor` as the event owner:

- score and review payloads persist the accepted official `ReviewDraftObservation` ID as `sourceObservationEventId`;
- the pre-existing deterministic score/review event ID rule is independently recomputed from that official source;
- each narrative audit entry persists its evidence ID plus its verification Observation source;
- the safe generic projection requires the same run's official native events, checks narrative sources are successful evidence-matching `VerificationObservation` instances, and rejects missing, wrong-type, or wrong-semantic sources;
- the projection ID is independently recomputed from the persisted evidence ID, official source ID, and ordered fact IDs, while existing consumed-fact, score-to-review, and projection-to-review cross-references remain required;
- the runner passes the same official native events to the generic product projection and does not mint or hash lineage identities.

The target GREEN was 6 passed: one normal product/extractor path plus five real-audit mutations. The five final mutation outcomes were all published `status=FAIL`, exit 1:

- nonexistent strict source Observation ID;
- official ActionEvent ID used as a narrative/score/review source;
- official review-draft Observation ID used with the wrong narrative-source semantics;
- coordinated narrative/review projection ID replacement;
- `review.completed` audit event ID replacement with all payload fields unchanged.

The normal path compares projection ID, narrative source Observation ID, score event ID, review event ID, fact IDs, and consumed fact IDs exactly against the persisted product audit. It additionally checks all narrative sources belong to the same official ObservationEvent set and both score/review anchors equal the accepted review-draft Observation ID used to derive their deterministic event IDs.

### Verification and frozen Clamd evidence

- Visual contract plus product path: 158 passed. The preserved visual contract alone still collects exactly 146 cases; the preserved pyc comparison remains 71 old/new ordered unique function names, no missing/extra names, and equal order.
- Result extractor/audit projection focused: 22 passed. The broader focused visual/extractor set was 185 passed.
- Task6 non-live focused: 208 passed, 1 guarded live-Clamd case skipped.
- Task5/OpenHands finite regression: 296 passed with `not real_llm and not staging_external and not external and not postgres`.
- Ruff check, Ruff format check, and strict mypy passed for the changed Python boundaries; `git diff --check` passed, staged paths were empty, and no `*.orig` file existed.
- All product executions used the official SDK `TestLLM`; no real visual provider, production LLM, live Clamd endpoint, or container was invoked.

The eight frozen Task6/Clamd path SHA-256 values were identical before and after this round:

```text
3ccb66c3efb8b706fadf5a266bbbd70bd8b5c9a918625d85fe583e072bbf90c8  scripts/run_real_image_evidence_gate.py
c5bdeb3ebe4f059a5530e1ceb90ba2b5b8d2268a35b468cadc43f47799a9860a  agent-server/focusproof/media_adapters/clamd_malware_scanner.py
667159308e640a18f03c748ff5fc24c1aa7e2d2a8784e1bde751873fa00e6f4a  agent-server/focusproof/bootstrap/media_composition.py
6623e6b81187d59ad4f1550c1670bb83c65b0d5a1fb1f03e6f678c78618f7f54  deploy/compose.staging.yml
56a49794d68e87071cab05eccac4b39cbd7e199f6bfdff734e1c1699c00335e4  agent-server/tests/ai5/test_real_media_gate_contract.py
b86e6fc3ab46be4f5e130373dc1d410d343f07a4728ba4c5db20abeff2e8bb70  agent-server/tests/media_adapters/test_clamd_malware_scanner.py
12b314ca9f7c3e8f08f485db3664d8248234be4317ceacb1205ec6aba65643b8  agent-server/tests/integration/test_media_malware_admission.py
3d0e88a862d12a96a3e14cae7c1126f00bad70589e6ff7167e8d57177b9b0d5f  agent-server/tests/media_adapters/test_media_composition.py
```

The cumulative untracked path inventory remained 103 paths with the same sorted-list SHA-256 `563cd48ab648abffaeebc2b0d99ab4e3a36af6de25c1f6c76730837257002a3c` before and after. These hashes and the path-scoped RED/GREEN evidence define the ownership boundary in the pre-existing dirty tree. This section does not claim independent acceptance.


## Residual risk and status

The reconstruction preserves the executable function inventory, pytest parameters, high-risk assertions, official SDK boundary, and observable gate contracts, but it cannot reproduce deleted comments, exact local expression structure, or exact failure-message wording. The historical 92 count remains unexplained and is retained only as an incident statement; 146 is the current reproducible baseline. A fresh independent reviewer must inspect semantics rather than accepting counts alone.

AI5_7_TASK6_FIX_ROUND1_READY_FOR_REVIEW

AI5_7_TASK6_FIX_ROUND2_READY_FOR_REVIEW

AI5_7_TASK6_FIX_ROUND3_READY_FOR_REVIEW

## Fix Round 4: independently derived visual fact identity

### Scope and root cause

Round 4 fixes the coordinated fact-identity attack without changing Task6 Clamd behavior, Monad, the real visual/LLM provider, or official OpenHands SDK types. The Round 3 safe projector proved that a lineage source was a successful official `VerificationObservation`, but it still accepted `facts[].factId` from the audit summary and recomputed the projection from those attacker-controlled IDs. Replacing every fact ID, both consumed-ID lists, the projection ID, and the review projection link therefore remained self-consistent and could publish PASS.

`focusproof.media_projection.visual_fact_identity` is now the single pure deterministic owner of visual-fact whitespace normalization, ordered fact IDs, text digests, and projection IDs. `ImageNarrativeProvider` uses it when creating product lineage. `project_safe_completed_review_lineage` uses the same formula only after independently reading the same run's official `VerificationObservation.facts["visual_facts"]`; it does not trust the stored fact IDs as expected values.

The independent extractor now requires exactly one ordered match for every recovered fact: count, `factType == "visual_text"`, normalized-text digest, derived fact ID, redaction marker, lineage consumed IDs, sorted score consumed IDs, derived projection, and review projection link. The source Observation must be paired with its official media `ActionEvent` by action ID, tool name, tool-call ID, action type, and evidence ID in the supplied native EventLog. A real Observation copied from another Conversation without its originating ActionEvent therefore fails closed.

The helper keeps the previous explanation-only fallback order. A broad regression initially caught an accidental change to this old behavior (`test_image_explanation_that_copies_goal_is_not_learning` scored 63); the provider was corrected without changing scoring code, after which the exact Task5/OpenHands set returned to 296 passed.

### TDD RED and mutation evidence

The Round 4 parameterized product-path contract was added before product edits. Existing Round 3 mutations stayed green while coordinated fact-ID replacement, fact reordering, fact type replacement, and fact insertion/deletion exposed exit-0/PASS publication. The initial fact-text and cross-session fixtures also exposed SDK deep-copy errors; they were narrowed to clone only the target official Observation. The corrected cross-session test then reproduced exit-0/PASS against the old projector.

Because the corrected fact-text fixture was completed after the first product edit, an explicit mutation check was run before declaring completion. A temporary single-file extractor copy removed independent derived fact-ID/text use and restored raw summary fact IDs. The exact command

```text
.venv/bin/python -m pytest -q agent-server/tests/ai5/test_real_visual_provider_product_path.py -k fact_text
```

then failed with `assert 0 == 1` and printed a published PASS report (`1 failed, 18 deselected`). The final extractor was restored from the pre-recorded mirror SHA-256 `a5f148ea6c448bda73af8ebf0c70e4e37c635404fab5a64d12e7650d4919abb3`; the same command then reported `1 passed, 18 deselected`.

The final Round 4 attack matrix covers all coordinated fact IDs, reorder, text change, type change, add, delete, and a real cross-session Observation. Together with the retained Round 3 real-audit and safe-summary cases it continues to reject missing source, ActionEvent-as-source, wrong Observation semantics, review-ID replacement, coordinated projection/review replacement, coordinated score/consumed replacement, duplicate consumed IDs, and cross-session provenance.

### Normal identity checks

The normal official-SDK product path keeps eleven identity groups true:

1. summary projection equals product narrative projection;
2. summary source equals the narrative Observation source;
3. summary score equals the product score event ID;
4. summary review equals the product review event ID;
5. ordered summary fact IDs equal ordered product fact IDs;
6. summary consumed IDs equal the score payload consumed IDs;
7. review-to-score link equals the score event ID;
8. review-to-projection link equals the narrative projection ID;
9. every narrative source belongs to the official ObservationEvent set;
10. score and review completion anchors are official and equal;
11. score/review event IDs are independently derived from that accepted review-draft Observation.

### Exact verification commands and outputs

Preserved visual baseline:

```text
.venv/bin/python -m pytest --collect-only -q agent-server/tests/ai5/test_real_visual_provider_gate_contract.py
146 tests collected in 5.19s
grep -c '^def test_' agent-server/tests/ai5/test_real_visual_provider_gate_contract.py
71
```

Visual contract plus product path:

```text
.venv/bin/python -m pytest -q agent-server/tests/ai5/test_real_visual_provider_gate_contract.py agent-server/tests/ai5/test_real_visual_provider_product_path.py
165 passed, 1 warning in 31.48s
```

Extractor/audit 22-pass command, recorded exactly as executed:

```text
.venv/bin/python -m pytest -q agent-server/tests/openhands_runtime/test_conversation_lifecycle.py agent-server/tests/openhands_runtime/test_event_projection.py agent-server/tests/openhands_runtime/test_failure_diagnostics.py agent-server/tests/openhands_runtime/test_runtime_failure.py agent-server/tests/integration/test_image_review.py agent-server/tests/runtime/test_audit_projection.py
22 passed in 7.12s
```

Task6/Clamd non-external contracts:

```text
.venv/bin/python -m pytest -q agent-server/tests/ai5/test_real_media_gate_contract.py agent-server/tests/media_adapters/test_clamd_malware_scanner.py agent-server/tests/media_core/test_malware_scanner_contract.py agent-server/tests/integration/test_media_malware_admission.py agent-server/tests/media_adapters/test_media_composition.py agent-server/tests/media_adapters/test_media_security_policy.py agent-server/tests/architecture/test_media_import_boundaries.py -m "not real_llm and not staging_external and not external and not postgres"
208 passed, 1 skipped in 3.32s
```

Task5/OpenHands/media finite regression:

```text
.venv/bin/python -m pytest -q agent-server/tests/openhands_adapter agent-server/tests/openhands_runtime agent-server/tests/integration/test_image_review.py agent-server/tests/domain/test_media_scoring.py -m "not real_llm and not staging_external and not external and not postgres"
296 passed, 1 warning in 109.92s
```

Static checks:

```text
.venv/bin/python -m ruff check scripts/run_real_visual_provider_gate.py agent-server/focusproof/media_projection/visual_fact_identity.py agent-server/focusproof/media_projection/image_narrative_provider.py agent-server/focusproof/openhands_runtime/result_extractor.py agent-server/tests/integration/test_image_review.py agent-server/tests/ai5/test_real_visual_provider_gate_contract.py agent-server/tests/ai5/test_real_visual_provider_product_path.py
All checks passed!
.venv/bin/python -m ruff format --check scripts/run_real_visual_provider_gate.py agent-server/focusproof/media_projection/visual_fact_identity.py agent-server/focusproof/media_projection/image_narrative_provider.py agent-server/focusproof/openhands_runtime/result_extractor.py agent-server/tests/integration/test_image_review.py agent-server/tests/ai5/test_real_visual_provider_gate_contract.py agent-server/tests/ai5/test_real_visual_provider_product_path.py
7 files already formatted
.venv/bin/python -m mypy --strict scripts/run_real_visual_provider_gate.py agent-server/focusproof/media_projection/visual_fact_identity.py agent-server/focusproof/media_projection/image_narrative_provider.py agent-server/focusproof/openhands_runtime/result_extractor.py
Success: no issues found in 4 source files
```

Final working-tree hygiene after the report update:

```text
git diff --check
exit 0
git diff --cached --name-only
<empty>
```

The final filesystem scan found zero `*.orig`/`*.rej` files. The process scan found no pytest, visual-gate, or Clamd process. `docker ps` contained only pre-existing Argus services and a four-week-old BuildKit container; there was no Round 4 or Clamd container. The cumulative worktree remained intentionally unstaged and uncommitted on `agent/monad-evidence-plugin`.

No real provider, production LLM, external Clamd endpoint, container, Monad plugin, or replacement SDK Runtime/Conversation/EventLog/Action/Observation/Tool was used or changed in Round 4.

AI5_7_TASK6_FIX_ROUND4_READY_FOR_REVIEW

## Fix Round 5: current official Conversation ownership

### Scope and root cause

Round 5 closes the remaining cross-session provenance gap without changing the SDK,
the shared visual-fact identity helper, scoring, Clamd, Postgres, Monad, or any AI5.8
work. `ConversationFactory` already creates the official OpenHands SDK 1.31.0
`LocalConversation` held by `ConversationHandle`, but `ConversationManager` copied
`conversation.state.events` to a caller-supplied `Sequence` before extraction, and the
publication projector independently accepted another caller-supplied native event list.
Round 4 therefore proved only that Action/Observation pairs were self-consistent within
that supplied list, not that they belonged to the current managed Conversation.

The minimal fix removes `native_events` from `RuntimeResultExtractor.extract` and from
`project_safe_completed_review_lineage`. Both now receive the current
`ConversationHandle`, require its conversation to be the official SDK
`LocalConversation`, require the SDK `ConversationState.id` to equal the handle UUID,
and only then snapshot `conversation.state.events` internally. The production visual
gate passes the exact handle returned by its current `ConversationManager`. There is no
compatibility list/DTO/run-ID path capable of publishing PASS.

### TDD RED and GREEN

The first RED used two complete real SDK `LocalConversation` runs through the product
app. It copied the foreign image `ActionEvent` and its linked successful
`VerificationObservation` as a pair, then coordinated evidence ID, ordered facts and
digests, both consumed-ID fields, projection ID, and review projection link. Against the
old projector the attack returned exit 0 and published PASS, so the expected-failure
test correctly failed:

```text
.venv/bin/python -m pytest agent-server/tests/ai5/test_real_visual_provider_product_path.py -k paired_cross_session_events -vv
1 failed, 19 deselected; assertion showed exit_code 0 instead of 1 and stdout published PASS
```

The separate arbitrary-list API RED proved the old safe-summary boundary still accepted
the deprecated list when no product audit was requested:

```text
.venv/bin/python -m pytest agent-server/tests/ai5/test_real_visual_provider_product_path.py -k caller_supplied_native_event_list -vv
1 failed, 20 deselected; DID NOT RAISE TypeError
```

After the interface change, the normal product path asserts that publication received
the exact current manager-owned `ConversationHandle`. The paired attack, the retained
single foreign Observation attack, fact-text list replacement, arbitrary list call, and
all prior audit/fact mutations fail closed. Direct extractor/audit integration tests now
construct official SDK `Conversation` objects, assert the returned type is
`LocalConversation`, and append their native events through the public
`ConversationState.append_event` API. No second Runtime, Conversation, EventLog, Action,
Observation, or Tool type was added.

### Exact verification commands and results

```text
.venv/bin/python -m pytest -q agent-server/tests/ai5/test_real_visual_provider_product_path.py
21 passed, 1 warning in 34.24s

.venv/bin/python -m pytest --collect-only -q agent-server/tests/ai5/test_real_visual_provider_gate_contract.py
146 tests collected in 7.51s

/usr/bin/grep -c ^def.test_ agent-server/tests/ai5/test_real_visual_provider_gate_contract.py
71

.venv/bin/python -m pytest -q agent-server/tests/ai5/test_real_visual_provider_gate_contract.py agent-server/tests/ai5/test_real_visual_provider_product_path.py
167 passed, 1 warning in 36.14s

.venv/bin/python -m pytest -q agent-server/tests/openhands_runtime/test_conversation_lifecycle.py agent-server/tests/openhands_runtime/test_event_projection.py agent-server/tests/openhands_runtime/test_failure_diagnostics.py agent-server/tests/openhands_runtime/test_runtime_failure.py agent-server/tests/integration/test_image_review.py agent-server/tests/runtime/test_audit_projection.py
22 passed in 6.38s

.venv/bin/python -m pytest -q agent-server/tests/ai5/test_real_media_gate_contract.py agent-server/tests/media_adapters/test_clamd_malware_scanner.py agent-server/tests/media_core/test_malware_scanner_contract.py agent-server/tests/integration/test_media_malware_admission.py agent-server/tests/media_adapters/test_media_composition.py agent-server/tests/media_adapters/test_media_security_policy.py agent-server/tests/architecture/test_media_import_boundaries.py -m "not real_llm and not staging_external and not external and not postgres"
208 passed, 1 skipped in 3.00s

.venv/bin/python -m pytest -q agent-server/tests/openhands_adapter agent-server/tests/openhands_runtime agent-server/tests/integration/test_image_review.py agent-server/tests/domain/test_media_scoring.py -m "not real_llm and not staging_external and not external and not postgres"
296 passed, 1 warning in 96.81s

.venv/bin/python -m pytest -q agent-server/tests/domain/test_media_scoring.py::test_image_explanation_that_copies_goal_is_not_learning
1 passed in 4.85s
```

The installed SDK was verified offline from distribution metadata rather than imported
through any provider path. The exact Python command parsed `openhands-sdk`'s `RECORD`,
located every row, recomputed every recorded SHA-256, and checked every recorded size.
It returned:

```text
.venv/bin/python -c "import base64,csv,hashlib,importlib.metadata as m,io; d=m.distribution('openhands-sdk'); rows=list(csv.reader(io.StringIO(d.read_text('RECORD') or ''))); missing=[p for p,h,s in rows if not d.locate_file(p).is_file()]; bad_hash=[p for p,h,s in rows if h and 'sha256='+base64.urlsafe_b64encode(hashlib.sha256(d.locate_file(p).read_bytes()).digest()).decode().rstrip('=') != h]; bad_size=[p for p,h,s in rows if s and d.locate_file(p).stat().st_size != int(s)]; print({'version':d.version,'recordRows':len(rows),'missing':missing,'badHash':bad_hash,'badSize':bad_size}); raise SystemExit(bool(missing or bad_hash or bad_size))"
{'version': '1.31.0', 'recordRows': 277, 'missing': [], 'badHash': [], 'badSize': []}
```

Static checks were run with these exact path sets:

```text
.venv/bin/python -m ruff check scripts/run_real_visual_provider_gate.py agent-server/focusproof/media_projection/visual_fact_identity.py agent-server/focusproof/media_projection/image_narrative_provider.py agent-server/focusproof/openhands_runtime/result_extractor.py agent-server/focusproof/openhands_runtime/manager.py agent-server/tests/integration/test_image_review.py agent-server/tests/persistence/test_restart_recovery.py agent-server/tests/ai5/test_real_visual_provider_gate_contract.py agent-server/tests/ai5/test_real_visual_provider_product_path.py
All checks passed!

.venv/bin/python -m ruff format --check scripts/run_real_visual_provider_gate.py agent-server/focusproof/media_projection/visual_fact_identity.py agent-server/focusproof/media_projection/image_narrative_provider.py agent-server/focusproof/openhands_runtime/result_extractor.py agent-server/focusproof/openhands_runtime/manager.py agent-server/tests/integration/test_image_review.py agent-server/tests/persistence/test_restart_recovery.py agent-server/tests/ai5/test_real_visual_provider_gate_contract.py agent-server/tests/ai5/test_real_visual_provider_product_path.py
9 files already formatted

.venv/bin/python -m mypy --strict scripts/run_real_visual_provider_gate.py agent-server/focusproof/media_projection/visual_fact_identity.py agent-server/focusproof/media_projection/image_narrative_provider.py agent-server/focusproof/openhands_runtime/result_extractor.py agent-server/focusproof/openhands_runtime/manager.py
Success: no issues found in 5 source files
```

All tests used official SDK `TestLLM`, fake-clean media scanning, SQLite/temp storage,
and local fixtures. No real LLM or visual provider, external Clamd/Postgres, container,
or Monad plugin was invoked. The cumulative worktree remains intentionally unstaged and
uncommitted for independent AI0 review.

AI5_7_TASK6_FIX_ROUND5_READY_FOR_REVIEW

## Fix Round 6: manager-owned publication provenance

### Scope and reviewed root cause

Round 6 accepts the independent review finding that Round 5 still placed the trust
boundary too low. The public publication helper and extractor accepted a caller-provided
`ConversationHandle`; checking only that its conversation was an official SDK
`LocalConversation` and that `ConversationState.id` equalled the handle UUID did not
prove manager ownership. A caller could construct a second official `LocalConversation`,
reuse the current UUID in a forged handle, and supply a complete internally consistent
foreign Action/Observation pair.

The publication trust boundary now belongs to `ConversationManager`. Its public
`project_safe_completed_review_lineage(session_id)` method accepts only the session ID,
acquires the existing per-session lock, obtains the handle through the manager's own
cache/restore path, snapshots the official `LocalConversation.state.events`, and invokes
the module-private projector with that trusted snapshot. For persistent sessions the
manager loads the stored owner and reuses `_get_or_restore_unlocked`; legacy sessions use
the manager cache. The product gate first builds the generic safe event summary and then
passes only `session_id` to this manager method.

`RuntimeResultExtractor` and `project_safe_completed_review_lineage` are no longer public
symbols. Their implementations are module-private, and the manager supplies the native
EventLog snapshot internally for both extraction and publication. The production gate
has no `ConversationHandle`, `LocalConversation`, native-event list, wrapped-list, or
caller-provided run identifier input. No ownership token, DTO, SDK type imitation, or
self-declared run/conversation field was added.

### True RED and GREEN attack contract

The Round 6 RED was written before the production change. It creates two real official
SDK conversations through the product application, copies the foreign media
`ActionEvent` and linked successful `VerificationObservation` as a complete pair, and
coordinates evidence ID, ordered fact IDs/digests, consumed IDs, projection identity,
and review projection link. It additionally creates another official
`LocalConversation` with the current conversation UUID, wraps the candidate events in an
attacker-controlled list subclass, and places that conversation into a forged handle
whose session and conversation IDs match the target. There is no `TypeError` catch.

Against Round 5 the actual product publication returned exit 0 and published PASS, so
the expected-failure assertion correctly produced RED:

```text
.venv/bin/python -m pytest agent-server/tests/ai5/test_real_visual_provider_product_path.py -k paired_cross_session_events -vv
1 failed, 20 deselected; assertion showed exit_code 0 instead of 1 and stdout published PASS
```

After the manager boundary change, the same forged handle and wrapped list have no input
channel into publication. The test tampers the real product audit coherently, while the
manager independently reads only its current managed EventLog. The current EventLog has
neither the foreign Action nor Observation, so the manager projector rejects the audit;
the real gate main path returns exit 1 and publishes FAIL. Direct regression assertions
also prove the two old public result-extractor entry points do not exist and that the
manager publication method signature contains only `self` and `session_id`.

```text
.venv/bin/python -m pytest agent-server/tests/ai5/test_real_visual_provider_product_path.py -k paired_cross_session_events -vv
1 passed, 20 deselected, 1 warning in 9.72s

.venv/bin/python -m pytest -q agent-server/tests/ai5/test_real_visual_provider_product_path.py
21 passed, 1 warning in 36.06s
```

The normal product test continues to exercise all eleven identity groups documented in
Round 4: exact projection, narrative source, score, review, ordered facts, consumed facts,
review-to-score, review-to-projection, official source membership, equal official
completion anchors, and deterministic score/review event IDs. All retained Round 4 and
Round 5 fact, source, pairing, cross-session, audit, and safe-summary mutations remain
fail-closed. The explanation-copy scoring fallback is unchanged.

### Exact verification commands and results

```text
.venv/bin/python -m pytest --collect-only -q agent-server/tests/ai5/test_real_visual_provider_gate_contract.py
146 tests collected in 5.93s

/usr/bin/grep -c ^def.test_ agent-server/tests/ai5/test_real_visual_provider_gate_contract.py
71

.venv/bin/python -m pytest -q agent-server/tests/ai5/test_real_visual_provider_gate_contract.py agent-server/tests/ai5/test_real_visual_provider_product_path.py
167 passed, 1 warning in 34.82s

.venv/bin/python -m pytest -q agent-server/tests/openhands_runtime/test_conversation_lifecycle.py agent-server/tests/openhands_runtime/test_event_projection.py agent-server/tests/openhands_runtime/test_failure_diagnostics.py agent-server/tests/openhands_runtime/test_runtime_failure.py agent-server/tests/integration/test_image_review.py agent-server/tests/runtime/test_audit_projection.py
22 passed in 7.03s

.venv/bin/python -m pytest -q agent-server/tests/ai5/test_real_media_gate_contract.py agent-server/tests/media_adapters/test_clamd_malware_scanner.py agent-server/tests/media_core/test_malware_scanner_contract.py agent-server/tests/integration/test_media_malware_admission.py agent-server/tests/media_adapters/test_media_composition.py agent-server/tests/media_adapters/test_media_security_policy.py agent-server/tests/architecture/test_media_import_boundaries.py -m "not real_llm and not staging_external and not external and not postgres"
208 passed, 1 skipped in 3.35s

.venv/bin/python -m pytest -q agent-server/tests/openhands_adapter agent-server/tests/openhands_runtime agent-server/tests/integration/test_image_review.py agent-server/tests/domain/test_media_scoring.py -m "not real_llm and not staging_external and not external and not postgres"
296 passed, 1 warning in 103.68s

.venv/bin/python -m pytest -q agent-server/tests/domain/test_media_scoring.py::test_image_explanation_that_copies_goal_is_not_learning
1 passed in 5.30s
```

Static verification used the Round 5 production/helper paths plus the manager and all
three directly affected regression files:

```text
.venv/bin/python -m ruff check scripts/run_real_visual_provider_gate.py agent-server/focusproof/media_projection/visual_fact_identity.py agent-server/focusproof/media_projection/image_narrative_provider.py agent-server/focusproof/openhands_runtime/result_extractor.py agent-server/focusproof/openhands_runtime/manager.py agent-server/tests/integration/test_image_review.py agent-server/tests/persistence/test_restart_recovery.py agent-server/tests/openhands_runtime/test_media_runtime_contribution.py agent-server/tests/ai5/test_real_visual_provider_gate_contract.py agent-server/tests/ai5/test_real_visual_provider_product_path.py
All checks passed!

.venv/bin/python -m ruff format --check scripts/run_real_visual_provider_gate.py agent-server/focusproof/media_projection/visual_fact_identity.py agent-server/focusproof/media_projection/image_narrative_provider.py agent-server/focusproof/openhands_runtime/result_extractor.py agent-server/focusproof/openhands_runtime/manager.py agent-server/tests/integration/test_image_review.py agent-server/tests/persistence/test_restart_recovery.py agent-server/tests/openhands_runtime/test_media_runtime_contribution.py agent-server/tests/ai5/test_real_visual_provider_gate_contract.py agent-server/tests/ai5/test_real_visual_provider_product_path.py
10 files already formatted

.venv/bin/python -m mypy --strict scripts/run_real_visual_provider_gate.py agent-server/focusproof/media_projection/visual_fact_identity.py agent-server/focusproof/media_projection/image_narrative_provider.py agent-server/focusproof/openhands_runtime/result_extractor.py agent-server/focusproof/openhands_runtime/manager.py
Success: no issues found in 5 source files
```

The installed SDK was verified offline from its distribution `RECORD`; all 277 rows for
version 1.31.0 existed and every recorded SHA-256 and size matched:

```text
{'version': '1.31.0', 'recordRows': 277, 'missing': [], 'badHash': [], 'badSize': []}
```

All executions used official SDK `TestLLM`, fake-clean scanning, local SQLite/temp
storage, and offline metadata. No real LLM or visual provider, external Clamd/Postgres,
container, Monad plugin, SDK source modification, or AI5.8 work was invoked.

Final worktree hygiene was checked after the report update:

```text
git diff --check
exit 0

git diff --cached --name-only
<empty>

/usr/bin/find . -type f -name *.orig -print
<empty>

/usr/bin/find . -type f -name *.rej -print
<empty>

/usr/bin/pgrep -af pytest
<no matches; exit 1>

/usr/bin/pgrep -af run_real_visual_provider_gate
<no matches; exit 1>

/usr/bin/pgrep -af clamd
<no matches; exit 1>
```

`docker ps` showed only the four pre-existing Argus/BuildKit containers
(`argus-pg`, `argus-minio`, `argus-es`, and
`buildx_buildkit_focusproof-ai4c-repro0`); there was no Round 6, visual-provider,
Clamd, or test Postgres container. The branch remains
`agent/monad-evidence-plugin` with the cumulative dirty worktree intentionally unstaged
and uncommitted for independent AI0 review.

AI5_7_TASK6_FIX_ROUND6_READY_FOR_REVIEW
