# AI5.7 Task 3 Implementation Report

## Scope

- Implemented only Task 3: ingestion, composition, and fail-closed profiles.
- Baseline HEAD remained `9a79998f6f62853d8dc000969ceb8a6f43040ba6`.
- Preserved the accepted Task 1/2 dirty tree; no stage, commit, push, merge, reset,
  checkout, revert, or SDK modification was performed.

## RED / GREEN

- Initial ingestion RED: `49 passed, 1 failed`; the clean path had no scan-attempt
  audit participation.
- Atomic rejection RED: `45 passed, 5 failed`; all frozen non-clean outcomes had a
  commit between attempt persistence and reservation rejection.
- Profile/composition RED: `41 passed, 2 failed`; production had no explicit visual
  fail-closed flag and composition still created `legacy-unverified` clamd limits.
- Final Task 3 specialty GREEN: `93 passed in 0.80s`.

## Implementation

- Extended the existing media UoW contract with the accepted Task 1
  `MediaScanAuditRepository`; it uses the same SQLAlchemy session and commit as media
  state and is constructed lazily so disabled media does not eagerly compose it.
- Ingestion derives deterministic attempt/receipt identities from the existing
  session/media/idempotency tuple. The Task 1 repository and database uniqueness
  constraints remain the concurrency/replay authority.
- Every returned frozen verdict records exactly one attempt. Actual clean records the
  attempt and one receipt in the same transaction before validation or media completion.
- Malicious, oversize, timeout, unavailable, error, and migration-only unknown record an
  attempt and reject the reservation in one transaction, then stop before validation,
  normalization, object staging, safe facts, or vision.
- A scanner exception is sanitized into frozen `error + daemon_error` audit semantics;
  cancellation continues to propagate without creating a fabricated scan outcome.
- Receipt hash, attempt ID, artifact hash, definitions freshness, and complete resource
  snapshot are transferred from the Task 2 verdict. Task 3 only places the existing
  quarantine identifier and an expiry value into the Task 1 receipt contract; it does not
  implement TTL enforcement, janitor behavior, or codec isolation.
- Production media policy exposes `visual_provider_enabled=False`. Composition passes one
  immutable `ClamdLimits` snapshot to the sole existing `ClamdMalwareScanner`.

## Atomicity and recovery matrix

| Path | Atomic database unit | Downstream/file result |
| --- | --- | --- |
| clean | attempt + receipt | commit precedes validation/finalization |
| non-clean verdict | attempt + reservation reject | no validator, normalizer, stage, safe fact, or vision |
| scanner exception | error attempt + reservation reject | sanitized detail; quarantine cleanup remains existing behavior |
| audit/receipt DB failure | UoW rollback | no completed media; outer cleanup/compensation runs |
| media finalize failure | clean audit remains durable; media transaction rolls back | staged object aborted by existing compensation |
| replay/concurrency | deterministic IDs + Task 1 uniqueness/replay repository | no duplicate attempt or receipt |
| cancellation | existing cancellation gate/cleanup | no half-completed media state or fabricated verdict |

## OpenHands reuse and untouched boundaries

No Runtime, Conversation, EventLog, Action, Observation, Tool, SDK source, Manager,
Factory, Synchronizer, ResultExtractor, scoring, Monad, agent loop, frontend, API
contract, safe-fact projection, quarantine policy, codec, janitor, deployment gate, or
Task 4-6 implementation was added or modified.

## Verification and residual risk

- Task 1/2 specialty regression: `120 passed in 8.38s`.
- Related media/persistence/API run: `468 passed, 5 skipped, 7 failed`. The seven failures
  are the disabled-process capability assertions: accepted Task 1
  `persistence.models` imports the authoritative scan CHECK from `media_core.models` at
  module import time. Task 3's scan-audit repository is lazy; removing its access does not
  remove this pre-existing Task 1 import. Task 3 intentionally did not duplicate the
  authoritative CHECK or revise the accepted Task 1 boundary.
