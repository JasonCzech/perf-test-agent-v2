from pathlib import Path

from src import runtime


def test_repo_root_defaults_to_project_root(monkeypatch) -> None:
    monkeypatch.delenv("PERF_TEST_AGENT_ROOT", raising=False)

    repo_root = runtime.get_repo_root()

    assert repo_root == Path(__file__).resolve().parents[1]


def test_repo_root_honors_environment_override(monkeypatch, tmp_path) -> None:
    override = tmp_path / "deploy-root"
    override.mkdir()
    monkeypatch.setenv("PERF_TEST_AGENT_ROOT", str(override))

    assert runtime.get_repo_root() == override.resolve()
