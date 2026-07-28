# AI4C.3 Task 4 Repair Report — Fix Round 1

Date: 2026-07-28
Branch: ai4c-production-readiness
Baseline HEAD: 03465ff316cdacd93f5590cc79c22f251f2dbfc1
Disposition: BLOCKED
Commit created: no

## Scope

All repository work was performed in WSL at `/home/holy/web3/focusproof-agent`. The inherited 23 staged Task 4 files were preserved. No Task 5, push, merge, amend, real LLM, secret read, Web3, multimodal work, or OpenHands runtime/protocol imitation was performed. OpenHands SDK 1.31.0 remains the sole Conversation/runtime/EventLog/Action/Observation/tool implementation.

## AI0 specification decision implemented locally

- Added `docs/architecture/ADR-0001-CANONICAL-RELEASE-DIGEST.md`, referenced by the AI4C production-readiness design and Task 4 plan.
- Defined namespace `focusproof.canonical-release.v1`.
- The canonical digest includes platform, every pinned Dockerfile stage base digest, runtime path, relevant OCI configuration, and every flattened runtime filesystem path/type/mode/uid/gid/file byte digest or link target.
- Tar ordering and mtime are excluded under the fixed release epoch.
- Only the three exact Next.js 15.5.18 preview fields in `/app/.next/prerender-manifest.json` are normalized. Missing, malformed, duplicate, ambiguous, or unknown-version normalization fails closed. Every other byte remains significant.
- Removed the former Next build-key injection, framework-cache seeding, Compose secret, environment example, test fixture, and deployment documentation plumbing.
- Added static proof that FocusProof frontend source uses neither Draft Mode nor Server Actions.
- Changed the external gate locally to compute and compare two canonical release digests while recording Docker `.Id` values only as diagnostics. Both rounds still invoke real Keycloak browser Authorization Code + PKCE through Next BFF/FastAPI and official OpenHands restart/restore behavior.

## TDD evidence

RED:

- Canonical release tests initially failed import because the strict/versioned API did not exist.
- The version-binding test failed because the canonical descriptor did not yet bind normalization to Next.js 15.5.18.
- Static removal tests failed while build-key documentation/plumbing remained.

GREEN:

- Canonical/static focused gate: 87 passed, 1 external test deselected, 6.03 seconds.
- Canonical tests cover allowlisted entropy equality; non-allowlisted bytes, mode, uid, gid, symlink target, configuration, platform, and pinned base differences; unknown schema/profile fail-closed behavior.

## Local verification

All commands removed `DASHSCOPE_API_KEY`, `OPENAI_API_KEY`, `FOCUSPROOF_LLM_API_KEY`, and `ANTHROPIC_API_KEY` from child environments.

- Backend non-real-LLM regression: 707 passed, 12 deselected, 16 warnings, 199.73 seconds.
- Ruff: all checks passed.
- Mypy: 150 source files, zero issues.
- Frontend ESLint: passed.
- Frontend TypeScript check: passed.
- Frontend Vitest: 76 passed.
- Frontend Next.js 15.5.18 production build: passed; four routes emitted.
- `git diff --check`: passed before the external gate.
- Capability preflight: container CLI, Compose, and PostgreSQL client available on Linux x86_64.

## Single allowed external gate

Command:

```bash
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY \
  .venv/bin/python -m pytest \
  agent-server/tests/ai4c/test_staging_stack.py::test_staging_external_stack_builds_runs_and_preserves_ids \
  -vv -s -m staging_external
```

Result: 1 failed in 774.61 seconds during round-1 frontend clean build, before stack startup or canonical digest computation.

Single root cause: the external gate correctly creates its release context from `git write-tree`. The round-1 repair changes were still unstaged so that the inherited staged tree contained the old frontend Dockerfile with the prohibited BuildKit Next-key mount. The repaired external helper no longer supplied that build input, so the clean frontend build failed closed when the stale Dockerfile attempted to read it.

Per the explicit one-run rule, the external gate was not rerun and no experimental build was attempted. Consequently there is no valid two-round canonical digest, browser Code+PKCE, or OpenHands restart/restore evidence for this fix round, and no GREEN commit may be created.

## Cleanup

After failure:

