"""
Read-only delivery verification against a Self-hosted LiveSync CouchDB backend.

Why this exists
---------------
The pipeline does not speak the LiveSync protocol and must not try to: chunk
encoding, ``h:`` index documents and E2EE are private to the plugin and change
between releases. A separate headless LiveSync node mirrors the vault directory
into CouchDB, and that node can die quietly while every file write still looks
successful locally.

Before this pipeline consumed its inbox it simply assumed "files written" meant
"delivered", which is how a stopped GitHub push turned into two days of notes
that existed on exactly one disk. This module is the check that was missing:
the pipeline asks the database whether the documents arrived, and treats any
doubt as "not published".

Fail-closed rules
-----------------
* connection errors, authentication failures and 5xx are raised, never
  swallowed - an unreachable database is not an empty database;
* a document that is still missing after the verification window is reported
  as missing, which keeps the note in the Zhihu collection;
* a node whose replication heartbeat is older than ``node_max_stale_minutes``
  marks the whole channel unhealthy even if some documents happen to exist.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence
from urllib.parse import quote

import httpx
from loguru import logger

from .config import LiveSyncConfig

MILESTONE_DOC = "_local/obsydian_livesync_milestone"


class PublishProbeError(RuntimeError):
    """The backend could not be consulted at all (not the same as 'missing')."""


@dataclass
class NodeStatus:
    """One entry of ``_local/obsydian_livesync_milestone.node_info``."""

    node_id: str
    device_name: str = ""
    vault_name: str = ""
    plugin_version: str = ""
    last_connected_ms: int = 0
    progress: str = ""

    @property
    def cursor(self) -> int:
        """Numeric part of the replication cursor, or 0 when unreadable."""
        try:
            return int(str(self.progress).split("-", 1)[0])
        except (TypeError, ValueError):
            return 0

    def age_seconds(self, now_ms: Optional[int] = None) -> float:
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        if not self.last_connected_ms:
            return float("inf")
        return max(0.0, (now - self.last_connected_ms) / 1000.0)


@dataclass
class PublishVerification:
    """Outcome of a delivery check for a batch of vault-relative paths."""

    confirmed: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    error: Optional[str] = None
    # True when nothing was proven per path (obfuscated ids or probe disabled)
    # and the verdict rests on the replication cursor only.
    indirect: bool = False

    @property
    def delivered(self) -> bool:
        """Every path is proven present, and the probe reported no error."""
        return not self.error and not self.missing and bool(self.confirmed)

    @property
    def safe_to_consume(self) -> bool:
        """
        Whether the caller may delete these notes' sources from Zhihu.

        Indirect (cursor-only) proof is deliberately excluded: it cannot show
        that a specific note arrived, and deleting the inbox entry on that basis
        is exactly how a silent node turns into permanently lost notes.
        """
        return self.delivered and not self.indirect


class PublishProber:
    """
    Talks to CouchDB with plain HTTP GET/HEAD requests.

    Credentials are expected to come from the environment
    (``LIVESYNC_COUCHDB_USER`` / ``LIVESYNC_COUCHDB_PASSWORD``) and are never
    logged. Document ids are the vault-relative paths, which stay readable even
    with end-to-end encryption enabled - E2EE encrypts contents and chunk ids,
    not path keys. If ``use_path_obfuscation`` is turned on, that assumption
    breaks and the prober degrades to cursor-based, clearly-marked indirect
    verification.
    """

    def __init__(self, config: LiveSyncConfig, client: Optional[httpx.AsyncClient] = None):
        self.config = config
        self._client = client
        self._owns_client = client is None

    # ------------------------------------------------------------------ setup

    @property
    def configured(self) -> bool:
        return bool(
            self.config.enabled
            and self.config.couchdb_url
            and self.config.db_name
            and self.config.username
            and self.config.password
        )

    def _base(self) -> str:
        return f"{self.config.couchdb_url.rstrip('/')}/{quote(self.config.db_name)}"

    def _doc_url(self, vault_path: str) -> str:
        cleaned = str(vault_path).lstrip("./").lstrip("/")
        return f"{self._base()}/{quote(cleaned)}"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.config.timeout,
                auth=(self.config.username, self.config.password),
            )
        return self._client

    async def aclose(self):
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(self, url: str, method: str = "GET") -> httpx.Response:
        client = await self._get_client()
        try:
            res = await client.request(method, url)
        except httpx.HTTPError as exc:
            # An unreachable or misbehaving database proves nothing. Never map
            # it onto "the note was not published" silently, and never onto
            # "it was published" either.
            raise PublishProbeError(f"Cannot reach CouchDB: {type(exc).__name__}: {exc}") from exc
        if res.status_code in (401, 403):
            raise PublishProbeError(
                f"CouchDB rejected the configured credentials (HTTP {res.status_code})"
            )
        if res.status_code >= 500:
            raise PublishProbeError(f"CouchDB returned HTTP {res.status_code}")
        return res

    # ------------------------------------------------------------ db metadata

    async def database_cursor(self) -> int:
        """Numeric ``update_seq`` of the target database."""
        res = await self._request(self._base())
        if res.status_code == 404:
            raise PublishProbeError(f"LiveSync database '{self.config.db_name}' does not exist")
        try:
            payload = res.json()
            return int(str(payload.get("update_seq", "")).split("-", 1)[0])
        except (ValueError, TypeError) as exc:
            raise PublishProbeError(f"Cannot parse database update_seq: {exc}") from exc

    async def node_statuses(self) -> list[NodeStatus]:
        """All peers registered in the milestone document (may be empty)."""
        res = await self._request(f"{self._base()}/{MILESTONE_DOC}")
        if res.status_code == 404:
            return []
        try:
            payload = res.json()
        except ValueError as exc:
            raise PublishProbeError(f"Milestone document is not valid JSON: {exc}") from exc
        nodes: list[NodeStatus] = []
        for node_id, info in (payload.get("node_info") or {}).items():
            if not isinstance(info, dict):
                continue
            nodes.append(
                NodeStatus(
                    node_id=node_id,
                    device_name=str(info.get("device_name", "")),
                    vault_name=str(info.get("vault_name", "")),
                    plugin_version=str(info.get("plugin_version", "")),
                    last_connected_ms=int(info.get("last_connected", 0) or 0),
                    progress=str(info.get("progress", "")),
                )
            )
        return nodes

    async def node_status(self) -> Optional[NodeStatus]:
        """
        The node this pipeline depends on, selected by ``node_name``.

        Returns ``None`` when no name is configured: the heartbeat gate is then
        simply not applied, and per-document existence remains the judge.
        """
        if not self.config.node_name:
            return None
        for node in await self.node_statuses():
            if node.device_name == self.config.node_name or node.node_id == self.config.node_name:
                return node
        return None

    async def node_is_fresh(self) -> tuple[bool, str]:
        """(alive, reason). A configured-but-absent or stale node is a failure."""
        node = await self.node_status()
        if node is None:
            if not self.config.node_name:
                return True, "no node_name configured; relying on document checks"
            return False, f"live node '{self.config.node_name}' is not registered in the milestone document"
        max_age = self.config.node_max_stale_minutes * 60.0
        age = node.age_seconds()
        if age > max_age:
            return False, (
                f"node '{node.device_name}' last replicated {age / 60.0:.1f} min ago "
                f"(limit {self.config.node_max_stale_minutes:.0f} min)"
            )
        return True, f"node '{node.device_name}' heartbeat {age / 60.0:.1f} min ago"

    # ---------------------------------------------------------- path existence

    async def doc_exists(self, vault_path: str) -> bool:
        res = await self._request(self._doc_url(vault_path), method="HEAD")
        if res.status_code == 405:
            # Some reverse proxies only forward GET; a bounded GET is equivalent.
            res = await self._request(f"{self._doc_url(vault_path)}?limit=1")
        return res.status_code == 200

    async def missing_docs(self, vault_paths: Sequence[str]) -> list[str]:
        missing: list[str] = []
        for path in vault_paths:
            if not str(path).strip():
                continue
            if not await self.doc_exists(path):
                missing.append(path)
        return missing

    # ------------------------------------------------------------- public API

    async def verify(
        self,
        vault_paths: Iterable[str],
        cursor_before: Optional[int] = None,
    ) -> PublishVerification:
        """
        Wait for the LiveSync node to publish ``vault_paths``.

        ``vault_paths`` are relative to the vault root, matching CouchDB
        document ids. Returns a :class:`PublishVerification`; callers must treat
        ``error`` and ``missing`` as "not delivered".
        """
        paths = [str(p) for p in vault_paths if str(p).strip()]
        if not paths:
            return PublishVerification()

        if not self.configured:
            return PublishVerification(
                missing=list(paths),
                error=(
                    "livesync verification is not configured (enable livesync.enabled "
                    "and provide LIVESYNC_COUCHDB_URL/DB_NAME/USER/PASSWORD)"
                ),
            )

        fresh, reason = await self._fresh_check()
        if not fresh:
            return PublishVerification(missing=list(paths), error=reason)

        if self.config.use_path_obfuscation:
            return await self._verify_by_cursor(paths, cursor_before)

        deadline = time.monotonic() + max(0.0, self.config.verify_timeout_seconds)
        poll = max(1.0, self.config.verify_poll_interval_seconds)
        missing = paths
        try:
            while True:
                missing = await self.missing_docs(missing)
                if not missing:
                    return PublishVerification(confirmed=list(paths))
                if time.monotonic() >= deadline:
                    break
                await asyncio.sleep(poll)
        except PublishProbeError as exc:
            return PublishVerification(confirmed=[p for p in paths if p not in missing],
                                       missing=list(missing), error=str(exc))
        return PublishVerification(
            confirmed=[p for p in paths if p not in missing],
            missing=list(missing),
            error=None,
        )

    async def _fresh_check(self) -> tuple[bool, str]:
        try:
            return await self.node_is_fresh()
        except PublishProbeError as exc:
            return False, str(exc)

    async def _verify_by_cursor(
        self,
        paths: list[str],
        cursor_before: Optional[int],
    ) -> PublishVerification:
        """
        Indirect fallback used when document ids are obfuscated.

        All this can prove is that the node replicated forward past the point
        where the notes were written, so the result is flagged ``indirect`` and
        callers should not use it to justify deleting the Zhihu source.
        """
        try:
            node = await self.node_status()
            cursor_after = await self.database_cursor()
        except PublishProbeError as exc:
            return PublishVerification(missing=paths, error=str(exc), indirect=True)
        if node is None:
            return PublishVerification(
                missing=paths,
                error="path obfuscation is on, so a node_name is required for cursor checks",
                indirect=True,
            )
        if cursor_before is not None and node.cursor <= cursor_before:
            return PublishVerification(
                missing=paths,
                error=f"node cursor did not advance past {cursor_before} (now {node.cursor})",
                indirect=True,
            )
        return PublishVerification(
            confirmed=list(paths),
            missing=[],
            error=None,
            indirect=True,
        )
