"""
Publish semantics of the sync engine.

The regression these tests pin down is the one that produced the 2026-10-05
incident: a delivery channel that was switched off still reported success, so
notes were consumed from the Zhihu inbox and existed on exactly one disk.
"""

import asyncio
import json
import os
from unittest.mock import patch

import pytest

from zhihu_pipeline.config import (
    ChromeConfig,
    Config,
    GitConfig,
    LiveSyncConfig,
    NotifyConfig,
    OutputConfig,
    SyncConfig,
    TaggerConfig,
    TelegramConfig,
)
from zhihu_pipeline.publish_probe import PublishVerification
from zhihu_pipeline.sync_engine import SyncEngine


def run(coro):
    return asyncio.run(coro)


def make_config(
    tmp_path,
    sync_mode: str = "livesync",
    git_enabled: bool = False,
    remove_after_sync: bool = True,
    auto_archive: bool = False,
    manifest_path: str = ".pipeline/manifest.json",
) -> Config:
    return Config(
        chrome=ChromeConfig(),
        telegram=TelegramConfig(enabled=False),
        notify=NotifyConfig(enabled=False),
        git=GitConfig(
            enabled=git_enabled,
            sync_mode=sync_mode,
            repo_url="https://example.invalid/notes.git" if git_enabled else "",
            user_name="tester",
            user_email="tester@example.com",
            auto_pull=git_enabled,
            auto_push=git_enabled,
        ),
        livesync=LiveSyncConfig(
            enabled=True,
            couchdb_url="http://127.0.0.1:5984",
            db_name="obsidian-vault",
            username="obsidian_admin",
            password="secret",
            node_name="zhihu-node",
        ),
        sync=SyncConfig(
            remove_after_sync=remove_after_sync,
            auto_archive=auto_archive,
            delay_min=0.0,
            delay_max=0.0,
            schedule_enabled=False,
        ),
        output=OutputConfig(
            vault_path=str(tmp_path),
            collection_dir="知乎收藏",
            manifest_path=manifest_path,
        ),
        tagger=TaggerConfig(enabled=False),
    )


def make_engine(tmp_path, **kwargs) -> SyncEngine:
    return SyncEngine(make_config(tmp_path, **kwargs))


class FakeContext:
    async def close(self):
        return None


def patch_browser(removal_log):
    """Replace the browser plumbing so _consume_inbox can be unit tested."""

    async def fake_context(self):
        return FakeContext()

    async def fake_page(context):
        return object()

    async def fake_login(page):
        return True, "tester"

    async def fake_remove(page, collection_title, item_type="answer", item_url=None):
        removal_log.append(collection_title)
        return True

    return (
        patch.object(SyncEngine, "get_browser_context", fake_context),
        patch("zhihu_pipeline.sync_engine.get_or_create_page", fake_page),
        patch("zhihu_pipeline.sync_engine.check_login", fake_login),
        patch("zhihu_pipeline.sync_engine.remove_from_collection", fake_remove),
    )


# ------------------------------------------------------------------- channels


def test_publish_channel_is_none_when_git_is_disabled(tmp_path):
    engine = make_engine(tmp_path, sync_mode="git", git_enabled=False)
    assert engine.publish_channel == "none"
    assert engine.uses_git() is False


def test_publish_channel_follows_configured_mode(tmp_path):
    assert make_engine(tmp_path, sync_mode="git", git_enabled=True).publish_channel == "git"
    assert make_engine(tmp_path, sync_mode="livesync").publish_channel == "livesync"
    assert make_engine(tmp_path, sync_mode="none").publish_channel == "none"


def test_livesync_mode_does_not_use_git(tmp_path):
    # The livesync switch must cover pull as well as push: pulling kept the
    # container coupled to a remote it no longer wrote to and dirtied the tree.
    engine = make_engine(tmp_path, sync_mode="livesync", git_enabled=True)
    assert engine.uses_git() is False


# --------------------------------------------------------------------- targets


def test_publish_targets_include_note_and_videos(tmp_path):
    engine = make_engine(tmp_path)
    item = {
        "local_path": "知乎收藏/我的收藏/2026-09-28 x.md",
        "video_paths": ["assets/知乎视频/2026-09-28 x/clip.mp4"],
    }
    assert engine._publish_targets(item) == [
        "知乎收藏/我的收藏/2026-09-28 x.md",
        "assets/知乎视频/2026-09-28 x/clip.mp4",
    ]


def test_inbox_action_respects_policy(tmp_path):
    engine = make_engine(tmp_path, remove_after_sync=True)
    engine._queue_inbox_action("answer_1", {"title": "T"}, "我的收藏")
    assert engine._pending_removals == [
        {
            "key": "answer_1",
            "action": "remove",
            "title": "T",
            "type": "",
            "url": "",
            "id": "",
            "collection": "我的收藏",
        }
    ]

    keep = make_engine(tmp_path, remove_after_sync=False, auto_archive=False)
    keep._queue_inbox_action("answer_1", {"title": "T"}, "我的收藏")
    assert keep._pending_removals == []


