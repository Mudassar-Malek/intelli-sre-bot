"""
Claude-powered SRE triage agent.

Given an alert, it:
1. Identifies the likely cause based on alert metadata and any relevant runbook
2. Recommends immediate mitigation actions
3. Generates follow-up diagnostic queries (Splunk SPL, kubectl commands, PromQL)
4. Produces a structured TriageResult
"""

import json
import os
from typing import Any

import anthropic

from .models import Alert, Severity, TriageResult, TriageStatus
from .runbook_loader import RunbookLoader

SYSTEM_PROMPT = """You are IntelliSREBot, a senior SRE with 12 years of experience in fintech production systems.

Your role during an incident:
1. Rapidly assess the blast radius and severity of an alert.
2. Identify the most likely root cause based on alert metadata, labels, and any provided runbook.
3. Recommend specific, ordered mitigation actions — not generic advice.
4. Generate concrete diagnostic follow-up queries in SPL (Splunk), PromQL, or kubectl as appropriate.
5. Flag if this looks like a false positive and why.

Output format rules:
- Lead with the single most likely root cause in one sentence.
- Mitigation actions must be numbered and specific (e.g. "Scale the payments-api deployment to 8 replicas" not "scale the service").
- Confidence: high (multiple corroborating signals), medium (pattern match), low (guessing from title only).
- Never say "it depends" without immediately saying what it depends ON and what to check.

You have access to tools to enrich your analysis. Use them before concluding.
"""

TOOLS = [
    {
        "name": "get_runbook",
        "description": "Retrieve the runbook for a specific alert or service to guide the triage.",
        "input_schema": {
            "type": "object",
            "properties": {
                "alert_title": {"type": "string", "description": "The alert name or title to look up."},
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
                "source_system": {"type": "string", "description": "prometheus, splunk, kubernetes, or datadog"},
                "labels": {"type": "object", "description": "Alert labels for context (service, namespace, env, etc.)"},
            },
            "required": ["alert_title", "source_system"],
        },
    },
    {
        "name": "classify_severity",
        "description": "Re-assess alert severity based on context (time of day, service criticality, customer impact).",
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
    def __init__(self, runbook_loader: RunbookLoader | None = None, model: str = "claude-sonnet-4-6"):
        self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self.model = model
        self.runbook_loader = runbook_loader or RunbookLoader()

    def triage(self, alert: Alert) -> TriageResult:
        """Run the full triage loop for an alert and return a structured result."""
        initial_message = self._build_initial_message(alert)
        messages = [{"role": "user", "content": initial_message}]
        raw_analysis = self._agentic_loop(messages, alert)
        return self._parse_result(alert, raw_analysis)

    def chat(self, alert: Alert, conversation: list[dict], user_message: str) -> str:
        """Continue an interactive triage conversation."""
        if not conversation:
            conversation.append({"role": "user", "content": self._build_initial_message(alert)})
        conversation.append({"role": "user", "content": user_message})
        return self._agentic_loop(conversation, alert)

    def _build_initial_message(self, alert: Alert) -> str:
        parts = [
            f"## Incoming Alert\n",
            f"**Title:** {alert.title}",
            f"**Source:** {alert.source}",
            f"**Severity:** {alert.severity.value}",
            f"**Fired at:** {alert.fired_at or 'unknown'}",
        ]
        if alert.labels:
            parts.append(f"**Labels:**\n```json\n{json.dumps(alert.labels, indent=2)}\n```")
        if alert.annotations:
            desc = alert.annotations.get("description") or alert.annotations.get("summary", "")
            if desc:
                parts.append(f"**Description:** {desc}")
        parts.append("\nPlease triage this alert. Use available tools to enrich your analysis.")
        return "\n".join(parts)

    def _agentic_loop(self, messages: list[dict], alert: Alert) -> str:
        while True:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )

            text_content = ""
            tool_uses = []
            for block in response.content:
                if block.type == "text":
                    text_content = block.text
                elif block.type == "tool_use":
                    tool_uses.append(block)

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn" or not tool_uses:
                return text_content

            tool_results = []
            for tool_use in tool_uses:
                result = self._dispatch_tool(tool_use.name, tool_use.input, alert)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": json.dumps(result, default=str),
                })
            messages.append({"role": "user", "content": tool_results})

    def _dispatch_tool(self, name: str, inputs: dict, alert: Alert) -> Any:
        if name == "get_runbook":
            _, content = self.runbook_loader.find(inputs["alert_title"], alert.labels)
            return {"found": content is not None, "content": content or "No runbook found for this alert."}

        if name == "generate_diagnostic_queries":
            return self._generate_queries(inputs, alert)

        if name == "classify_severity":
            return self._classify_severity(inputs)

        raise ValueError(f"Unknown tool: {name}")

    def _generate_queries(self, inputs: dict, alert: Alert) -> dict:
        source = inputs.get("source_system", alert.source)
        title = inputs.get("alert_title", alert.title)
        labels = inputs.get("labels", alert.labels)
        service = labels.get("service", "unknown")
        namespace = labels.get("namespace", "default")
        env = labels.get("env", "production")

        queries: dict[str, list[str]] = {}

        if source in ("splunk", "prometheus", "datadog"):
            queries["splunk_spl"] = [
                f'index=app_logs service="{service}" env="{env}" level=ERROR | stats count by host | sort -count',
                f'index=app_logs service="{service}" | timechart span=1m count by level',
                f'index=infra_metrics service="{service}" metric_name=error_rate | stats max(value) by host',
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
                f'container_memory_usage_bytes{{namespace="{namespace}",pod=~"{service}.*"}}',
            ]

        return queries

    def _classify_severity(self, inputs: dict) -> dict:
        service = inputs.get("service", "unknown")
        env = inputs.get("environment", "unknown")
        original = inputs.get("alert_severity", "unknown")

        critical_services = {"payments", "auth", "fraud-detection", "settlement", "ledger"}
        is_critical_service = any(s in service.lower() for s in critical_services)
        is_prod = env.lower() in ("production", "prod")

        if is_prod and is_critical_service:
            effective = Severity.CRITICAL.value
            note = f"{service} is a payment-critical service in {env}; always treat as CRITICAL regardless of alert label."
        elif is_prod and not is_critical_service:
            effective = original
            note = f"Production environment — maintain original severity {original}."
        else:
            effective = Severity.LOW.value
            note = f"Non-production environment ({env}) — downgrade severity for triage purposes."

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
