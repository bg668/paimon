"""
Workspace storage layer with atomic writes and append-only event log (Phase 1).

Directory layout created by WorkspaceStore.init():
    <root>/
        meta.json          — workspace State snapshot
        tasks/             — one <task_id>.json per TaskNode
        artifacts/         — free-form output blobs
        event_log.jsonl    — append-only event stream
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from schemas import Event, State


class WorkspaceStore:
    """Manages the on-disk workspace for one agent run."""

    _SUBDIRS = ("tasks", "artifacts")

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._tasks_dir = self.root / "tasks"
        self._artifacts_dir = self.root / "artifacts"
        self._meta_path = self.root / "meta.json"
        self._event_log_path = self.root / "event_log.jsonl"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def init(self, workspace_id: str | None = None) -> State:
        """Create directory structure and write initial meta.json."""
        self.root.mkdir(parents=True, exist_ok=True)
        for sub in self._SUBDIRS:
            (self.root / sub).mkdir(exist_ok=True)

        if self._meta_path.exists():
            state = State.model_validate_json(self._meta_path.read_text())
        else:
            state = State(workspace_id=workspace_id) if workspace_id else State()
            self._atomic_write_json(self._meta_path, state.model_dump())

        return state

    # ------------------------------------------------------------------
    # Atomic JSON write
    # ------------------------------------------------------------------

    def _atomic_write_json(self, target: Path, data: Any) -> None:
        """Write *data* to *target* atomically via .tmp + fsync + os.replace."""
        tmp = target.with_suffix(".tmp")
        encoded = json.dumps(data, ensure_ascii=False, indent=2)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(encoded)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)

    # ------------------------------------------------------------------
    # Task persistence
    # ------------------------------------------------------------------

    def write_task(self, task_data: dict[str, Any]) -> None:
        """Atomically persist a task dict to tasks/<id>.json."""
        task_id = task_data["id"]
        target = self._tasks_dir / f"{task_id}.json"
        self._atomic_write_json(target, task_data)

    def read_task(self, task_id: str) -> dict[str, Any]:
        """Read and return a task dict from tasks/<id>.json."""
        path = self._tasks_dir / f"{task_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def list_tasks(self) -> list[dict[str, Any]]:
        """Return all task dicts found in the tasks/ directory."""
        tasks = []
        for p in self._tasks_dir.glob("*.json"):
            tasks.append(json.loads(p.read_text(encoding="utf-8")))
        return tasks

    # ------------------------------------------------------------------
    # Event log (append-only)
    # ------------------------------------------------------------------

    def append_event(self, event: Event) -> None:
        """Append a single Event as a JSON line to event_log.jsonl."""
        line = event.model_dump_json() + "\n"
        with open(self._event_log_path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

    def read_events(self) -> list[dict[str, Any]]:
        """Read all events from the log; returns empty list if log absent."""
        if not self._event_log_path.exists():
            return []
        events: list[dict[str, Any]] = []
        with open(self._event_log_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    # ------------------------------------------------------------------
    # Artifact helpers
    # ------------------------------------------------------------------

    def write_artifact(self, name: str, data: Any) -> None:
        """Atomically write an artifact JSON file to artifacts/<name>.json."""
        target = self._artifacts_dir / f"{name}.json"
        self._atomic_write_json(target, data)

    def read_artifact(self, name: str) -> Any:
        """Read and return an artifact from artifacts/<name>.json."""
        path = self._artifacts_dir / f"{name}.json"
        return json.loads(path.read_text(encoding="utf-8"))
