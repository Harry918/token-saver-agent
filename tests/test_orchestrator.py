from pathlib import Path

from token_saver.config import Settings
from token_saver.orchestrator import Orchestrator
from token_saver.types import ModelResult, Task, TaskType, Tier


class FakeProvider:
    def __init__(self, responses: list[str], name: str) -> None:
        self.responses = iter(responses)
        self.name = name
        self.calls = 0

    def run(self, task, messages, model):
        self.calls += 1
        return ModelResult(next(self.responses), self.name, model)


def settings(tmp_path: Path, attempts: int = 2) -> Settings:
    return Settings(
        db_path=tmp_path / "runs.sqlite3",
        headroom_enabled=False,
        max_local_attempts=attempts,
    )


def test_local_success_does_not_escalate(tmp_path: Path) -> None:
    local = FakeProvider(["Done"], "local")
    codex = FakeProvider(["Cloud done"], "codex")
    result = Orchestrator(settings(tmp_path), local, codex).run(Task("Summarize this"))
    assert result.decision.tier == Tier.LOCAL
    assert not result.escalated
    assert codex.calls == 0


def test_local_failure_retries_then_escalates(tmp_path: Path) -> None:
    local = FakeProvider(
        ["ESCALATE: missing context", "ESCALATE: still missing"], "local"
    )
    codex = FakeProvider(["Resolved"], "codex")
    result = Orchestrator(settings(tmp_path), local, codex).run(
        Task("Explain unfamiliar module")
    )
    assert result.escalated
    assert local.calls == 2
    assert codex.calls == 1
    assert result.result.text == "Resolved"


def test_local_coding_result_edits_workspace(tmp_path: Path) -> None:
    response = '{"summary":"created app","operations":[{"op":"write","path":"app.py","content":"print(42)\\n"}]}'
    local = FakeProvider([response], "local")
    codex = FakeProvider(["Cloud done"], "codex")
    task = Task("Create app.py", task_type=TaskType.CODING, workspace=str(tmp_path))

    result = Orchestrator(settings(tmp_path), local, codex).run(task)

    assert (tmp_path / "app.py").read_text() == "print(42)\n"
    assert result.result.changed_files == ["app.py"]
    assert result.result.text == "created app"
    assert not result.escalated
