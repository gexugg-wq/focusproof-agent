from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_SERVER = ROOT / "agent-server"
if str(AGENT_SERVER) not in sys.path:
    sys.path.insert(0, str(AGENT_SERVER))

import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi import FastAPI  # noqa: E402
import focusproof.api.app  # noqa: E402
from focusproof.api.models import SubmitEvidenceRequest  # noqa: E402
from openhands.sdk.llm import Message, MessageToolCall, TextContent  # noqa: E402
import openhands.sdk.testing  # noqa: E402

LOOPBACK_HOST = "127.0.0.1"
SMOKE_EVIDENCE_TEXT = (
    "Append-only event replay rebuilds state by applying immutable events in "
    "sequence, preserving the history needed to reproduce the current view."
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the production FocusProof FastAPI application with a deterministic "
            "OpenHands SDK TestLLM on IPv4 loopback only."
        )
    )
    parser.add_argument("--host", required=True, choices=(LOOPBACK_HOST,))
    parser.add_argument("--port", required=True, type=_port)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--scenario", required=True, choices=("general-flow",))
    return parser.parse_args(argv)


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65_535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _apply_migrations(database_url: str) -> None:
    config = Config(ROOT / "alembic.ini")
    config.set_main_option(
        "script_location",
        str(ROOT / "agent-server" / "migrations"),
    )
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def _general_flow_llm_factory(session_id: str) -> openhands.sdk.testing.TestLLM:
    evidence_id = focusproof.api.app._evidence_id_for_request(
        session_id,
        SubmitEvidenceRequest(
            evidenceType="text",
            textContent=SMOKE_EVIDENCE_TEXT,
        ),
    )
    verify = MessageToolCall(
        id=f"verify_{session_id}",
        name="focusproof_text_evidence_verification",
        arguments=json.dumps({"evidence_id": evidence_id}),
        origin="completion",
    )
    question = MessageToolCall(
        id=f"question_{session_id}",
        name="focusproof_learner_input",
        arguments=json.dumps(
            {
                "question": (
                    "Explain why retaining earlier events makes replay reproducible."
                ),
                "reason": "The final review needs an independent learner explanation.",
                "requested_evidence_type": "text",
            }
        ),
        origin="completion",
    )
    draft = MessageToolCall(
        id=f"draft_{session_id}",
        name="focusproof_review_draft",
        arguments=json.dumps(
            {
                "credibility_findings": [
                    "Repository-backed text evidence was inspected."
                ],
                "understanding_findings": [
                    "The learner supplied a concrete replay explanation."
                ],
                "contradictions": [],
                "recommended_next_step": "Apply replay to one additional event sequence.",
                "confidence": 0.72,
            }
        ),
        origin="completion",
    )
    return openhands.sdk.testing.TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="Inspect the submitted evidence.")],
                tool_calls=[verify],
            ),
            Message(
                role="assistant",
                content=[TextContent(text="Request one independent explanation.")],
                tool_calls=[question],
            ),
            Message(
                role="assistant",
                content=[TextContent(text="Submit the completed review draft.")],
                tool_calls=[draft],
            ),
        ]
    )


def _scenario_factory(
    scenario: str,
) -> Callable[[str], openhands.sdk.testing.TestLLM]:
    if scenario == "general-flow":
        return _general_flow_llm_factory
    raise ValueError(f"Unsupported deterministic scenario: {scenario}")


def build_app(args: argparse.Namespace) -> FastAPI:
    if args.host != LOOPBACK_HOST:
        raise ValueError("AI4B test server is restricted to 127.0.0.1")
    data_dir = Path(args.data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    _apply_migrations(str(args.database_url))
    return focusproof.api.app.create_app(
        database_url=str(args.database_url),
        data_dir=data_dir,
        llm_factory=_scenario_factory(str(args.scenario)),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    app = build_app(args)
    uvicorn.run(
        app,
        host=LOOPBACK_HOST,
        port=args.port,
        log_config=None,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
