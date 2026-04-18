"""
MCP server exposing IntelliSREBot as composable tools.
Allows kubectl-ai, Claude Desktop, or other MCP hosts to invoke SRE triage.

Start: python -m src.mcp_server
"""

import asyncio
import json

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .models import Alert, Severity
from .runbook_loader import RunbookLoader
from .sre_agent import IntelliSREAgent

app = Server("intelli-sre-bot")
_agent = IntelliSREAgent(runbook_loader=RunbookLoader())


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="triage_alert",
            description=(
                "Triage a production alert using AI-powered SRE analysis. "
                "Returns root cause, recommended actions, and diagnostic queries."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Alert title/name"},
                    "source": {"type": "string", "description": "Alert source (prometheus, datadog, splunk, etc.)"},
                    "severity": {"type": "string", "description": "Alert severity (critical, high, medium, low)"},
                    "labels": {"type": "object", "description": "Key-value labels from the alert"},
                    "description": {"type": "string", "description": "Alert description or message body"},
                },
                "required": ["title", "source", "severity"],
            },
        ),
        Tool(
            name="list_runbooks",
            description="List available SRE runbooks that can be matched to alerts.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="get_runbook",
            description="Retrieve the full content of a specific runbook.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Runbook name (from list_runbooks)"},
                },
                "required": ["name"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    loader = RunbookLoader()

    if name == "triage_alert":
        alert = Alert(
            id=arguments.get("title", "unknown").replace(" ", "_").lower(),
            title=arguments["title"],
            source=arguments["source"],
            severity=Severity(arguments.get("severity", "unknown").lower()),
            labels=arguments.get("labels", {}),
            annotations={"description": arguments.get("description", "")},
            raw_payload=arguments,
        )
        result = _agent.triage(alert)
        from dataclasses import asdict
        return [TextContent(type="text", text=json.dumps(asdict(result), indent=2, default=str))]

    if name == "list_runbooks":
        runbooks = loader.list_runbooks()
        return [TextContent(type="text", text=json.dumps(runbooks, indent=2))]

    if name == "get_runbook":
        rb_name = arguments["name"]
        content = loader._cache.get(rb_name)
        if not content:
            return [TextContent(type="text", text=f"Runbook '{rb_name}' not found.")]
        return [TextContent(type="text", text=content)]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
