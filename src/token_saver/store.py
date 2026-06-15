from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .types import ModelResult, RouteDecision, Task, VerificationResult


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
    escalated INTEGER NOT NULL DEFAULT 0
)
"""


class RunStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def save(
        self,
        task: Task,
        decision: RouteDecision,
        result: ModelResult,
        verification: VerificationResult,
        escalated: bool,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO runs (
                    task_id, task_json, tier, score, provider, model, response,
                    verification_passed, verification_reason, escalated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                ),
            )

    def recent(self, limit: int = 20) -> list[dict[str, object]]:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]
