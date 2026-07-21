import os
import pytest
import yaml
from zhihu_pipeline.config import TaggerConfig
from zhihu_pipeline.storage import ManifestManager, generate_markdown
from zhihu_pipeline.tagger import (
    tag_single_file,
    call_lm_studio,
    sanitize_term,
    sanitize_metadata,
    ensure_code_blocks_fenced,
)


class DummyConfig:
    enabled = True
    backend = "local"
    lm_studio_url = "http://localhost:1234"
    model = "dummy-model"
    timeout = 10
    valid_domains = ["AI", "Engineering", "Life"]


# ============================================================
# Original tests (preserved)
# ============================================================

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
    assert "domain:\n- AI" in md
    assert "concept:\n- test-concept" in md
    assert "level: intermediate" in md
    assert "summary: 这是一句测试摘要。" in md
    assert "clippings" not in md


def test_manifest_manager_tagging_status(tmp_path):
    manifest_file = tmp_path / "manifest.json"
    manager = ManifestManager(str(manifest_file))

    manager.add_item("test_key", {"title": "Test"}, tagging_status="pending")
    data = manager.load()
    item = data["synced_items"]["test_key"]
    assert item["tagging_status"] == "pending"
    assert item["tagged_at"] is None

    untagged = manager.get_untagged_items()
    assert len(untagged) == 1
    assert untagged[0][0] == "test_key"

    manager.update_tagging_status("test_key", "tagged")
    untagged_after = manager.get_untagged_items()
    assert len(untagged_after) == 0

    updated_item = manager.load()["synced_items"]["test_key"]
    assert updated_item["tagging_status"] == "tagged"
    assert updated_item["tagged_at"] is not None


def test_tag_single_file_idempotency(tmp_path):
    file_path = tmp_path / "test.md"
    content = """\
---
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

    cfg = DummyConfig()
    success = tag_single_file(str(file_path), cfg)
    assert success is True


# ============================================================
# New tests: Layer 2 — sanitize_term
# ============================================================

class TestSanitizeTerm:
    def test_acronym_forced_uppercase(self):
        assert sanitize_term("rag") == "RAG"
        assert sanitize_term("llm") == "LLM"
        assert sanitize_term("bm25") == "BM25"
        assert sanitize_term("api") == "API"

    def test_acronym_case_insensitive(self):
        assert sanitize_term("Rag") == "RAG"
        assert sanitize_term("LLm") == "LLM"

    def test_proper_noun_capitalization(self):
        assert sanitize_term("python") == "Python"
        assert sanitize_term("docker") == "Docker"
        assert sanitize_term("chatgpt") == "ChatGPT"
        assert sanitize_term("deepseek") == "DeepSeek"

    def test_generic_term_kebab_case(self):
        assert sanitize_term("machine learning") == "machine-learning"
        assert sanitize_term("vibe coding") == "vibe-coding"
        assert sanitize_term("prompt engineering") == "prompt-engineering"

    def test_blacklist_terms_filtered(self):
        assert sanitize_term("zhihu") == ""
        assert sanitize_term("clippings") == ""
        assert sanitize_term("article") == ""
        assert sanitize_term("null") == ""

    def test_surrounding_junk_stripped(self):
        assert sanitize_term("'RAG'") == "RAG"
        assert sanitize_term('"python"') == "Python"
        assert sanitize_term("vibe-coding,") == "vibe-coding"
        assert sanitize_term("  llm  ") == "LLM"

    def test_empty_string_returns_empty(self):
        assert sanitize_term("") == ""
        assert sanitize_term("   ") == ""


# ============================================================
# New tests: Layer 2 — sanitize_metadata
# ============================================================

class TestSanitizeMetadata:
    def test_deduplication(self):
        metadata = {
            "concept": ["RAG", "rag", "Rag", "Python", "python"]
        }
        result = sanitize_metadata(metadata)
        assert result["concept"] == ["RAG", "Python"]

    def test_blacklist_removal(self):
        metadata = {
            "tags": ["zhihu", "clippings", "AI"],
            "concept": ["llm", "null", "Docker"]
        }
        result = sanitize_metadata(metadata)
        assert "zhihu" not in result["tags"]
        assert "clippings" not in result["tags"]
        assert "null" not in result["concept"]
        assert "LLM" in result["concept"]
        assert "Docker" in result["concept"]

    def test_mixed_input_normalized(self):
        metadata = {
            "concept": ["'rag'", "machine learning", "chatgpt"]
        }
        result = sanitize_metadata(metadata)
        assert "RAG" in result["concept"]
        assert "machine-learning" in result["concept"]
        assert "ChatGPT" in result["concept"]


# ============================================================
# New tests: Layer 3 — ensure_code_blocks_fenced
# ============================================================

class TestEnsureCodeBlocksFenced:
    def test_python3_normalized(self):
        body = "```python3\nprint('hello')\n```"
        result = ensure_code_blocks_fenced(body)
        assert "```python\n" in result
        assert "python3" not in result

    def test_python_uppercase_normalized(self):
        body = "```Python\nprint('hello')\n```"
        result = ensure_code_blocks_fenced(body)
        assert "```python\n" in result

    def test_js_normalized(self):
        body = "```js\nconsole.log('hi')\n```"
        result = ensure_code_blocks_fenced(body)
        assert "```javascript\n" in result

    def test_normal_code_block_unchanged(self):
        body = "```python\nprint('hello')\n```"
        result = ensure_code_blocks_fenced(body)
        assert result == body

    def test_no_code_block_unchanged(self):
        body = "这是一段普通文字，没有代码块。"
        result = ensure_code_blocks_fenced(body)
        assert result == body
