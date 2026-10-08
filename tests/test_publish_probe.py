"""Delivery verification against CouchDB must fail closed, never optimistically."""

import asyncio
import time

from zhihu_pipeline.config import LiveSyncConfig
from zhihu_pipeline.publish_probe import (
    MILESTONE_DOC,
    NodeStatus,
    PublishProbeError,
    PublishProber,
)


def run(coro):
    return asyncio.run(coro)


def make_config(**overrides) -> LiveSyncConfig:
    base = dict(
        enabled=True,
        couchdb_url="http://127.0.0.1:5984",
        db_name="obsidian-vault",
        username="obsidian_admin",
        password="secret",
        node_name="zhihu-node",
        timeout=1.0,
        verify_timeout_seconds=0.0,
        verify_poll_interval_seconds=1.0,
        node_max_stale_minutes=30.0,
    )
    base.update(overrides)
    return LiveSyncConfig(**base)


class FakeResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


def fresh_ms() -> int:
    return int(time.time() * 1000) - 60_000


def milestone(nodes: dict) -> FakeResponse:
    return FakeResponse(200, {"node_info": nodes})


def build_prober(responder):
    """responder(url, method, json_body) -> FakeResponse | Exception"""
    prober = PublishProber(make_config())

    async def _fake_request(url, method="GET", json_body=None):
        result = responder(url, method, json_body)
        if isinstance(result, Exception):
            raise result
        return result

    prober._request = _fake_request
    return prober


def all_docs_rows(keys, present):
    """Mimic POST /_all_docs?keys=...: missing keys come back as error rows."""
    rows = [
        {"id": k, "key": k, "value": {"rev": "1-abc"}} if k in present else {"key": k, "error": "not_found"}
        for k in keys
    ]
    return FakeResponse(200, {"total_rows": len(present), "offset": 0, "rows": rows})


def test_a_tombstone_is_not_delivery():
    """Deleted rows carry an id and no error; counting them would approve deletions."""
    prober = build_prober(
        lambda url, method, body=None: (
            milestone({"n1": {"device_name": "zhihu-node", "last_connected": fresh_ms()}})
            if MILESTONE_DOC in url
            else FakeResponse(200, {"rows": [
                {"id": "知乎收藏/a.md", "key": "知乎收藏/a.md",
                 "value": {"rev": "3-def", "deleted": True}}
            ]})
        )
    )
    result = run(prober.verify(["知乎收藏/a.md"]))
    assert not result.delivered and result.missing == ["知乎收藏/a.md"]



def test_unconfigured_prober_fails_closed():
    prober = PublishProber(make_config(password=""))
    result = run(prober.verify(["知乎收藏/a.md"]))
    assert result.error
    assert "not configured" in result.error
    assert result.missing == ["知乎收藏/a.md"]
    assert not result.delivered


def test_stale_node_blocks_verification_even_if_docs_exist():
    prober = build_prober(
        lambda url, method, body=None: (
            milestone({
                "n1": {
                    "device_name": "zhihu-node",
                    "last_connected": fresh_ms() - 60 * 60 * 1000,  # 1h old
                    "progress": "67300-x",
                }
            })
            if MILESTONE_DOC in url
            else FakeResponse(200)
        )
    )
    result = run(prober.verify(["知乎收藏/a.md"]))
    assert result.error and "last replicated" in result.error
    assert not result.delivered and result.missing == ["知乎收藏/a.md"]


def test_missing_node_is_treated_as_dead_channel():
    prober = build_prober(
        lambda url, method, body=None: (
            milestone({"other": {"device_name": "macbook", "last_connected": fresh_ms()}})
            if MILESTONE_DOC in url
            else FakeResponse(200)
        )
    )
    result = run(prober.verify(["知乎收藏/a.md"]))
    assert result.error and "not registered" in result.error


def test_document_present_reports_delivered():
    prober = build_prober(
        lambda url, method, body=None: (
            milestone({"n1": {"device_name": "zhihu-node", "last_connected": fresh_ms(),
                              "progress": "67300-x"}})
            if MILESTONE_DOC in url
            else all_docs_rows(body["keys"], {"知乎收藏/a.md"})
        )
    )
    result = run(prober.verify(["知乎收藏/a.md"]))
    assert result.delivered and result.safe_to_consume
    assert result.confirmed == ["知乎收藏/a.md"] and not result.missing


def test_document_absent_is_reported_as_missing():
    prober = build_prober(
        lambda url, method, body=None: (
            milestone({"n1": {"device_name": "zhihu-node", "last_connected": fresh_ms()}})
            if MILESTONE_DOC in url
            else all_docs_rows(body["keys"], set())
        )
    )
    result = run(prober.verify(["知乎收藏/a.md"]))
    assert not result.delivered and not result.safe_to_consume
    assert result.missing == ["知乎收藏/a.md"] and result.error is None


