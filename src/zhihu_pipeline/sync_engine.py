import os
import random
import asyncio
from datetime import datetime
from loguru import logger

from zhihu_pipeline.auth import connect_chrome, get_or_create_page, check_login
from zhihu_pipeline.fetcher import fetch_collections, fetch_collection_items, fetch_content_detail
from zhihu_pipeline.parser import html_to_markdown
from zhihu_pipeline.images import download_images
from zhihu_pipeline.comments import fetch_comments
from zhihu_pipeline.storage import ManifestManager, generate_markdown, save_markdown_file, sanitize_filename

class SyncEngine:
    def __init__(self, config):
        self.config = config
        
        # Manifest path: {vault_path}/{collection_dir}/manifest.json
        self.manifest_dir = os.path.join(self.config.output.vault_path, self.config.output.collection_dir)
        self.manifest_path = os.path.join(self.manifest_dir, "manifest.json")
        
        # Initialize ManifestManager
        self.manifest = ManifestManager(self.manifest_path)

    async def ensure_chrome_connected(self):
        """
        Check if Chrome is running on debug port. If not, automatically launch start_chrome.sh.
        """
        try:
            browser, context = await connect_chrome(self.config.chrome.debug_port)
            return browser, context
        except Exception:
            logger.info("Chrome debugging port is not active. Attempting to start Chrome automatically...")
            script_path = "./start_chrome.sh"
            if os.path.exists(script_path):
                import subprocess
                subprocess.Popen(["bash", script_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                logger.info("Launched start_chrome.sh. Waiting 4 seconds for Chrome to initialize...")
                await asyncio.sleep(4.0)
                try:
                    browser, context = await connect_chrome(self.config.chrome.debug_port)
                    return browser, context
                except Exception as e:
                    raise ConnectionError(f"Launched Chrome but still failed to connect: {e}")
            else:
                raise ConnectionError("Chrome is not running and start_chrome.sh was not found in the current directory.")

    async def run(self, full_sync: bool = False, target_collection: str = None):
        """
        Orchestrate the full synchronization process.
        """
        logger.info("Starting synchronization process...")
        
        # 1. Connect Chrome (with auto-start)
        try:
            browser, context = await self.ensure_chrome_connected()
        except Exception as e:
            logger.error(f"Cannot run sync: {e}")
            return
            
        page = await get_or_create_page(context)
        
        # 2. Check Login Status with interactive helper
        logged_in, username = await check_login(page)
        if not logged_in:
            print("\n" + "="*60)
            print("【提示】检测到您尚未登录知乎。")
            print("已经在后台为您打开了 Chrome 浏览器，请在浏览器中完成知乎登录。")
            print("="*60 + "\n")
            
            while not logged_in:
                input("👉 请在浏览器登录成功后，回到这里按【回车键】继续...")
                logger.info("Retrying login check...")
                logged_in, username = await check_login(page)
                if not logged_in:
                    print("⚠️ 仍未检测到登录态，请确认您已成功登录并看到知乎首页，然后重试。")
            
            print(f"\n✅ 登录成功！当前用户: {username}\n")

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
                    logger.debug(f"Skipping item {item_id} due to unsupported type: {item_type}")
                    continue

                unique_key = f"{item_type}_{item_id}"
                if not full_sync and self.manifest.is_synced(unique_key):
                    logger.debug(f"Item {unique_key} already synced. Skipping.")
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
                    from zhihu_pipeline.storage import format_date
                    raw_time = detail.get("created_time") or detail.get("created_time_str")
                    date_str = format_date(raw_time)
                    filename = f"{date_str} {sanitized_note_name}.md"
                    target_filepath = os.path.join(col_folder, filename)
                    saved_path = save_markdown_file(final_markdown, target_filepath, str(item_id))

                    # Update Manifest
                    rel_local_path = os.path.relpath(saved_path, self.config.output.vault_path)
                    self.manifest.add_item(unique_key, {
                        "title": item_title,
                        "type": item_type,
                        "local_path": rel_local_path,
                        "zhihu_url": item["url"],
                        "collection": col_title
                    })

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

    async def check_auth(self):
        """
        Utility command to verify connection and login status.
        """
        logger.info(f"Testing connection on port {self.config.chrome.debug_port}...")
        try:
            browser, context = await connect_chrome(self.config.chrome.debug_port)
            page = await get_or_create_page(context)
            ok, username = await check_login(page)
            if ok:
                logger.info(f"Connection OK. Logged in as: {username}")
                print(f"Zhihu Connection: OK\nLogin User: {username}")
            else:
                logger.warning("Connection OK, but user is LOGGED OUT.")
                print("Zhihu Connection: OK\nLogin Status: LOGGED OUT")
            await browser.close()
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
