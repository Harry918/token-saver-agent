from token_saver.router import PolicyRouter
from token_saver.types import Risk, Task, TaskType, Tier


def test_small_task_routes_local() -> None:
    decision = PolicyRouter().route(Task("Summarize this function"))
    assert decision.tier == Tier.LOCAL


def test_medium_task_still_routes_local_first() -> None:
    task = Task("Refactor parser", expected_files=3, subsystems=2)
    decision = PolicyRouter().route(task)
    assert decision.tier == Tier.LOCAL
    assert "local-first" in decision.reasons[-1]


def test_high_risk_task_routes_frontier() -> None:
    task = Task(
        "Change production authentication", risk=Risk.HIGH, task_type=TaskType.CODING
    )
    assert PolicyRouter().route(task).tier == Tier.CODEX
