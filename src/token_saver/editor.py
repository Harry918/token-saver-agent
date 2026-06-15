from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .workspace import IGNORED_PARTS


@dataclass(slots=True)
class EditResult:
    summary: str
    changed_files: list[str]
    error: str | None = None


class WorkspaceEditor:
    def __init__(self, max_files: int = 10, max_total_bytes: int = 250_000) -> None:
        self.max_files = max_files
        self.max_total_bytes = max_total_bytes

    def apply(self, workspace: str | None, response: str) -> EditResult:
        root = Path(workspace or ".").resolve()
        if not root.is_dir():
            return EditResult("", [], f"workspace does not exist: {root}")
        try:
            payload = json.loads(self._extract_json(response))
        except (json.JSONDecodeError, ValueError) as exc:
            return EditResult("", [], f"invalid edit JSON: {exc}")

        if not isinstance(payload, dict) or not isinstance(
            payload.get("operations"), list
        ):
            return EditResult("", [], "edit response must contain an operations array")
        operations = payload["operations"]
        if not operations:
            return EditResult(
                str(payload.get("summary", "")), [], "no file operations supplied"
            )
        if len(operations) > self.max_files:
            return EditResult(
                "", [], f"too many operations: {len(operations)} > {self.max_files}"
            )

        validated: list[tuple[Path, str, str | None]] = []
        total_bytes = 0
        seen: set[Path] = set()
        for operation in operations:
            if not isinstance(operation, dict):
                return EditResult("", [], "each operation must be an object")
            op = operation.get("op")
            relative = operation.get("path")
            if op not in {"write", "delete"} or not isinstance(relative, str):
                return EditResult(
                    "", [], "operations require op=write|delete and a string path"
                )
            try:
                target = self._safe_target(root, relative)
            except ValueError as exc:
                return EditResult("", [], str(exc))
            if target in seen:
                return EditResult("", [], f"duplicate operation for {relative}")
            seen.add(target)
            content = operation.get("content") if op == "write" else None
            if op == "write" and not isinstance(content, str):
                return EditResult(
                    "", [], f"write operation for {relative} requires string content"
                )
            total_bytes += len((content or "").encode("utf-8"))
            validated.append((target, op, content))
        if total_bytes > self.max_total_bytes:
            return EditResult(
                "", [], f"edit payload exceeds {self.max_total_bytes} bytes"
            )

        changed: list[str] = []
        for target, op, content in validated:
            relative = target.relative_to(root).as_posix()
            if op == "write":
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content or "", encoding="utf-8")
            elif target.exists():
                target.unlink()
            changed.append(relative)
        return EditResult(str(payload.get("summary", "Files updated")), changed)

    @staticmethod
    def _extract_json(response: str) -> str:
        text = response.strip()
        if text.startswith("```"):
            first_newline = text.find("\n")
            final_fence = text.rfind("```")
            if first_newline == -1 or final_fence <= first_newline:
                raise ValueError("incomplete fenced JSON")
            text = text[first_newline + 1 : final_fence].strip()
        return text

    @staticmethod
    def _safe_target(root: Path, relative: str) -> Path:
        normalized = PurePosixPath(relative)
        if normalized.is_absolute() or ".." in normalized.parts or not normalized.parts:
            raise ValueError(f"unsafe path: {relative}")
        if any(
            part in IGNORED_PARTS or part.startswith(".") for part in normalized.parts
        ):
            raise ValueError(f"protected path: {relative}")
        target = root.joinpath(*normalized.parts)
        current = root
        for part in normalized.parts[:-1]:
            current = current / part
            if current.is_symlink():
                raise ValueError(f"symlinked parent is not writable: {relative}")
        if target.is_symlink():
            raise ValueError(f"symlink target is not writable: {relative}")
        if not target.resolve(strict=False).is_relative_to(root):
            raise ValueError(f"path escapes workspace: {relative}")
        return target
