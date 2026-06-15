from __future__ import annotations

import subprocess
from pathlib import Path

from .types import ModelResult, Task, TaskType, VerificationResult


class Verifier:
    def verify(self, task: Task, result: ModelResult) -> VerificationResult:
        if result.text.lstrip().upper().startswith("ESCALATE:"):
            return VerificationResult(False, result.text.strip())
        if result.edit_error:
            return VerificationResult(False, result.edit_error)
        if task.task_type != TaskType.CODING or not task.verify_command:
            return VerificationResult(True, "no executable verification required")

        completed = subprocess.run(
            task.verify_command,
            cwd=Path(task.workspace or ".").resolve(),
            shell=True,
            text=True,
            capture_output=True,
            timeout=300,
        )
        output = (completed.stdout + "\n" + completed.stderr).strip()[-12000:]
        if completed.returncode == 0:
            return VerificationResult(True, "verification command passed", output)
        return VerificationResult(
            False,
            f"verification command exited with {completed.returncode}",
            output,
        )
