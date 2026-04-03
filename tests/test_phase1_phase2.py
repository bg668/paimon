"""
TDD test suite for Phase 1 (storage) and Phase 2 (task management).

Covers:
  test_workspace_atomic_write       — verifies write integrity and no .tmp residue
  test_task_dependency_unblock      — A completed → B's blocked_by cleared
  test_task_priority_queue          — correct selection among multiple pending tasks
  test_schema_validation            — invalid fields raise ValidationError
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make sure the parent package is importable regardless of how pytest is invoked.
sys.path.insert(0, str(Path(__file__).parent.parent))

from pydantic import ValidationError

from schemas import Event, EventType, State, TaskNode, TaskStatus, ToolResult
from task_manager import TaskManager
from workspace_store import WorkspaceStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ws(tmp_path: Path) -> WorkspaceStore:
    store = WorkspaceStore(tmp_path / "workspace")
    store.init()
    return store


@pytest.fixture()
def tm(ws: WorkspaceStore) -> TaskManager:
    return TaskManager(ws)


# ---------------------------------------------------------------------------
# test_workspace_atomic_write
# ---------------------------------------------------------------------------


class TestWorkspaceAtomicWrite:
    def test_init_creates_required_dirs(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        store = WorkspaceStore(root)
        store.init()
        assert (root / "tasks").is_dir()
        assert (root / "artifacts").is_dir()
        assert (root / "meta.json").is_file()

    def test_atomic_write_content_integrity(self, ws: WorkspaceStore) -> None:
        target = ws.root / "test_output.json"
        payload = {"key": "value", "nested": {"a": 1}}
        ws._atomic_write_json(target, payload)
        assert json.loads(target.read_text()) == payload

    def test_no_tmp_file_residue(self, ws: WorkspaceStore) -> None:
        target = ws.root / "clean.json"
        ws._atomic_write_json(target, {"x": 42})
        assert not target.with_suffix(".tmp").exists()

    def test_overwrite_is_atomic(self, ws: WorkspaceStore) -> None:
        target = ws.root / "overwrite.json"
        ws._atomic_write_json(target, {"v": 1})
        ws._atomic_write_json(target, {"v": 2})
        assert json.loads(target.read_text())["v"] == 2

    def test_event_log_append_only(self, ws: WorkspaceStore) -> None:
        e1 = Event(event_type=EventType.TASK_CREATED, task_id="t1")
        e2 = Event(event_type=EventType.TASK_COMPLETED, task_id="t1")
        ws.append_event(e1)
        ws.append_event(e2)
        events = ws.read_events()
        assert len(events) == 2
        assert events[0]["task_id"] == "t1"
        assert events[0]["event_type"] == EventType.TASK_CREATED.value
        assert events[1]["event_type"] == EventType.TASK_COMPLETED.value

    def test_write_and_read_task(self, ws: WorkspaceStore) -> None:
        task = TaskNode(name="demo")
        ws.write_task(task.model_dump())
        result = ws.read_task(task.id)
        assert result["name"] == "demo"
        assert result["id"] == task.id


# ---------------------------------------------------------------------------
# test_task_dependency_unblock
# ---------------------------------------------------------------------------


class TestTaskDependencyUnblock:
    def test_blocking_prevents_scheduling(self, tm: TaskManager) -> None:
        a = TaskNode(name="A", priority=1)
        b = TaskNode(name="B", priority=1, blocked_by=[a.id])
        tm.create_task(a)
        tm.create_task(b)

        nxt = tm.get_next_runnable_task()
        assert nxt is not None
        assert nxt.id == a.id, "B should not be runnable while blocked by A"

    def test_complete_a_unblocks_b(self, tm: TaskManager) -> None:
        a = TaskNode(name="A", priority=1)
        b = TaskNode(name="B", priority=1, blocked_by=[a.id])
        tm.create_task(a)
        tm.create_task(b)

        tm.mark_task_completed(a.id)

        # Re-read B from disk to verify persistence
        b_raw = tm._store.read_task(b.id)
        assert b_raw["blocked_by"] == [], "B's blocked_by must be empty after A completes"
        assert b_raw["status"] == TaskStatus.PENDING.value

        nxt = tm.get_next_runnable_task()
        assert nxt is not None
        assert nxt.id == b.id

    def test_dependency_unblocked_event_emitted(self, tm: TaskManager) -> None:
        a = TaskNode(name="A")
        b = TaskNode(name="B", blocked_by=[a.id])
        tm.create_task(a)
        tm.create_task(b)
        tm.mark_task_completed(a.id)

        events = tm._store.read_events()
        types = [e["event_type"] for e in events]
        assert EventType.DEPENDENCY_UNBLOCKED.value in types

    def test_multiple_dependencies_partial_unblock(self, tm: TaskManager) -> None:
        a = TaskNode(name="A")
        b = TaskNode(name="B")
        c = TaskNode(name="C", blocked_by=[a.id, b.id])
        tm.create_task(a)
        tm.create_task(b)
        tm.create_task(c)

        tm.mark_task_completed(a.id)

        c_raw = tm._store.read_task(c.id)
        assert c_raw["blocked_by"] == [b.id], "only A should be removed"

        nxt = tm.get_next_runnable_task()
        assert nxt is not None
        assert nxt.id != c.id, "C still has B as blocker"


# ---------------------------------------------------------------------------
# test_task_priority_queue
# ---------------------------------------------------------------------------


class TestTaskPriorityQueue:
    def test_lower_priority_number_wins(self, tm: TaskManager) -> None:
        low = TaskNode(name="low_priority", priority=5)
        high = TaskNode(name="high_priority", priority=1)
        tm.create_task(low)
        tm.create_task(high)

        nxt = tm.get_next_runnable_task()
        assert nxt is not None
        assert nxt.id == high.id

    def test_same_priority_oldest_wins(self, tm: TaskManager) -> None:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        older = TaskNode(
            name="older",
            priority=3,
            created_at=(now - timedelta(seconds=60)).isoformat(),
        )
        newer = TaskNode(
            name="newer",
            priority=3,
            created_at=now.isoformat(),
        )
        tm.create_task(newer)
        tm.create_task(older)

        nxt = tm.get_next_runnable_task()
        assert nxt is not None
        assert nxt.id == older.id

    def test_blocked_tasks_excluded(self, tm: TaskManager) -> None:
        dep = TaskNode(name="dep", priority=99)
        blocker = TaskNode(name="blocker", priority=1)
        blocked = TaskNode(name="blocked", priority=0, blocked_by=[blocker.id])
        tm.create_task(dep)
        tm.create_task(blocker)
        tm.create_task(blocked)

        nxt = tm.get_next_runnable_task()
        assert nxt is not None
        # 'blocked' has priority=0 but must not be selected; 'blocker' has priority=1
        assert nxt.id == blocker.id

    def test_empty_queue_returns_none(self, tm: TaskManager) -> None:
        assert tm.get_next_runnable_task() is None

    def test_completed_tasks_excluded(self, tm: TaskManager) -> None:
        t = TaskNode(name="T", priority=1)
        tm.create_task(t)
        tm.mark_task_completed(t.id)
        assert tm.get_next_runnable_task() is None


# ---------------------------------------------------------------------------
# test_schema_validation
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    def test_task_invalid_priority_type(self) -> None:
        with pytest.raises(ValidationError):
            TaskNode(name="bad", priority="not_an_int")  # type: ignore[arg-type]

    def test_task_invalid_status(self) -> None:
        with pytest.raises(ValidationError):
            TaskNode(name="bad", status="unknown_status")  # type: ignore[arg-type]

    def test_task_missing_required_name(self) -> None:
        with pytest.raises(ValidationError):
            TaskNode()  # type: ignore[call-arg]

    def test_task_invalid_iso8601_timestamp(self) -> None:
        with pytest.raises(ValidationError):
            TaskNode(name="bad", created_at="not-a-date")

    def test_event_invalid_event_type(self) -> None:
        with pytest.raises(ValidationError):
            Event(event_type="bad_type")  # type: ignore[arg-type]

    def test_tool_result_requires_fields(self) -> None:
        with pytest.raises(ValidationError):
            ToolResult()  # type: ignore[call-arg]

    def test_state_invalid_timestamp(self) -> None:
        with pytest.raises(ValidationError):
            State(created_at="January 1 2026")

    def test_task_negative_priority_invalid(self) -> None:
        with pytest.raises(ValidationError):
            TaskNode(name="bad", priority=-1)

    def test_valid_task_roundtrip(self) -> None:
        t = TaskNode(name="ok", priority=0, blocked_by=[])
        restored = TaskNode.model_validate_json(t.model_dump_json())
        assert restored.id == t.id
        assert restored.status == TaskStatus.PENDING
