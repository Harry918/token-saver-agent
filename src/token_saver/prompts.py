from __future__ import annotations

import json

from .types import Task
from .workspace import collect_workspace


SYSTEM_PROMPT = """You are a bounded local-first agent. Follow the objective and constraints exactly.
Never claim a command passed unless its output is supplied. If essential context is missing or the task
is unsafe, respond with ESCALATE: followed by a concise reason.

For coding tasks, return ONLY one JSON object with this exact shape:
{"summary":"short description","operations":[{"op":"write","path":"relative/path","content":"complete file content"}]}
Allowed operations are write and delete. Paths must be relative to the workspace. Use write for both new
and existing files, always providing the complete resulting file. Make the smallest defensible change.
Do not use Markdown fences or include commentary outside the JSON object."""


def build_messages(
    task: Task, prior_failure: str | None = None
) -> list[dict[str, str]]:
    payload = {
        "objective": task.objective,
        "type": task.task_type,
        "constraints": task.constraints,
        "workspace": task.workspace,
        "context": task.context,
        "verification_command": task.verify_command,
    }
    if str(task.task_type) == "coding":
        payload["workspace_snapshot"] = collect_workspace(task.workspace)
    if prior_failure:
        payload["prior_failure"] = prior_failure
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, indent=2)},
    ]


def build_escalation_messages(
    task: Task, local_result: str, failure: str
) -> list[dict[str, str]]:
    package = {
        "objective": task.objective,
        "task_type": task.task_type,
        "workspace": task.workspace,
        "constraints": task.constraints,
        "context": task.context,
        "local_attempt": local_result,
        "verification_failure": failure,
        "verification_command": task.verify_command,
        "instruction": "Complete the task, verify it, and report the concrete outcome.",
    }
    return [
        {
            "role": "system",
            "content": "You are the escalation worker for a local-first agent system.",
        },
        {"role": "user", "content": json.dumps(package, indent=2)},
    ]
