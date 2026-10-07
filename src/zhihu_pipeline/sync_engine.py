import os
import random
import asyncio
from datetime import datetime
from loguru import logger

from zhihu_pipeline.auth import launch_browser_context, get_or_create_page, check_login, handle_qr_login
from zhihu_pipeline.fetcher import fetch_collections, fetch_collection_items, fetch_content_detail
from zhihu_pipeline.parser import html_to_markdown
from zhihu_pipeline.images import download_images
from zhihu_pipeline.videos import (
    VideoDownloadError,
    download_videos,
    extract_pin_detail,
    extract_pin_video_assets,
    render_video_embeds,
)
from zhihu_pipeline.comments import fetch_comments
from zhihu_pipeline.storage import ManifestManager, generate_markdown, save_markdown_file, sanitize_filename, format_date
from zhihu_pipeline.archiver import archive_item, remove_from_collection
from zhihu_pipeline.tagger import run_tagging_pass
from zhihu_pipeline.git_sync import git_pull, git_push
from zhihu_pipeline.publish_probe import PublishProber, PublishProbeError

class SyncEngine:
    def __init__(self, config):
        self.config = config

        # Manifest location: output.manifest_path when configured, otherwise the
        # legacy {vault_path}/{collection_dir}/manifest.json. A dot-directory is
        # preferred because Obsidian does not surface it, so the pipeline state
        # file cannot be carried to another device and back by the Git channel
        # or by a LiveSync node.
        self.manifest_dir, self.manifest_path = self._resolve_manifest_paths()
        legacy_path = os.path.join(
            self.config.output.vault_path, self.config.output.collection_dir, "manifest.json"
        )
        if self.manifest_path != legacy_path and os.path.exists(legacy_path):
            try:
                os.makedirs(self.manifest_dir, exist_ok=True)
                os.replace(legacy_path, self.manifest_path)
                logger.info(f"Migrated manifest to {self.manifest_path}")
            except OSError as exc:
                # Keep using the legacy file rather than starting from scratch:
                # an empty manifest would make the pipeline forget every article
                # it already downloaded and re-crawl the whole collection.
                logger.error(f"Cannot migrate manifest to {self.manifest_path}: {exc}")
                self.manifest_dir = os.path.dirname(legacy_path)
                self.manifest_path = legacy_path

        # Initialize ManifestManager
        self.manifest = ManifestManager(self.manifest_path)
        self.prober = PublishProber(config.livesync)
        # CouchDB replication cursor captured before this run writes anything,
        # used only for the indirect (path-obfuscated) delivery check.
        self._cursor_before = None
        # Per-run delivery bookkeeping; both are reset at the start of run().
        self._awaiting_delivery: dict[str, list[str]] = {}
        self._pending_removals: list[dict] = []

    def _resolve_manifest_paths(self) -> tuple[str, str]:
        """Return (directory, file path) of manifest.json inside the vault."""
        configured = (self.config.output.manifest_path or "").strip()
        if not configured:
            configured = os.path.join(self.config.output.collection_dir, "manifest.json")
        path = os.path.join(self.config.output.vault_path, configured)
        if not path.startswith(self.config.output.vault_path + os.sep) and path != self.config.output.vault_path:
            logger.critical(
                f"output.manifest_path={configured!r} escapes the vault; falling back to the legacy location."
            )
            path = os.path.join(
                self.config.output.vault_path, self.config.output.collection_dir, "manifest.json"
            )
        if os.path.isdir(path):
            path = os.path.join(path, "manifest.json")
        return os.path.dirname(path), path

    @property
    def publish_channel(self) -> str:
        """
        Which channel is supposed to deliver notes off this machine.

        "git" only when GitHub sync is actually usable; otherwise the configured
        mode. Silently reporting success for a channel that is switched off is
        what let a dead delivery path look healthy for days.
        """
        mode = self.config.git.sync_mode
        if mode == "git" and not self.config.git.enabled:
            return "none"
        return mode

    def uses_git(self) -> bool:
        return self.publish_channel == "git"

    async def get_browser_context(self):
        """
        Launch or obtain the persistent browser context.
        """
        return await launch_browser_context(
            user_data_dir=self.config.chrome.user_data_dir,
            headless=self.config.chrome.headless
        )

    def _is_supported_item(self, item):
        if item["type"] in ["answer", "article"]:
            return True
        if item["type"] == "pin" and self.config.sync.video_enabled:
            try:
                return bool(extract_pin_video_assets(item.get("raw_content") or {}, self.config.sync.video_quality))
            except VideoDownloadError as error:
                logger.warning(f"Skipping pin {item.get('id')} without a usable video rendition: {error}")
        return False

    async def run(self, full_sync: bool = False, target_collection: str = None):
        """
        Orchestrate the full synchronization process.
        """
        logger.info("Starting synchronization process...")

        # Per-run delivery bookkeeping.
        # keys awaiting proof of delivery -> vault-relative paths
        self._awaiting_delivery: dict[str, list[str]] = {}
        # items that may leave the Zhihu inbox only once delivery is confirmed
        self._pending_removals: list[dict] = []

        # 0. Pull the latest notes repository, but only when Git is the channel.
        # In livesync mode GitHub is not part of the pipeline at all: pulling
        # would keep the container coupled to a remote it no longer pushes to,
        # leave a permanently dirty work tree, and re-download private notes.
        if self.uses_git():
            if self.config.git.auto_pull:
                if not git_pull(self.config.output.vault_path, self.config.git):
                    raise RuntimeError("Git pull failed; refusing to sync against a stale vault.")
        elif self.config.git.sync_mode == "livesync":
            logger.info(
                "Sync mode is 'livesync': GitHub is not consulted. A headless "
                "LiveSync node is expected to publish the vault directory."
            )
            self._cursor_before = await self._capture_delivery_cursor()
        else:
            logger.warning(
                "Sync mode is 'none': nothing will publish these notes. They stay "
                "in the Zhihu inbox and every run will report them as unpublished."
            )
            self._cursor_before = None

        # 1. Launch Browser Context
        try:
            context = await self.get_browser_context()
        except Exception as e:
            logger.error(f"Cannot launch browser context: {e}")
            raise RuntimeError(f"Cannot launch browser context: {e}")
            
        try:
            page = await get_or_create_page(context)
            
            # 2. Check Login Status & Auto QR Login if needed
            logged_in, username = await check_login(page)
            if not logged_in:
                logger.warning("User is not logged in. Initiating automated QR code login flow...")
                logged_in, username = await handle_qr_login(page, self.config.notify)
                if not logged_in:
                    logger.error("QR login failed or timed out. Aborting sync.")
                    raise RuntimeError("QR code login failed or timed out.")
                
            logger.info(f"Login verified. Active user: {username}")

            # 3. Retrieve Collections
            collections = await fetch_collections(page)
            if not collections:
                logger.warning("No collections found.")
                return

            # Filter collections based on sync options and target_collection
            sync_collections = self.config.sync.collections
            collections_to_sync = []
            for col in collections:
                title = col.get("title", "")
                
                # Skip archive collection
                if title == self.config.sync.archive_name:
                    logger.debug(f"Skipping archive collection from sync: '{title}'")
                    continue

                # Filter by target_collection CLI flag
                if target_collection and title != target_collection:
                    continue
                    
                # Filter by config.yaml setting
                if sync_collections != "all" and isinstance(sync_collections, list):
                    if title not in sync_collections:
                        continue
                        
                collections_to_sync.append(col)

            if not collections_to_sync:
                logger.warning("No collections matched the sync filters.")
                return

            logger.info(f"Scanning {len(collections_to_sync)} collections for new items...")
            
            total_synced = 0
            total_failed = 0
            start_time = datetime.now()

            # 4. Synchronize each collection
            for col in collections_to_sync:
                col_id = col["id"]
                col_title = col["title"]
                logger.info(f"Syncing collection: '{col_title}' (ID: {col_id})")

                # Create collection folder
                col_folder = os.path.join(self.config.output.vault_path, self.config.output.collection_dir, sanitize_filename(col_title))
                os.makedirs(col_folder, exist_ok=True)

                items = await fetch_collection_items(page, col_id)
                new_items = []

                # Filter out already synced items
                for item in items:
                    item_type = item["type"]
                    item_id = item["id"]
                    
                    # Check item type
                    if not self._is_supported_item(item):
                        logger.debug(f"Skipping local download for item {item_id} due to unsupported type: {item_type}")
                        if self.config.sync.auto_archive:
                            logger.info(f"'{item['title']}' is of unsupported type '{item_type}', but remains in active collection. Archiving on Zhihu...")
                            try:
                                await page.goto(item["url"], wait_until="domcontentloaded", timeout=20000)
                                try:
                                    await page.wait_for_load_state("networkidle", timeout=3000)
                                except Exception:
                                    pass
                                
                                archived = await archive_item(
                                    page=page,
                                    item_type=item_type,
                                    item_id=str(item_id),
                                    current_collection_title=col_title,
                                    archive_collection_title=self.config.sync.archive_name
                                )
                                if archived:
                                    logger.info(f"Successfully archived unsupported item: '{item['title']}'")
                                    delay = random.uniform(self.config.sync.delay_min, self.config.sync.delay_max)
                                    logger.info(f"Waiting {delay:.1f}s before next request...")
                                    await asyncio.sleep(delay)
                            except Exception as e:
                                logger.error(f"Failed to archive unsupported item '{item['title']}': {e}")
                        continue

                    unique_key = f"{item_type}_{item_id}"
                    if not full_sync and self.manifest.is_synced(unique_key):
                        # Already on disk. Only proven deliveries may leave the
                        # inbox; anything else is re-verified this run.
                        self._track_existing_item(unique_key, item, col_title)
                        continue
                    new_items.append(item)

                logger.info(f"Found {len(new_items)} new items to sync in '{col_title}'.")
                
                # Sync each new item
                for idx, item in enumerate(new_items):
                    item_type = item["type"]
                    item_id = item["id"]
                    item_title = item["title"]
                    unique_key = f"{item_type}_{item_id}"
                    
                    logger.info(f"[{idx+1}/{len(new_items)}] Processing: {item_title} ({item_type} {item_id})")
                    
                    try:
                        # Fetch details. Pin content is already present in the collection API payload.
                        if item_type == "pin":
                            detail = extract_pin_detail(item)
                        else:
                            detail = await fetch_content_detail(page, item, self.config.selectors)
                        html_content = detail.get("content_html", "")

                        pin_video_assets = []
                        if item_type == "pin":
                            pin_video_assets = extract_pin_video_assets(
                                item.get("raw_content") or {}, self.config.sync.video_quality
                            )
                        
                        if not html_content and not pin_video_assets:
                            if detail.get("is_deleted"):
                                logger.warning(f"Item is deleted on Zhihu: '{item_title}'. Marking as deleted in manifest.")
                                self.manifest.add_item(unique_key, {
                                    "title": item_title,
                                    "type": item_type,
                                    "local_path": "",
                                    "zhihu_url": item["url"],
                                    "collection": col_title,
                                    "status": "deleted"
                                })
                                continue
                            logger.warning(f"Could not retrieve content body for item: {item_title}. Skipping.")
                            total_failed += 1
                            continue

                        # Convert to Markdown
                        markdown_body = html_to_markdown(html_content)

                        # Download images and replace paths
                        sanitized_note_name = sanitize_filename(item_title)
                        markdown_body_local = await download_images(markdown_body, sanitized_note_name, self.config.output.vault_path)

                        downloaded_videos = []
                        if pin_video_assets:
                            try:
                                downloaded_videos = await download_videos(
                                    pin_video_assets,
                                    sanitized_note_name,
                                    self.config.output.vault_path,
                                    max_size_mb=self.config.sync.video_max_size_mb,
                                )
                            except VideoDownloadError as video_error:
                                logger.error(f"Video download failed for {unique_key}: {video_error}")
                                total_failed += 1
                                continue
                            video_md = render_video_embeds(downloaded_videos)
                            if video_md:
                                markdown_body_local = f"{markdown_body_local.strip()}\n\n{video_md}".strip()

                        # Fetch comments if requested
                        comments_md = ""
                        if self.config.sync.include_comments and item_type in ["answer", "article"]:
                            comments_md = await fetch_comments(page, item_type, str(item_id), self.config.sync.max_comments)

                        # Assemble final Markdown text
                        file_content_dict = {
                            "title": item_title,
                            "content_markdown": markdown_body_local,
                            "author_name": detail.get("author_name", "Anonymous"),
                            "created_time": detail.get("created_time"),
                            "vote_count": detail.get("vote_count", 0),
                            "zhihu_url": item["url"],
                            "zhihu_type": item_type,
                            "collection_name": col_title
                        }
                        
                        final_markdown = generate_markdown(file_content_dict, comments_md)

                        # Save Markdown file
                        raw_time = detail.get("created_time") or detail.get("created_time_str")
                        date_str = format_date(raw_time)
                        filename = f"{date_str} {sanitized_note_name}.md"
                        target_filepath = os.path.join(col_folder, filename)
                        saved_path = save_markdown_file(final_markdown, target_filepath, str(item_id))

                        # Update Manifest
                        rel_local_path = os.path.relpath(saved_path, self.config.output.vault_path)
                        initial_tagging_status = "pending"
                        manifest_item = {
                            "title": item_title,
                            "type": item_type,
                            "local_path": rel_local_path,
                            "zhihu_url": item["url"],
                            "collection": col_title
                        }
                        if downloaded_videos:
                            manifest_item["video_quality"] = self.config.sync.video_quality
                            manifest_item["video_paths"] = [video["vault_path"] for video in downloaded_videos]
                            manifest_item["video_bytes"] = sum(video["size"] for video in downloaded_videos)
                        self.manifest.add_item(
                            unique_key,
                            manifest_item,
                            tagging_status=initial_tagging_status,
                            publish_status="pending",
                        )

                        # The note now exists on this disk only. Register it for
                        # delivery verification and queue the inbox cleanup; the
                        # actual removal happens after the publish step proves
                        # the document reached the shared vault.
                        self._awaiting_delivery[unique_key] = self._publish_targets(manifest_item)
                        self._queue_inbox_action(unique_key, item, col_title)

                        total_synced += 1
                        logger.info(f"Successfully synced: '{item_title}'")
                        
                        # Sleep delay to prevent rate limits
                        delay = random.uniform(self.config.sync.delay_min, self.config.sync.delay_max)
                        logger.info(f"Waiting {delay:.1f}s before next request...")
                        await asyncio.sleep(delay)

                    except Exception as e:
                        logger.exception(f"Failed to sync item {unique_key}: {e}")
                        total_failed += 1

            duration = datetime.now() - start_time
            logger.info("=== Synchronization Finished ===")
            logger.info(f"Total Synced: {total_synced} | Failed: {total_failed} | Time elapsed: {duration}")

        finally:
            try:
                playwright_instance = getattr(context, '_playwright_instance', None)
                await context.close()
                if playwright_instance:
                    await playwright_instance.stop()
            except Exception:
                pass

        if self.config.tagger.enabled:
            logger.info("=== Starting Auto-Tagging Pass ===")
            success, fail = run_tagging_pass(self.manifest, self.config.output.vault_path, self.config.tagger)
            logger.info(f"Tagging finished: {success} tagged, {fail} failed (will retry next time).")

        # 5. Publish this run's notes through the configured channel and keep a
        # per-item verdict. Reporting "published" without proof is what made a
        # stopped delivery channel look healthy.
        publish = await self._publish_batch()

        # 6. Only confirmed deliveries may be consumed from the Zhihu inbox.
        consumed, consume_failed = await self._consume_inbox(publish["confirmed"])

        return {
            "synced": total_synced,
            "failed": total_failed,
            "tagged": success if self.config.tagger.enabled else 0,
            "tag_failed": fail if self.config.tagger.enabled else 0,
            "publish_channel": publish["channel"],
            "published": publish["published"],
            "publish_pending": publish["missing"],
            "publish_error": publish["error"],
            "publish_indirect": publish["indirect"],
            "inbox_consumed": consumed,
            "inbox_consumed_failed": consume_failed,
            "duration": str(duration)
        }

    # ---------------------------------------------------------------- delivery

    def _publish_targets(self, manifest_item: dict) -> list[str]:
        """
        Vault-relative documents that must exist for this note to be "delivered".

        The Markdown note and any embedded video are checked. Inline images are
        not: a note with a missing image still renders, and probing every image
        would multiply the request count for the same failure mode.
        """
        targets = []
        note_path = str(manifest_item.get("local_path", "") or "").strip()
        if note_path:
            targets.append(note_path)
        targets.extend(
            str(p) for p in (manifest_item.get("video_paths") or []) if str(p).strip()
        )
        return targets

    def _track_existing_item(self, unique_key: str, item: dict, col_title: str):
        """
        Handle a collection entry that is already recorded in the manifest.

        Items whose delivery was never proven go back into this run's
        verification set instead of being removed from the inbox.
        """
        manifest_item = self.manifest.data.get("synced_items", {}).get(unique_key, {})
        status = manifest_item.get("publish_status", "pending")
        if status == "published":
            self._queue_inbox_action(unique_key, item, col_title)
            return

        paths = self._publish_targets(manifest_item)
        if paths:
            self._awaiting_delivery[unique_key] = paths
        logger.info(
            f"'{item.get('title')}' is on disk but its delivery is unproven "
            f"(publish_status={status!r}); keeping it in the inbox for now."
        )

    def _queue_inbox_action(self, unique_key: str, item: dict, col_title: str):
        """Record what should happen to this inbox entry once delivery is proven."""
        if self.config.sync.remove_after_sync:
            action = "remove"
        elif self.config.sync.auto_archive:
            action = "archive"
        else:
            return
        self._pending_removals.append(
            {
                "key": unique_key,
                "action": action,
                "title": item.get("title", ""),
                "type": item.get("type", ""),
                "url": item.get("url", ""),
                "id": item.get("id", ""),
                "collection": col_title,
            }
        )

    async def _capture_delivery_cursor(self):
        """Remember the CouchDB cursor so an obfuscated vault can be checked indirectly."""
        if not (self.config.git.sync_mode == "livesync" and self.prober.configured):
            return None
        try:
            cursor = await self.prober.database_cursor()
            logger.info(f"LiveSync delivery cursor before this run: {cursor}")
            return cursor
        except PublishProbeError as exc:
            logger.error(f"Cannot read LiveSync cursor: {exc}")
            return None

    async def _publish_batch(self) -> dict:
        """
        Deliver the notes written by this run and return a per-item verdict.

        ``published`` is deliberately tri-state: True only when the channel
        proved delivery, False on a proven failure, and None when no channel is
        configured to deliver anything at all.
        """
        channel = self.publish_channel
        confirmed: set[str] = set()
        missing: list[str] = []
        error: str | None = None
        indirect = False
        # "pending" = retry later, "unverified" = the channel could not be asked,
        # "failed" = the channel answered and said no. Keeping these apart lets
        # an operator tell a slow node apart from a dead one.
        not_delivered_status = "pending"

        # Cover everything the manifest still considers unproven, not just this
        # run's output: entries recorded before publish tracking existed would
        # otherwise sit in the backlog forever and make /status misleading.
        for key, item in self.manifest.get_publish_pending_items():
            if key in self._awaiting_delivery:
                continue
            recorded_paths = self._publish_targets(item)
            if not recorded_paths:
                continue
            on_disk = all(
                os.path.exists(os.path.join(self.config.output.vault_path, p))
                for p in recorded_paths
            )
            if on_disk:
                self._awaiting_delivery[key] = recorded_paths

        if not self._awaiting_delivery:
            # Nothing new to deliver. Do not claim a successful publish for a
            # channel that was never exercised; callers only need the verdict
            # when this run actually produced output.
            return {
                "channel": channel,
                "published": None,
                "confirmed": confirmed,
                "missing": missing,
                "error": None,
                "indirect": False,
            }

        paths = sorted({p for group in self._awaiting_delivery.values() for p in group})

        if channel == "git":
            pushed = git_push(
                self.config.output.vault_path,
                self.config.git,
                f"docs: auto sync {len(self._awaiting_delivery)} zhihu note(s) [skip ci]",
                # Notes and downloaded Zhihu videos are both pipeline-owned.
                # Keep the scope explicit so private notes and .obsidian data
                # in the shared vault can never be staged accidentally.
                include_paths=[
                    self.config.output.collection_dir,
                    "assets/知乎视频",
                    "assets/知乎附件",
                ],
            )
            if pushed:
                confirmed.update(self._awaiting_delivery.keys())
            else:
                error = "GitHub push failed; notes are only on this disk."
                missing = paths
                not_delivered_status = "failed"
                logger.error(error)
        elif channel == "livesync":
            verification = await self.prober.verify(paths, cursor_before=self._cursor_before)
            error = verification.error
            indirect = verification.indirect
            confirmed_paths = set(verification.confirmed)
            if verification.delivered and indirect:
                # A replication cursor can show that the node moved forward, but
                # not that this specific note arrived. That is not enough to
                # delete the only upstream copy.
                missing = list(paths)
                error = (
                    "delivery proof is indirect (use_path_obfuscation is on); "
                    "inbox entries are kept until per-document proof is possible"
                )
                logger.error(error)
            else:
                for key, key_paths in self._awaiting_delivery.items():
                    if key_paths and all(p in confirmed_paths for p in key_paths):
                        confirmed.add(key)
                missing = verification.missing
                if verification.delivered:
                    logger.info(f"LiveSync node published {len(paths)} document(s).")
                elif error:
                    logger.error(f"LiveSync delivery could not be verified: {error}")
                else:
                    logger.error(
                        f"{len(missing)} document(s) have not reached CouchDB yet: "
                        f"{', '.join(missing[:3])}{' ...' if len(missing) > 3 else ''}"
                    )
        else:
            error = "git.sync_mode='none': no delivery channel is configured."
            missing = paths
            not_delivered_status = "unverified"
            logger.error(error)

        if error and channel != "git":
            # The channel could not be consulted (or could only answer
            # indirectly), which is different from it reporting a failure.
            not_delivered_status = "unverified"

        for key in confirmed:
            self.manifest.update_publish_status(key, "published")
        for key in self._awaiting_delivery:
            if key not in confirmed:
                self.manifest.update_publish_status(key, not_delivered_status)

        published: bool | None
        if confirmed and not missing and not error:
            published = True
        elif error or missing:
            published = False
        else:
            published = None

        return {
            "channel": channel,
            "published": published,
            "confirmed": confirmed,
            "missing": missing,
            "error": error,
            "indirect": indirect,
        }

    async def _consume_inbox(self, confirmed_keys: set[str]) -> tuple[int, int]:
        """
        Remove or archive inbox entries whose delivery has been proven.

        Indirect proof is not enough here: deleting the last copy of a note on a
        cursor guess is how this pipeline loses data.
        """
        actionable = [entry for entry in self._pending_removals if entry["key"] in confirmed_keys]
        if not actionable:
            held = len(self._pending_removals)
            if held:
                logger.warning(
                    f"{held} item(s) stay in the Zhihu inbox because their delivery "
                    "is not confirmed yet."
                )
            return 0, 0

        ok = 0
        failed = 0
        context = None
        try:
            context = await self.get_browser_context()
            page = await get_or_create_page(context)
            logged_in, _ = await check_login(page)
            if not logged_in:
                logger.error("Login lost before inbox cleanup; entries are kept for the next run.")
                return 0, len(actionable)

            for entry in actionable:
                try:
                    if entry["action"] == "remove":
                        done = await remove_from_collection(
                            page=page,
                            collection_title=entry["collection"],
                            item_type=entry["type"],
                            item_url=entry.get("url") or None,
                        )
                    else:
                        # archive_item clicks the on-page Collect button, so the
                        # tab has to be sitting on the item first.
                        if entry.get("url"):
                            await page.goto(entry["url"], wait_until="domcontentloaded", timeout=20000)
                            try:
                                await page.wait_for_load_state("networkidle", timeout=3000)
                            except Exception:
                                pass
                        done = await archive_item(
                            page=page,
                            item_type=entry["type"],
                            item_id=str(entry["id"]),
                            current_collection_title=entry["collection"],
                            archive_collection_title=self.config.sync.archive_name,
                        )
                    if done:
                        ok += 1
                        logger.info(f"Inbox consumed: '{entry['title']}'")
                    else:
                        failed += 1
                        logger.warning(f"Could not consume inbox entry '{entry['title']}' (delivery already recorded).")
                except Exception as exc:
                    failed += 1
                    logger.error(f"Error consuming inbox entry '{entry['title']}': {exc}")

                delay = random.uniform(self.config.sync.delay_min, self.config.sync.delay_max)
                await asyncio.sleep(delay)
        finally:
            if context is not None:
                try:
                    playwright_instance = getattr(context, '_playwright_instance', None)
                    await context.close()
                    if playwright_instance:
                        await playwright_instance.stop()
                except Exception:
                    pass

        logger.info(f"Inbox cleanup: {ok} consumed, {failed} failed.")
        return ok, failed

    async def check_auth(self):
        """
        Utility command to verify connection and login status.
        """
        logger.info(f"Testing browser launch with profile {self.config.chrome.user_data_dir}...")
        try:
            context = await self.get_browser_context()
            page = await get_or_create_page(context)
            ok, username = await check_login(page)
            if ok:
                logger.info(f"Connection OK. Logged in as: {username}")
                print(f"Zhihu Connection: OK\nLogin User: {username}")
            else:
                logger.warning("Browser launched OK, but user is LOGGED OUT.")
                print("Zhihu Connection: OK\nLogin Status: LOGGED OUT (QR Code flow will trigger on next sync)")
            playwright_instance = getattr(context, '_playwright_instance', None)
            await context.close()
            if playwright_instance:
                await playwright_instance.stop()
        except Exception as e:
            logger.error(f"Authentication check failed: {e}")
            print(f"Zhihu Connection: FAILED. {e}")

    def show_status(self):
        """
        Utility command to print current sync stats.
        """
        stats = self.manifest.get_stats()
        print("\n=== Zhihu Pipeline Sync Status ===")
        print(f"Manifest Path: {self.manifest_path}")
        print(f"Total Synced Items: {stats['total_active']}")
        print(f"Total Removed Items: {stats['total_removed']}")
        print(f"Delivery Unconfirmed: {stats.get('total_unpublished', 0)}")
        print(f"Publish Channel: {self.publish_channel}")
        print(f"Last Sync Date: {stats['last_sync'] if stats['last_sync'] else 'Never'}")
        print("==================================\n")
