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
        message = body["choices"][0]["message"]
        content = message.get("content") or ""
        if not content and message.get("reasoning_content"):
            raise RuntimeError(
                "llama.cpp returned only reasoning_content. Restart llama-server with "
                "`--reasoning off` so Token Saver receives normal message.content."
            )
        usage = body.get("usage", {})
        return ModelResult(
            text=content,
            provider="llama.cpp",
            model=model,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )


def check_llama_cpp(settings: Settings) -> dict[str, object]:
    base_url = settings.local_url.rstrip("/")
    health_url = base_url.removesuffix("/v1") + "/health"
    result: dict[str, object] = {
        "local_url": settings.local_url,
        "health_url": health_url,
        "ok": False,
    }
    try:
        with urllib.request.urlopen(
            urllib.request.Request(health_url), timeout=10
        ) as response:
            result["health_status"] = response.status
            result["health_body"] = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        result["error"] = f"llama.cpp health check failed: {exc}"
        return result

    try:
        probe = LlamaCppProvider(settings).run(
            Task("Reply with exactly LOCAL_OK"),
            [{"role": "user", "content": "Reply with exactly LOCAL_OK"}],
            settings.local_model,
        )
    except RuntimeError as exc:
        result["error"] = str(exc)
        return result

    result.update(
        {
            "ok": probe.text.strip() == "LOCAL_OK",
            "probe_text": probe.text.strip(),
            "model": probe.model,
            "input_tokens": probe.input_tokens,
            "output_tokens": probe.output_tokens,
        }
    )
    if not result["ok"]:
        result["error"] = "local model probe did not return LOCAL_OK"
    return result


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
