"""
FastAPI webhook server that receives alerts from Alertmanager / Datadog
and triggers IntelliSREBot triage automatically.

Start: uvicorn src.webhook_server:app --host 0.0.0.0 --port 8080
"""

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse

from .alert_parser import from_alertmanager, from_datadog, from_generic
from .models import Alert
from .runbook_loader import RunbookLoader
from .sre_agent import IntelliSREAgent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="IntelliSREBot", version="0.1.0")

_agent = IntelliSREAgent(runbook_loader=RunbookLoader())
_triage_results: dict[str, Any] = {}


def _run_triage(alert: Alert) -> None:
    logger.info("Triaging alert %s (severity=%s)", alert.id, alert.severity.value)
    try:
        result = _agent.triage(alert)
        _triage_results[alert.id] = result
        logger.info("Triage complete for %s: %s", alert.id, result.summary[:80])
    except Exception as exc:
        logger.error("Triage failed for %s: %s", alert.id, exc)
        _triage_results[alert.id] = {"error": str(exc)}


@app.post("/webhook/alertmanager")
async def alertmanager_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    alerts = from_alertmanager(payload)
    if not alerts:
        raise HTTPException(status_code=400, detail="No alerts found in payload")
    for alert in alerts:
        background_tasks.add_task(_run_triage, alert)
    return {"status": "accepted", "alert_count": len(alerts), "ids": [a.id for a in alerts]}


@app.post("/webhook/datadog")
async def datadog_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    alert = from_datadog(payload)
    background_tasks.add_task(_run_triage, alert)
    return {"status": "accepted", "id": alert.id}


@app.post("/webhook/generic")
async def generic_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    alert = from_generic(payload)
    background_tasks.add_task(_run_triage, alert)
    return {"status": "accepted", "id": alert.id}


@app.get("/triage/{alert_id}")
async def get_triage_result(alert_id: str):
    result = _triage_results.get(alert_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No triage result for alert {alert_id}")
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=500, content=result)
    from dataclasses import asdict
    return asdict(result)


@app.get("/health")
async def health():
    return {"status": "ok"}
