"""
Loads runbooks from the /runbooks directory and matches them to alerts
using alert title and label heuristics.
"""

import re
from pathlib import Path


class RunbookLoader:
    def __init__(self, runbook_dir: str | None = None):
        if runbook_dir is None:
            runbook_dir = str(Path(__file__).parent.parent / "runbooks")
        self.runbook_dir = Path(runbook_dir)
        self._cache: dict[str, str] = {}
        self._load_all()

    def _load_all(self) -> None:
        if not self.runbook_dir.exists():
            return
        for path in self.runbook_dir.glob("*.md"):
            self._cache[path.stem.lower()] = path.read_text()

    def find(self, alert_title: str, labels: dict[str, str]) -> tuple[str | None, str | None]:
        """Return (runbook_name, runbook_content) or (None, None) if no match."""
        normalized = re.sub(r"[^a-z0-9]+", "_", alert_title.lower()).strip("_")

        # Exact match first
        if normalized in self._cache:
            return normalized, self._cache[normalized]

        # Partial match — find longest key that is a substring of the alert title
        candidates = [(k, v) for k, v in self._cache.items() if k in normalized or normalized in k]
        if candidates:
            best = max(candidates, key=lambda x: len(x[0]))
            return best

        return None, None

    def list_runbooks(self) -> list[str]:
        return sorted(self._cache.keys())
