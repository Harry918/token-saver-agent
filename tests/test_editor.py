import json

from token_saver.editor import WorkspaceEditor


def test_editor_writes_and_deletes_inside_workspace(tmp_path) -> None:
    old = tmp_path / "old.txt"
    old.write_text("old", encoding="utf-8")
    response = json.dumps(
        {
            "summary": "updated files",
            "operations": [
                {"op": "write", "path": "src/app.py", "content": "print('ok')\n"},
                {"op": "delete", "path": "old.txt"},
            ],
        }
    )
    result = WorkspaceEditor().apply(str(tmp_path), response)
    assert result.error is None
    assert (tmp_path / "src/app.py").read_text() == "print('ok')\n"
    assert not old.exists()


def test_editor_rejects_workspace_escape(tmp_path) -> None:
    response = json.dumps(
        {
            "summary": "bad",
            "operations": [{"op": "write", "path": "../escape", "content": "x"}],
        }
    )
    result = WorkspaceEditor().apply(str(tmp_path), response)
    assert result.error == "unsafe path: ../escape"
    assert not (tmp_path.parent / "escape").exists()


def test_editor_rejects_protected_paths(tmp_path) -> None:
    response = json.dumps(
        {
            "summary": "bad",
            "operations": [{"op": "write", "path": ".git/config", "content": "x"}],
        }
    )
    assert (
        WorkspaceEditor().apply(str(tmp_path), response).error
        == "protected path: .git/config"
    )
