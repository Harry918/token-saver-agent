from pathlib import Path

import pytest

from token_saver.config import Settings
from token_saver.telegram import TelegramBot, TelegramSettings, _load_token
from token_saver.types import (
    ModelResult,
    RouteDecision,
    RunResult,
    Tier,
    VerificationResult,
)


class FakeClient:
    def __init__(self) -> None:
        self.messages = []
        self.typing = []

    def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))

    def send_typing(self, chat_id):
        self.typing.append(chat_id)


class FakeOrchestrator:
    def __init__(self) -> None:
        self.tasks = []

    def run(self, task):
        self.tasks.append(task)
        return RunResult(
            task.task_id,
            RouteDecision(Tier.LOCAL, 2, []),
            ModelResult("done", "llama.cpp", "local", changed_files=["app.py"]),
            VerificationResult(True, "passed"),
            False,
        )


def telegram_settings(tmp_path: Path) -> TelegramSettings:
    return TelegramSettings(
        allowed_user_ids=frozenset({42}),
        projects={"demo": tmp_path},
        verify_commands={"demo": "python -m pytest -q"},
    )


def update(user_id=42, text="/projects", chat_type="private"):
    return {
        "update_id": 1,
        "message": {
            "text": text,
            "chat": {"id": 99, "type": chat_type},
            "from": {"id": user_id},
        },
    }


def test_denies_unlisted_users_but_allows_whoami(tmp_path: Path) -> None:
    client = FakeClient()
    bot = TelegramBot(
        client, Settings(), telegram_settings(tmp_path), FakeOrchestrator()
    )
    bot.handle_update(update(user_id=7, text="/whoami"))
    bot.handle_update(update(user_id=7, text="/ask hello"))
    assert "user ID is 7" in client.messages[0][1]
    assert "Access denied" in client.messages[1][1]


def test_rejects_group_chats(tmp_path: Path) -> None:
    client = FakeClient()
    bot = TelegramBot(
        client, Settings(), telegram_settings(tmp_path), FakeOrchestrator()
    )
    bot.handle_update(update(text="/ask hello", chat_type="group"))
    assert "private chats" in client.messages[0][1]


def test_code_uses_only_configured_project_and_verifier(tmp_path: Path) -> None:
    client = FakeClient()
    orchestrator = FakeOrchestrator()
    bot = TelegramBot(client, Settings(), telegram_settings(tmp_path), orchestrator)
    bot.handle_update(update(text="/code fix the app"))
    task = orchestrator.tasks[0]
    assert task.workspace == str(tmp_path)
    assert task.verify_command == "python -m pytest -q"
    assert "Changed files: app.py" in client.messages[-1][1]


def test_unknown_project_alias_is_rejected(tmp_path: Path) -> None:
    bot = TelegramBot(
        FakeClient(), Settings(), telegram_settings(tmp_path), FakeOrchestrator()
    )
    assert "Unknown project" in bot.handle_command(42, 99, "/use missing")


def test_telegram_settings_reject_unknown_verifier(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "token_saver.telegram.load_config",
        lambda: {
            "telegram": {
                "allowed_user_ids": [42],
                "projects": {"demo": str(tmp_path)},
                "verify_commands": {"other": "pytest"},
            }
        },
    )
    with pytest.raises(ValueError, match="unknown projects"):
        TelegramSettings.from_config(Settings(workspace_roots=(tmp_path,)))


def test_load_token_from_private_file(monkeypatch, tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_FILE", str(token_file))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert _load_token() == "secret-token"


def test_rejects_public_token_file(monkeypatch, tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("secret-token", encoding="utf-8")
    token_file.chmod(0o644)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_FILE", str(token_file))
    with pytest.raises(RuntimeError, match="group or other users"):
        _load_token()
