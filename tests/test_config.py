from pathlib import Path

import pytest

from token_saver.config import Settings, load_config, write_config


def test_from_env_uses_concrete_defaults(monkeypatch, tmp_path: Path) -> None:
    for name in (
        "TOKEN_SAVER_LOCAL_URL",
        "TOKEN_SAVER_LOCAL_MODEL",
        "TOKEN_SAVER_CODEX_MODEL",
        "TOKEN_SAVER_CODEX_MINI_MODEL",
        "TOKEN_SAVER_DB",
    ):
        monkeypatch.delenv(name, raising=False)

    projects = tmp_path / "projects"
    projects.mkdir()
    config = tmp_path / "config.toml"
    write_config([projects], config)
    monkeypatch.setenv("TOKEN_SAVER_CONFIG", str(config))
    monkeypatch.setenv("TOKEN_SAVER_DB", str(tmp_path / "tasks.sqlite3"))

    settings = Settings.from_env()

    assert settings.local_url == "http://127.0.0.1:8080/v1"
    assert isinstance(settings.local_model, str)


def test_from_env_requires_configuration(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TOKEN_SAVER_CONFIG", str(tmp_path / "missing.toml"))
    with pytest.raises(FileNotFoundError, match="token-saver init"):
        Settings.from_env()


def test_write_config_rejects_missing_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must already exist"):
        write_config([tmp_path / "missing"], tmp_path / "config.toml")


def test_config_round_trip_and_workspace_boundary(monkeypatch, tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    projects = tmp_path / "projects"
    projects.mkdir()
    write_config([projects], config)
    monkeypatch.setenv("TOKEN_SAVER_CONFIG", str(config))
    monkeypatch.setenv("TOKEN_SAVER_DB", str(tmp_path / "state.sqlite3"))

    assert load_config()["workspace_roots"] == [str(projects.resolve())]
    settings = Settings.from_env()
    assert settings.validate_workspace(str(projects / "demo")) == projects / "demo"
    with pytest.raises(ValueError, match="outside configured project roots"):
        settings.validate_workspace(str(tmp_path / "elsewhere"))


def test_config_rejects_empty_workspace_roots(monkeypatch, tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text("version = 1\nworkspace_roots = []\n", encoding="utf-8")
    monkeypatch.setenv("TOKEN_SAVER_CONFIG", str(config))
    with pytest.raises(ValueError, match="at least one workspace root"):
        Settings.from_env()
