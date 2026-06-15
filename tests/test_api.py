from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from token_saver.api import (
    ApiServer,
    ApiSettings,
    RateLimiter,
    load_api_token,
    make_handler,
)
from token_saver.config import Settings
from token_saver.types import (
    ModelResult,
    RouteDecision,
    RunResult,
    Tier,
    VerificationResult,
)


class FakeOrchestrator:
    def run(self, task):
        return RunResult(
            task.task_id,
            RouteDecision(Tier.CODEX_MINI, 10, []),
            ModelResult("done", "codex", "test-model", changed_files=["app.py"]),
            VerificationResult(True, "passed"),
            False,
        )


def api_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **overrides
) -> ApiServer:
    token_file = tmp_path / "api-token"
    token_file.write_text("test-token", encoding="utf-8")
    token_file.chmod(0o600)
    monkeypatch.setattr(
        "token_saver.deploy.load_config",
        lambda: {"backend": {"apps_root": str(tmp_path / "generated-backends")}},
    )
    api = ApiSettings(
        token_file=token_file,
        allowed_origins=frozenset({"https://example.github.io"}),
        backend_deploy_enabled=False,
        **overrides,
    )
    return ApiServer(
        Settings(workspace_roots=(tmp_path,), db_path=tmp_path / "runs.sqlite3"),
        api,
        orchestrator_factory=lambda: FakeOrchestrator(),
    )


def request(handler, method: str, path: str, body=None, headers=None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        payload = None if body is None else json.dumps(body)
        connection.request(method, path, payload, headers or {})
        response = connection.getresponse()
        data = response.read().decode("utf-8")
        return response.status, dict(response.headers), json.loads(data or "{}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_api_rejects_missing_auth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    handler = make_handler(api_server(monkeypatch, tmp_path))
    status, _, body = request(handler, "GET", "/api/runs")
    assert status == 401
    assert body["error"] == "unauthorized"


def test_api_rejects_unallowed_origin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    handler = make_handler(api_server(monkeypatch, tmp_path))
    status, _, body = request(
        handler,
        "GET",
        "/api/runs",
        headers={
            "Authorization": "Bearer test-token",
            "Origin": "https://evil.example",
        },
    )
    assert status == 403
    assert body["error"] == "origin not allowed"


def test_backend_endpoint_generates_without_deploying(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    handler = make_handler(api_server(monkeypatch, tmp_path))
    status, headers, body = request(
        handler,
        "POST",
        "/api/backend",
        {"slug": "demo-api", "objective": "build a tiny api"},
        {
            "Authorization": "Bearer test-token",
            "Origin": "https://example.github.io",
            "Content-Type": "application/json",
        },
    )
    assert status == 200
    assert headers["Access-Control-Allow-Origin"] == "https://example.github.io"
    assert body["generated"] is True
    assert "url" not in body
    assert (tmp_path / "generated-backends" / "demo-api").is_dir()


def test_backend_endpoint_blocks_deploy_when_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    handler = make_handler(api_server(monkeypatch, tmp_path))
    status, _, body = request(
        handler,
        "POST",
        "/api/backend",
        {"slug": "demo-api", "objective": "build a tiny api", "deploy": True},
        {
            "Authorization": "Bearer test-token",
            "Content-Type": "application/json",
        },
    )
    assert status == 403
    assert body["error"] == "backend deployment is disabled"


def test_api_rejects_large_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    handler = make_handler(api_server(monkeypatch, tmp_path, max_body_bytes=20))
    status, _, body = request(
        handler,
        "POST",
        "/api/backend",
        {"slug": "demo-api", "objective": "x" * 100},
        {
            "Authorization": "Bearer test-token",
            "Content-Type": "application/json",
        },
    )
    assert status == 413
    assert body["error"] == "body too large"


def test_load_api_token_rejects_public_file(tmp_path: Path) -> None:
    token_file = tmp_path / "api-token"
    token_file.write_text("test-token", encoding="utf-8")
    token_file.chmod(0o644)
    with pytest.raises(RuntimeError, match="group or other users"):
        load_api_token(ApiSettings(token_file=token_file))


def test_rate_limiter_blocks_after_limit() -> None:
    limiter = RateLimiter(2)
    assert limiter.allow("127.0.0.1")
    assert limiter.allow("127.0.0.1")
    assert not limiter.allow("127.0.0.1")
