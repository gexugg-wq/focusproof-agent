# AI5 Image Provider Gate Report

Status: `PASS_LOCAL_STAGING_REAL_PROVIDER`

Validated on 2026-08-14 with Linux, Python 3.12, OpenHands SDK 1.31.0,
and the configured OpenAI-compatible `openai/qwen3.7-plus` provider.

## Acceptance Result

The real visual-provider gate generated a fresh random six-character code,
rendered it only into PNG pixels, submitted that PNG through the normal
FocusProof image-evidence endpoint, and ran the normal review flow. The
learner goal, evidence explanation, and follow-up answer did not contain the
code.

The code was recovered in the model-generated native OpenHands `ActionEvent`
and propagated through a native `ObservationEvent`. The real PNG evidence
entered the normal evidence and official OpenHands LLM path, and the review
completed two learner follow-up rounds. This proves pixel access; the result
cannot be explained by copying the code from learner text or by TestLLM/fake
vision behavior.

The exact guarded test passed:

```text
pytest agent-server/tests/ai5/test_real_image_provider.py -m real_llm -q
1 passed in 36.00s
```

Accepted checks:

- official OpenHands `LLM`, `Message`, `ImageContent`, `LocalConversation`,
  `ActionEvent`, `ObservationEvent`, and `ToolDefinition` were used directly;
- runtime mode was `openhands-local-real`, not SDK `TestLLM`;
- `LLM.vision_is_active()` returned true;
- the random pixel challenge matched native model output;
- `focusproof_media_evidence_verification` ran in the Conversation tool loop;
- at least one native Action and Observation were produced;
- Monad capability count was zero;
- the report contract rejects API keys, object keys, Base64, and data URLs;
- provider calls were bounded to six calls, 120 seconds, and one concurrent
  review.

The public Review projection intentionally remains modality-neutral and does
not echo the code. Exact matching is asserted against native model output in
the authoritative OpenHands EventLog, while the user-facing Review continues
to expose the generic credibility score and summary.

## Review contract correction

The earlier null score/summary observation was caused by an obsolete acceptance
consumer reading top-level fields. The completed-response contract is
`response.reviewResult.score` and `response.reviewResult.summary`; the Review
API, persistence, and official OpenHands path were not defective.

The real visual gate now uses an explicit fail-closed state machine. Only
`reviewStatus="awaiting_user"` may continue to questions; `completed`
immediately validates the nested result, and failed, missing, null, or unknown
states terminate the gate. Independent review returned
`APPROVED_CONTRACT_GATE`; the deterministic contract gate passed 19 tests.

## Architecture Decision

Visual support is now an explicit provider capability:
`FOCUSPROOF_LLM_SUPPORTS_VISION=true`. It defaults to false. When enabled,
FocusProof registers the configured model through LiteLLM's public model
metadata API and constructs the official OpenHands LLM with vision enabled.
No model name is hard-coded in runtime code, and no second Runtime,
Conversation, EventLog, Action/Observation type, or tool protocol was added.

The former `BLOCKED_BY_OFFICIAL_SDK_GATE` conclusion is superseded for local
and staging real-provider validation. The accepted path uses public OpenHands
SDK surfaces directly and does not require a wrapper around the Agent or
Conversation.

## Remaining Production Boundaries

This result does not mean public-production image upload is complete:

- production malicious-file scanning implementation and deterministic code
  gates are complete, but the real external clamd clean/EICAR execution gate
  remains a release blocker for public uploads;
- native Conversation EventLog and application persistence retain only stable
  evidence/media identifiers, safe artifact metadata, and structured Observation
  facts; they do not retain image data URLs, Base64, or raw image bytes;
- the Tool executor performs an owner/session-scoped, bounded object-store read,
  revalidates MIME, size, hash, and dimensions, and constructs the official SDK
  `ImageContent` only for the duration of the visual LLM call;
- a production identity provider and production deployment verification remain
  separate release gates;
- audio, video, PDF, OCR pipelines, and ASR are outside this image gate.

## AI5.3 Production Media Security Status

Historical code-gate claim (superseded/non-production): deterministic scanner
tests were reviewed, but they did not establish production implementation or
acceptance. Current evidence verifies only `fake-clean` isolation with
`productionMalwareScanningVerified=false`. AI5.7 owns `ScannerPort`, structured
`ScanResult`, a replaceable production adapter, and the fail-closed production boundary.

