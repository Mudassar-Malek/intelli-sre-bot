"""
Interactive CLI — paste an alert payload or describe an alert in plain English.
Run: python -m src.cli
"""

import json
import os
import readline  # noqa: F401
import sys

from .alert_parser import from_generic
from .models import Alert, Severity
from .runbook_loader import RunbookLoader
from .sre_agent import IntelliSREAgent

BANNER = """
╔══════════════════════════════════════════════════════════╗
║         IntelliSREBot — AI-Powered Alert Triage          ║
║  Type an alert description or paste a JSON payload       ║
║  Commands: 'runbooks'  'reset'  'exit'                   ║
╚══════════════════════════════════════════════════════════╝
"""


def _require_env(var: str) -> str:
    val = os.environ.get(var)
    if not val:
        print(f"[error] {var} is not set", file=sys.stderr)
        sys.exit(1)
    return val


def main() -> None:
    _require_env("ANTHROPIC_API_KEY")

    agent = IntelliSREAgent(runbook_loader=RunbookLoader())
    print(BANNER)

    conversation: list[dict] = []
    current_alert: Alert | None = None

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit"):
            print("Bye.")
            break

        if user_input.lower() == "reset":
            conversation = []
            current_alert = None
            print("[conversation and alert context cleared]")
            continue

        if user_input.lower() == "runbooks":
            books = agent.runbook_loader.list_runbooks()
            print("Available runbooks:", ", ".join(books) if books else "(none found)")
            continue

        # Try to parse as JSON alert payload
        if user_input.startswith("{"):
            try:
                payload = json.loads(user_input)
                current_alert = from_generic(payload)
                conversation = []
                print(f"\n[Alert loaded: {current_alert.title} / {current_alert.severity.value}]")
                print("\nIntelliSREBot: ", end="", flush=True)
                reply = agent.chat(current_alert, conversation, "Triage this alert.")
                print(reply)
                continue
            except json.JSONDecodeError:
                pass

        # Plain English input — treat as alert description if no alert is set
        if current_alert is None:
            current_alert = Alert(
                id="cli_" + user_input[:20].replace(" ", "_").lower(),
                title=user_input,
                source="cli",
                severity=Severity.UNKNOWN,
                labels={},
                annotations={"description": user_input},
                raw_payload={},
            )
            conversation = []

        print("\nIntelliSREBot: ", end="", flush=True)
        try:
            reply = agent.chat(current_alert, conversation, user_input)
            print(reply)
        except Exception as exc:
            print(f"\n[error] {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