- no Task 4 Compose container remained;
- no Task 4 named volume remained;
- no `/tmp/focusproof-task4-*` directory remained;
- the two staging image tags created/retained by the run were explicitly removed;
- the pre-existing BuildKit builder was not modified or removed;
- no user database or user service was touched.

## Disposition

BLOCKED. The only blocking condition is exhaustion of the single permitted external gate after the Git-index context used the inherited staged Dockerfile. No commit was created and no second attempt was made.


## Fix round 2 external evidence

The corrected staged snapshot completed both clean build/run rounds and both real browser/OpenHands flows. Backend canonical digest was identical in both rounds (`sha256:9d7c349b30b146655c88f1e0d06892cd10bc2660472961e1c665dca110b77fcb`). Frontend canonical digests differed (`sha256:8ba04fc50c310bfad79021f147738d7b28ae22fc5ca1bcca05a5e4fa55e53bee` versus `sha256:78c3ee7ea3ae190de89c37e4d297b75bae7b72de32d40308e705d3f76e006610`). OCI frontend IDs also differed and remained diagnostic only.

Both rounds passed real Keycloak browser Authorization Code + PKCE through the Next BFF and FastAPI and official OpenHands LocalConversation restart/restore. Round 1 conversation/review were `39f0f42f-5bac-573a-a866-96f8a815763f` / `rev_72917a4ca8dd4cfb84bc7a77e1360fef`; round 2 were `aa441251-f667-5dcf-aa0d-3668017d54a2` / `rev_e441618bfff244fe831820c77116e529`. Each proved product events 4 -> 7 -> 7, native-source events 3 -> 4 -> 4, and completed review status.

Independent read-only record comparison identified exactly 560 frontend differences: 555 Node compile-cache files, four npm debug logs, and the empty-action server-reference manifest encryption key.

## Fix round 3 design

Round 3 removes the two build-tool cache roots from the final runtime image and makes their presence a canonicalization error. A testable Node postprocessor validates the exact server-reference manifest schema, requires both action maps to be empty, and only then replaces the unused random key with a fixed public 32-byte base64 placeholder. The manifest remains byte-significant to the canonicalizer; no allowlist was expanded. Canonical mismatch diagnostics now compare safe records containing paths, metadata, sizes, content SHA-256 values, hashed link targets, and round-only markers without file contents or environment values.

## Fix round 4 local determinism repair

Date: 2026-07-28
Disposition: READY_FOR_AI0_LOCAL_DETERMINISM_REVIEW
Commit created: no

The inherited read-only rootfs comparison contained 36,127 records per frontend round and 17 content differences, including five size differences. The remaining differences were restricted to three semantically equal manifests whose key order differed and generated server page, chunk, and client-reference bundles. The earlier `/tmp/node-compile-cache`, `/root/.npm`, and random server-reference key differences were absent.

The local characterization used two independent clean Git-index source archives with independent `npm ci` trees. A first parallel run at distinct absolute paths demonstrated that path identity contaminates Next generated types and bundles, so it was not accepted as the controlled RED. Rebuilding the two sources concurrently in separate unprivileged mount namespaces at the same logical path removed that variable. On the available local Node 18.19.1 runtime, the remaining old-config sample reproduced only the already allowlisted prerender preview entropy; the prior 17-record Node 22 rootfs evidence therefore remains the authoritative RED for this intermittent build race.

The minimum repair is:

- Next.js 15.5.18 `experimental.cpus: 1`;
- Next.js 15.5.18 `experimental.webpackBuildWorker: false`, confirmed present in the installed framework config schema;
- a strict post-build canonicalizer for exactly `app-build-manifest.json`, `app-path-routes-manifest.json`, `server/app-paths-manifest.json`, and `server/pages-manifest.json`;
- recursive object-key ordering with array ordering preserved, full pre-write validation, exact schema checks, and fail-closed path/type handling.

No business behavior, compiled JavaScript, server/client-reference bundle, canonical digest allowlist, OpenHands runtime, protocol, or SDK integration changed. OpenHands SDK 1.31.0 remains directly reused.

TDD evidence:

