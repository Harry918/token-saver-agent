from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class CompressionResult:
    messages: list[dict[str, str]]
    tokens_saved: int = 0
    backend: str = "none"


class ContextCompressor:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def compress(self, messages: list[dict[str, str]], model: str) -> CompressionResult:
        if not self.enabled:
            return CompressionResult(messages=messages)
        try:
            from headroom import compress
        except ImportError:
            return CompressionResult(messages=messages, backend="headroom-unavailable")

        result: Any = compress(messages, model=model)
        compressed = [dict(message) for message in result.messages]
        return CompressionResult(
            messages=compressed,
            tokens_saved=int(getattr(result, "tokens_saved", 0)),
            backend="headroom",
        )
