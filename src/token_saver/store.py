from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .types import ModelResult, RouteDecision, RunUsage, Task, VerificationResult


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    task_json TEXT NOT NULL,
    tier TEXT NOT NULL,
    score INTEGER NOT NULL,
    provider TEXT,
    model TEXT,
    response TEXT,
    verification_passed INTEGER,
    verification_reason TEXT,
    escalated INTEGER NOT NULL DEFAULT 0,
    usage_json TEXT NOT NULL DEFAULT '{}'
)
"""


class RunStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(SCHEMA)
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(runs)").fetchall()
            }
            if "usage_json" not in columns:
                connection.execute(
                    "ALTER TABLE runs ADD COLUMN usage_json TEXT NOT NULL DEFAULT '{}'"
                )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def save(
        self,
        task: Task,
        decision: RouteDecision,
        result: ModelResult,
        verification: VerificationResult,
        escalated: bool,
        usage: RunUsage | None = None,
    ) -> None:
        usage_json = json.dumps((usage or RunUsage()).to_dict())
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO runs (
                    task_id, task_json, tier, score, provider, model, response,
                    verification_passed, verification_reason, escalated, usage_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task.task_id,
                    json.dumps(task.to_dict()),
                    decision.tier,
                    decision.score,
                    result.provider,
                    result.model,
                    result.text,
                    int(verification.passed),
                    verification.reason,
                    int(escalated),
                    usage_json,
                ),
            )

    def recent(self, limit: int = 20) -> list[dict[str, object]]:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def stats(self, limit: int = 100) -> dict[str, int | float]:
        runs = self.recent(limit)
        local_calls = 0
        codex_calls = 0
        local_tokens = 0
        codex_tokens = 0
        for run in runs:
            try:
                usage = json.loads(str(run.get("usage_json") or "{}"))
            except json.JSONDecodeError:
                usage = {}
            local_calls += int(usage.get("local_calls") or 0)
            codex_calls += int(usage.get("codex_calls") or 0)
            local_tokens += int(usage.get("local_tokens") or 0)
            codex_tokens += int(usage.get("codex_tokens") or 0)
        total_calls = local_calls + codex_calls
        total_tokens = local_tokens + codex_tokens
        savings = (
            100 * local_tokens / total_tokens
            if total_tokens
            else (100 * local_calls / total_calls if total_calls else 0.0)
        )
        return {
            "runs": len(runs),
            "local_calls": local_calls,
            "codex_calls": codex_calls,
            "local_tokens": local_tokens,
            "codex_tokens": codex_tokens,
            "local_work_percent": round(
                100 * local_calls / total_calls if total_calls else 0.0, 2
            ),
            "codex_work_percent": round(
                100 * codex_calls / total_calls if total_calls else 0.0, 2
            ),
            "estimated_gpt_token_savings_percent": round(savings, 2),
        }