- Targeted Ruff: passed.
- Targeted strict mypy: passed for all five Task 3 production modules.

Residual risk: production definitions values are immutable composition inputs. Live clamd
definitions retrieval/certification and the real gate remain Task 6. The visual provider
remains disabled.

## Final fresh handoff evidence

- Combined Task 3 plus Task 1/2 specialty suites: `213 passed in 8.56s`.
- Full Ruff: passed.
- Full strict mypy: `115 source files` passed.
- `git diff --check`: passed.
- Staged state: empty.
- Windows patch transit and accidental `.orig` intermediates were removed.

## Fix Round 1

AI0 rejected the first handoff because Task 3 still left three production gaps:

- P1-A: disabled-media fresh processes imported `focusproof.media_core` through
  `persistence.models` and `persistence.audit_projection`.
- P1-B: `MediaSecurityPolicy.visual_provider_enabled=False` did not constrain the
  actual official OpenHands SDK LLM construction path.
- P1-C/P2: scanner exceptions before a verdict used ingestion hardcoded fallback scan
  resource values, and oversize needed explicit Task 3 ingestion coverage.

### Fix files

- Added `agent-server/focusproof/contracts/media_scan.py` as the single neutral scan
  contract for frozen result kinds, rejection codes, legal-pair mapping, CHECK SQL, and
  immutable audit snapshots.
- Updated `media_core.models` to reuse and re-export the contract values instead of
  owning a second copy.
- Updated `persistence.models` to import CHECK SQL directly from the neutral contract;
  updated `persistence.audit_projection` so media dataclasses are imported only when scan
  audit records are actually materialized.
- Updated `media_core.ports`, `clamd_malware_scanner`, and deterministic fake scanner so
  every scanner exposes an authoritative pre-scan `audit_snapshot`.
- Updated ingestion so verdict and pre-verdict exception attempts use that snapshot, and
  so non-clean audit write failures roll back with the reservation reject instead of
  causing an outer independent reject.
- Updated `config.profiles` so production forces `RealLlmPolicy.supports_vision=False`
  until a real visual safety gate is certified; staging/local preserve explicit vision
  capability.
- Updated tests only to assert the new required contracts and architecture boundary.

### RED evidence

- Disabled media fresh-process RED: 7 existing failures reproduced in
  `agent-server/tests/api/test_product_capabilities.py`.
- Fix Round 1 RED after added tests:
  `14 failed, 2 passed` across disabled imports, production vision gate, clamd snapshot,
  scanner exception snapshot, audit/reject rollback, and neutral contract import.

### GREEN evidence

- Targeted Fix Round 1 GREEN: `18 passed in 45.87s`.
- Task1+Task2+Task3 combined regression:
  `229 passed in 55.92s`.
- Full agent-server regression after all fixes:
  `1651 passed, 7 skipped, 14 deselected, 470 warnings in 449.62s`.
- Ruff: `All checks passed!`.
- Strict mypy: `Success: no issues found in 116 source files`.
- `git diff --check`: passed.
- `git diff --cached --name-only`: empty.
- Windows bridge directories: zero `.ai5_7_task3_bridge*` entries remain.

### Atomicity and recovery proof updates

- Clean path still records attempt + clean receipt before validation/finalization.
- Malicious, oversize, timeout, unavailable, error, and legacy unknown still record one
  attempt, reject once, create zero receipts, and do not call validation,
  normalization, staging, safe-fact, or vision paths.
- Scanner exceptions before verdict now record an error attempt from the scanner's
  authoritative snapshot: backend, definitions version/freshness/age, max bytes,
  concurrency, deadline, and socket timeout all come from `audit_snapshot`, not ingestion
  constants.
- If scan audit write fails on a non-clean path, the attempted audit and reservation
  reject remain in the same UoW rollback; the outer cleanup does not create a separate
  reject.

### OpenHands and vision boundary

