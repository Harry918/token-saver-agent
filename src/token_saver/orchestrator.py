from __future__ import annotations

from .compression import ContextCompressor
from .config import Settings
from .editor import WorkspaceEditor
from .prompts import build_escalation_messages, build_messages
from .providers import CodexProvider, LlamaCppProvider, Provider
from .router import PolicyRouter
from .store import RunStore
from .types import (
    ModelResult,
    RunResult,
    RunUsage,
    Task,
    TaskType,
    Tier,
    VerificationResult,
)
from .verifier import Verifier


class Orchestrator:
    def __init__(
        self,
        settings: Settings | None = None,
        local_provider: Provider | None = None,
        codex_provider: Provider | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self.router = PolicyRouter()
        self.compressor = ContextCompressor(self.settings.headroom_enabled)
        self.local = local_provider or LlamaCppProvider(self.settings)
        self.codex = codex_provider or CodexProvider()
        self.editor = WorkspaceEditor()
        self.verifier = Verifier()
        self.store = RunStore(self.settings.db_path)

    def run(self, task: Task) -> RunResult:
        task.workspace = str(self.settings.validate_workspace(task.workspace))
        decision = self.router.route(task)
        escalated = False
        usage = RunUsage()

        if decision.tier == Tier.LOCAL:
            result, verification = self._run_local(task, usage)

            attempts = 1
            while (
                not verification.passed
                and attempts < self.settings.max_local_attempts
                and result.provider != "local-error"
            ):
                messages = build_messages(
                    task, verification.reason + "\n" + verification.output
                )
                compressed = self.compressor.compress(
                    messages, self.settings.local_model
                )
                try:
                    result = self.local.run(
                        task, compressed.messages, self.settings.local_model
                    )
                    self._record_usage(usage, result, is_codex=False)
                    self._apply_local_edits(task, result)
                    verification = self.verifier.verify(task, result)
                except RuntimeError as exc:
                    result = ModelResult(
                        text="",
                        provider="local-error",
                        model=self.settings.local_model,
                    )
                    verification = VerificationResult(False, str(exc))
                attempts += 1

            if not verification.passed:
                escalated = True
                escalation = build_escalation_messages(
                    task, result.text, verification.reason
                )
                compressed = self.compressor.compress(
                    escalation, self.settings.codex_model
                )
                result = self.codex.run(
                    task, compressed.messages, self.settings.codex_model
                )
                self._record_usage(usage, result, is_codex=True)
                verification = self.verifier.verify(task, result)
        else:
            model = (
                self.settings.codex_mini_model
                if decision.tier == Tier.CODEX_MINI
                else self.settings.codex_model
            )
            messages = build_messages(task)
            compressed = self.compressor.compress(messages, model)
            result = self.codex.run(task, compressed.messages, model)
            self._record_usage(usage, result, is_codex=True)
            verification = self.verifier.verify(task, result)

        usage.estimated_gpt_token_savings_percent = self._estimate_savings(usage)
        self.store.save(task, decision, result, verification, escalated, usage)
        return RunResult(task.task_id, decision, result, verification, escalated, usage)

    def _run_local(
        self, task: Task, usage: RunUsage
    ) -> tuple[ModelResult, VerificationResult]:
        messages = build_messages(task)
        compressed = self.compressor.compress(messages, self.settings.local_model)
        try:
            result = self.local.run(
                task, compressed.messages, self.settings.local_model
            )
            self._record_usage(usage, result, is_codex=False)
        except RuntimeError as exc:
            result = ModelResult(
                text="", provider="local-error", model=self.settings.local_model
            )
            return result, VerificationResult(False, str(exc))
        self._apply_local_edits(task, result)
        return result, self.verifier.verify(task, result)

    def _apply_local_edits(self, task: Task, result) -> None:
        if task.task_type != TaskType.CODING or result.text.lstrip().upper().startswith(
            "ESCALATE:"
        ):
            return
        edit = self.editor.apply(task.workspace, result.text)
        result.changed_files = edit.changed_files
        result.edit_error = edit.error
        if not edit.error:
            result.text = edit.summary

    @staticmethod
    def _record_usage(usage: RunUsage, result: ModelResult, is_codex: bool) -> None:
        tokens = (result.input_tokens or 0) + (result.output_tokens or 0)
        if is_codex:
            usage.codex_calls += 1
            usage.codex_tokens += tokens
        else:
            usage.local_calls += 1
            usage.local_tokens += tokens

    @staticmethod
    def _estimate_savings(usage: RunUsage) -> float:
        total_calls = usage.local_calls + usage.codex_calls
        if total_calls == 0:
            return 0.0
        if usage.codex_calls == 0:
            return 100.0
        if usage.local_calls == 0:
            return 0.0
        total_tokens = usage.local_tokens + usage.codex_tokens
        local_has_tokens = usage.local_tokens > 0
        codex_has_tokens = usage.codex_tokens > 0
        if total_tokens > 0 and local_has_tokens and codex_has_tokens:
            return 100 * usage.local_tokens / total_tokens
        return 100 * usage.local_calls / total_calls
