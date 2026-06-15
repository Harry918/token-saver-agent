from __future__ import annotations

import hmac
import json
import os
import time
from collections import deque
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .config import Settings, load_config
from .deploy import BackendDeployer, BackendSettings, build_backend_task
from .orchestrator import Orchestrator
from .store import RunStore


MAX_OBJECTIVE_CHARS = 4000


@dataclass(frozen=True, slots=True)
class ApiSettings:
    host: str = "127.0.0.1"
    port: int = 8787
    token_file: Path | None = None
    allowed_origins: frozenset[str] = frozenset()
    max_body_bytes: int = 32768
    rate_limit_per_minute: int = 20
    backend_deploy_enabled: bool = False

    @classmethod
    def from_config(cls) -> "ApiSettings":
        section = load_config().get("api", {})
        if not isinstance(section, dict):
            raise ValueError("api configuration must be a TOML table")
        raw_origins = section.get("allowed_origins", [])
        if not isinstance(raw_origins, list) or not all(
            isinstance(origin, str) for origin in raw_origins
        ):
            raise ValueError("api.allowed_origins must be an array of strings")
        token_file = section.get("token_file")
        if token_file is not None and not isinstance(token_file, str):
            raise ValueError("api.token_file must be a string")
        return cls(
            host=str(section.get("host", "127.0.0.1")),
            port=int(section.get("port", 8787)),
            token_file=Path(token_file).expanduser() if token_file else None,
            allowed_origins=frozenset(raw_origins),
            max_body_bytes=int(section.get("max_body_bytes", 32768)),
            rate_limit_per_minute=int(section.get("rate_limit_per_minute", 20)),
            backend_deploy_enabled=bool(section.get("backend_deploy_enabled", False)),
        )


class RateLimiter:
    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.requests: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        if self.limit <= 0:
            return False
        now = time.monotonic()
        cutoff = now - self.window_seconds
        bucket = self.requests.setdefault(key, deque())
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= self.limit:
            return False
        bucket.append(now)
        return True


class ApiServer:
    def __init__(
        self,
        settings: Settings,
        api: ApiSettings,
        orchestrator_factory: Callable[[], Orchestrator] | None = None,
    ) -> None:
        self.settings = settings
        self.api = api
        self.token = load_api_token(api)
        self.limiter = RateLimiter(api.rate_limit_per_minute)
        self.orchestrator_factory = orchestrator_factory or (
            lambda: Orchestrator(settings)
        )

    def serve_forever(self) -> None:
        handler = make_handler(self)
        server = ThreadingHTTPServer((self.api.host, self.api.port), handler)
        print(f"Token Saver API listening on http://{self.api.host}:{self.api.port}")
        server.serve_forever()


def load_api_token(api: ApiSettings) -> str:
    token = os.getenv("TOKEN_SAVER_API_TOKEN")
    if token:
        return token.strip()
    token_file_env = os.getenv("TOKEN_SAVER_API_TOKEN_FILE")
    token_file = Path(token_file_env).expanduser() if token_file_env else api.token_file
    if not token_file:
        raise RuntimeError(
            "set TOKEN_SAVER_API_TOKEN, TOKEN_SAVER_API_TOKEN_FILE, or api.token_file before starting the API"
        )
    try:
        mode = token_file.stat().st_mode & 0o777
    except OSError as exc:
        raise RuntimeError(f"cannot read API token file: {exc}") from exc
    if mode & 0o077:
        raise RuntimeError(
            "API token file must not be accessible by group or other users"
        )
    token = token_file.read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError("API token must not be empty")
    return token


def run_api_server() -> None:
    settings = Settings.from_env()
    api = ApiSettings.from_config()
    ApiServer(settings, api).serve_forever()


