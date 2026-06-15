from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import Settings, config_path, interactive_setup, load_config, write_config
from .api import run_api_server
from .deploy import BackendDeployer, BackendSettings, build_backend_task
from .orchestrator import Orchestrator
from .router import PolicyRouter
from .store import RunStore
from .telegram import run_telegram_bot
from .types import Risk, Task, TaskType


def _task(args: argparse.Namespace) -> Task:
    return Task(
        objective=args.objective,
        task_type=TaskType(args.type),
        risk=Risk(args.risk),
        workspace=args.workspace,
        constraints=args.constraint or [],
        verify_command=args.verify,
        expected_files=args.files,
        subsystems=args.subsystems,
        ambiguity=args.ambiguity,
        tool_depth=args.tool_depth,
        domain_novelty=args.novelty,
    )


def _add_task_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("objective")
    parser.add_argument(
        "--type", choices=[item.value for item in TaskType], default="general"
    )
    parser.add_argument("--risk", choices=[item.value for item in Risk], default="low")
    parser.add_argument("--workspace", default=str(Path.cwd()))
    parser.add_argument("--constraint", action="append")
    parser.add_argument("--verify")
    parser.add_argument("--files", type=int, default=0)
    parser.add_argument("--subsystems", type=int, default=1)
    parser.add_argument("--ambiguity", type=int, default=0)
    parser.add_argument("--tool-depth", type=int, default=0)
    parser.add_argument("--novelty", type=int, default=0)


def main() -> None:
    parser = argparse.ArgumentParser(prog="token-saver")
    subparsers = parser.add_subparsers(dest="command", required=True)
    route_parser = subparsers.add_parser("route", help="Preview the routing decision")
    _add_task_args(route_parser)
    run_parser = subparsers.add_parser("run", help="Run a task")
    _add_task_args(run_parser)
    history_parser = subparsers.add_parser("history", help="Show recent runs")
    history_parser.add_argument("--limit", type=int, default=20)
    init_parser = subparsers.add_parser("init", help="Configure allowed project roots")
    init_parser.add_argument(
        "--project-root",
        action="append",
        help="Allowed project root; repeat for multiple roots",
    )
    subparsers.add_parser("config", help="Show configuration path and allowed roots")
    subparsers.add_parser("telegram", help="Run the private Telegram bot bridge")
    subparsers.add_parser("api", help="Run the authenticated HTTP API for a web UI")
    backend_parser = subparsers.add_parser(
        "backend", help="Generate and optionally deploy a Dockerized backend app"
    )
    backend_parser.add_argument("slug")
    backend_parser.add_argument("objective", nargs="+")
    backend_parser.add_argument(
        "--no-deploy",
        action="store_true",
        help="Generate and verify files, but do not run docker compose",
    )
    args = parser.parse_args()

    if args.command == "init":
        if args.project_root:
            roots = [Path(root) for root in args.project_root]
            if not all(root.expanduser().is_absolute() for root in roots):
                parser.error("--project-root values must be absolute paths")
            target = write_config(roots)
            print(f"Configuration written to {target}")
        else:
            interactive_setup()
        return

    if not load_config():
        interactive_setup()
    settings = Settings.from_env()
    if args.command == "config":
        print(
            json.dumps(
                {
                    "config_path": str(config_path()),
                    "workspace_roots": [str(root) for root in settings.workspace_roots],
                    "database": str(settings.db_path),
                    "local_url": settings.local_url,
                },
                indent=2,
            )
        )
        return
    if args.command == "telegram":
        try:
            run_telegram_bot()
        except RuntimeError as exc:
            parser.error(str(exc))
        return
    if args.command == "api":
        try:
            run_api_server()
        except RuntimeError as exc:
            parser.error(str(exc))
        return
    if args.command == "history":
        print(json.dumps(RunStore(settings.db_path).recent(args.limit), indent=2))
        return

    if args.command == "backend":
        backend = BackendSettings.from_config(settings)
        objective = " ".join(args.objective)
        task = build_backend_task(args.slug, objective, backend)
        outcome = Orchestrator(settings).run(task)
        response = {
            "slug": args.slug,
            "workspace": task.workspace,
            "generated": outcome.verification.passed,
            "provider": outcome.result.provider,
            "escalated": outcome.escalated,
            "changed_files": outcome.result.changed_files,
        }
        if outcome.verification.passed and not args.no_deploy:
            deployment = BackendDeployer(
                backend.apps_root,
                backend.port_start,
                backend.port_end,
                backend.host,
            ).deploy(args.slug)
            response["url"] = deployment.url
            response["port"] = deployment.port
        else:
            response["verification"] = outcome.verification.reason
        print(json.dumps(response, indent=2))
        return

    task = _task(args)
    if args.command == "route":
        decision = PolicyRouter().route(task)
        print(
            json.dumps(
                {
                    "tier": decision.tier,
                    "score": decision.score,
                    "reasons": decision.reasons,
                },
                indent=2,
            )
        )
        return

    outcome = Orchestrator(settings).run(task)
    print(outcome.result.text)
    print(
        json.dumps(
            {
                "task_id": outcome.task_id,
                "tier": outcome.decision.tier,
                "score": outcome.decision.score,
                "provider": outcome.result.provider,
                "model": outcome.result.model,
                "verified": outcome.verification.passed,
                "verification": outcome.verification.reason,
                "escalated": outcome.escalated,
                "changed_files": outcome.result.changed_files,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
