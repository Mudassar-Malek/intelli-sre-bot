"""Shared data models for IntelliSREBot."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class TriageStatus(str, Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    MITIGATED = "mitigated"
    RESOLVED = "resolved"
    FALSE_POSITIVE = "false_positive"


@dataclass
class Alert:
    id: str
    title: str
    source: str                      # "prometheus", "datadog", "splunk", "pagerduty", etc.
    severity: Severity
    labels: dict[str, str] = field(default_factory=dict)
    annotations: dict[str, str] = field(default_factory=dict)
    raw_payload: dict[str, Any] = field(default_factory=dict)
    fired_at: str = ""


@dataclass
class TriageResult:
    alert_id: str
    status: TriageStatus
    severity: Severity
    summary: str
    likely_cause: str
    recommended_actions: list[str]
    runbook_url: str | None
    follow_up_queries: list[str]
    confidence: str                  # "high" | "medium" | "low"
    raw_analysis: str