- No OpenHands SDK source was modified.
- No Runtime, Conversation, EventLog, Action, Observation, Tool, LLM, or SDK stand-in was
  created.
- The actual `build_openhands_llm()` path now receives a production policy whose
  `supports_vision` is forced false, so the official `openhands.sdk.LLM` is constructed
  with `disable_vision=True` even when `FOCUSPROOF_LLM_SUPPORTS_VISION=true`.
- Staging and local-dev retain explicit opt-in vision behavior for existing non-production
  regression coverage.

### Untouched boundaries

No Task 4 quarantine TTL/codec/janitor behavior, Task 5 legacy projection, Task 6 real
gate, frontend contracts, Monad/scoring, or agent loop work was implemented in Fix Round
1.

## Fix Round 2

AI0 rejected Fix Round 1 because the neutral media scan contract was not yet the only
authority: the migration still hand-copied the scan result/rejection CHECK SQL, and
ingestion still hand-copied the default non-clean result to rejection-code mapping.

### Root cause

- `agent-server/migrations/versions/0006_media_scan_audit_and_receipts.py` embedded a
  local string copy of the frozen CHECK expression instead of importing
  `focusproof.contracts.media_scan.SCAN_RESULT_REJECTION_CODE_CHECK_SQL`.
- `agent-server/focusproof/media_core/ingestion.py` embedded a local
  `ScanResultKind -> ScanRejectionCode` dictionary for non-clean scan attempts instead
  of resolving through the neutral scan contract.

### Files changed

- `agent-server/focusproof/contracts/media_scan.py`: added immutable
  `DEFAULT_SCAN_REJECTION_CODES` and `default_scan_rejection_code()`.
- `agent-server/migrations/versions/0006_media_scan_audit_and_receipts.py`: removed the
  copied CHECK SQL and now directly imports the neutral contract CHECK constant. The
  migration imports no `focusproof.media_core` module.
- `agent-server/focusproof/media_core/ingestion.py`: removed the copied default
  rejection dictionary and now calls `default_scan_rejection_code()` for every non-clean
  verdict that does not already carry a rejection code.
- `agent-server/tests/persistence/test_media_scan_audit.py`: added anti-copy tests that
  compare domain re-export, ORM metadata, and migration CHECK SQL to the neutral
  contract, and verify importing the migration does not load `focusproof.media_core`.
- `agent-server/tests/media_core/test_ingestion.py`: added an anti-copy test that
  monkeypatches the neutral resolver at the ingestion boundary and verifies every
  non-clean outcome flows through it.

### RED evidence

- New ingestion anti-copy RED:
  `ImportError: cannot import name 'default_scan_rejection_code' from 'focusproof.contracts.media_scan'`.
- New migration anti-copy RED:
  `2 failed` because the migration had no `SCAN_RESULT_REJECTION_CODE_CHECK_SQL` symbol
  and its local CHECK did not prove neutral-contract reuse.

### GREEN and regression evidence

- New unique-authority tests:
  `3 passed in 0.45s`.
- Task 1 persistence / SQLite / PostgreSQL offline and concurrency:
  `46 passed, 5 skipped in 12.47s`.
- Task 2 scanner / limits / media security policy:
  `142 passed in 2.35s`.
- Task 3 ingestion / transaction / cancellation:
  `54 passed in 0.34s`.
- Disabled fresh-process capability regression:
  `11 passed in 47.32s`.
- Task 1+2+3 combined regression:
  `232 passed in 55.15s`.
- Full `agent-server/tests`:
  `1654 passed, 7 skipped, 14 deselected, 470 warnings in 450.62s`.
- Ruff:
  `All checks passed!`.
- Strict mypy:
  `Success: no issues found in 116 source files`.

### Unique authority chain

The independent authority chain is now:

`focusproof.contracts.media_scan`
-> `media_core.models` re-export for domain callers
-> `persistence.models` ORM CHECK constraint
-> Alembic migration CHECK constraint
-> `media_core.ingestion` default non-clean rejection resolver.

