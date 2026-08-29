# FocusProof Agent

FocusProof Agent is a general learning verification system. It evaluates whether a learning session produced credible, reviewable evidence, using the learner's goal, submitted evidence, tool observations, explanations, follow-up answers, learning output, reflection and next-step plan.

AI1 through AI5 engineering are complete. As of the authoritative 2026-08-27
acceptance sync, AI5 image-foundation engineering, the pinned local visual
gate, the live Clamd production-scanning boundary, PostgreSQL migration and
concurrency gates, and the shared image-publish/review lock are complete. The
independent AI5.8 audit was initially rejected, its three fix rounds completed,
and Round 3 was independently reverified as accepted. A visible-browser local
acceptance also completed a non-Web3 text-plus-PNG session through official
OpenHands with `openai/qwen3.7-plus`; the persisted image evidence carried its
explanation in `textContent` and the deterministic scorer returned
`LikelyLearning` with score `65` and confidence `0.72`. Media remains
default-off and detachable. The former chain-specific plugin slice has been
removed. This is engineering/runtime acceptance, not public production
authorization: real-provider use remains explicit and disabled by default,
and managed OIDC, public deployment, and external long-term operations/SLOs
remain unapproved. Audio/PDF/OCR/ASR are not implemented. AI6 multimodal
expansion requires separate AI0 approval.

## Why Python Agent Server

The runtime direction is Python because FocusProof directly uses OpenHands SDK Agent, Conversation, Tool, Action and Observation abstractions. Python also keeps tool executors and FastAPI service boundaries close to the agent runtime.

## Why OpenHands-Native

FocusProof directly uses suitable public OpenHands SDK runtime capabilities, including Conversation, Agent, native events and the tool protocol. It does not maintain OpenHands-inspired mirror implementations when the SDK already provides the behavior. FocusProof owns learning evidence, scoring, authorization, product persistence projections and review semantics, connected to the native runtime through thin adapters.
Media security remains outside Agent decisions and generic scoring; scoring does not fork by modality or chain-specific branches.

## Domain-General Scope

FocusProof keeps learning evidence domain-general. Text, URL and image evidence are current product paths; optional wallet metadata can remain generic evidence context but no chain-specific verifier is active. Future domain plugins require separate approval.

## Project Structure

```text
focusproof-agent/
  agent-server/
    focusproof/
      api/
      bootstrap/
      config/
      contracts/
      database/
      domain/
      media_adapters/
      media_core/
      media_projection/
      openhands_adapter/
      openhands_runtime/
      persistence/
      runtime/
      agents/
    tests/
      fixtures/
  contracts/
  docs/
    architecture/
    project-management/
    protocol/
    research/
  frontend/
  scripts/
```

## Local Development

```bash
cd /home/holy/web3/focusproof-agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
alembic upgrade head
python -m pytest agent-server/tests -v
python -m ruff check agent-server
python -m mypy agent-server
```

Development uses Python `venv` plus `pip` in WSL. Database migrations are run
explicitly before starting the development server. Application startup only
checks the Alembic revision and never applies migrations automatically.

## Quality Gates

Run tiered quality gates from WSL with `scripts/run_quality_gate.py`.

```bash
cd /home/holy/web3/focusproof-agent
.venv/bin/python scripts/run_quality_gate.py --tier fast
.venv/bin/python scripts/run_quality_gate.py --tier integration --dry-run
.venv/bin/python scripts/run_quality_gate.py --tier release --allow-real-provider
```

`fast` is deterministic and excludes Docker, network, PostgreSQL, and real LLM
providers. `integration` inherits fast and adds local infrastructure checks such
as PostgreSQL, backup/restore, deterministic E2E, and Clamd integration without
real LLM spend. `release` inherits integration and is fail-closed unless
`--allow-real-provider` is explicit; it may use live Clamd, real Qwen/OpenHands
provider calls, and final manifest generation, so it carries external service
cost and configuration risk. Use `--list` or `--dry-run` to inspect commands
without executing providers.

Repository-wide formatting is an independent maintenance command:
`python -m ruff format`. Current historical formatting debt does not block the
functional or security release gates. Python files added or modified in the
current change must still pass a targeted `ruff format --check` before handoff.

## AI Work Split

- AI0 owns controller and architecture documents.
- AI1 owns scaffold and OpenHands feasibility research.
- AI2 owns the completed Python Agent Server and OpenHands Conversation runtime.
- AI3 owns the completed frontend MVP and optional wallet user flow.
- AI4A owns the completed general text/URL verification framework.
- AI4B owns the completed general quality, security and release-readiness baseline.
- AI4C engineering and AI5 image-foundation/runtime acceptance are complete,
  including the 2026-08-27 real-provider browser closure.
- AI6 multimodal expansion requires separate AI0 approval. Audio/PDF/OCR/ASR remain unimplemented.
- Optional Web3 proof recording remains a domain-plugin backlog and cannot redefine the general runtime.
