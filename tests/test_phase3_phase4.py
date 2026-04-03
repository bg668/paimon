"""
TDD test suite for Phase 3 (Orchestrator + Mock Agents) and Phase 4 (ToolSandbox).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from workspace_store import WorkspaceStore
from orchestrator import Orchestrator
from tool_sandbox import tool, execute_tool
from schemas import EventType


@pytest.fixture()
def ws(tmp_path):
    store = WorkspaceStore(tmp_path / "workspace")
    store.init()
    return store


class TestFullWorkflowLoop:
    """test_full_workflow_loop: Happy Path from Planner to completion."""
    def test_full_workflow_loop(self, ws):
        orch = Orchestrator(ws, retry_limit=2)
        result = orch.run_workflow("Build a simple report")
        assert result["status"] == "completed"
        # Verify tasks were created
        tasks = ws.list_tasks()
        assert len(tasks) >= 2  # at least planner + 1 executor
        # Verify at least one artifact was written
        artifacts = list((ws.root / "artifacts").glob("*.json"))
        assert len(artifacts) >= 1


class TestReviewRetryMechanism:
    """test_review_retry_mechanism: Reviewer rejects once, then passes on retry."""
    def test_review_retry_mechanism(self, ws):
        orch = Orchestrator(ws, retry_limit=2)
        result = orch.run_workflow("Write a document")
        assert result["status"] == "completed"
        # Check that REVIEW_REJECTED event was logged (first review rejects)
        events = ws.read_events()
        event_types = [e["event_type"] for e in events]
        assert EventType.REVIEW_REJECTED.value in event_types
        assert EventType.REVIEW_PASSED.value in event_types

    def test_workflow_fail_after_retry_limit(self, ws):
        """If retry_limit=0, reviewer always rejects on first try → workflow_failed."""
        orch = Orchestrator(ws, retry_limit=0)
        result = orch.run_workflow("Impossible task")
        assert result["status"] == "workflow_failed"
        events = ws.read_events()
        event_types = [e["event_type"] for e in events]
        assert EventType.WORKFLOW_FAILED.value in event_types


class TestToolExceptionSafety:
    """test_tool_exception_safety: Tool exceptions don't crash the main flow."""
    def test_tool_exception_safety(self):
        @tool
        def broken_tool(x):
            raise ValueError("intentional boom")

        result = execute_tool("broken_tool", {"x": 1})
        assert result["status"] == "failed"
        assert "intentional boom" in result["error"]
        assert result["output"] is None

    def test_tool_success(self):
        @tool
        def add(a, b):
            return a + b

        result = execute_tool("add", {"a": 2, "b": 3})
        assert result["status"] == "ok"
        assert result["output"] == 5


class TestEventLogIntegrity:
    """test_event_log_integrity: event_log.jsonl captures all key events."""
    def test_event_log_integrity(self, ws):
        orch = Orchestrator(ws, retry_limit=2)
        orch.run_workflow("Full trace test")
        events = ws.read_events()
        event_types = [e["event_type"] for e in events]
        # Must include all key milestones
        assert EventType.WORKFLOW_STARTED.value in event_types
        assert EventType.TASK_CREATED.value in event_types
        assert EventType.TASK_COMPLETED.value in event_types
        assert EventType.WORKFLOW_COMPLETED.value in event_types
        # All events must have id and timestamp
        for e in events:
            assert "id" in e
            assert "timestamp" in e
            assert "event_type" in e
