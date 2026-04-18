"""Tests for RunbookLoader."""

import pytest
import tempfile
import os
from pathlib import Path

from src.runbook_loader import RunbookLoader


@pytest.fixture
def runbook_dir(tmp_path):
    (tmp_path / "high_error_rate.md").write_text("# High Error Rate\nCheck pods.")
    (tmp_path / "pod_crashloopbackoff.md").write_text("# CrashLoop\nhttps://wiki/crashloop")
    return str(tmp_path)


def test_exact_match(runbook_dir):
    loader = RunbookLoader(runbook_dir=runbook_dir)
    name, content = loader.find("high_error_rate", {})
    assert name == "high_error_rate"
    assert "High Error Rate" in content


def test_partial_match(runbook_dir):
    loader = RunbookLoader(runbook_dir=runbook_dir)
    name, content = loader.find("HighErrorRate", {})
    assert name is not None
    assert content is not None


def test_no_match(runbook_dir):
    loader = RunbookLoader(runbook_dir=runbook_dir)
    name, content = loader.find("completely_unrelated_alert", {})
    assert name is None
    assert content is None


def test_list_runbooks(runbook_dir):
    loader = RunbookLoader(runbook_dir=runbook_dir)
    books = loader.list_runbooks()
    assert "high_error_rate" in books
    assert "pod_crashloopbackoff" in books


def test_missing_dir():
    loader = RunbookLoader(runbook_dir="/nonexistent/path")
    assert loader.list_runbooks() == []
