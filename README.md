# FocusProof Agent

FocusProof Agent is a general learning verification system. It evaluates whether a learning session produced credible, reviewable evidence, using the learner's goal, submitted evidence, tool observations, explanations, follow-up answers, learning output, reflection and next-step plan.

The current phase is AI1 scaffold and OpenHands SDK feasibility research. This repository intentionally contains only the sustainable monorepo skeleton, Python Agent Server package foundation, frontend and contracts placeholders, and a minimal health check.

## Why Python Agent Server

The runtime direction is Python because FocusProof wants to learn from OpenHands SDK-style Agent, Conversation, Tool, Action and Observation abstractions. Python also keeps future tool executors and FastAPI service boundaries close to the agent runtime.

## Why OpenHands-Inspired

OpenHands provides useful architecture ideas for event-centered agent systems: a conversation container, one-step agent decisions, tool execution boundaries and structured observations. FocusProof borrows those runtime ideas while keeping learning evidence, scoring and review semantics as FocusProof-owned protocol.

## Why Web3 Is Only The First Plugin

Web3 evidence such as transaction hashes, contract addresses and wallet addresses can be externally verified, so it is a strong first demo domain. The core product is broader: programming, math, language learning, reading, research and exam preparation should all fit through domain plugins.

## Project Structure

```text
focusproof-agent/
  agent-server/
    focusproof/
      api/
      runtime/
      agents/
      domain/plugins/web3/
      tools/
      database/
      contracts/
    tests/
  contracts/
  docs/
    architecture/
    project-management/
    protocol/
    research/
  fixtures/
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

AI1 used Python `venv` plus `pip` for local verification. Database migrations are
run explicitly before starting the development server. Application startup only
checks the Alembic revision and never applies migrations automatically.

## AI Work Split

- AI0 owns controller and architecture documents.
- AI1 owns scaffold and OpenHands feasibility research.
- AI2 should implement the Python Agent Server runtime, EventLog, database, agents and tools.
- AI3 should implement the frontend and wallet user flows.
- AI4 should implement contracts, integration tests, security and deployment.
