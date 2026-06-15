from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4


class TaskType(StrEnum):
    CODING = "coding"
    GENERAL = "general"
    RESEARCH = "research"
    OPERATIONS = "operations"


class Risk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Tier(StrEnum):
    LOCAL = "local"
    CODEX_MINI = "codex-mini"
    CODEX = "codex"


@dataclass(slots=True)
class Task:
    objective: str
    task_type: TaskType = TaskType.GENERAL
    risk: Risk = Risk.LOW
    workspace: str | None = None
    constraints: list[str] = field(default_factory=list)
    context: list[dict[str, str]] = field(default_factory=list)
    verify_command: str | None = None
    expected_files: int = 0
    subsystems: int = 1
    ambiguity: int = 0
    tool_depth: int = 0
    domain_novelty: int = 0
    task_id: str = field(default_factory=lambda: str(uuid4()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RouteDecision:
    tier: Tier
    score: int
    reasons: list[str]


@dataclass(slots=True)
class ModelResult:
    text: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    changed_files: list[str] = field(default_factory=list)
    edit_error: str | None = None


@dataclass(slots=True)
class VerificationResult:
    passed: bool
    reason: str
    output: str = ""


@dataclass(slots=True)
class RunResult:
    task_id: str
    decision: RouteDecision
    result: ModelResult
    verification: VerificationResult
    escalated: bool = False
