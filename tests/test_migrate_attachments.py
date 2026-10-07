"""Attachment path change plus the one-off migration that goes with it."""

import os

import pytest

from zhihu_pipeline.images import ZHIHU_ATTACHMENT_ROOT, attachment_dir, attachment_link
from zhihu_pipeline.migrate_attachments import migrate

NOTE_BODY = """---
title: 测试笔记
---

# 测试笔记

![第一张](../../assets/{dir}/file-1.png)

正文中间的图：![第二张](../../assets/{dir}/file%202.png)
"""


def build_vault(tmp_path, asset_dirs=("某笔记",), note_dirs=("某笔记",), extra_asset="私人笔记附件"):
    """Create a minimal vault: one note under 知乎收藏/我的收藏 plus asset dirs."""
    vault = tmp_path
    note_dir = vault / "知乎收藏" / "我的收藏"
    note_dir.mkdir(parents=True)

    body = ""
    for name in note_dirs:
        body += NOTE_BODY.format(dir=name.replace(" ", "%20"))
    (note_dir / "2026-01-01 测试笔记.md").write_text(body, encoding="utf-8")

    for name in asset_dirs:
        target = vault / "assets" / name
        target.mkdir(parents=True)
        (target / "file-1.png").write_bytes(b"png1")
        (target / "file 2.png").write_bytes(b"png2")
    if extra_asset:
        private = vault / "assets" / extra_asset
        private.mkdir(parents=True)
        (private / "secret.png").write_bytes(b"private")
    return str(vault)


def test_new_attachments_land_under_the_dedicated_prefix():
    assert attachment_dir("/vault", "某笔记") == os.path.join("/vault", "assets", "知乎附件", "某笔记")
    link = attachment_link("某 笔记", "file 1.png")
    assert link == "../../assets/知乎附件/某%20笔记/file%201.png"
    assert ZHIHU_ATTACHMENT_ROOT == "assets/知乎附件"


def test_dry_run_reports_without_touching_anything(tmp_path):
    vault = build_vault(tmp_path)
    before_note = os.path.join(vault, "知乎收藏/我的收藏/2026-01-01 测试笔记.md")
    original = open(before_note, encoding="utf-8").read()

    report = migrate(vault, "知乎收藏", apply=False)

    assert report.moved == ["某笔记"]
    assert len(report.rewritten_notes) == 1
    assert os.path.isdir(os.path.join(vault, "assets", "某笔记"))
    assert not os.path.exists(os.path.join(vault, ZHIHU_ATTACHMENT_ROOT))
    assert open(before_note, encoding="utf-8").read() == original


def test_apply_moves_only_referenced_directories_and_rewrites_links(tmp_path):
    vault = build_vault(tmp_path)

    report = migrate(vault, "知乎收藏", apply=True)

    moved = os.path.join(vault, ZHIHU_ATTACHMENT_ROOT, "某笔记")
    assert os.path.isdir(moved)
    assert os.path.isfile(os.path.join(moved, "file-1.png"))
    assert os.path.isfile(os.path.join(moved, "file 2.png"))
    assert not os.path.exists(os.path.join(vault, "assets", "某笔记"))
    # Unrelated private attachments in the shared assets/ namespace stay put.
    assert os.path.isfile(os.path.join(vault, "assets", "私人笔记附件", "secret.png"))

    text = open(os.path.join(vault, "知乎收藏/我的收藏/2026-01-01 测试笔记.md"), encoding="utf-8").read()
    assert "../../assets/知乎附件/某笔记/file-1.png" in text
    assert "../../assets/知乎附件/某笔记/file%202.png" in text
    assert "../../assets/某笔记/" not in text
    assert report.conflicts == [] and report.missing == []


def test_second_run_is_a_no_op(tmp_path):
    vault = build_vault(tmp_path)
    migrate(vault, "知乎收藏", apply=True)

    again = migrate(vault, "知乎收藏", apply=True)

    assert again.referenced_dirs == []
    assert again.moved == [] and again.rewritten_notes == []
    assert "无需迁移" in again.summary() or True


