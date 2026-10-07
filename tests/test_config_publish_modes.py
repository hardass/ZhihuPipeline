"""Configuration plumbing for the publish channel switches."""

import os

from zhihu_pipeline.config import load_config

BASE = """
sync:
  schedule_enabled: false
tagger:
  enabled: false
notify:
  enabled: false
telegram:
  enabled: false
git:
  enabled: {git_enabled}
  sync_mode: {sync_mode}
  repo_url: "https://example.invalid/notes.git"
  user_name: tester
  user_email: tester@example.com
livesync:
  enabled: {livesync_enabled}
  couchdb_url: "http://127.0.0.1:5984"
  db_name: obsidian-vault
  username: obsidian_admin
  node_name: zhihu-node
"""

GIT_ENV_KEYS = ("GIT_ENABLED", "GIT_SYNC_MODE", "GIT_REPO_URL", "GIT_BRANCH",
                "GIT_USER_NAME", "GIT_USER_EMAIL")
LIVESYNC_ENV_KEYS = ("LIVESYNC_ENABLED", "LIVESYNC_COUCHDB_URL", "LIVESYNC_DB_NAME",
                     "LIVESYNC_COUCHDB_USER", "LIVESYNC_COUCHDB_PASSWORD",
                     "LIVESYNC_NODE_NAME")


def write_config(tmp_path, **fmt) -> str:
    values = {
        "git_enabled": "true",
        "sync_mode": "git",
        "livesync_enabled": "false",
    }
    values.update(fmt)
    path = tmp_path / "config.yaml"
    path.write_text(BASE.format(**values), encoding="utf-8")
    return str(path)


def clean_env(monkeypatch):
    for key in (*GIT_ENV_KEYS, *LIVESYNC_ENV_KEYS):
        monkeypatch.delenv(key, raising=False)


def test_livesync_mode_is_preserved(tmp_path, monkeypatch):
    clean_env(monkeypatch)
    config = load_config(write_config(tmp_path, sync_mode="livesync", git_enabled="false",
                                       livesync_enabled="true"))
    assert config.git.sync_mode == "livesync"
    assert config.livesync.enabled is True
    assert config.livesync.couchdb_url == "http://127.0.0.1:5984"


def test_livesync_aliases_are_normalised(tmp_path, monkeypatch):
    clean_env(monkeypatch)
    config = load_config(write_config(tmp_path, sync_mode="livesync_node"))
    assert config.git.sync_mode == "livesync"


def test_unknown_mode_fails_closed_instead_of_guessing(tmp_path, monkeypatch):
    # A typo must not silently fall back to pushing private notes to GitHub.
    clean_env(monkeypatch)
    config = load_config(write_config(tmp_path, sync_mode="livesycn"))
    assert config.git.sync_mode == "none"


def test_sync_mode_can_be_overridden_by_environment(tmp_path, monkeypatch):
    clean_env(monkeypatch)
    monkeypatch.setenv("GIT_SYNC_MODE", "none")
    config = load_config(write_config(tmp_path, sync_mode="git"))
    assert config.git.sync_mode == "none"


def test_git_enabled_false_is_not_parsed_as_true(tmp_path, monkeypatch):
    # bool("false") is True in Python, which used to switch the channel on.
    clean_env(monkeypatch)
    monkeypatch.setenv("GIT_ENABLED", "false")
    config = load_config(write_config(tmp_path, git_enabled="true"))
    assert config.git.enabled is False


def test_couchdb_credentials_come_from_the_environment(tmp_path, monkeypatch):
    # The password is never stored in config.yaml on purpose.
    clean_env(monkeypatch)
    monkeypatch.setenv("LIVESYNC_COUCHDB_PASSWORD", "from-env")
    monkeypatch.setenv("LIVESYNC_COUCHDB_URL", "http://127.0.0.1:5984/")
    config = load_config(write_config(tmp_path, livesync_enabled="true"))
    assert config.livesync.password == "from-env"
    assert config.livesync.couchdb_url == "http://127.0.0.1:5984"


def test_default_mode_is_git_for_existing_installations(tmp_path, monkeypatch):
    clean_env(monkeypatch)
    path = tmp_path / "minimal.yaml"
    path.write_text("sync:\n  schedule_enabled: false\n", encoding="utf-8")
    config = load_config(str(path))
    assert config.git.sync_mode == "git"
    assert config.git.enabled is False
    assert config.livesync.enabled is False
    assert config.output.manifest_path == ""
