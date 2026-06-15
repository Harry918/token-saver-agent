from __future__ import annotations

import os
import json
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


def config_path() -> Path:
    override = os.getenv("TOKEN_SAVER_CONFIG")
    if override:
        return Path(override).expanduser()
    base = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "token-saver" / "config.toml"


def state_path() -> Path:
    override = os.getenv("TOKEN_SAVER_DB")
    if override:
        return Path(override).expanduser()
    base = Path(os.getenv("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "token-saver" / "tasks.sqlite3"


def load_config(path: Path | None = None) -> dict[str, Any]:
    target = path or config_path()
    if not target.exists():
        return {}
    with target.open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"invalid configuration in {target}")
    return data


def write_config(project_roots: list[Path], path: Path | None = None) -> Path:
    target = path or config_path()
    resolved_roots = [root.expanduser().resolve() for root in project_roots]
    invalid = [root for root in resolved_roots if not root.is_dir()]
    if invalid:
        raise ValueError(
            f"project roots must already exist: {', '.join(str(root) for root in invalid)}"
        )
    roots = [str(root) for root in resolved_roots]
    if not roots:
        raise ValueError("at least one project root is required")
    target.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(
        [
            "version = 1",
            f"workspace_roots = {json.dumps(roots)}",
            "",
            "[models]",
            'local_url = "http://127.0.0.1:8080/v1"',
            'local_model = "yuxinlu1/gemma-4-12B-coder-fable5-composer2.5-v1-GGUF:Q4_K_M"',
            'codex_model = "gpt-5.5"',
            'codex_mini_model = "gpt-5.4-mini"',
            "",
            "[runtime]",
            "max_local_attempts = 2",
            "headroom_enabled = true",
            "request_timeout = 180",
            "",
        ]
    )
    target.write_text(content, encoding="utf-8")
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return target


def interactive_setup(path: Path | None = None) -> Path:
    if not sys.stdin.isatty():
        raise RuntimeError(
            "configuration missing; run `token-saver init` in an interactive terminal"
        )
    print("Token Saver Agent needs the folders that contain projects it may access.")
    print(
        "Enter one absolute project-root path per line. Submit an empty line when finished."
    )
    roots: list[Path] = []
    while True:
        raw = input("Project root: ").strip()
        if not raw:
            break
        root = Path(raw).expanduser()
        if not root.is_absolute():
            print("Please enter an absolute path.")
            continue
        if not root.is_dir():
            print("That directory does not exist.")
            continue
        roots.append(root)
    if not roots:
        raise RuntimeError("setup cancelled: no project roots supplied")
    target = write_config(roots, path)
    print(f"Configuration written to {target}")
    return target


@dataclass(frozen=True, slots=True)
class Settings:
    local_url: str = "http://127.0.0.1:8080/v1"
    local_model: str = "yuxinlu1/gemma-4-12B-coder-fable5-composer2.5-v1-GGUF:Q4_K_M"
    codex_model: str = "gpt-5.5"
    codex_mini_model: str = "gpt-5.4-mini"
    db_path: Path = field(default_factory=state_path)
    max_local_attempts: int = 2
    headroom_enabled: bool = True
    request_timeout: int = 180
    workspace_roots: tuple[Path, ...] = ()

    @classmethod
    def from_env(cls) -> "Settings":
        defaults = cls()
        config = load_config()
        if not config:
            raise FileNotFoundError(
                f"configuration not found at {config_path()}; run `token-saver init` first"
            )
        models = config.get("models", {})
        runtime = config.get("runtime", {})
        raw_roots = config.get("workspace_roots", [])
        if not isinstance(models, dict) or not isinstance(runtime, dict):
            raise ValueError("models and runtime configuration must be TOML tables")
        if not isinstance(raw_roots, list) or not all(
            isinstance(root, str) for root in raw_roots
        ):
            raise ValueError("workspace_roots must be an array of paths")
        if config and not raw_roots:
            raise ValueError("configuration must contain at least one workspace root")
        return cls(
            local_url=os.getenv(
                "TOKEN_SAVER_LOCAL_URL", models.get("local_url", defaults.local_url)
            ),
            local_model=os.getenv(
                "TOKEN_SAVER_LOCAL_MODEL",
                models.get("local_model", defaults.local_model),
            ),
            codex_model=os.getenv(
                "TOKEN_SAVER_CODEX_MODEL",
                models.get("codex_model", defaults.codex_model),
            ),
            codex_mini_model=os.getenv(
                "TOKEN_SAVER_CODEX_MINI_MODEL",
                models.get("codex_mini_model", defaults.codex_mini_model),
            ),
            db_path=state_path(),
            max_local_attempts=int(
                os.getenv(
                    "TOKEN_SAVER_MAX_LOCAL_ATTEMPTS",
                    str(runtime.get("max_local_attempts", defaults.max_local_attempts)),
                )
            ),
            headroom_enabled=_bool(
                "TOKEN_SAVER_HEADROOM",
                bool(runtime.get("headroom_enabled", defaults.headroom_enabled)),
            ),
            request_timeout=int(
                os.getenv(
                    "TOKEN_SAVER_REQUEST_TIMEOUT",
                    str(runtime.get("request_timeout", defaults.request_timeout)),
                )
            ),
            workspace_roots=tuple(
                Path(root).expanduser().resolve() for root in raw_roots
            ),
        )

    def validate_workspace(self, workspace: str | None) -> Path:
        target = Path(workspace or ".").expanduser().resolve()
        if not self.workspace_roots:
            return target
        if not any(
            target == root or target.is_relative_to(root)
            for root in self.workspace_roots
        ):
            roots = ", ".join(str(root) for root in self.workspace_roots)
            raise ValueError(
                f"workspace {target} is outside configured project roots: {roots}"
            )
        return target