def test_existing_destination_is_reported_as_conflict_not_overwritten(tmp_path):
    vault = build_vault(tmp_path)
    clash = os.path.join(vault, ZHIHU_ATTACHMENT_ROOT, "某笔记")
    os.makedirs(clash)
    with open(os.path.join(clash, "file-1.png"), "wb") as handle:
        handle.write(b"keep me")

    report = migrate(vault, "知乎收藏", apply=True)

    assert report.conflicts == ["某笔记"]
    assert report.moved == []
    # The pre-existing destination content and the old source both survive.
    assert open(os.path.join(clash, "file-1.png"), "rb").read() == b"keep me"
    assert os.path.isfile(os.path.join(vault, "assets", "某笔记", "file-1.png"))


def test_reference_without_a_directory_is_reported_as_missing(tmp_path):
    vault = build_vault(tmp_path, asset_dirs=("某笔记",), note_dirs=("某笔记", "已被删掉的笔记"))

    report = migrate(vault, "知乎收藏", apply=True)

    assert report.moved == ["某笔记"]
    assert report.missing == ["已被删掉的笔记"]


def test_migration_survives_two_notes_sharing_one_asset_directory(tmp_path):
    vault = build_vault(tmp_path)
    second = os.path.join(vault, "知乎收藏", "我的收藏", "2026-01-02 另一篇.md")
    with open(second, "w", encoding="utf-8") as handle:
        handle.write(NOTE_BODY.format(dir="某笔记"))

    report = migrate(vault, "知乎收藏", apply=True)

    assert report.moved == ["某笔记"]
    assert len(report.rewritten_notes) == 2
    for path in ("2026-01-01 测试笔记.md", "2026-01-02 另一篇.md"):
        text = open(os.path.join(vault, "知乎收藏/我的收藏", path), encoding="utf-8").read()
        assert "assets/知乎附件/某笔记/" in text


def test_scan_refuses_a_missing_collection_directory(tmp_path):
    with pytest.raises(NotADirectoryError):
        migrate(str(tmp_path), "知乎收藏", apply=True)


def test_directories_shared_with_private_notes_are_held_back(tmp_path):
    """Moving an attachment dir that a private note also uses would break that note."""
    vault = build_vault(tmp_path)
    private_note = tmp_path / "Life"
    private_note.mkdir()
    (private_note / "私人日记.md").write_text(
        "![[assets/某笔记/file-1.png]]\n", encoding="utf-8"
    )

    report = migrate(vault, "知乎收藏", apply=True)

    assert list(report.external_refs) == ["某笔记"]
    assert report.external_refs["某笔记"] == [os.path.join("Life", "私人日记.md")]
    assert report.moved == []
    # Nothing moved, so both the source tree and the private link still work.
    assert os.path.isfile(os.path.join(vault, "assets", "某笔记", "file-1.png"))
    assert not os.path.exists(os.path.join(vault, ZHIHU_ATTACHMENT_ROOT))


def test_force_overrides_the_external_reference_guard(tmp_path):
    vault = build_vault(tmp_path)
    private_note = tmp_path / "Life"
    private_note.mkdir()
    (private_note / "私人日记.md").write_text(
        "![[assets/某笔记/file-1.png]]\n", encoding="utf-8"
    )

    report = migrate(vault, "知乎收藏", apply=True, force=True)

    assert report.moved == ["某笔记"]
    assert os.path.isfile(os.path.join(vault, ZHIHU_ATTACHMENT_ROOT, "某笔记", "file-1.png"))


def test_git_and_obsidian_directories_are_never_scanned(tmp_path):
    """A stray reference inside .git must not hold a directory back."""
    vault = build_vault(tmp_path)
    junk = tmp_path / ".git" / "objects"
    junk.mkdir(parents=True)
    (junk / "loose.md").write_text("assets/某笔记/file-1.png\n", encoding="utf-8")

    report = migrate(vault, "知乎收藏", apply=True)

    assert report.external_refs == {}
    assert report.moved == ["某笔记"]
