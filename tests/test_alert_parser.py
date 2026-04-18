"""Tests for alert parsers."""

from src.alert_parser import from_alertmanager, from_datadog, from_generic
from src.models import Severity


def test_alertmanager_parse():
    payload = {
        "alerts": [
            {
                "labels": {"alertname": "HighErrorRate", "severity": "critical", "service": "payments-api"},
                "annotations": {"description": "Error rate above 10% for 5 minutes"},
                "startsAt": "2026-04-18T14:23:00Z",
            }
        ]
    }
    alerts = from_alertmanager(payload)
    assert len(alerts) == 1
    assert alerts[0].title == "HighErrorRate"
    assert alerts[0].severity == Severity.CRITICAL
    assert alerts[0].source == "prometheus"
    assert alerts[0].labels["service"] == "payments-api"


def test_alertmanager_empty_payload():
    alerts = from_alertmanager({"alerts": []})
    assert alerts == []


def test_datadog_parse():
    payload = {
        "id": "12345",
        "title": "P2 - High latency on payments",
        "priority": "P2",
        "body": "p99 latency exceeded 2s threshold",
        "tags": {"env": "production", "service": "payments-api"},
    }
    alert = from_datadog(payload)
    assert alert.severity == Severity.HIGH
    assert alert.source == "datadog"
    assert alert.id == "12345"


def test_generic_parse_unknown_severity():
    payload = {"title": "Disk Usage High", "severity": "warning", "host": "worker-01"}
    alert = from_generic(payload)
    assert alert.severity == Severity.MEDIUM
    assert alert.title == "Disk Usage High"


def test_severity_mapping():
    # Datadog priority labels P1-P4
    for raw, expected in [("P1", Severity.CRITICAL), ("p2", Severity.HIGH), ("P3", Severity.MEDIUM), ("P4", Severity.LOW)]:
        payload = {"id": "x", "title": "test", "priority": raw, "tags": {}}
        alert = from_datadog(payload)
        assert alert.severity == expected, f"Failed for {raw}"

    # Generic severity labels (warning, critical, etc.) go through from_generic
    for raw, expected in [("warning", Severity.MEDIUM), ("critical", Severity.CRITICAL), ("info", Severity.LOW)]:
        payload = {"title": "test", "severity": raw}
        alert = from_generic(payload)
        assert alert.severity == expected, f"Failed for {raw}"
