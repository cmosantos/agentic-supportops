from pathlib import Path

from core.config import load_local_environment


def test_process_environment_takes_precedence_over_local_env(
    tmp_path: Path, monkeypatch
) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text("OPENAI_MODEL=local-model\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_MODEL", "process-model")

    load_local_environment(env_file)

    assert __import__("os").environ["OPENAI_MODEL"] == "process-model"
