import os
import subprocess
from loguru import logger
from .config import GitConfig


def _run_git_cmd(cmd: list[str], cwd: str) -> tuple[int, str, str]:
    """Execute a git command in the specified directory."""
    try:
        res = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False
        )
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return -1, "", str(e)


def ensure_git_repo(vault_path: str, git_config: GitConfig) -> bool:
    """
    Ensure that the vault directory is a valid git repository with remote configured.
    """
    if not git_config.enabled or not git_config.repo_url:
        return False

    # Prevent dubious ownership errors in container volume mounts
    _run_git_cmd(["git", "config", "--global", "--add", "safe.directory", "*"], cwd=vault_path)

    os.makedirs(vault_path, exist_ok=True)
    git_dir = os.path.join(vault_path, ".git")

    if not os.path.exists(git_dir):
        logger.info(f"Initializing git repository in {vault_path}...")
        _run_git_cmd(["git", "init"], cwd=vault_path)

    # Set user identity
    _run_git_cmd(["git", "config", "user.name", git_config.user_name], cwd=vault_path)
    _run_git_cmd(["git", "config", "user.email", git_config.user_email], cwd=vault_path)

    # Configure remote origin
    code, remotes, _ = _run_git_cmd(["git", "remote"], cwd=vault_path)
    if "origin" in remotes.split():
        _run_git_cmd(["git", "remote", "set-url", "origin", git_config.repo_url], cwd=vault_path)
    else:
        _run_git_cmd(["git", "remote", "add", "origin", git_config.repo_url], cwd=vault_path)

    _run_git_cmd(["git", "branch", "-M", git_config.branch], cwd=vault_path)
    return True


def git_pull(vault_path: str, git_config: GitConfig) -> bool:
    """Pull latest changes from remote repository before syncing."""
    if not git_config.enabled or not git_config.auto_pull or not git_config.repo_url:
        return False

    ensure_git_repo(vault_path, git_config)
    logger.info(f"Pulling latest notes from GitHub ({git_config.branch})...")
    code, out, err = _run_git_cmd(
        ["git", "pull", "--rebase", "origin", git_config.branch],
        cwd=vault_path
    )
    if code == 0:
        logger.info("Git pull completed successfully.")
        return True
    else:
        logger.warning(f"Git pull encountered an issue (will continue sync): {err}")
        return False


def git_push(vault_path: str, git_config: GitConfig, commit_message: str = "docs: auto sync zhihu collections [skip ci]") -> bool:
    """Commit and push changes to remote GitHub repository."""
    if not git_config.enabled or not git_config.auto_push or not git_config.repo_url:
        return False

    ensure_git_repo(vault_path, git_config)
    
    # Check if there are changes
    code, status_out, _ = _run_git_cmd(["git", "status", "--porcelain"], cwd=vault_path)
    if not status_out:
        logger.info("No git changes to commit.")
        return True

    logger.info(f"Staging changes in {vault_path}...")
    _run_git_cmd(["git", "add", "-A"], cwd=vault_path)

    logger.info(f"Committing: {commit_message}")
    code, out, err = _run_git_cmd(["git", "commit", "-m", commit_message], cwd=vault_path)
    if code != 0:
        logger.error(f"Git commit failed: {err}")
        return False

    logger.info(f"Pushing to GitHub ({git_config.branch})...")
    code, out, err = _run_git_cmd(["git", "push", "origin", git_config.branch], cwd=vault_path)
    if code == 0:
        logger.info("🎉 Successfully pushed latest Zhihu notes to GitHub!")
        return True
    else:
        logger.error(f"Failed to git push: {err}")
        return False