The two duplicate definitions removed in Fix Round 2 were:

1. the hand-written migration result/rejection CHECK SQL;
2. the hand-written ingestion default non-clean rejection-code dictionary.

No OpenHands SDK source, Runtime, Conversation, EventLog, Action, Observation, Tool,
Monad/scoring, frontend contract, Task 4 quarantine/codec/janitor, Task 5 legacy
projection, or Task 6 real gate behavior was added or modified.

## Fix Round 3

AI0 rejected Fix Round 2 because `ClamdMalwareScanner._verdict()` still contained the
last production-local copy of the frozen `ScanResultKind -> ScanRejectionCode` default
mapping.

### Root cause

`agent-server/focusproof/media_adapters/clamd_malware_scanner.py` imported the neutral
scan enums, but `_verdict()` still built a local `codes = {...}` dictionary containing
the same default rejection pairs as `focusproof.contracts.media_scan`.

### Files changed

- `agent-server/focusproof/media_adapters/clamd_malware_scanner.py`: removed the local
  `codes` dictionary. Clean verdicts now explicitly use `rejection_code=None`; every
  non-clean verdict calls `default_scan_rejection_code(result)` from the neutral
  contract. Status/outcome values, details, definitions/resource snapshots, and
  exception semantics were left unchanged.
- `agent-server/tests/media_adapters/test_clamd_malware_scanner.py`: added an AST
  anti-copy test that fails if `_verdict()` reintroduces a local
  `ScanResultKind -> ScanRejectionCode` dictionary, plus monkeypatch-backed behavior
  tests proving clean does not call the resolver and each frozen non-clean outcome does.

### RED evidence

- New clamd anti-copy RED:
  `7 failed in 0.22s`.
- Failure modes were the expected ones: the AST test found the `_verdict()` dictionary,
  and the behavior tests failed because the clamd module did not expose/import
  `default_scan_rejection_code`.

### GREEN and regression evidence

- New clamd anti-copy + behavior tests:
  `7 passed in 0.06s`.
- Full clamd scanner tests:
  `42 passed in 1.80s`.
- Source scan for production mappings:
  only `focusproof.contracts.media_scan.py` contains `ScanResultKind.*:
  ScanRejectionCode.*`; clamd and ingestion only call `default_scan_rejection_code()`.
- Neutral contract + Task 1 exact pairs:
  `89 passed in 7.27s`.
- Task 2 scanner / limits / media security policy:
  `149 passed in 2.34s`.
- Task 3 non-clean / oversize / exception / audit rollback:
  `9 passed in 0.06s`.
- Task 1+2+3 combined regression:
  `239 passed in 55.34s`.
- Full `agent-server/tests`:
  `1661 passed, 7 skipped, 14 deselected, 470 warnings in 448.58s`.
- Ruff:
  `All checks passed!`.
- Strict mypy:
  `Success: no issues found in 116 source files`.

### Final unique authority state

The frozen scan result/rejection defaults now have one production authority:
`focusproof.contracts.media_scan`.

Consumers now use it as follows:

- `media_core.models` re-exports the neutral domain values for media-domain callers;
- `persistence.models` and migration `0006` use the neutral CHECK SQL;
- `media_core.ingestion` resolves default non-clean rejection codes through
  `default_scan_rejection_code()`;
- `media_adapters.clamd_malware_scanner` resolves default non-clean rejection codes
  through the same neutral function.

`media_core/ports.py` still owns scanner-status to API error-code mapping because that is
a separate API error contract, not a scan result/rejection persistence contract.

No Task 4 quarantine TTL/codec/janitor behavior, Task 5 legacy projection, Task 6 real
gate, OpenHands SDK source, Runtime, Conversation, EventLog, Action, Observation, Tool,
Monad/scoring, frontend contract, or agent loop work was added or modified in Fix Round
3.
