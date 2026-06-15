from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol

from .config import Settings
from .types import ModelResult, Task


class Provider(Protocol):
    def run(
        self, task: Task, messages: list[dict[str, str]], model: str
    ) -> ModelResult: ...


class LlamaCppProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run(
        self, task: Task, messages: list[dict[str, str]], model: str
    ) -> ModelResult:
        url = f"{self.settings.local_url.rstrip('/')}/chat/completions"
        payload = json.dumps(
            {
                "model": model,
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": 4096,
            }
        ).encode()
        request = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer local",
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.settings.request_timeout
            ) as response:
                body = json.load(response)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(
                f"Local llama.cpp server unavailable at {url}: {exc}"
            ) from exc
        usage = body.get("usage", {})
        return ModelResult(
            text=body["choices"][0]["message"]["content"],
            provider="llama.cpp",
            model=model,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )


class CodexProvider:
    def run(
        self, task: Task, messages: list[dict[str, str]], model: str
    ) -> ModelResult:
        try:
            from openai_codex import Codex, Sandbox
        except ImportError as exc:
            raise RuntimeError(
                "Codex escalation requires the optional dependency: pip install -e '.[codex]'"
            ) from exc

        prompt = messages[-1]["content"]
        workspace = Path(task.workspace or ".").resolve()
        with Codex() as codex:
            thread = codex.thread_start(
                model=model,
                sandbox=Sandbox.workspace_write,
                cwd=str(workspace),
            )
            result = thread.run(prompt)
        return ModelResult(text=result.final_response, provider="codex", model=model)