- RED: the focused postprocessor suite had four expected failures: the canonicalizer mode was absent in three cases and the loaded Next configuration had no worker policy.
- GREEN: 12 postprocessor tests passed.
- Focused canonical/static gate: 102 passed, 1 `staging_external` test deselected.
- Two clean concurrent GREEN builds each produced 86 postprocessed `.next` file records. After applying only the ADR v1 prerender normalization, canonical differences were zero and both safe aggregate record digests were `sha256:0a08f082c3344afa8bb1ec1c98cc4c9f811887bce2dbf7c93404cfc420916a58`.

Credential-free local gates:

- backend non-real-LLM suite with `LITELLM_LOCAL_MODEL_COST_MAP=true`: 722 passed, 12 deselected, 16 warnings;
- Ruff: all checks passed;
- Mypy: 151 source files, zero issues;
- frontend ESLint and TypeScript: passed;
- frontend Vitest: 76 passed;
- frontend Next.js production build: passed with four routes.

One focused pytest invocation accidentally omitted the marker exclusion and entered the `staging_external` test. It was detected during the first backend image build and terminated before Compose startup or canonical digest computation. No Task 4 Compose container, staging image tag, or new builder remained; the pre-existing BuildKit builder was left untouched. This run is not acceptance evidence and was not rerun. All subsequent focused and full commands explicitly excluded `staging_external`.

Per fix-round-4 scope, no staging external gate, commit, push, merge, amend, Task 5, real LLM, secret read, Web3, or multimodal work is authorized. Stop for AI0 local determinism review.

## AI0 scoped marker-policy fix round

Date: 2026-07-29
Disposition: READY_FOR_AI0_MARKER_POLICY_REREVIEW
Commit created: no

AI0 identified one P1: marker registration alone did not prevent a plain pytest invocation from collecting and executing `real_llm`, `postgres`, or `staging_external`. The earlier accidental external entry proved that callers could not safely rely on every command spelling an exclusion correctly.

The minimum repair adds pytest's official default marker expression in `pyproject.toml`:

```text
-m 'not real_llm and not postgres and not staging_external'
```

Pytest prepends configured `addopts` and applies an explicit command-line `-m` later, so `-m staging_external` remains the deliberate authorization override. No collection hook, test-path exception, hidden test, fixture bypass, frontend change, Docker invocation, Postgres invocation, or provider invocation was added.

TDD RED, using collection only:

- ordinary AI4C collection selected the staging external node;
- whole-file collection selected the staging external node;
- a focused external node ID without `-m` selected the external test;
- the static policy contract found no configured `addopts`;
- explicit `-m staging_external --collect-only` already selected exactly the intended test and remained the required preserved behavior.

GREEN evidence:

- marker-policy contract: 5 passed;
- plain backend command with no `-m`: 727 passed, 12 deselected, 16 warnings;
- explicit `-m staging_external --collect-only`: 1/75 selected, 74 deselected, and no test executed;
- Ruff: all checks passed;
- Mypy: 152 source files, zero issues.

The 12 default deselections are the ten PostgreSQL tests, one real-provider smoke, and one staging external test. Explicit marker selection remains visible and collectable. No external test, Docker command, real LLM, or PostgreSQL test ran in this fix round.

## Final staging external acceptance

Date: 2026-07-29
Disposition: GREEN

AI0 ran the explicitly authorized `staging_external` gate once from the complete staged Git-index context. The two clean rounds completed the production browser flow, local OIDC Authorization Code + PKCE exchange, FastAPI/BFF path, and official OpenHands SDK 1.31.0 `LocalConversation` restart/restore flow. The gate passed: `1 passed in 1529.73s`.

Strict canonical release digests were identical across both rounds:

- agent-server: `sha256:847371add386c19f67b4f017608aef2aac163f33e8bab55ca155ca64ba504e0e`;
- frontend: `sha256:3f667ff29bff08bdc5ee16db045695ed853bbf4055be2e6ea1b6ab091caf5146`.

The backend OCI image ID was also identical. The frontend OCI image IDs differed, as expected for non-semantic image metadata; they remained diagnostic only and did not affect the strict canonical release decision.

Both native recovery rounds completed with stable event continuity: product events `4 -> 7 -> 7`, native source events `3 -> 4 -> 4`, and `reviewStatus=completed`. No OpenHands Runtime, Conversation, EventLog, Action/Observation, Tool protocol, or SDK implementation was copied or replaced.
