from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Settings, load_config
from .types import Risk, Task, TaskType


SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")


@dataclass(frozen=True, slots=True)
class BackendDeployment:
    slug: str
    app_dir: Path
    port: int
    url: str


@dataclass(frozen=True, slots=True)
class BackendSettings:
    apps_root: Path
    port_start: int = 8500
    port_end: int = 8999
    host: str = "127.0.0.1"

    @classmethod
    def from_config(cls, settings: Settings) -> "BackendSettings":
        section = load_config().get("backend", {})
        if not isinstance(section, dict):
            raise ValueError("backend configuration must be a TOML table")
        default_root = settings.workspace_roots[0] / "generated-backends"
        apps_root = Path(section.get("apps_root", str(default_root))).expanduser()
        resolved = settings.validate_workspace(str(apps_root))
        return cls(
            apps_root=resolved,
            port_start=int(section.get("port_start", 8500)),
            port_end=int(section.get("port_end", 8999)),
            host=str(section.get("host", "127.0.0.1")),
        )


def build_backend_task(slug: str, objective: str, backend: BackendSettings) -> Task:
    deployer = BackendDeployer(
        backend.apps_root, backend.port_start, backend.port_end, backend.host
    )
    app_dir = deployer._app_dir(slug)
    app_dir.mkdir(parents=True, exist_ok=True)
    prompt = f"""Create a Dockerized backend application for this request:

{objective}

Write a complete backend app in this workspace. It may use Python, Node.js, or another simple runtime,
but it must include:
- Dockerfile
- compose.yaml
- README.md
- application source files

compose.yaml requirements:
- exactly one app service
- build from the local Dockerfile
- map host port using ${{TOKEN_SAVER_BACKEND_PORT:-8500}} to the app's internal port
- do not use privileged mode, host networking, host pid/ipc, or Docker socket mounts
- use restart: unless-stopped

The app must listen on 0.0.0.0 inside the container. Keep dependencies minimal."""
    verify = (
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "text = Path('compose.yaml').read_text().lower()\n"
        "assert Path('Dockerfile').exists(), 'missing Dockerfile'\n"
        "assert Path('compose.yaml').exists(), 'missing compose.yaml'\n"
        "assert '${token_saver_backend_port:-' in text, 'compose must use TOKEN_SAVER_BACKEND_PORT'\n"
        "for forbidden in ['/var/run/docker.sock', 'privileged: true', 'network_mode: host', 'pid: host', 'ipc: host']:\n"
        "    assert forbidden not in text, f'forbidden compose setting: {forbidden}'\n"
        "PY"
    )
    return Task(
        objective=prompt,
        task_type=TaskType.CODING,
        risk=Risk.MEDIUM,
        workspace=str(app_dir),
        verify_command=verify,
        expected_files=5,
        subsystems=2,
    )


class BackendDeployer:
    def __init__(
        self,
        apps_root: Path,
        port_start: int = 8500,
        port_end: int = 8999,
        host: str = "127.0.0.1",
    ) -> None:
        self.apps_root = apps_root.expanduser().resolve()
        self.port_start = port_start
        self.port_end = port_end
        self.host = host

    def deploy(self, slug: str) -> BackendDeployment:
        app_dir = self._app_dir(slug)
        compose_file = app_dir / "compose.yaml"
        dockerfile = app_dir / "Dockerfile"
        if not compose_file.exists() or not dockerfile.exists():
            raise RuntimeError("backend app must include Dockerfile and compose.yaml")
        self._validate_compose(compose_file)
        if shutil.which("docker") is None:
            raise RuntimeError("docker command is not available")
        port = self._allocate_port(compose_file.parent)
        env = {**os.environ, "TOKEN_SAVER_BACKEND_PORT": str(port)}
        command = self._compose_command(compose_file)
        subprocess.run(command, cwd=app_dir, check=True, env=env)
        return BackendDeployment(slug, app_dir, port, f"http://{self.host}:{port}")

    def _app_dir(self, slug: str) -> Path:
        if not SLUG_RE.fullmatch(slug):
            raise ValueError(
                "slug must be 2-41 chars of lowercase letters, numbers, and hyphens"
            )
        app_dir = (self.apps_root / slug).resolve()
        if not app_dir.is_relative_to(self.apps_root):
            raise ValueError("slug escapes apps root")
        return app_dir

    def _allocate_port(self, app_dir: Path) -> int:
        configured = self._read_port_file(app_dir / ".token-saver-port")
        if configured is not None:
            return configured
        used = {
            port
            for path in self.apps_root.glob("*/.token-saver-port")
            if (port := self._read_port_file(path)) is not None
        }
        for port in range(self.port_start, self.port_end + 1):
            if port not in used:
                (app_dir / ".token-saver-port").write_text(str(port), encoding="utf-8")
                return port
        raise RuntimeError("no backend ports available")

    @staticmethod
    def _compose_command(compose_file: Path) -> list[str]:
        docker = shutil.which("docker")
        if docker is None:
            raise RuntimeError("docker command is not available")
        compose_check = subprocess.run(
            [docker, "compose", "version"],
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if compose_check.returncode == 0:
            return [docker, "compose", "-f", str(compose_file), "up", "-d", "--build"]
        docker_compose = shutil.which("docker-compose")
        if docker_compose:
            return [docker_compose, "-f", str(compose_file), "up", "-d", "--build"]
        raise RuntimeError("Docker Compose is not available")

    @staticmethod
    def _read_port_file(path: Path) -> int | None:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        try:
            return int(text)
        except ValueError:
            return None

    @staticmethod
    def _validate_compose(path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        forbidden = [
            "/var/run/docker.sock",
            "privileged: true",
            "network_mode: host",
            "pid: host",
            "ipc: host",
        ]
        lowered = text.lower()
        for item in forbidden:
            if item in lowered:
                raise RuntimeError(f"forbidden compose setting: {item}")
        if "${TOKEN_SAVER_BACKEND_PORT:-" not in text:
            raise RuntimeError(
                "compose.yaml must map host port through TOKEN_SAVER_BACKEND_PORT"
            )
