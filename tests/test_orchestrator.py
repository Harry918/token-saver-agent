from pathlib import Path

from token_saver.config import Settings
from token_saver.orchestrator import Orchestrator
from token_saver.store import RunStore
from token_saver.types import ModelResult, Risk, RunUsage, Task, TaskType, Tier


class FakeProvider:
    def __init__(self, responses: list[str], name: str) -> None:
        self.responses = iter(responses)
        self.name = name
        self.calls = 0

    def run(self, task, messages, model):
        self.calls += 1
        return ModelResult(next(self.responses), self.name, model, 10, 5)


class FailingProvider:
    def run(self, task, messages, model):
        raise RuntimeError("local provider timed out")


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
    assert result.usage.local_work_percent == 100
    assert result.usage.codex_work_percent == 0
    assert result.usage.estimated_gpt_token_savings_percent == 100


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
    assert result.usage.local_calls == 2
    assert result.usage.codex_calls == 1
    assert result.usage.local_work_percent == 66.67
    assert result.usage.codex_work_percent == 33.33
    assert result.usage.estimated_gpt_token_savings_percent == 66.66666666666667


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


def test_local_provider_error_escalates_to_codex(tmp_path: Path) -> None:
    codex = FakeProvider(["Cloud fallback"], "codex")

    result = Orchestrator(settings(tmp_path), FailingProvider(), codex).run(
        Task("Create a small app")
    )

    assert result.escalated
    assert result.result.text == "Cloud fallback"
    assert codex.calls == 1


def test_high_risk_still_routes_directly_to_codex(tmp_path: Path) -> None:
    local = FakeProvider(["local should not run"], "local")
    codex = FakeProvider(["Cloud direct"], "codex")

    result = Orchestrator(settings(tmp_path), local, codex).run(
        Task("Change production authentication", risk=Risk.HIGH)
    )

    assert result.decision.tier == Tier.CODEX
    assert local.calls == 0
    assert codex.calls == 1
    assert result.usage.local_work_percent == 0
    assert result.usage.codex_work_percent == 100


def test_store_stats_aggregates_usage(tmp_path: Path) -> None:
    local = FakeProvider(["Done"], "local")
    codex = FakeProvider(["Cloud done"], "codex")
    Orchestrator(settings(tmp_path), local, codex).run(Task("Summarize this"))

    stats = RunStore(tmp_path / "runs.sqlite3").stats()

    assert stats["local_calls"] == 1
    assert stats["codex_calls"] == 0
    assert stats["estimated_gpt_token_savings_percent"] == 100


def test_savings_falls_back_to_call_share_when_codex_tokens_are_missing() -> None:
    usage = RunUsage(local_calls=2, codex_calls=1, local_tokens=700, codex_tokens=0)

    assert Orchestrator._estimate_savings(usage) == 66.66666666666667
