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
| SEC-03 | Tools are native, read-only, schema-bound, and repository-backed | `test_verification_executor_loads_authoritative_evidence_by_id`, `test_focusproof_tool_models_are_native_openhands_types`, `test_focusproof_tools_are_declared_read_only` | Only registered SDK tool calls can produce observations; no default programming tool is enabled | Review tool registry changes before release |
| SEC-04 | URL verification denies SSRF, rebinding, and unsafe redirects | `test_policy_blocks_unsafe_targets`, `test_policy_blocks_hostname_when_any_resolved_address_is_unsafe`, `test_fetcher_pins_connection_to_policy_validated_address`, `test_fetcher_dns_rebinding_cannot_change_the_pinned_request_address`, `test_fetcher_revalidates_redirect_target_before_request` | Unsafe URL becomes a blocked or inconclusive observation without a request to the protected target | Revalidate proxy and DNS behavior in the staging network |
| SEC-05 | URL failures and facts redact sensitive components | `test_url_failure_observations_exclude_sensitive_url_and_exception_details`, `test_url_observation_redacts_query_secrets_from_facts_and_sources`, `test_url_observation_redacts_path_userinfo_port_redirect_and_excerpt_secrets` | Error response excludes raw URL, userinfo, query values, excerpts, and exception internals | Inspect structured staging logs |
| SEC-06 | Untrusted strings render as text and cannot inject active HTML | Frontend tests `renders a malicious learning goal as text`, `renders malicious review findings and questions as text`, and `renders malicious Build Log event labels as text` | Text remains visible but no script, unsafe image, or handler is created | Repeat browser inspection after any rendering-library change |
| SEC-07 | The BFF forwards only approved headers and returns safe errors | Frontend test `forwards only content-type and never returns environment or fetch errors`; API-boundary tests for safe 503 and non-JSON failures | Browser authorization, cookie, and provider-key headers are dropped; environment values are not returned | Confirm reverse proxy also strips untrusted forwarding headers |
| SEC-08 | Input, metadata, and request bodies are bounded | `test_session_input_bounds_return_422`, `test_evidence_input_bounds_and_shape_return_422`, `test_answer_input_bounds_return_422`, `test_oversized_request_body_is_rejected_before_validation`, `test_chunked_oversized_request_is_rejected_without_content_length` | Invalid fields return 422; oversized bodies return only `request_too_large` with `retryable=false` | Reconcile proxy limits with the ASGI ceiling |
| SEC-09 | Replay is idempotent and reviewed facts are frozen | Duplicate/replay and reviewed-session tests in `test_api_security.py`, including `test_reviewed_session_freeze_survives_fastapi_restart` | Identical replay returns the original fact; changed facts return stable non-retryable `session_finalized` | Concurrent identical Answer may return retryable busy and must be retried safely |
| SEC-10 | Secrets are absent from release artifacts and default test processes | `test_env_example_contains_only_placeholders_for_sensitive_names`, `test_tracked_release_text_contains_no_unapproved_secret_material`, `test_smoke_prints_only_ids_and_statuses`, `test_check_uses_argument_arrays_and_removes_provider_keys` | No provider value, private-key material, raw evidence, or environment value is printed or tracked | Use a staging secret manager and rotate on suspected exposure |
| SEC-11 | Public release is explicitly blocked without production identity | `test_docs_preserve_public_release_identity_blocker` | Documentation cannot present development anonymous identity as production authentication | AI0 must approve identity, authorization, revocation, and operator ownership |

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