The separate real external service gate is
`BLOCKED_EXTERNAL_SERVICE_GATE` / `REAL_CLAMD_GATE_BLOCKED`. The pinned image
`clamav/clamav:1.5.3-debian@sha256:e6243e...828c` could not be pulled because
the Docker daemon timed out connecting to `registry-1.docker.io:443`. No clean
or EICAR run occurred, no container or image remains, and the repository
baseline was unchanged. This is not production scanning acceptance.

## Deterministic Regression Evidence

- visual capability RED tests failed first because `RealLlmPolicy` had no
  `supports_vision` field;
- focused visual capability tests: `2 passed`;
- complete LLM operations tests: `20 passed`;
- targeted Ruff: PASS;
- targeted Mypy: PASS;
- formal real-provider test: `1 passed in 36.00s`.

## Layered AI5 conclusion

- Image input foundation and real visual interpretation: complete.
- Production malware-scanning implementation and deterministic code gate:
  complete.
- Real external clamd clean/EICAR execution:
  `BLOCKED_EXTERNAL_SERVICE_GATE` / `REAL_CLAMD_GATE_BLOCKED`, pending network
  recovery or a reachable clamd endpoint.
- Overall AI5: not fully complete until that external service gate passes.

FocusProof continues to reuse official OpenHands LLM, Conversation, EventLog,
Action/Observation, and Tool surfaces directly. It introduces no imitation
Runtime/Conversation/EventLog, and media security remains independent of Agent
decisions, scoring, and Monad.

## V6 pinned real-image gate (2026-08-20 authoritative sync)

Status: `V6_REAL_IMAGE_GATE_FINAL_ACCEPTED`

This section supersedes earlier gate accounting and security-completion wording
in this report. It does not declare the whole AI5 image phase complete.

The deterministic image foundation and the OpenHands SDK 1.31.0 native
`ImageContent` -> `MessageEvent` -> `Conversation` event chain are complete.
The pinned real input is
`agent-server/tests/fixtures/real-vision/focusproof-general-session.png`, size
`66594`, SHA-256
`9a2fc6ac6864101e14e933e503840705392f5153fd2a4b2b7b9da246aeac4e67`.
AI0's P1 repair decision promoted the independently reviewed, content-valid PNG
into this dedicated fixture. Playwright capture output is disposable and is not
an immutable gate input. The original V6 bytes recorded below were not
recoverable from the current Git object/index history; no image was generated
or fabricated for this repair.

V6 evidence:

- provider/model: `openai/qwen3.7-plus`;
- exactly one visual provider completion;
- zero agent-decision completions;
- no retry;
- eight structured visual facts;
- `parseStage=complete` and `errorCategory=none`;
- review `completed` and runner `PASS`;
- independent review: `V6_REAL_IMAGE_GATE_FINAL_ACCEPTED`;
- report SHA-256:
  `80305ffa837cf42bb79ab3a10f2e14c7ffd83ff426ed95fab01d1037f750afc3`;
- sidecar SHA-256:
  `80d76c711bb3c168cb0bbc2b992c1734e6201a69e527770bb0f473fca079ae17`.

## Layered status and remaining gates

1. Deterministic foundation/native OpenHands event chain: complete.
2. Pinned real-PNG V6 acceptance: complete.
3. Production malicious-media/virus scanning: incomplete. Fake-clean is only
   local isolation evidence; `productionMalwareScanningVerified=false`.
4. Broader image sets, formats, sizes, concurrency, recovery, and cost:
   pending acceptance.

The next phase is **AI5.7 Production Media Safety Boundary**: implement a
`ScannerPort`, structured `ScanResult`, fail-closed policy, and replaceable
production scanner adapter. Acceptance covers `clean`, `malicious`, `timeout`,
`unavailable`, and `oversize`; `timeout` and `unavailable` fail closed. Raw
media stays quarantined and cannot enter the LLM or OpenHands events before a
clean result. The adapter is replaceable, logs are redacted, and general flows
must regress successfully.

AI5.7 explicitly does not change the Agent loop, general scoring, evidence
model, or Monad; hard-code a scanner vendor; or claim production safety
complete. FocusProof remains a general knowledge-learning verification
product, with Monad default-off and detachable. The implementation must keep
directly reusing the official OpenHands SDK and must not create imitation
Runtime, Conversation, EventLog, ImageContent, or Tool abstractions.
