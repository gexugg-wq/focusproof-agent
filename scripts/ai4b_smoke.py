from __future__ import annotations

import argparse
import json
import secrets
import sys
from collections.abc import Mapping, Sequence
from typing import Any, TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

SMOKE_EVIDENCE_TEXT = (
    "Append-only event replay rebuilds state by applying immutable events in "
    "sequence, preserving the history needed to reproduce the current view."
)
SMOKE_ANSWER_TEXT = (
    "Earlier events remain available, so replay can start from an empty state "
    "and deterministically apply the same ordered history again."
)


class SmokeError(RuntimeError):
    pass


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a vendor-neutral FocusProof health and HTTP smoke without printing "
            "learner content, secrets, or environment values."
        )
    )
    parser.add_argument("--base-url", required=True, type=_base_url)
    parser.add_argument(
        "--scripted-review",
        action="store_true",
        help="Exercise the deterministic general-flow review on the AI4B test server.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    args = parser.parse_args(argv)
    if (
        args.scripted_review
        and urlparse(str(args.base_url)).hostname != "127.0.0.1"
    ):
        parser.error("--scripted-review is restricted to the loopback AI4B test server")
    return args


def _base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise argparse.ArgumentTypeError("base URL must be absolute HTTP(S)")
    return value.rstrip("/")


def request_json(
    method: str,
    url: str,
    *,
    payload: Mapping[str, Any] | None = None,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    body = None
    headers = {"accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["content-type"] = "application/json"
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read()
            if not 200 <= response.status < 300:
                raise SmokeError(f"HTTP request failed with status {response.status}")
    except HTTPError as exc:
        raise SmokeError(f"HTTP request failed with status {exc.code}") from None
    except (TimeoutError, URLError):
        raise SmokeError("HTTP request failed before a safe response was received") from None
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SmokeError("HTTP response was not valid JSON") from None
    if not isinstance(parsed, dict):
        raise SmokeError("HTTP response was not a JSON object")
    return parsed


def _endpoint(base_url: str, path: str) -> str:
    return urljoin(f"{base_url}/", path.lstrip("/"))


def run_smoke(
    *,
    base_url: str,
    scripted_review: bool,
    output: TextIO,
    timeout_seconds: float = 10.0,
) -> int:
    health = request_json(
        "GET",
        _endpoint(base_url, "/health"),
        timeout_seconds=timeout_seconds,
    )
    health_status = str(health.get("status", "unknown"))
    readiness = health.get("readiness")
    print(
        f"health status={health_status} readiness={'ready' if readiness is None else 'degraded'}",
        file=output,
    )
    if health_status != "ok" or readiness is not None:
        raise SmokeError("FocusProof health endpoint is not ready")

    suffix = secrets.token_hex(8)
    created = request_json(
        "POST",
        _endpoint(base_url, "/sessions"),
        payload={
            "domain": "general",
            "title": f"AI4B local smoke {suffix}",
            "goal": "Explain why append-only event replay can reproduce state.",
            "expectedOutput": "A concise independent explanation",
            "plannedMinutes": 10,
        },
        timeout_seconds=timeout_seconds,
    )
    session_id = str(created["sessionId"])
    print(f"session id={session_id} status=created", file=output)

    submitted = request_json(
        "POST",
        _endpoint(base_url, f"/sessions/{session_id}/evidence"),
        payload={
            "evidenceType": "text",
            "textContent": SMOKE_EVIDENCE_TEXT,
        },
        timeout_seconds=timeout_seconds,
    )
    evidence_id = str(submitted["evidenceId"])
    print(
        f"evidence id={evidence_id} syncPending={bool(submitted.get('syncPending'))}",
        file=output,
    )
    if not scripted_review:
        print("review status=skipped", file=output)
        return 0

    first = request_json(
        "POST",
        _endpoint(base_url, f"/sessions/{session_id}/review"),
        timeout_seconds=timeout_seconds,
    )
    first_status = str(first.get("reviewStatus", "unknown"))
    print(f"review status={first_status}", file=output)
    questions = first.get("agentQuestions")
    if first_status != "awaiting_user" or not isinstance(questions, list) or not questions:
        raise SmokeError("Scripted review did not request learner input")
    question = questions[0]
    if not isinstance(question, dict) or "questionId" not in question:
        raise SmokeError("Scripted review returned an invalid question")

    request_json(
        "POST",
        _endpoint(base_url, f"/sessions/{session_id}/answer"),
        payload={
            "questionId": str(question["questionId"]),
            "answer": SMOKE_ANSWER_TEXT,
        },
        timeout_seconds=timeout_seconds,
    )
    completed = request_json(
        "POST",
        _endpoint(base_url, f"/sessions/{session_id}/review"),
        timeout_seconds=timeout_seconds,
    )
    final_status = str(completed.get("reviewStatus", "unknown"))
    print(f"review status={final_status}", file=output)
    if final_status != "completed":
        raise SmokeError("Scripted review did not complete")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run_smoke(
            base_url=args.base_url,
            scripted_review=args.scripted_review,
            output=sys.stdout,
            timeout_seconds=args.timeout_seconds,
        )
    except (KeyError, SmokeError) as exc:
        print(f"smoke status=failed reason={type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
