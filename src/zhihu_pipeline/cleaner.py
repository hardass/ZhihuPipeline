import asyncio
import random
from typing import Dict, Any, Optional
from loguru import logger
from playwright.async_api import Page

from zhihu_pipeline.fetcher import fetch_collections, fetch_collection_items
from zhihu_pipeline.archiver import remove_from_collection

async def delete_collection(
    page: Page,
    target_name_or_id: str = "archive"
) -> Dict[str, Any]:
    """
    Delete a Zhihu collection directly via API.
    """
    logger.info(f"Preparing to delete collection: '{target_name_or_id}'...")
    col_id: Optional[int] = None
    col_title: str = target_name_or_id

    if str(target_name_or_id).isdigit():
        col_id = int(target_name_or_id)
        col_title = f"Collection-{col_id}"
    else:
        collections = await fetch_collections(page)
        for col in collections:
            if col.get("title", "").strip().lower() == target_name_or_id.strip().lower():
                col_id = col["id"]
                col_title = col.get("title", target_name_or_id)
                break
                
    if not col_id:
        logger.warning(f"Collection '{target_name_or_id}' not found among user collections.")
        return {"status": "not_found", "collection_title": target_name_or_id}

    logger.info(f"Deleting collection '{col_title}' (ID: {col_id})...")
    res = await page.evaluate("""
        async (colId) => {
            try {
                const resp = await fetch(`https://www.zhihu.com/api/v4/collections/${colId}`, {
                    method: 'DELETE'
                });
                const data = await resp.json();
                return { status: resp.status, data };
            } catch (e) {
                return { error: e.toString() };
            }
        }
    """, col_id)

    if res.get("status") == 200 and res.get("data", {}).get("success"):
        logger.info(f"Successfully deleted collection '{col_title}' (ID: {col_id}).")
        return {"status": "success", "collection_title": col_title, "collection_id": col_id}
    else:
        logger.error(f"Failed to delete collection '{col_title}': {res}")
        return {"status": "failed", "collection_title": col_title, "collection_id": col_id, "error": res}

async def clear_collection_contents(
    page: Page,
    target_name_or_id: str = "archive",
    delay_min: float = 0.2,
    delay_max: float = 0.4
) -> Dict[str, Any]:
    """
    Fetch all items inside a Zhihu collection and remove every item from it.
    """
    logger.info(f"Preparing to clear collection: '{target_name_or_id}'...")
    
    col_id: Optional[int] = None
    col_title: str = target_name_or_id

    # Check if target is directly a numeric ID
    if str(target_name_or_id).isdigit():
        col_id = int(target_name_or_id)
        col_title = f"Collection-{col_id}"
    else:
        collections = await fetch_collections(page)
        for col in collections:
            if col.get("title", "").strip().lower() == target_name_or_id.strip().lower():
                col_id = col["id"]
                col_title = col.get("title", target_name_or_id)
                break
                
    if not col_id:
        logger.error(f"Collection '{target_name_or_id}' not found among user collections.")
        return {"total": 0, "removed": 0, "failed": 0, "status": "not_found"}

    logger.info(f"Target collection identified: '{col_title}' (ID: {col_id})")
    
    # 1. Fetch all items in the collection
    items = await fetch_collection_items(page, col_id)
    total_items = len(items)
    
    if total_items == 0:
        logger.info(f"Collection '{col_title}' is already empty. No items to clear.")
        return {"total": 0, "removed": 0, "failed": 0, "status": "already_empty"}

    logger.info(f"Found {total_items} items in '{col_title}'. Beginning batch removal...")
    
    removed_count = 0
    failed_count = 0

    for idx, item in enumerate(items):
        item_id = item["id"]
        item_type = item["type"]
        item_title = item.get("title", f"{item_type} {item_id}")
        
        logger.info(f"[{idx+1}/{total_items}] Removing: {item_title} ({item_type} {item_id})...")
        
        success = await remove_from_collection(page, col_id, item_id, item_type)
        if success:
            removed_count += 1
            logger.info(f"[{idx+1}/{total_items}] Successfully removed: {item_title}")
        else:
            failed_count += 1
            logger.warning(f"[{idx+1}/{total_items}] Failed to remove: {item_title}")
            
        # Throttling delay to avoid 429 rate limit
        delay = random.uniform(delay_min, delay_max)
        await asyncio.sleep(delay)

    logger.info(f"=== Clear Collection Finished for '{col_title}' ===")
    logger.info(f"Total: {total_items} | Removed: {removed_count} | Failed: {failed_count}")
    
    return {
        "collection_title": col_title,
        "collection_id": col_id,
        "total": total_items,
        "removed": removed_count,
        "failed": failed_count,
        "status": "success"
    }
