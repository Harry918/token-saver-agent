from __future__ import annotations

import io
import json
from contextlib import contextmanager

import pytest

from token_saver.config import Settings
from token_saver.providers import LlamaCppProvider
from token_saver.types import Task


@contextmanager
def fake_response(body: dict):
    yield io.BytesIO(json.dumps(body).encode("utf-8"))


def test_llama_provider_reads_message_content(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        return fake_response(
            {
                "choices": [{"message": {"content": "LOCAL_OK"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3},
            }
        )

    monkeypatch.setattr("token_saver.providers.urllib.request.urlopen", fake_urlopen)

    result = LlamaCppProvider(Settings()).run(
        Task("Say ok"), [{"role": "user", "content": "Say ok"}], "local-model"
    )

    assert result.text == "LOCAL_OK"
    assert result.input_tokens == 2
    assert result.output_tokens == 3


def test_llama_provider_explains_reasoning_only_response(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        return fake_response(
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "reasoning_content": "thinking without final content",
                        }
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3},
            }
        )

    monkeypatch.setattr("token_saver.providers.urllib.request.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="--reasoning off"):
        LlamaCppProvider(Settings()).run(
            Task("Say ok"), [{"role": "user", "content": "Say ok"}], "local-model"
        )
