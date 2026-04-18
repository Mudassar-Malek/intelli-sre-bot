"""
Claude-powered SRE triage agent.
All configurable values (critical services, model, thresholds) come from config.yaml.
"""

import json
import os
from pathlib import Path
from typing import Any

import anthropic
import yaml

from .models import Alert, Severity, TriageResult, TriageStatus
from .runbook_loader import RunbookLoader

# Load config — allows behaviour changes without code edits
_config_path = Path(__file__).parent.parent / "config.yaml"
_config: dict = yaml.safe_load(_config_path.read_text()) if _config_path.exists() else {}

SYSTEM_PROMPT = """You are IntelliSREBot, a senior SRE with deep production experience.

Your role during an incident:
1. Rapidly assess the blast radius and severity of an alert.
2. Identify the most likely root cause based on alert metadata and any provided runbook.
3. Recommend specific, ordered mitigation actions — not generic advice.
4. Generate concrete diagnostic follow-up queries (SPL, PromQL, kubectl).
5. Flag if this looks like a false positive and why.

Output format rules:
- Lead with the single most likely root cause in one sentence.
- Mitigation actions must be numbered and specific.
- Confidence: high / medium / low — state which and why.
- Never say "it depends" without immediately saying what it depends ON.
"""

TOOLS = [
    {
        "name": "get_runbook",
        "description": "Retrieve the runbook for a specific alert or service to guide triage.",
        "input_schema": {
            "type": "object",
            "properties": {
                "alert_title": {"type": "string", "description": "Alert name or title to look up."},
            },
            "required": ["alert_title"],
        },
    },
    {
        "name": "generate_diagnostic_queries",
        "description": "Generate Splunk SPL, PromQL, or kubectl diagnostic commands for an alert.",
        "input_schema": {
            "type": "object",
            "properties": {
                "alert_title": {"type": "string"},
                "source_system": {
                    "type": "string",
                    "description": "prometheus, splunk, kubernetes, or datadog",
                },
                "labels": {
                    "type": "object",
                    "description": "Alert labels (service, namespace, env, etc.)",
                },
            },
            "required": ["alert_title", "source_system"],
        },
    },
    {
        "name": "classify_severity",
        "description": "Re-assess severity based on service criticality and environment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "alert_severity": {"type": "string"},
                "service": {"type": "string"},
                "environment": {"type": "string"},
                "labels": {"type": "object"},
            },
            "required": ["alert_severity", "service", "environment"],
        },
    },
]


