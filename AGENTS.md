# FocusProof Agent Working Agreement

## Product

FocusProof is a domain-general learning-evidence verifier. The active product
supports text, HTTPS URL, and optional image evidence. Monad/Web3 is retired
from the active runtime; do not reintroduce chain-specific branches into core
evidence, scoring, tools, or the Agent loop.

## Runtime And Stack

- Work in WSL Ubuntu at `/home/holy/web3/focusproof-agent`.
- Backend: Python 3.12, FastAPI, SQLAlchemy/Alembic.
- Agent runtime: official `openhands-sdk==1.31.0` Conversation, EventLog,
  Action/Observation, Tool, and LLM/LiteLLM public APIs.
- Frontend: Next.js 15, React, TypeScript.
- SQLite is for local demo; PostgreSQL gates cover staging/concurrency.

## Non-Negotiable Boundaries

- Reuse suitable public OpenHands SDK behavior directly. Never build a second
  Runtime, Conversation, EventLog, Agent loop, Action/Observation, or Tool
  protocol. Record a real SDK gap before adding a minimal adapter.
- FocusProof owns learning semantics, deterministic scoring, authorization,
  persistence projections, media admission, and API translation.
- Tools return observations; tool success never proves understanding.
- Media remains detachable and default-off. Raw media must pass admission
  before reaching a provider or OpenHands event.
- Never read, print, commit, or modify `.env` or secret values during tests.

## Commands

```bash
cd /home/holy/web3/focusproof-agent
.venv/bin/python scripts/run_quality_gate.py --tier fast
.venv/bin/python -m ruff check agent-server
.venv/bin/python -m mypy agent-server
cd frontend && npm run lint && npm run typecheck && npm test -- --run
```

Use `--tier integration` for local PostgreSQL/Clamd/E2E infrastructure. Use
`--tier release --allow-real-provider` only with explicit authorization because
it can call external providers and production-style scanning.

## Structure And Current State

- `agent-server/focusproof/`: product backend and OpenHands boundary.
- `frontend/`: browser product and BFF.
- `docs/architecture/`, `docs/protocol/`: current contracts.
- `docs/research/`, `docs/superpowers/`: historical evidence and plans.
- `scripts/run_quality_gate.py`: canonical tiered gate.

AI1-AI5 engineering is complete. A real `qwen3.7-plus` browser flow with one
PNG plus explanation completed through official OpenHands on 2026-08-27.
Public deployment, managed OIDC, and long-term external operations/SLOs remain
unapproved. Audio/PDF/OCR/ASR (AI6) require separate AI0 approval.
