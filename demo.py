#!/usr/bin/env python3
"""
demo.py: showcases the Planner → Executor → Reviewer closed loop.

Run:
    python demo.py
"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import tempfile
from workspace_store import WorkspaceStore
from orchestrator import Orchestrator
from schemas import EventType

def main():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = WorkspaceStore(Path(tmpdir) / "demo_workspace")
        store.init()

        print("=== Mini-Agent Demo: Planner → Executor → Reviewer ===\n")
        orch = Orchestrator(store, retry_limit=2)
        result = orch.run_workflow("Write a technical design document for a caching layer")

        print(f"Workflow result: {result['status']}\n")

        print("--- Tasks created ---")
        for task in store.list_tasks():
            print(f"  [{task['status']:10}] role={task.get('role','?'):8}  name={task['name']}")

        print("\n--- Artifacts written ---")
        for artifact in sorted((store.root / "artifacts").glob("*.json")):
            print(f"  {artifact.name}")
            content = json.loads(artifact.read_text())
            print(f"    {json.dumps(content, indent=4)[:200]}")

        print("\n--- Audit log (event_log.jsonl) ---")
        for event in store.read_events():
            print(f"  [{event['timestamp'][:19]}] {event['event_type']:30} task_id={event.get('task_id','—')}")

if __name__ == "__main__":
    main()