# ------------------------------------------------------------------- publishing


def test_livesync_confirmation_marks_published(tmp_path):
    engine = make_engine(tmp_path)
    engine.manifest.add_item(
        "answer_1",
        {"title": "T", "local_path": "知乎收藏/我的收藏/a.md", "collection": "我的收藏"},
        publish_status="pending",
    )
    engine._awaiting_delivery = {"answer_1": ["知乎收藏/我的收藏/a.md"]}
    engine._queue_inbox_action(
        "answer_1", {"title": "T", "type": "answer", "url": "u", "id": "1"}, "我的收藏"
    )

    async def fake_verify(paths, cursor_before=None):
        return PublishVerification(confirmed=list(paths))

    engine.prober.verify = fake_verify
    result = run(engine._publish_batch())

    assert result["published"] is True
    assert result["channel"] == "livesync"
    assert result["confirmed"] == {"answer_1"}
    assert engine.manifest.data["synced_items"]["answer_1"]["publish_status"] == "published"

    removals = []
    ctx, page, login, remove = patch_browser(removals)
    with ctx, page, login, remove:
        consumed, failed = run(engine._consume_inbox(result["confirmed"]))
    assert (consumed, failed) == (1, 0)
    assert removals == ["我的收藏"]


def test_unconfirmed_delivery_never_consumes_the_inbox(tmp_path):
    engine = make_engine(tmp_path)
    engine.manifest.add_item(
        "answer_1",
        {"title": "T", "local_path": "知乎收藏/我的收藏/a.md", "collection": "我的收藏"},
        publish_status="pending",
    )
    engine._awaiting_delivery = {"answer_1": ["知乎收藏/我的收藏/a.md"]}
    engine._queue_inbox_action(
        "answer_1", {"title": "T", "type": "answer", "url": "u", "id": "1"}, "我的收藏"
    )

    async def fake_verify(paths, cursor_before=None):
        # The node is silent: nothing arrived, but the database is reachable.
        return PublishVerification(missing=list(paths))

    engine.prober.verify = fake_verify
    result = run(engine._publish_batch())

    assert result["published"] is False
    assert result["missing"] == ["知乎收藏/我的收藏/a.md"]
    assert engine.manifest.data["synced_items"]["answer_1"]["publish_status"] == "pending"

    removals = []
    ctx, page, login, remove = patch_browser(removals)
    with ctx, page, login, remove:
        consumed, failed = run(engine._consume_inbox(result["confirmed"]))
    # The note stays in the Zhihu collection so a later run can retry delivery.
    assert (consumed, failed) == (0, 0)
    assert removals == []


def test_probe_error_marks_items_unverified(tmp_path):
    engine = make_engine(tmp_path)
    engine.manifest.add_item(
        "answer_1",
        {"title": "T", "local_path": "知乎收藏/我的收藏/a.md"},
        publish_status="pending",
    )
    engine._awaiting_delivery = {"answer_1": ["知乎收藏/我的收藏/a.md"]}

    async def fake_verify(paths, cursor_before=None):
        return PublishVerification(missing=list(paths), error="CouchDB rejected the credentials")

    engine.prober.verify = fake_verify
    result = run(engine._publish_batch())

    assert result["published"] is False
    assert "credentials" in result["error"]
    assert engine.manifest.data["synced_items"]["answer_1"]["publish_status"] == "unverified"


def test_indirect_proof_does_not_publish_or_consume(tmp_path):
    engine = make_engine(tmp_path)
    engine.manifest.add_item(
        "answer_1",
        {"title": "T", "local_path": "知乎收藏/我的收藏/a.md"},
        publish_status="pending",
    )
    engine._awaiting_delivery = {"answer_1": ["知乎收藏/我的收藏/a.md"]}
    engine._queue_inbox_action(
        "answer_1", {"title": "T", "type": "answer", "url": "u", "id": "1"}, "我的收藏"
    )

    async def fake_verify(paths, cursor_before=None):
        return PublishVerification(confirmed=list(paths), indirect=True)

    engine.prober.verify = fake_verify
    result = run(engine._publish_batch())
    removals = []
    ctx, page, login, remove = patch_browser(removals)
    with ctx, page, login, remove:
        consumed, _ = run(engine._consume_inbox(result["confirmed"]))
    # A cursor that advanced proves replication happened, not that this note
    # arrived, so the inbox entry must survive.
    assert result["indirect"] is True
    assert result["published"] is False
    assert result["confirmed"] == set()
    assert engine.manifest.data["synced_items"]["answer_1"]["publish_status"] == "unverified"
    assert consumed == 0 and removals == []


