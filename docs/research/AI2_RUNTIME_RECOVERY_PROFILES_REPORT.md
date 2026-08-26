# AI2 Runtime Recovery Report — Fix Round 1

Date: 2026-08-25
Status: `READY_FOR_AI0_REVIEW_FIX1`

## Scope

This phase adds two explicit internal runtime profiles without changing the public API:

- `demo-deterministic` reuses the official OpenHands `TestLLM` controlled provider path.
- `demo-real-vision` requires a complete real-provider policy and fails closed through the existing readiness boundary when configuration is incomplete.

No scoring, evidence, OpenHands SDK type, Conversation/EventLog semantic, media-safety, Monad, image-idempotency, or frontend Composer change was made.

## Changed Files

- `agent-server/focusproof/config/profiles.py`: registers both profiles and prevents real-provider construction for `demo-deterministic`.
- `agent-server/focusproof/config/identity.py`: reuses anonymous development identity for both demo profiles only when OIDC configuration is absent; supplied invalid OIDC remains fail-closed.
- `agent-server/focusproof/api/app.py`: separates identity/runtime validation, keeps readiness/dependency envelopes consistent, and maps typed provider infrastructure failures to the existing retryable `runtime_unavailable` envelope.
- `agent-server/focusproof/openhands_runtime/factory.py`: adds a narrow `RuntimeUnavailableError` subtype for provider infrastructure failures without changing OpenHands SDK types.
- `agent-server/focusproof/openhands_runtime/manager.py`: classifies official OpenHands rate-limit, timeout, and service-unavailable exceptions along `cause`/`context` chains; business, parsing, and judgment failures retain learning-failure behavior.
- `agent-server/tests/ai2/test_runtime_recovery_profiles.py`: full lifespan coverage for anonymous demos, official TestLLM selection, no deterministic real-provider construction, no real-mode TestLLM fallback, missing-provider consistency, and invalid-OIDC classification.
- `agent-server/tests/ai2/test_provider_infrastructure_recovery.py`: same-session failure/recovery coverage proving no review/score pollution and exactly-once successful review/score persistence.
- `docs/research/AI2_RUNTIME_RECOVERY_PROFILES_REPORT.md`: this report.

The first two production files already contained cumulative AI5 changes. Only narrow AI2 hunks were added; existing changes were not reverted or cleaned.

## OpenHands APIs Reused

- Official `openhands.sdk.testing.TestLLM` through the existing controlled factory.
- Official `openhands.sdk.LLM` provider construction.
- Official `Conversation` / `LocalConversation` and native EventLog.
- Existing FocusProof `/ready`, dependency readiness, and HTTP 503 boundaries.

## FocusProof-Owned SDK Gaps

None added. Provider configuration validation and readiness classification remain FocusProof policy boundaries.

## TDD and Verification

Task 1 RED: `5 failed, 3 passed`. Failures demonstrated anonymous demo rejection, `/ready` versus `/sessions` disagreement, and broad profile-based validation misclassification.

Task 1 GREEN: `8 passed, 1 warning`.

Task 2 RED: `4 failed`. The official `LLMRateLimitError` attempt was preserved in native OpenHands EventLog and projected as `error.occurred`, while the API incorrectly returned `conversationMode/reviewStatus=failed`; typed cause-chain classification was absent.

Task 2 GREEN: `4 passed, 1 warning`. The first infrastructure failure returned retryable HTTP 503 with no review row, score, completion/score event, or fabricated learning-failure observation. Same-session recovery produced exactly one completed review and score.

Final focused plus API/runtime/persistence regression selection: `38 passed, 1 warning`.

- Ruff: PASS.
- strict mypy: PASS (`6 source files`).
- `git diff --check`: PASS.

The warning is the pre-existing Starlette `TestClient`/httpx deprecation warning.

## Real Provider Smoke

The existing secure loader reported provider `PRESENT`, model `openai/qwen3.6-flash-2026-04-16`, valid dotenv format, and no structured missing fields. No secret was printed or copied into repository files.

Text smoke used official LLM -> Conversation -> EventLog. It created the native Conversation and two `MessageEvent` records, then failed closed before a completion was recorded: real call `NO`, media `text/plain`, ActionEvent `0`, ObservationEvent `0`, review `failed`, sanitized error `ConversationRunError`.

The unchanged real visual gate used the canonical PNG and reached the official product/OpenHands path. Real Provider attempted: `true`. The Provider returned a quota error, so the request returned HTTP 503 and failed closed; no review or visual facts were fabricated and there was no TestLLM fallback. Safe EventLog counts before failure were MessageEvent `2`, ActionEvent `0`, ObservationEvent `0`; media was `image/png`, result `FAIL`, review `not_run`, visual facts `0`. This is not a real-visual PASS.

## Residual Risks and Manual Acceptance

- Real text and PNG acceptance remain unpassed because the configured Provider quota is exhausted.
- After quota restoration, rerun the existing real visual gate with the configured non-sensitive model identifier and one canonical PNG.
- Rerun one bounded text Conversation smoke and require at least one real completion plus native Action/Observation evidence.