def make_handler(server: ApiServer) -> type[BaseHTTPRequestHandler]:
    class TokenSaverApiHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:
            return

        def do_OPTIONS(self) -> None:
            if not self._origin_allowed():
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "origin not allowed"})
                return
            self._send_empty(HTTPStatus.NO_CONTENT)

        def do_GET(self) -> None:
            if self.path == "/health":
                self._send_json(HTTPStatus.OK, {"ok": True})
                return
            if not self._authorized():
                return
            if self.path.startswith("/api/runs"):
                self._handle_runs()
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:
            if not self._authorized():
                return
            if self.path == "/api/backend":
                self._handle_backend()
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def _authorized(self) -> bool:
            if not self._origin_allowed():
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "origin not allowed"})
                return False
            client_ip = self.client_address[0] if self.client_address else "unknown"
            if not server.limiter.allow(client_ip):
                self._send_json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "rate limited"})
                return False
            header = self.headers.get("Authorization", "")
            scheme, _, value = header.partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(
                value, server.token
            ):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return False
            return True

        def _origin_allowed(self) -> bool:
            origin = self.headers.get("Origin")
            return not origin or origin in server.api.allowed_origins

        def _read_json(self) -> dict[str, Any] | None:
            raw_length = self.headers.get("Content-Length")
            try:
                length = int(raw_length or "0")
            except ValueError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid length"})
                return None
            if length <= 0:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "empty body"})
                return None
            if length > server.api.max_body_bytes:
                self._send_json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body too large"}
                )
                return None
            content_type = self.headers.get("Content-Type", "")
            if "application/json" not in content_type:
                self._send_json(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "json required"}
                )
                return None
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
                return None
            if not isinstance(body, dict):
                self._send_json(
                    HTTPStatus.BAD_REQUEST, {"error": "json object required"}
                )
                return None
            return body

        def _handle_backend(self) -> None:
            body = self._read_json()
            if body is None:
                return
            slug = body.get("slug")
            objective = body.get("objective")
            deploy = bool(body.get("deploy", False))
            if not isinstance(slug, str) or not isinstance(objective, str):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "slug and objective must be strings"},
                )
                return
            objective = objective.strip()
            if not objective or len(objective) > MAX_OBJECTIVE_CHARS:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": f"objective must be 1-{MAX_OBJECTIVE_CHARS} characters"},
                )
                return
            if deploy and not server.api.backend_deploy_enabled:
                self._send_json(
                    HTTPStatus.FORBIDDEN,
                    {"error": "backend deployment is disabled"},
                )
                return
            try:
                backend = BackendSettings.from_config(server.settings)
                task = build_backend_task(slug, objective, backend)
                outcome = server.orchestrator_factory().run(task)
                response: dict[str, Any] = {
                    "slug": slug,
                    "workspace": task.workspace,
                    "generated": outcome.verification.passed,
                    "provider": outcome.result.provider,
                    "model": outcome.result.model,
                    "escalated": outcome.escalated,
                    "verification": outcome.verification.reason,
                    "changed_files": outcome.result.changed_files,
                    "usage": outcome.usage.to_dict(),
                }
                if outcome.verification.passed and deploy:
                    deployment = BackendDeployer(
                        backend.apps_root,
                        backend.port_start,
                        backend.port_end,
                        backend.host,
                    ).deploy(slug)
                    response["url"] = deployment.url
                    response["port"] = deployment.port
                self._send_json(HTTPStatus.OK, response)
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        def _handle_runs(self) -> None:
            limit = 20
            if "?" in self.path:
                query = self.path.split("?", 1)[1]
                for part in query.split("&"):
                    key, _, value = part.partition("=")
                    if key == "limit":
                        try:
                            limit = min(max(int(value), 1), 100)
                        except ValueError:
                            pass
            runs = RunStore(server.settings.db_path).recent(limit)
            self._send_json(HTTPStatus.OK, {"runs": runs})

        def _send_empty(self, status: HTTPStatus) -> None:
            self.send_response(status)
            self._send_common_headers()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self._send_common_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_common_headers(self) -> None:
            origin = self.headers.get("Origin")
            if origin and origin in server.api.allowed_origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
                self.send_header(
                    "Access-Control-Allow-Headers", "Authorization, Content-Type"
                )
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
            )

    return TokenSaverApiHandler