def test_partial_delivery_only_confirms_the_documents_that_arrived():
    prober = build_prober(
        lambda url, method, body=None: (
            milestone({"n1": {"device_name": "zhihu-node", "last_connected": fresh_ms()}})
            if MILESTONE_DOC in url
            else all_docs_rows(body["keys"], {"知乎收藏/a.md"})
        )
    )
    result = run(prober.verify(["知乎收藏/a.md", "知乎收藏/b.md"]))
    assert result.confirmed == ["知乎收藏/a.md"]
    assert result.missing == ["知乎收藏/b.md"]
    assert not result.safe_to_consume


def test_unreachable_couchdb_is_an_error_not_an_empty_database():
    prober = build_prober(
        lambda url, method, body=None: (
            PublishProbeError("Cannot reach CouchDB: connect timeout")
        )
    )
    result = run(prober.verify(["知乎收藏/a.md"]))
    assert result.error and "Cannot reach CouchDB" in result.error
    assert not result.delivered


def test_existence_is_checked_by_document_id_not_by_url_path():
    """
    Regression: GET /{db}/{quoted path} answers 404 for ids that _all_docs returns
    with a revision, so the URL form reported every delivered note as missing.
    """
    seen = []

    prober = build_prober(
        lambda url, method, body=None: (
            seen.append((url, method, body)) or (
                milestone({"n1": {"device_name": "zhihu-node", "last_connected": fresh_ms()}})
                if MILESTONE_DOC in url
                else all_docs_rows(body["keys"], {"知乎收藏/我的收藏/2026-09-28 Jev 新玩法.md"})
            )
        )
    )
    path = "知乎收藏/我的收藏/2026-09-28 Jev 新玩法.md"
    assert run(prober.doc_exists(path)) is True
    posts = [call for call in seen if call[1] == "POST"]
    assert posts and posts[0][0].endswith("/obsidian-vault/_all_docs")
    assert posts[0][2] == {"keys": [path]}
    assert not any("%20" in call[0] for call in seen)


def test_freshest_identity_wins_when_a_cli_node_re_registers():
    """A CLI node keeps its device_name but gets a new id on every start."""
    prober = build_prober(
        lambda url, method, body=None: milestone({
            "old1": {"device_name": "zhihu-node", "last_connected": fresh_ms() - 9 * 60_000},
            "live1": {"device_name": "zhihu-node", "last_connected": fresh_ms()},
        })
    )
    node = run(prober.node_status())
    assert node.node_id == "live1"
    fresh, reason = run(prober.node_is_fresh())
    assert fresh, reason


def test_path_obfuscation_only_proves_indirectly_and_blocks_consumption():
    prober = PublishProber(make_config(use_path_obfuscation=True))

    async def _fake_request(url, method="GET", json_body=None):
        if MILESTONE_DOC in url:
            return milestone({
                "n1": {"device_name": "zhihu-node", "last_connected": fresh_ms(),
                       "progress": "67400-x"}
            })
        return FakeResponse(200, {"update_seq": "67400-z"})

    prober._request = _fake_request
    result = run(prober.verify(["知乎收藏/a.md"], cursor_before=67295))
    assert result.delivered
    assert result.indirect
    # Cursor-only proof must never justify deleting the Zhihu source.
    assert not result.safe_to_consume


def test_obfuscated_cursor_that_has_not_advanced_is_a_failure():
    prober = PublishProber(make_config(use_path_obfuscation=True))

    async def _fake_request(url, method="GET", json_body=None):
        if MILESTONE_DOC in url:
            return milestone({
                "n1": {"device_name": "zhihu-node", "last_connected": fresh_ms(),
                       "progress": "67000-x"}
            })
        return FakeResponse(200, {"update_seq": "67400-z"})

    prober._request = _fake_request
    result = run(prober.verify(["知乎收藏/a.md"], cursor_before=67295))
    assert not result.delivered and result.error


def test_lookups_are_batched_and_paths_normalised():
    """`./notes/x.md` and `notes/x.md` are the same document; >100 paths need batches."""
    bodies = []

    def responder(url, method, body=None):
        if MILESTONE_DOC in url:
            return milestone({"n1": {"device_name": "zhihu-node", "last_connected": fresh_ms()}})
        bodies.append(body["keys"])
        return all_docs_rows(body["keys"], set(body["keys"]))

    prober = build_prober(responder)
    paths = [f"知乎收藏/我的收藏/n{i}.md" for i in range(140)]
    missing = run(prober.missing_docs(["./" + paths[0]] + paths[1:]))
    assert missing == []
    assert [len(b) for b in bodies] == [100, 40]
    assert bodies[0][0] == paths[0]


def test_node_status_cursor_and_age_parsing():
    node = NodeStatus(node_id="n1", device_name="z", progress="67300-g1AAAA", last_connected_ms=fresh_ms())
    assert node.cursor == 67300
    assert 59 < node.age_seconds() < 62
    assert NodeStatus(node_id="n1", progress="").cursor == 0
    assert NodeStatus(node_id="n1").age_seconds() == float("inf")
