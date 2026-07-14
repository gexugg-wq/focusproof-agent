# FocusProof Agent

FocusProof Agent is a general learning verification system. It evaluates whether a learning session produced credible, reviewable evidence, using the learner's goal, submitted evidence, tool observations, explanations, follow-up answers, learning output, reflection and next-step plan.

AI1 through AI4A are complete. The repository now contains the OpenHands-native Python review runtime, persistent product projections, a general text/URL verification framework, and the frontend MVP. The next development phase is AI4B.0, a design gate for optional on-chain proof recording, contract boundaries, integration security and Monad Testnet deployment sequencing.

## Why Python Agent Server

The runtime direction is Python because FocusProof directly uses OpenHands SDK Agent, Conversation, Tool, Action and Observation abstractions. Python also keeps tool executors and FastAPI service boundaries close to the agent runtime.

## Why OpenHands-Native

FocusProof directly uses suitable public OpenHands SDK runtime capabilities, including Conversation, Agent, native events and the tool protocol. It does not maintain OpenHands-inspired mirror implementations when the SDK already provides the behavior. FocusProof owns learning evidence, scoring, authorization, product persistence projections and review semantics, connected to the native runtime through thin adapters.

## Why Web3 Is Only The First Plugin

Web3 evidence such as transaction hashes, contract addresses and wallet addresses can be externally verified, so it is a strong first demo domain. The core product is broader: programming, math, language learning, reading, research and exam preparation should all fit through domain plugins.

## Project Structure

```text
focusproof-agent/
  agent-server/
    focusproof/
      api/
      openhands_adapter/
      openhands_runtime/
      persistence/
      runtime/
      agents/
      domain/plugins/web3/
      tools/
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

Development uses Python `venv` plus `pip` in WSL. Database migrations are run
explicitly before starting the development server. Application startup only
checks the Alembic revision and never applies migrations automatically.

## AI Work Split

- AI0 owns controller and architecture documents.
- AI1 owns scaffold and OpenHands feasibility research.
- AI2 owns the completed Python Agent Server and OpenHands Conversation runtime.
- AI3 owns the completed frontend MVP and optional wallet user flow.
- AI4A owns the completed general text/URL verification framework.
- AI4B is next: contract, proof-recording integration, security and deployment, split into sequential design and implementation gates.
