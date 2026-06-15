from types import SimpleNamespace

import openai_codex

from token_saver.providers import CodexProvider
from token_saver.types import Task


def test_codex_provider_passes_workspace_to_thread(monkeypatch, tmp_path) -> None:
    observed = {}

    class FakeThread:
        def run(self, prompt):
            observed["prompt"] = prompt
            return SimpleNamespace(final_response="done")

    class FakeCodex:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def thread_start(self, **kwargs):
            observed.update(kwargs)
            return FakeThread()

    monkeypatch.setattr(openai_codex, "Codex", FakeCodex)
    task = Task("Fix it", workspace=str(tmp_path))
    result = CodexProvider().run(
        task, [{"role": "user", "content": "payload"}], "gpt-test"
    )

    assert observed["cwd"] == str(tmp_path.resolve())
    assert observed["model"] == "gpt-test"
    assert observed["prompt"] == "payload"
    assert result.text == "done"
