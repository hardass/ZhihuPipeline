import json

import pytest

from zhihu_pipeline.storage import ManifestManager


def test_corrupt_manifest_fails_closed(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text('{"synced_items": ', encoding="utf-8")

    with pytest.raises(RuntimeError, match="Manifest is invalid"):
        ManifestManager(str(manifest_path))

    # The guard must not replace the corrupt file with an empty manifest.
    assert manifest_path.read_text(encoding="utf-8") == '{"synced_items": '


def test_manifest_save_is_valid_json(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manager = ManifestManager(str(manifest_path))
    manager.add_item("answer_1", {"title": "Test"})

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["synced_items"]["answer_1"]["tagging_status"] == "pending"
    assert not list(tmp_path.glob(".manifest.*.tmp"))
