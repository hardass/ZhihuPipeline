import os
import pytest
import yaml
from zhihu_pipeline.config import TaggerConfig
from zhihu_pipeline.storage import ManifestManager, generate_markdown
from zhihu_pipeline.tagger import tag_single_file, call_lm_studio

class DummyConfig:
    enabled = True
    backend = "local"
    lm_studio_url = "http://localhost:1234"
    model = "dummy-model"
    timeout = 10
    valid_domains = ["AI", "Engineering", "Life"]

def test_tagger_config_defaults():
    cfg = TaggerConfig()
    assert cfg.enabled is False
    assert cfg.backend == "local"
    assert "AI" in cfg.valid_domains

def test_generate_markdown_with_tagging_metadata():
    content = {
        "title": "测试文章",
        "zhihu_url": "https://zhuanlan.zhihu.com/p/123",
        "author_name": "测试作者",
        "content_markdown": "文章正文内容。",
        "domain": ["AI"],
        "concept": ["test-concept"],
        "level": "intermediate",
        "summary": "这是一句测试摘要。"
    }
    md = generate_markdown(content)
    # Check frontmatter block
    assert "domain:\n- AI" in md
    assert "concept:\n- test-concept" in md
    assert "level: intermediate" in md
    assert "summary: 这是一句测试摘要。" in md
    assert "clippings" not in md  # default tags only has "zhihu" now

def test_manifest_manager_tagging_status(tmp_path):
    manifest_file = tmp_path / "manifest.json"
    manager = ManifestManager(str(manifest_file))
    
    # Test adding item with default pending
    manager.add_item("test_key", {"title": "Test"}, tagging_status="pending")
    data = manager.load()
    item = data["synced_items"]["test_key"]
    assert item["tagging_status"] == "pending"
    assert item["tagged_at"] is None
    
    # Test get untagged items
    untagged = manager.get_untagged_items()
    assert len(untagged) == 1
    assert untagged[0][0] == "test_key"
    
    # Test update status to tagged
    manager.update_tagging_status("test_key", "tagged")
    untagged_after = manager.get_untagged_items()
    assert len(untagged_after) == 0
    
    updated_item = manager.load()["synced_items"]["test_key"]
    assert updated_item["tagging_status"] == "tagged"
    assert updated_item["tagged_at"] is not None

def test_tag_single_file_idempotency(tmp_path):
    file_path = tmp_path / "test.md"
    # Pre-existing tagged file content
    content = """---
title: Existing Tagged
domain:
- AI
concept:
- c1
level: advanced
summary: Done
tags:
- zhihu
---

Body content
"""
    file_path.write_text(content, encoding="utf-8")
    
    # Run tagger (should not call call_lm_studio as it's already tagged)
    cfg = DummyConfig()
    success = tag_single_file(str(file_path), cfg)
    assert success is True
