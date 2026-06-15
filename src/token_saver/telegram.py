from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings, load_config
from .deploy import BackendDeployer, BackendSettings, build_backend_task
from .orchestrator import Orchestrator
from .types import Risk, Task, TaskType


@dataclass(frozen=True, slots=True)
class TelegramSettings:
    allowed_user_ids: frozenset[int]
    projects: dict[str, Path]
    verify_commands: dict[str, str]
    poll_timeout: int = 30

    @classmethod
    def from_config(cls, settings: Settings) -> "TelegramSettings":
        section = load_config().get("telegram", {})
        if not isinstance(section, dict):
            raise ValueError("telegram configuration must be a TOML table")
        raw_users = section.get("allowed_user_ids", [])
        raw_projects = section.get("projects", {})
        raw_verify = section.get("verify_commands", {})
        if not isinstance(raw_users, list) or not all(
            isinstance(user_id, int) for user_id in raw_users
        ):
            raise ValueError("telegram.allowed_user_ids must be an array of integers")
        if not isinstance(raw_projects, dict) or not all(
            isinstance(alias, str) and isinstance(path, str)
            for alias, path in raw_projects.items()
        ):
            raise ValueError("telegram.projects must map aliases to paths")
        if not isinstance(raw_verify, dict) or not all(
            isinstance(alias, str) and isinstance(command, str)
            for alias, command in raw_verify.items()
        ):
            raise ValueError("telegram.verify_commands must map aliases to commands")

        projects = {
            alias: settings.validate_workspace(path)
            for alias, path in raw_projects.items()
        }
        if not projects:
            raise ValueError(
                "telegram.projects must contain at least one project alias"
            )
        unknown = set(raw_verify) - set(projects)
        if unknown:
            raise ValueError(
                f"telegram.verify_commands contains unknown projects: {', '.join(sorted(unknown))}"
            )
        return cls(
            allowed_user_ids=frozenset(raw_users),
            projects=projects,
            verify_commands=dict(raw_verify),
            poll_timeout=int(section.get("poll_timeout", 30)),
        )


class TelegramClient:
    def __init__(self, token: str, timeout: int = 45) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.timeout = timeout

    def call(self, method: str, data: dict[str, Any] | None = None) -> Any:
        payload = urllib.parse.urlencode(data or {}).encode()
        request = urllib.request.Request(f"{self.base_url}/{method}", data=payload)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.load(response)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"Telegram API request failed: {exc}") from exc
        if not body.get("ok"):
            raise RuntimeError(
                f"Telegram API error: {body.get('description', 'unknown error')}"
            )
        return body.get("result")

    def get_updates(
        self, offset: int | None, poll_timeout: int
    ) -> list[dict[str, Any]]:
        data: dict[str, Any] = {
            "timeout": poll_timeout,
            "allowed_updates": json.dumps(["message"]),
        }
        if offset is not None:
            data["offset"] = offset
        return self.call("getUpdates", data)

    def send_message(self, chat_id: int, text: str) -> None:
        chunks = [
            text[index : index + 4000] for index in range(0, len(text), 4000)
        ] or [""]
        for chunk in chunks:
            self.call("sendMessage", {"chat_id": chat_id, "text": chunk})

    def send_typing(self, chat_id: int) -> None:
        self.call("sendChatAction", {"chat_id": chat_id, "action": "typing"})


