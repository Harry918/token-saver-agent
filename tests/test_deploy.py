from pathlib import Path

import pytest

from token_saver.deploy import BackendDeployer, BackendSettings, build_backend_task


def test_backend_deployer_rejects_bad_slug(tmp_path: Path) -> None:
    deployer = BackendDeployer(tmp_path)
    with pytest.raises(ValueError, match="slug"):
        deployer._app_dir("../escape")


def test_backend_deployer_rejects_docker_socket_mount(tmp_path: Path) -> None:
    compose = tmp_path / "compose.yaml"
    compose.write_text(
        """
services:
  app:
    build: .
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    ports:
      - "${TOKEN_SAVER_BACKEND_PORT:-8500}:8000"
""",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="docker.sock"):
        BackendDeployer._validate_compose(compose)


def test_backend_deployer_requires_token_saver_port(tmp_path: Path) -> None:
    compose = tmp_path / "compose.yaml"
    compose.write_text(
        """
services:
  app:
    build: .
    ports:
      - "8500:8000"
""",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="TOKEN_SAVER_BACKEND_PORT"):
        BackendDeployer._validate_compose(compose)


def test_build_backend_task_uses_apps_root(tmp_path: Path) -> None:
    backend = BackendSettings(apps_root=tmp_path)
    task = build_backend_task("demo-api", "make a tiny api", backend)
    assert task.workspace == str(tmp_path / "demo-api")
    assert (tmp_path / "demo-api").is_dir()
    assert "Dockerfile" in task.objective
    assert "compose.yaml" in task.objective
