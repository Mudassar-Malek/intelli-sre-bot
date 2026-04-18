"""
Normalize alerts from different sources into a common Alert model.
Supports Alertmanager (Prometheus), Datadog, and generic webhook payloads.
"""

from .models import Alert, Severity


def _map_severity(raw: str) -> Severity:
    mapping = {
        "critical": Severity.CRITICAL,
        "p1": Severity.CRITICAL,
        "error": Severity.HIGH,
        "high": Severity.HIGH,
        "p2": Severity.HIGH,
        "warning": Severity.MEDIUM,
        "medium": Severity.MEDIUM,
        "p3": Severity.MEDIUM,
        "info": Severity.LOW,
        "low": Severity.LOW,
        "p4": Severity.LOW,
    }
    return mapping.get(raw.lower(), Severity.UNKNOWN)


def from_alertmanager(payload: dict) -> list[Alert]:
    """Parse an Alertmanager webhook payload (can contain multiple alerts)."""
    alerts = []
    for raw in payload.get("alerts", []):
        labels = raw.get("labels", {})
        annotations = raw.get("annotations", {})
        alerts.append(Alert(
            id=labels.get("alertname", "unknown") + "_" + raw.get("startsAt", "")[:19],
            title=labels.get("alertname", "Unknown Alert"),
            source="prometheus",
            severity=_map_severity(labels.get("severity", "unknown")),
            labels=labels,
            annotations=annotations,
            raw_payload=raw,
            fired_at=raw.get("startsAt", ""),
        ))
    return alerts


def from_datadog(payload: dict) -> Alert:
    """Parse a Datadog monitor webhook payload."""
    title = payload.get("title", "Datadog Alert")
    alert_id = str(payload.get("id", "unknown"))
    priority = payload.get("priority", "normal")
    severity_map = {"P1": Severity.CRITICAL, "P2": Severity.HIGH, "P3": Severity.MEDIUM, "P4": Severity.LOW}
    severity = severity_map.get(priority.upper(), Severity.UNKNOWN)

    return Alert(
        id=alert_id,
        title=title,
        source="datadog",
        severity=severity,
        labels={
            "monitor_id": alert_id,
            "env": payload.get("tags", {}).get("env", "unknown"),
            "service": payload.get("tags", {}).get("service", "unknown"),
        },
        annotations={"description": payload.get("body", "")},
        raw_payload=payload,
        fired_at=payload.get("date", ""),
    )


def from_generic(payload: dict) -> Alert:
    """Best-effort parse for unknown webhook formats."""
    title = (
        payload.get("title")
        or payload.get("alertname")
        or payload.get("name")
        or "Unknown Alert"
    )
    severity_raw = (
        payload.get("severity")
        or payload.get("priority")
        or payload.get("level")
        or "unknown"
    )
    return Alert(
        id=str(payload.get("id", hash(str(payload)))),
        title=title,
        source=payload.get("source", "unknown"),
        severity=_map_severity(severity_raw),
        labels={k: str(v) for k, v in payload.items() if isinstance(v, str)},
        annotations={},
        raw_payload=payload,
        fired_at=str(payload.get("timestamp", "")),
    )
