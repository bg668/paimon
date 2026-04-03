"""
AgentRunner: dispatches tasks to role-specific mock agent behaviors.
"""
from __future__ import annotations

import re
from typing import Any

from audit_log import AuditLog
from schemas import EventType, TaskNode
from workspace_store import WorkspaceStore


class AgentRunner:
    def __init__(self, store: WorkspaceStore, audit: AuditLog) -> None:
        self._store = store
        self._audit = audit

    def run(self, task: TaskNode) -> dict[str, Any]:
        """Dispatch task by role and return a standardised result dict."""
        self._audit.append_event(EventType.AGENT_STARTED, task_id=task.id, payload={"role": task.role})

        if task.role == "planner":
            result = self._run_planner(task)
        elif task.role == "executor":
            result = self._run_executor(task)
        elif task.role == "reviewer":
            result = self._run_reviewer(task)
        else:
            result = {
                "status": "failed",
                "summary": f"Unknown role: {task.role}",
                "artifact_refs": [],
                "next_action": "reject",
                "structured_output": {},
            }

        self._audit.append_event(EventType.AGENT_COMPLETED, task_id=task.id, payload={"role": task.role, "status": result["status"]})
        return result

    # ------------------------------------------------------------------
    # Role implementations
    # ------------------------------------------------------------------

    def _run_planner(self, task: TaskNode) -> dict[str, Any]:
        sub_tasks = [
            {"name": f"Execute: {task.name}", "description": f"Execute the work for: {task.description}"},
            {"name": f"Verify: {task.name}", "description": f"Verify the output for: {task.description}"},
        ]
        return {
            "status": "ok",
            "summary": f"Planner created {len(sub_tasks)} sub-tasks for '{task.name}'",
            "artifact_refs": [],
            "next_action": "complete",
            "structured_output": {"sub_tasks": sub_tasks},
        }

    def _run_executor(self, task: TaskNode) -> dict[str, Any]:
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", task.name)[:40]
        artifact_name = f"{safe_name}_{task.id[:8]}"
        artifact_data = {
            "task_id": task.id,
            "task_name": task.name,
            "retry_count": task.retry_count,
            "output": f"Executed task '{task.name}' successfully (attempt {task.retry_count + 1})",
        }
        self._store.write_artifact(artifact_name, artifact_data)
        return {
            "status": "ok",
            "summary": f"Executor completed '{task.name}' on attempt {task.retry_count + 1}",
            "artifact_refs": [artifact_name],
            "next_action": "complete",
            "structured_output": {"artifact": artifact_name},
        }

    def _run_reviewer(self, task: TaskNode) -> dict[str, Any]:
        # Controllable reject-then-pass: reject on first attempt, pass on retry
        if task.retry_count == 0:
            return {
                "status": "ok",
                "summary": f"Reviewer rejected '{task.name}' on first pass",
                "artifact_refs": [],
                "next_action": "reject",
                "structured_output": {"reason": "First-pass review requires revision"},
            }
        return {
            "status": "ok",
            "summary": f"Reviewer approved '{task.name}' after retry",
            "artifact_refs": [],
            "next_action": "complete",
            "structured_output": {"approved": True},
        }
