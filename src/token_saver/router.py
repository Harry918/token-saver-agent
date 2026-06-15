from __future__ import annotations

from .types import Risk, RouteDecision, Task, TaskType, Tier


HIGH_RISK_TERMS = {
    "authentication",
    "authorization",
    "cryptography",
    "production",
    "migration",
    "delete data",
    "security review",
    "payment",
}


class PolicyRouter:
    def route(self, task: Task) -> RouteDecision:
        objective = task.objective.lower()
        forced_reasons: list[str] = []
        if task.risk == Risk.HIGH:
            forced_reasons.append("task explicitly marked high risk")
        matches = sorted(term for term in HIGH_RISK_TERMS if term in objective)
        if matches:
            forced_reasons.append(f"high-risk domain: {', '.join(matches)}")
        if task.task_type == TaskType.RESEARCH:
            forced_reasons.append("research may require current, sourced information")
        if forced_reasons:
            return RouteDecision(Tier.CODEX, 99, forced_reasons)

        score = (
            2 * max(1, task.subsystems)
            + 2 * task.expected_files
            + task.ambiguity
            + task.tool_depth
            + task.domain_novelty
            + (3 if task.risk == Risk.MEDIUM else 0)
        )
        reasons = [
            f"subsystems={task.subsystems}",
            f"expected_files={task.expected_files}",
            f"risk={task.risk}",
        ]
        tier = Tier.LOCAL
        reasons.append("local-first; Codex only after verification failure")
        return RouteDecision(tier, score, reasons)
