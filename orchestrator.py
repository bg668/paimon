"""
Orchestrator: drives the Planner → Executor → Reviewer workflow loop.
"""
from __future__ import annotations

from datetime import datetime, timezone

from agent_runner import AgentRunner
from audit_log import AuditLog
from schemas import EventType, TaskNode, TaskStatus
from task_manager import TaskManager
from workspace_store import WorkspaceStore


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Orchestrator:
    def __init__(self, store: WorkspaceStore, retry_limit: int = 2) -> None:
        self._store = store
        self._retry_limit = retry_limit
        self._audit = AuditLog(store)
        self._manager = TaskManager(store)
        self._runner = AgentRunner(store, self._audit)

    def run_workflow(self, request: str) -> dict:
        self._audit.append_event(EventType.WORKFLOW_STARTED, payload={"request": request})

        # --- Planner ---
        planner_task = TaskNode(name=f"Plan: {request}", description=request, role="planner")
        self._manager.create_task(planner_task)
        planner_result = self._runner.run(planner_task)
        self._manager.mark_task_completed(planner_task.id)

        sub_tasks = planner_result.get("structured_output", {}).get("sub_tasks", [])
        if not sub_tasks:
            sub_tasks = [{"name": request, "description": request}]

        # --- Executor → Reviewer loop per sub-task ---
        for sub in sub_tasks:
            executor_task = TaskNode(
                name=sub.get("name", request),
                description=sub.get("description", ""),
                role="executor",
                parent_task_id=planner_task.id,
            )
            self._manager.create_task(executor_task)

            while True:
                # Run executor
                exec_result = self._runner.run(executor_task)
                if exec_result["status"] != "ok":
                    self._manager.mark_task_failed(executor_task.id, exec_result.get("summary", "executor failed"))
                    self._audit.append_event(EventType.WORKFLOW_FAILED, payload={"reason": "executor failed"})
                    return {"status": "workflow_failed"}

                # Persist artifact refs onto the task
                executor_task.output_refs = exec_result.get("artifact_refs", [])
                self._store.write_task(executor_task.model_dump())

                # Create reviewer task with the current executor retry_count
                reviewer_task = TaskNode(
                    name=f"Review: {executor_task.name}",
                    description=executor_task.description,
                    role="reviewer",
                    retry_count=executor_task.retry_count,
                    parent_task_id=executor_task.id,
                )
                self._manager.create_task(reviewer_task)
                review_result = self._runner.run(reviewer_task)

                if review_result.get("next_action") == "complete":
                    self._manager.mark_task_completed(reviewer_task.id)
                    self._manager.mark_task_completed(executor_task.id)
                    self._audit.append_event(EventType.REVIEW_PASSED, task_id=executor_task.id)
                    break
                else:
                    # Reviewer rejected
                    self._audit.append_event(EventType.REVIEW_REJECTED, task_id=executor_task.id)
                    self._manager.mark_task_failed(reviewer_task.id, "review rejected")

                    if executor_task.retry_count >= self._retry_limit:
                        self._manager.mark_task_failed(executor_task.id, "exceeded retry limit")
                        self._audit.append_event(EventType.WORKFLOW_FAILED, payload={"reason": "retry limit exceeded"})
                        return {"status": "workflow_failed"}

                    # Increment retry_count and reset executor status to pending
                    executor_task.retry_count += 1
                    executor_task.status = TaskStatus.PENDING
                    executor_task.updated_at = _utcnow_iso()
                    self._store.write_task(executor_task.model_dump())

        self._audit.append_event(EventType.WORKFLOW_COMPLETED, payload={"request": request})
        return {"status": "completed"}
