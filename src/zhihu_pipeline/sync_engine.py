import os
import random
import asyncio
from datetime import datetime
from loguru import logger

from zhihu_pipeline.auth import launch_browser_context, get_or_create_page, check_login, handle_qr_login
from zhihu_pipeline.fetcher import fetch_collections, fetch_collection_items, fetch_content_detail
from zhihu_pipeline.parser import html_to_markdown
from zhihu_pipeline.images import download_images
from zhihu_pipeline.comments import fetch_comments
from zhihu_pipeline.storage import ManifestManager, generate_markdown, save_markdown_file, sanitize_filename, format_date
from zhihu_pipeline.archiver import archive_item, remove_from_collection
from zhihu_pipeline.tagger import run_tagging_pass
from zhihu_pipeline.git_sync import git_pull, git_push

class SyncEngine:
    def __init__(self, config):
        self.config = config
        
        # Manifest path: {vault_path}/{collection_dir}/manifest.json
        self.manifest_dir = os.path.join(self.config.output.vault_path, self.config.output.collection_dir)
        self.manifest_path = os.path.join(self.manifest_dir, "manifest.json")
        
        # Initialize ManifestManager
        self.manifest = ManifestManager(self.manifest_path)

    async def get_browser_context(self):
        """
        Launch or obtain the persistent browser context.
        """
        return await launch_browser_context(
            user_data_dir=self.config.chrome.user_data_dir,
            headless=self.config.chrome.headless
        )

    async def run(self, full_sync: bool = False, target_collection: str = None):
        """
        Orchestrate the full synchronization process.
        """
        logger.info("Starting synchronization process...")
        
        # 0. Pull latest notes repository if Git sync is enabled
        if self.config.git.enabled and self.config.git.auto_pull:
            git_pull(self.config.output.vault_path, self.config.git)

        # 1. Launch Browser Context
        try:
            context = await self.get_browser_context()
        except Exception as e:
            logger.error(f"Cannot launch browser context: {e}")
            return
            
        try:
            page = await get_or_create_page(context)
            
            # 2. Check Login Status & Auto QR Login if needed
            logged_in, username = await check_login(page)
            if not logged_in:
                logger.warning("User is not logged in. Initiating automated QR code login flow...")
                logged_in, username = await handle_qr_login(page, self.config.telegram)
                if not logged_in:
                    logger.error("QR login failed or timed out. Aborting sync.")
                    return
                
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
                    if item_type not in ["answer", "article"]:
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
                        if self.config.sync.remove_after_sync:
                            logger.info(f"'{item['title']}' is already synced locally. Removing from collection '{col_title}'...")
                            try:
                                removed = await remove_from_collection(
                                    page=page,
                                    collection_title=col_title,
                                    item_type=item_type,
                                    item_url=item.get("url")
                                )
                                if removed:
                                    logger.info(f"Successfully removed previously synced item from '{col_title}'.")
                                else:
                                    logger.warning(f"Could not remove previously synced item from '{col_title}'.")
                                delay = random.uniform(self.config.sync.delay_min, self.config.sync.delay_max)
                                await asyncio.sleep(delay)
                            except Exception as e:
                                logger.error(f"Failed to remove previously synced item '{item['title']}': {e}")
                        elif self.config.sync.auto_archive:
                            logger.info(f"'{item['title']}' is already synced locally, but remains in active collection. Archiving now...")
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
                                    logger.info(f"Successfully archived previously synced item: '{item['title']}'")
                                    delay = random.uniform(self.config.sync.delay_min, self.config.sync.delay_max)
                                    logger.info(f"Waiting {delay:.1f}s before next request...")
                                    await asyncio.sleep(delay)
                            except Exception as e:
                                logger.error(f"Failed to archive previously synced item '{item['title']}': {e}")
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
                        # Fetch details
                        detail = await fetch_content_detail(page, item, self.config.selectors)
                        html_content = detail.get("content_html", "")
                        
                        if not html_content:
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

                        # Fetch comments if requested
                        comments_md = ""
                        if self.config.sync.include_comments:
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
                        initial_tagging_status = "pending" if self.config.tagger.enabled else "skipped"
                        self.manifest.add_item(unique_key, {
                            "title": item_title,
                            "type": item_type,
                            "local_path": rel_local_path,
                            "zhihu_url": item["url"],
                            "collection": col_title
                        }, tagging_status=initial_tagging_status)

                        # Remove from collection if remove_after_sync is enabled (Inbox queue pattern)
                        if self.config.sync.remove_after_sync:
                            try:
                                removed = await remove_from_collection(
                                    page=page,
                                    collection_title=col_title,
                                    item_type=item_type
                                )
                                if removed:
                                    logger.info(f"Successfully removed '{item_title}' from collection '{col_title}'.")
                                else:
                                    logger.warning(f"Could not remove '{item_title}' from collection '{col_title}'.")
                            except Exception as re_err:
                                logger.error(f"Error removing item '{item_title}' from collection: {re_err}")
                        elif self.config.sync.auto_archive:
                            try:
                                archived = await archive_item(
                                    page=page,
                                    item_type=item_type,
                                    item_id=str(item_id),
                                    current_collection_title=col_title,
                                    archive_collection_title=self.config.sync.archive_name
                                )
                                if archived:
                                    logger.info(f"Item '{item_title}' successfully moved to archive collection.")
                                else:
                                    logger.warning(f"Item '{item_title}' could not be archived.")
                            except Exception as ae:
                                logger.error(f"Error during auto-archiving item: {ae}")

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
                await context.close()
            except Exception:
                pass

        if self.config.tagger.enabled:
            logger.info("=== Starting Auto-Tagging Pass ===")
            success, fail = run_tagging_pass(self.manifest, self.config.output.vault_path, self.config.tagger)
            logger.info(f"Tagging finished: {success} tagged, {fail} failed (will retry next time).")

        # 4. Push updated notes repository to GitHub if Git sync is enabled
        if self.config.git.enabled and self.config.git.auto_push:
            git_push(
                self.config.output.vault_path,
                self.config.git,
                f"docs: auto sync {total_synced} zhihu note(s) [skip ci]"
            )

        return {
            "synced": total_synced,
            "failed": total_failed,
            "duration": str(duration)
        }

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
            await context.close()
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
        print(f"Last Sync Date: {stats['last_sync'] if stats['last_sync'] else 'Never'}")
        print("==================================\n")
