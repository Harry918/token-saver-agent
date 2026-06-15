from __future__ import annotations

from pathlib import Path


IGNORED_PARTS = {
    ".git",
    ".hg",
    ".svn",
    ".token-saver",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    "node_modules",
    "vendor",
}

TEXT_SUFFIXES = {
    "",
    ".c",
    ".cc",
    ".cfg",
    ".conf",
    ".cpp",
    ".css",
    ".csv",
    ".env",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


def collect_workspace(
    workspace: str | None, max_bytes: int = 96_000
) -> dict[str, object]:
    root = Path(workspace or ".").resolve()
    if not root.is_dir():
        return {"root": str(root), "files": [], "truncated": False}

    files: list[dict[str, str]] = []
    used = 0
    truncated = False
    candidates = sorted(path for path in root.rglob("*") if path.is_file())
    for path in candidates:
        relative = path.relative_to(root)
        if any(
            part in IGNORED_PARTS or part.startswith(".") for part in relative.parts
        ):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES or path.is_symlink():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        size = len(content.encode("utf-8"))
        if used + size > max_bytes:
            truncated = True
            continue
        files.append({"path": relative.as_posix(), "content": content})
        used += size
    return {"root": str(root), "files": files, "truncated": truncated}