class TelegramBot:
    def __init__(
        self,
        client: TelegramClient,
        settings: Settings,
        telegram: TelegramSettings,
        orchestrator: Orchestrator | None = None,
    ) -> None:
        self.client = client
        self.settings = settings
        self.telegram = telegram
        self.orchestrator = orchestrator or Orchestrator(settings)
        self.selected_projects: dict[int, str] = {}

    def run_forever(self) -> None:
        offset: int | None = None
        while True:
            try:
                updates = self.client.get_updates(offset, self.telegram.poll_timeout)
                for update in updates:
                    offset = int(update["update_id"]) + 1
                    self.handle_update(update)
            except KeyboardInterrupt:
                return
            except RuntimeError as exc:
                print(exc)
                time.sleep(3)

    def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("text"), str):
            return
        chat = message.get("chat", {})
        sender = message.get("from", {})
        chat_id = chat.get("id")
        user_id = sender.get("id")
        if not isinstance(chat_id, int) or not isinstance(user_id, int):
            return
        if chat.get("type") != "private":
            self.client.send_message(
                chat_id, "This bot only accepts commands in private chats."
            )
            return

        text = message["text"].strip()
        if text == "/whoami":
            self.client.send_message(chat_id, f"Your Telegram user ID is {user_id}.")
            return
        if user_id not in self.telegram.allowed_user_ids:
            self.client.send_message(
                chat_id, "Access denied. Ask the bot owner to allow your user ID."
            )
            return

        try:
            reply = self.handle_command(user_id, chat_id, text)
        except Exception as exc:
            reply = f"Task failed: {exc}"
        self.client.send_message(chat_id, reply)

    def handle_command(self, user_id: int, chat_id: int, text: str) -> str:
        if text in {"/start", "/help"}:
            return self.help_text()
        if text == "/projects":
            selected = self._selected_alias(user_id)
            return "Projects:\n" + "\n".join(
                f"{'*' if alias == selected else '-'} {alias}"
                for alias in sorted(self.telegram.projects)
            )
        if text.startswith("/use "):
            alias = text[5:].strip()
            if alias not in self.telegram.projects:
                return f"Unknown project: {alias}. Use /projects to list aliases."
            self.selected_projects[user_id] = alias
            return f"Selected project: {alias}"
        if text.startswith("/ask "):
            return self._run_task(user_id, chat_id, text[5:].strip(), TaskType.GENERAL)
        if text.startswith("/code "):
            return self._run_task(user_id, chat_id, text[6:].strip(), TaskType.CODING)
        if text.startswith("/backend "):
            return self._run_backend(chat_id, text[9:].strip())
        return self.help_text()

    def _run_task(
        self, user_id: int, chat_id: int, objective: str, task_type: TaskType
    ) -> str:
        if not objective:
            return "Please include a task after the command."
        alias = self._selected_alias(user_id)
        workspace = self.telegram.projects[alias]
        self.client.send_typing(chat_id)
        task = Task(
            objective=objective,
            task_type=task_type,
            risk=Risk.LOW,
            workspace=str(workspace),
            verify_command=(
                self.telegram.verify_commands.get(alias)
                if task_type == TaskType.CODING
                else None
            ),
            expected_files=1 if task_type == TaskType.CODING else 0,
        )
        outcome = self.orchestrator.run(task)
        changed = ", ".join(outcome.result.changed_files) or "none"
        return (
            f"{outcome.result.text}\n\n"
            f"Project: {alias}\n"
            f"Provider: {outcome.result.provider} ({outcome.result.model})\n"
            f"Verified: {outcome.verification.passed}\n"
            f"Escalated: {outcome.escalated}\n"
            f"Local/GPT split: {outcome.usage.local_work_percent}% / "
            f"{outcome.usage.codex_work_percent}%\n"
            f"Estimated GPT token savings: "
            f"{outcome.usage.estimated_gpt_token_savings_percent:.2f}%\n"
            f"Changed files: {changed}"
        )

    def _selected_alias(self, user_id: int) -> str:
        return self.selected_projects.get(user_id, sorted(self.telegram.projects)[0])

    def _run_backend(self, chat_id: int, body: str) -> str:
        slug, _, objective = body.partition(" ")
        if not slug or not objective:
            return "Usage: /backend <slug> <what to build>"
        backend = BackendSettings.from_config(self.settings)
        self.client.send_typing(chat_id)
        task = build_backend_task(slug, objective, backend)
        outcome = self.orchestrator.run(task)
        if not outcome.verification.passed:
            return (
                f"Backend generation failed for {slug}.\n"
                f"Verification: {outcome.verification.reason}"
            )
        try:
            deployment = BackendDeployer(
                backend.apps_root,
                backend.port_start,
                backend.port_end,
                backend.host,
            ).deploy(slug)
            deploy_line = f"URL: {deployment.url}"
        except Exception as exc:
            deploy_line = f"Generated but not deployed: {exc}"
        changed = ", ".join(outcome.result.changed_files) or "none"
        return (
            f"Backend app: {slug}\n"
            f"Workspace: {task.workspace}\n"
            f"{deploy_line}\n"
            f"Provider: {outcome.result.provider}\n"
            f"Escalated: {outcome.escalated}\n"
            f"Local/GPT split: {outcome.usage.local_work_percent}% / "
            f"{outcome.usage.codex_work_percent}%\n"
            f"Estimated GPT token savings: "
            f"{outcome.usage.estimated_gpt_token_savings_percent:.2f}%\n"
            f"Changed files: {changed}"
        )

    @staticmethod
    def help_text() -> str:
        return (
            "Commands:\n"
            "/whoami - show your Telegram user ID\n"
            "/projects - list configured project aliases\n"
            "/use <alias> - select a project\n"
            "/ask <task> - run a general task\n"
            "/code <task> - run a coding task in the selected project\n"
            "/backend <slug> <task> - generate and deploy a Dockerized backend app"
        )


def run_telegram_bot() -> None:
    token = _load_token()
    if not token:
        raise RuntimeError(
            "set TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN_FILE before starting the bot"
        )
    settings = Settings.from_env()
    telegram = TelegramSettings.from_config(settings)
    TelegramBot(TelegramClient(token), settings, telegram).run_forever()


def _load_token() -> str | None:
    token_file = os.getenv("TELEGRAM_BOT_TOKEN_FILE")
    if token_file:
        path = Path(token_file).expanduser()
        try:
            mode = path.stat().st_mode & 0o777
        except OSError as exc:
            raise RuntimeError(f"cannot read Telegram token file: {exc}") from exc
        if mode & 0o077:
            raise RuntimeError(
                "Telegram token file must not be accessible by group or other users"
            )
        token = path.read_text(encoding="utf-8").strip()
        return token or None
    return os.getenv("TELEGRAM_BOT_TOKEN")