class IntelliSREAgent:
    def __init__(self, runbook_loader: RunbookLoader | None = None, model: str | None = None):
        self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self.model = model or _config.get("claude_model", "claude-sonnet-4-6")
        self.max_tokens = _config.get("max_tokens", 4096)
        self.runbook_loader = runbook_loader or RunbookLoader()
        # Load critical services from config — no hardcoding
        self.critical_services: set[str] = set(_config.get("critical_services", []))
        self.prod_environments: set[str] = set(_config.get("production_environments", ["prod", "production"]))

    def triage(self, alert: Alert) -> TriageResult:
        messages = [{"role": "user", "content": self._build_initial_message(alert)}]
        raw_analysis = self._agentic_loop(messages, alert)
        return self._parse_result(alert, raw_analysis)

    def chat(self, alert: Alert, conversation: list[dict], user_message: str) -> str:
        if not conversation:
            conversation.append({"role": "user", "content": self._build_initial_message(alert)})
        conversation.append({"role": "user", "content": user_message})
        return self._agentic_loop(conversation, alert)

    def _build_initial_message(self, alert: Alert) -> str:
        parts = [
            "## Incoming Alert\n",
            f"**Title:** {alert.title}",
            f"**Source:** {alert.source}",
            f"**Severity:** {alert.severity.value}",
            f"**Fired at:** {alert.fired_at or 'unknown'}",
        ]
        if alert.labels:
            parts.append(f"**Labels:**\n```json\n{json.dumps(alert.labels, indent=2)}\n```")
        desc = alert.annotations.get("description") or alert.annotations.get("summary", "")
        if desc:
            parts.append(f"**Description:** {desc}")
        parts.append("\nPlease triage this alert. Use available tools to enrich your analysis.")
        return "\n".join(parts)

    def _agentic_loop(self, messages: list[dict], alert: Alert) -> str:
        while True:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )

            text = ""
            tool_uses = []
            for block in response.content:
                if block.type == "text":
                    text = block.text
                elif block.type == "tool_use":
                    tool_uses.append(block)

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn" or not tool_uses:
                return text

            tool_results = []
            for tu in tool_uses:
                result = self._dispatch_tool(tu.name, tu.input, alert)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(result, default=str),
                })
            messages.append({"role": "user", "content": tool_results})

    def _dispatch_tool(self, name: str, inputs: dict, alert: Alert) -> Any:
        if name == "get_runbook":
            _, content = self.runbook_loader.find(inputs["alert_title"], alert.labels)
            return {"found": content is not None, "content": content or "No runbook found."}

        if name == "generate_diagnostic_queries":
            return self._generate_queries(inputs, alert)

        if name == "classify_severity":
            return self._classify_severity(inputs)

        raise ValueError(f"Unknown tool: {name}")

    def _generate_queries(self, inputs: dict, alert: Alert) -> dict:
        source = inputs.get("source_system", alert.source)
        labels = inputs.get("labels", alert.labels)
        service = labels.get("service", "your-service")
        namespace = labels.get("namespace", "default")
        env = labels.get("env", "production")

        queries: dict[str, list[str]] = {}

        if source in ("splunk", "prometheus", "datadog"):
            queries["splunk_spl"] = [
                f'index=app_logs service="{service}" env="{env}" level=ERROR | stats count by host | sort -count',
                f'index=app_logs service="{service}" | timechart span=1m count by level',
            ]

        if source in ("kubernetes", "prometheus") or "namespace" in labels:
            queries["kubectl"] = [
                f"kubectl get pods -n {namespace} --sort-by='.status.startTime'",
                f"kubectl describe deployment {service} -n {namespace}",
                f"kubectl logs -n {namespace} -l app={service} --tail=100 --previous",
                f"kubectl top pods -n {namespace} -l app={service}",
            ]
            queries["promql"] = [
                f'rate(http_requests_total{{service="{service}",status=~"5.."}}[5m])',
                f'histogram_quantile(0.99, rate(http_request_duration_seconds_bucket{{service="{service}"}}[5m]))',
            ]

        return queries

    def _classify_severity(self, inputs: dict) -> dict:
        service = inputs.get("service", "")
        env = inputs.get("environment", "")
        original = inputs.get("alert_severity", "unknown")

        # Use config-driven lists — no hardcoded service names
        is_critical = any(s in service.lower() for s in self.critical_services)
        is_prod = env.lower() in self.prod_environments

        if is_prod and is_critical:
            effective = Severity.CRITICAL.value
            note = (
                f"'{service}' is in the configured critical_services list and runs in "
                f"'{env}' (a production environment). Always treat as CRITICAL."
            )
        elif is_prod:
            effective = original
            note = f"Production environment — maintain original severity '{original}'."
        else:
            effective = Severity.LOW.value
            note = f"Non-production environment ('{env}') — downgrade for triage purposes."

        return {"original_severity": original, "effective_severity": effective, "note": note}

    def _parse_result(self, alert: Alert, raw_analysis: str) -> TriageResult:
        lines = raw_analysis.strip().split("\n")
        summary = next((l.lstrip("# ").strip() for l in lines if l.strip()), raw_analysis[:120])
        _, runbook_content = self.runbook_loader.find(alert.title, alert.labels)
        runbook_url = None
        if runbook_content:
            import re
            m = re.search(r'\[.*?\]\((https?://[^\)]+)\)', runbook_content)
            if m:
                runbook_url = m.group(1)
        return TriageResult(
            alert_id=alert.id,
            status=TriageStatus.INVESTIGATING,
            severity=alert.severity,
            summary=summary,
            likely_cause="See full analysis below",
            recommended_actions=[],
            runbook_url=runbook_url,
            follow_up_queries=[],
            confidence="medium",
            raw_analysis=raw_analysis,
        )
