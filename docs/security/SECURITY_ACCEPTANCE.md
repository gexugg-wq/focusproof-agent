# AI4B Security Acceptance Map

## Acceptance position

This map links SEC-01 through SEC-11 to executable evidence and required manual
inspection. It does not claim production authentication. The current
development identity is a public deployment blocker, and production
authentication is not implemented or complete.

Run Python commands from the repository root in WSL Ubuntu with Python 3.12.
Default commands use SDK `TestLLM`; no real provider key is required.

| ID | Security claim | Automated evidence | Expected safe behavior | Manual or residual check |
| --- | --- | --- | --- | --- |
| SEC-01 | Every session-derived endpoint enforces owner isolation | `test_every_session_derived_endpoint_denies_non_owner_without_state_change` in `agent-server/tests/ai4b/test_api_security.py` | Non-owner receives the same non-enumerating denial and owner facts remain unchanged | Production identity design is still absent |
| SEC-02 | User text cannot forge native actions, observations, or completed reviews | `test_user_text_and_llm_claims_cannot_forge_authoritative_runtime_facts` | JSON-shaped or prompt-injection text remains learner data; no tool-success fact or review is fabricated | Inspect new tools for authoritative repository lookup |
| SEC-03 | LLM text cannot claim an authoritative tool fact without a native observation | `test_user_text_and_llm_claims_cannot_forge_authoritative_runtime_facts`, `test_manager_run_uses_native_action_tool_and_observation_flow` | A claimed success remains untrusted message content; authoritative facts require the registered SDK tool call and matching native observation | Review every new verification capability before release |
| SEC-04 | Prompt injection cannot override the tool allowlist, repository lookup, or scoring boundary | `test_prompt_like_text_stays_user_content_and_sdk_secrets_are_redacted`, `test_forbidden_default_tools_are_never_assembled`, `test_completed_review_score_is_owned_by_focusproof` | Prompt-like evidence remains learner content; default programming tools stay disabled and FocusProof owns the score | Recheck this boundary whenever tools or prompts change |
| SEC-05 | URL verification denies SSRF, rebinding, unsafe redirects, resource exhaustion, and sensitive URL leakage | `test_policy_blocks_unsafe_targets`, `test_policy_blocks_hostname_when_any_resolved_address_is_unsafe`, `test_fetcher_dns_rebinding_cannot_change_the_pinned_request_address`, `test_fetcher_revalidates_redirect_target_before_request`, `test_fetcher_stops_and_closes_slow_stream_at_total_deadline`, and URL redaction tests in `test_url_evidence_tool.py` | Unsafe or over-budget URL work becomes blocked/inconclusive without contacting a protected target or returning raw sensitive URL data | Revalidate proxy and DNS behavior in the staging network |
| SEC-06 | Untrusted strings render as inert text | Frontend tests `renders a malicious learning goal as text`, `renders malicious review findings and questions as text`, and `renders malicious Build Log event labels as text` | Text remains visible but no script, unsafe image, or handler is created | Repeat browser inspection after any rendering-library change |
| SEC-07 | Input, metadata, and request bodies are bounded | `test_session_input_bounds_return_422`, `test_evidence_input_bounds_and_shape_return_422`, `test_answer_input_bounds_return_422`, `test_oversized_request_body_is_rejected_before_validation`, `test_chunked_oversized_request_is_rejected_without_content_length` | Invalid fields return 422; oversized bodies return only `request_too_large` with `retryable=false` | Reconcile proxy limits with the ASGI ceiling |
| SEC-08 | Replay and concurrent review have one logical result and explicit conflicts | Duplicate/replay tests in `test_api_security.py`, `test_two_concurrent_reviews_enter_conversation_run_once`, and `test_concurrent_identical_answer_allows_retryable_503_and_safe_retry` | Identical replay returns the original fact; review execution is single-entry per Session; a safe retry creates no duplicate answer, event, or result | Cross-process locking remains SQLite/file-lock dependent |
| SEC-09 | Internal failures are sanitized and never become false success | `test_schema_out_of_date_is_sanitized`, `test_sqlite_locked_is_sanitized`, `test_sdk_conversation_creation_failure_is_explicit`, and frontend API-boundary tests for safe 503/non-JSON failures | Responses exclude paths, SQL, secrets, traces, raw evidence, and provider internals | Inspect structured staging logs |
| SEC-10 | Browser and BFF configuration expose no provider secret | Frontend test `forwards only content-type and never returns environment or fetch errors`; `test_check_uses_argument_arrays_and_removes_provider_keys` | Browser authorization, cookie, and provider-key headers are dropped; provider keys are removed from test subprocesses and no environment value is returned | Confirm reverse proxy also strips untrusted forwarding headers |
| SEC-11 | Release text, logs, reports, and screenshots contain no credential or raw secret fixture | `test_env_example_contains_only_placeholders_for_sensitive_names`, `test_tracked_release_text_contains_no_unapproved_secret_material`, `test_smoke_prints_only_ids_and_statuses`, plus the AI4B screenshot static/visual scan | No provider value, private-key material, raw evidence, or environment value is printed or tracked | Use a staging secret manager and rotate on suspected exposure |

## Commands

Backend security boundary:

```bash
.venv/bin/python3.12 -m pytest agent-server/tests/ai4b/test_api_security.py -q
.venv/bin/python3.12 -m pytest \
  agent-server/tests/openhands_runtime/test_url_safety.py \
  agent-server/tests/openhands_runtime/test_url_evidence_tool.py \
  agent-server/tests/openhands_runtime/test_tool_execution.py -q
```

Release artifacts:

```bash
.venv/bin/python3.12 -m pytest \
  agent-server/tests/ai4b/test_release_artifacts.py -q
.venv/bin/python3.12 scripts/ai4b_smoke.py --help
.venv/bin/python3.12 scripts/ai4b_check.py --help
.venv/bin/python3.12 scripts/run_ai4b_test_server.py --help
```

Frontend evidence is owned by Task 3 and later frontend gates. Task 5 does not
run Node or npm. The authoritative frontend commands remain:

```text
npm --prefix frontend run test
npm --prefix frontend run lint
npm --prefix frontend run typecheck
```

## Manual acceptance checklist

- Confirm the deterministic server rejects every host other than
  `127.0.0.1`.
- Confirm the test-server process has no real provider-key variables.
- Inspect staging reverse-proxy header and body-size policy.
- Inspect one URL failure and one API failure log for learner text, raw URLs,
  exception internals, or environment values.
- Verify database and conversation directories are readable only by the
  service account and backup operator.
- Exercise backup and restore on a copy before any release decision.
- Confirm no public route is enabled while the development identity remains.

## Safe error contract

Safe errors use stable codes, a bounded session identifier where required, and
an explicit retryability value. They do not include raw evidence, answers,
source URLs, exception messages, SQL, filesystem paths, provider responses, or
environment values. Permanent `session_finalized` errors are not presented as
temporary busy states.

## Residual risk

These tests prove deterministic boundaries, not universal semantic relevance
or production identity. Detailed but semantically unrelated evidence can still
evade low-confidence lexical association heuristics. Do not claim otherwise
until semantic Agent/LLM assessment is integrated with deterministic scoring
boundaries.
