from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_KEY_NAMES = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "DASHSCOPE_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "LLM_API_KEY",
        "OPENAI_API_KEY",
    }
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run AI4B release gates with argument-array subprocesses and a child "
            "environment stripped of real provider credentials."
        )
    )
    parser.add_argument(
        "--backend-only",
        action="store_true",
        help="Run Python backend gates without invoking frontend Node commands.",
    )
    return parser.parse_args(argv)


def build_child_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    child = dict(os.environ if source is None else source)
    for name in PROVIDER_KEY_NAMES:
        child.pop(name, None)
    return child


def backend_commands() -> list[list[str]]:
    return [
        [
            sys.executable,
            "-m",
            "pytest",
            "agent-server/tests",
            "-q",
            "-m",
            "not real_llm",
        ],
        [sys.executable, "-m", "ruff", "check", "agent-server"],
        [sys.executable, "-m", "mypy", "agent-server"],
        ["git", "diff", "--check"],
    ]


def frontend_commands() -> list[list[str]]:
    return [
        ["npm", "--prefix", "frontend", "run", "lint"],
        ["npm", "--prefix", "frontend", "run", "typecheck"],
        ["npm", "--prefix", "frontend", "run", "test"],
        ["npm", "--prefix", "frontend", "run", "build"],
        ["npm", "--prefix", "frontend", "run", "test:e2e"],
        ["npm", "--prefix", "frontend", "audit", "--omit=dev"],
    ]


def run_checks(
    *,
    commands: Sequence[Sequence[str]],
    child_env: dict[str, str],
    output: TextIO,
) -> int:
    for command_values in commands:
        command = list(command_values)
        started = time.monotonic()
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=child_env,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        duration = time.monotonic() - started
        print(
            f"command={shlex.join(command)} duration={duration:.2f}s "
            f"exit={completed.returncode}",
            file=output,
        )
        if completed.returncode != 0:
            return completed.returncode
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    commands = backend_commands()
    if not args.backend_only:
        commands.extend(frontend_commands())
    return run_checks(
        commands=commands,
        child_env=build_child_env(),
        output=sys.stdout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