def test_git_mode_marks_published_only_after_successful_push(tmp_path):
    engine = make_engine(tmp_path, sync_mode="git", git_enabled=True)
    engine.manifest.add_item(
        "answer_1",
        {"title": "T", "local_path": "知乎收藏/我的收藏/a.md"},
        publish_status="pending",
    )
    engine._awaiting_delivery = {"answer_1": ["知乎收藏/我的收藏/a.md"]}

    with patch("zhihu_pipeline.sync_engine.git_push", return_value=False):
        result = run(engine._publish_batch())
    assert result["published"] is False
    assert result["channel"] == "git"
    assert "push failed" in result["error"]
    # A channel that answered "no" is recorded as failed, not merely unverified.
    assert engine.manifest.data["synced_items"]["answer_1"]["publish_status"] == "failed"

    with patch("zhihu_pipeline.sync_engine.git_push", return_value=True):
        # reset state for the success path
        engine.manifest.update_publish_status("answer_1", "pending")
        result = run(engine._publish_batch())
    assert result["published"] is True
    assert engine.manifest.data["synced_items"]["answer_1"]["publish_status"] == "published"


def test_mode_none_reports_no_channel_and_publishes_nothing(tmp_path):
    engine = make_engine(tmp_path, sync_mode="none")
    engine.manifest.add_item(
        "answer_1",
        {"title": "T", "local_path": "知乎收藏/我的收藏/a.md"},
        publish_status="pending",
    )
    engine._awaiting_delivery = {"answer_1": ["知乎收藏/我的收藏/a.md"]}
    result = run(engine._publish_batch())
    assert result["published"] is False
    assert result["channel"] == "none"
    assert "no delivery channel" in result["error"]


# ---------------------------------------------------------------- manifest file


def test_manifest_migrates_out_of_the_synced_tree(tmp_path):
    legacy_dir = tmp_path / "知乎收藏"
    legacy_dir.mkdir(parents=True)
    legacy = legacy_dir / "manifest.json"
    legacy.write_text(
        json.dumps({"version": 1, "last_sync": "x", "synced_items": {"answer_1": {"title": "T"}}}),
        encoding="utf-8",
    )

    engine = make_engine(tmp_path)
    assert engine.manifest_path == str(tmp_path / ".pipeline" / "manifest.json")
    assert not legacy.exists()
    # The migrated content must survive: an empty manifest would make the
    # pipeline forget every article it already downloaded.
    assert "answer_1" in engine.manifest.data["synced_items"]


def test_manifest_path_cannot_escape_the_vault(tmp_path):
    engine = make_engine(tmp_path, manifest_path="../../outside/manifest.json")
    assert engine.manifest_path.startswith(str(tmp_path))


def test_publish_helpers_round_trip(tmp_path):
    engine = make_engine(tmp_path)
    engine.manifest.add_item("answer_1", {"title": "T"}, publish_status="pending")
    engine.manifest.add_item("answer_2", {"title": "U"}, publish_status="published")

    assert engine.manifest.get_stats()["total_unpublished"] == 1
    pending_keys = {key for key, _ in engine.manifest.get_publish_pending_items()}
    assert pending_keys == {"answer_1"}

    engine.manifest.update_publish_status("answer_1", "published")
    assert engine.manifest.get_stats()["total_unpublished"] == 0


# --------------------------------------------------------------- legacy backlog


def test_legacy_backlog_is_verified_and_cleared(tmp_path):
    """Entries recorded before publish tracking existed must not linger forever."""
    engine = make_engine(tmp_path, sync_mode="git", git_enabled=True)
    note_dir = tmp_path / "知乎收藏" / "我的收藏"
    note_dir.mkdir(parents=True)
    (note_dir / "legacy.md").write_text("# legacy\n", encoding="utf-8")
    engine.manifest.add_item(
        "answer_1",
        {"title": "T", "local_path": "知乎收藏/我的收藏/legacy.md"},
        publish_status="pending",
    )
    assert engine._awaiting_delivery == {}

    with patch("zhihu_pipeline.sync_engine.git_push", return_value=True):
        result = run(engine._publish_batch())

    assert result["published"] is True
    assert result["confirmed"] == {"answer_1"}
    assert engine.manifest.data["synced_items"]["answer_1"]["publish_status"] == "published"
    assert engine.manifest.get_stats()["total_unpublished"] == 0


def test_backlog_entries_whose_files_are_missing_are_left_alone(tmp_path):
    engine = make_engine(tmp_path, sync_mode="git", git_enabled=True)
    engine.manifest.add_item(
        "answer_404",
        {"title": "Gone", "local_path": "知乎收藏/我的收藏/does-not-exist.md"},
        publish_status="pending",
    )

    with patch("zhihu_pipeline.sync_engine.git_push", return_value=True):
        result = run(engine._publish_batch())

    # Nothing on disk to deliver, so the entry is not silently marked published.
    assert result["published"] is None
    assert "answer_404" not in result["confirmed"]
    assert engine.manifest.get_stats()["total_unpublished"] == 1
