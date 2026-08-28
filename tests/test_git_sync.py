import pytest
from unittest.mock import patch, MagicMock
from zhihu_pipeline.config import GitConfig
from zhihu_pipeline.git_sync import ensure_git_repo, git_pull, git_push


@pytest.fixture
def dummy_git_config():
    return GitConfig(
        enabled=True,
        repo_url="https://user:token@github.com/user/notes.git",
        branch="main",
        user_name="tester",
        user_email="tester@example.com",
        auto_pull=True,
        auto_push=True
    )


def test_git_disabled_returns_false():
    cfg = GitConfig(enabled=False)
    assert ensure_git_repo("/tmp/test", cfg) is False
    assert git_pull("/tmp/test", cfg) is False
    assert git_push("/tmp/test", cfg) is False


def test_git_pull_calls_correct_command(dummy_git_config):
    with patch("zhihu_pipeline.git_sync._run_git_cmd") as mock_cmd, \
         patch("os.path.exists", return_value=True):
        mock_cmd.return_value = (0, "Already up to date.", "")
        
        ok = git_pull("/tmp/vault", dummy_git_config)
        assert ok is True
        mock_cmd.assert_any_call(["git", "pull", "--rebase", "origin", "main"], cwd="/tmp/vault")


def test_git_push_when_changes_exist(dummy_git_config):
    with patch("zhihu_pipeline.git_sync._run_git_cmd") as mock_cmd, \
         patch("os.path.exists", return_value=True):
        
        # Mock status returns modified files
        def side_effect(cmd, cwd):
            if "status" in cmd:
                return 0, " M notes.md", ""
            return 0, "", ""

        mock_cmd.side_effect = side_effect

        ok = git_push("/tmp/vault", dummy_git_config, "test commit")
        assert ok is True
        mock_cmd.assert_any_call(["git", "add", "-A"], cwd="/tmp/vault")
        mock_cmd.assert_any_call(["git", "commit", "-m", "test commit"], cwd="/tmp/vault")
        mock_cmd.assert_any_call(["git", "push", "origin", "main"], cwd="/tmp/vault")
