import asyncio
import json
import random
from typing import List, Dict, Any, Optional
from loguru import logger
from playwright.async_api import Page, Response

async def fetch_collections(page: Page) -> List[Dict[str, Any]]:
    """
    Fetch all collections of the logged-in user.
    """
    logger.info("Fetching collections from https://www.zhihu.com/collections/mine ...")
    
    collected_data = []
    
    async def on_response(response: Response):
        if "/api/v4/" in response.url and "collections" in response.url and response.status == 200:
            try:
                # Ensure it's a JSON response
                content_type = response.headers.get("content-type", "")
                if "json" in content_type:
                    data = await response.json()
                    if "data" in data:
                        collected_data.extend(data["data"])
            except Exception as e:
                logger.debug(f"Non-JSON or error response on collection URL: {e}")

    page.on("response", on_response)
    
    try:
        await page.goto("https://www.zhihu.com/collections/mine", wait_until="networkidle", timeout=20000)
    except Exception as e:
        logger.warning(f"Initial load networkidle timeout, proceeding: {e}")
        
    # Scroll to load all collections if there is paging
    # We will do a few scrolls to ensure we hit the bottom
    for _ in range(5):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1.5)
        
    page.remove_listener("response", on_response)
    
    # Deduplicate collections by ID
    unique_collections = {}
    for item in collected_data:
        c_id = item.get("id")
        if c_id:
            unique_collections[c_id] = {
                "id": c_id,
                "title": item.get("title", ""),
                "item_count": item.get("item_count", 0)
            }
            
    result = list(unique_collections.values())
    logger.info(f"Found {len(result)} collections.")
    return result

async def fetch_collection_items(page: Page, collection_id: int) -> List[Dict[str, Any]]:
    """
    Fetch all items inside a specific collection.
    """
    url = f"https://www.zhihu.com/collection/{collection_id}"
    logger.info(f"Fetching items from collection: {url}")
    
    collected_items = []
    
    async def on_response(response: Response):
        if f"/api/v4/collections/{collection_id}/items" in response.url and response.status == 200:
            try:
                data = await response.json()
                if "data" in data:
                    collected_items.extend(data["data"])
            except Exception as e:
                logger.error(f"Error parsing collection items JSON: {e}")

    page.on("response", on_response)
    
    try:
        await page.goto(url, wait_until="networkidle", timeout=25000)
    except Exception as e:
        logger.warning(f"Initial collection page load timeout, proceeding: {e}")
        
    # Navigate through pagination pages to load all items
    for page_num in range(1, 100):  # Safety limit of 100 pages
        current_count = len(collected_items)
        logger.info(f"Loaded {current_count} items from collection page {page_num}...")
        
        # Scroll to bottom to ensure pagination buttons are rendered
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1.5)
        
        # Locate Next Page button
        next_btn = page.locator('button.PaginationButton-next, button:has-text("下一页"), a:has-text("下一页")').first
        if await next_btn.count() == 0 or not await next_btn.is_visible():
            logger.info("Next page button not found or not visible. Reached the end.")
            break
            
        # Check if disabled
        is_disabled = await next_btn.evaluate("node => node.disabled")
        if is_disabled:
            logger.info("Next page button is disabled. Reached the end.")
            break
            
        # Click next page
        logger.info(f"Navigating to collection page {page_num + 1}...")
        try:
            await next_btn.scroll_into_view_if_needed()
            await next_btn.click()
            # Random wait to prevent rate limits
            await asyncio.sleep(random.uniform(2.5, 4.0))
        except Exception as e:
            logger.warning(f"Failed to navigate to next page: {e}")
            break
            
    page.remove_listener("response", on_response)
    
    # Process items and normalize them
    normalized_items = []
    seen_ids = set()
    for item in collected_items:
        content = item.get("content", {})
        if not content:
            continue
            
        c_type = content.get("type")
        c_id = content.get("id")
        
        # Deduplicate
        unique_key = f"{c_type}_{c_id}"
        if unique_key in seen_ids:
            continue
        seen_ids.add(unique_key)
        
        url_token = ""
        title = ""
        item_url = ""
        
        if c_type == "answer":
            title = content.get("question", {}).get("title", "")
            q_id = content.get("question", {}).get("id", "")
            item_url = f"https://www.zhihu.com/question/{q_id}/answer/{c_id}"
        elif c_type == "article":
            title = content.get("title", "")
            item_url = f"https://zhuanlan.zhihu.com/p/{c_id}"
        elif c_type == "pin":
            title = content.get("excerpt_title", "想法")
            item_url = f"https://www.zhihu.com/pin/{c_id}"
        else:
            # Other unsupported types like zvideo
            title = content.get("title", "未知类型")
            item_url = content.get("url", "")
            
        normalized_items.append({
            "type": c_type,
            "id": c_id,
            "url": item_url,
            "title": title,
            "raw_content": content
        })
        
    logger.info(f"Total items fetched from collection {collection_id}: {len(normalized_items)}")
    return normalized_items

async def fetch_content_detail(page: Page, item: Dict[str, Any], selectors: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fetch the detailed content of a single answer or article.
    Uses a hybrid approach: intercepts API response if possible, falls back to DOM selectors.
    """
    url = item["url"]
    item_type = item["type"]
    item_id = item["id"]
    logger.info(f"Fetching detail content for {item_type} {item_id}: {url}")
    
    xhr_data = {}
    
    async def on_response(response: Response):
        if response.status == 200:
            if item_type == "answer" and f"/api/v4/answers/{item_id}" in response.url:
                try:
                    data = await response.json()
                    if "content" in data:
                        xhr_data["json"] = data
                except Exception:
                    pass
            elif item_type == "article" and f"/api/v4/articles/{item_id}" in response.url:
                try:
                    data = await response.json()
                    if "content" in data:
                        xhr_data["json"] = data
                except Exception:
                    pass

    page.on("response", on_response)
    
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        
        # 404 Status check
        if response and response.status == 404:
            logger.warning(f"Zhihu returned 404 for {item_type} {item_id}: {url}. It has been deleted.")
            page.remove_listener("response", on_response)
            return {
                "title": item["title"],
                "content_html": "",
                "author_name": "",
                "author_url": "",
                "vote_count": 0,
                "created_time": None,
                "question_title": "",
                "is_deleted": True
            }
            
        # Check page title or ErrorPage class
        title = await page.title()
        if "404" in title or await page.locator(".ErrorPage").count() > 0:
            logger.warning(f"Zhihu ErrorPage/404 detected for {item_type} {item_id}: {url}. It has been deleted.")
            page.remove_listener("response", on_response)
            return {
                "title": item["title"],
                "content_html": "",
                "author_name": "",
                "author_url": "",
                "vote_count": 0,
                "created_time": None,
                "question_title": "",
                "is_deleted": True
            }

        # Give some time for network idle and dynamic components to render
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception as e:
        logger.warning(f"Timeout waiting for page load state, checking if data exists: {e}")
        
    page.remove_listener("response", on_response)
    
    detail = {
        "title": item["title"],
        "content_html": "",
        "author_name": "",
        "author_url": "",
        "vote_count": 0,
        "created_time": None,
        "question_title": ""
    }
    
    # Path A: Check if we captured XHR data
    if "json" in xhr_data:
        logger.info("Extracting content from intercepted XHR response.")
        data = xhr_data["json"]
        detail["content_html"] = data.get("content", "")
        detail["author_name"] = data.get("author", {}).get("name", "")
        detail["author_url"] = f"https://www.zhihu.com/people/{data.get('author', {}).get('url_token', '')}" if data.get("author", {}).get("url_token") else ""
        detail["vote_count"] = data.get("voteup_count", 0)
        detail["created_time"] = data.get("created_time")
        if item_type == "answer":
            detail["title"] = data.get("question", {}).get("title", detail["title"])
            detail["question_title"] = data.get("question", {}).get("title", "")
        elif item_type == "article":
            detail["title"] = data.get("title", detail["title"])
        return detail
        
    # Path B: DOM selector fallback
    logger.info("XHR not intercepted. Falling back to DOM selector extraction.")
    try:
        sel = selectors.get(item_type, {})
        if item_type == "answer":
            # Title
            title_sel = sel.get("question_title", "h1.QuestionHeader-title")
            if await page.locator(title_sel).count() > 0:
                detail["title"] = await page.locator(title_sel).first.inner_text()
                detail["question_title"] = detail["title"]
                
            # Content
            content_sel = sel.get("content", "div.RichContent-inner")
            # Wait for content to be visible
            await page.wait_for_selector(content_sel, timeout=5000)
            # Find rich text inner
            inner_content = page.locator(f"{content_sel} .RichText")
            if await inner_content.count() > 0:
                detail["content_html"] = await inner_content.first.inner_html()
            else:
                detail["content_html"] = await page.locator(content_sel).first.inner_html()
                
            # Author
            author_sel = sel.get("author", "div.AuthorInfo meta[itemprop='name']")
            if await page.locator(author_sel).count() > 0:
                detail["author_name"] = await page.locator(author_sel).first.get_attribute("content") or ""
            else:
                # Try fallback for author
                author_anchor = page.locator(".AuthorInfo-name a")
                if await author_anchor.count() > 0:
                    detail["author_name"] = await author_anchor.first.inner_text()
                    detail["author_url"] = "https://www.zhihu.com" + (await author_anchor.first.get_attribute("href") or "")
                    
            # Vote Count
            vote_sel = sel.get("vote_count", "button.VoteButton--up")
            if await page.locator(vote_sel).count() > 0:
                vote_text = await page.locator(vote_sel).first.inner_text()
                # Parse number
                import re
                nums = re.findall(r'\d+', vote_text.replace(',', ''))
                if nums:
                    detail["vote_count"] = int(nums[0])
                    
            # Time
            time_sel = sel.get("time", "div.ContentItem-time")
            if await page.locator(time_sel).count() > 0:
                time_text = await page.locator(time_sel).first.inner_text()
                # Note: We'll just store the text for now or parse it later
                detail["created_time_str"] = time_text
                
        elif item_type == "article":
            # Title
            title_sel = sel.get("title", "h1.Post-Title")
            if await page.locator(title_sel).count() > 0:
                detail["title"] = await page.locator(title_sel).first.inner_text()
                
            # Content
            content_sel = sel.get("content", "div.Post-RichTextContainer")
            await page.wait_for_selector(content_sel, timeout=5000)
            detail["content_html"] = await page.locator(content_sel).first.inner_html()
            
            # Author
            author_sel = sel.get("author", "div.AuthorInfo meta[itemprop='name']")
            if await page.locator(author_sel).count() > 0:
                detail["author_name"] = await page.locator(author_sel).first.get_attribute("content") or ""
            
            # Time
            time_sel = sel.get("time", "div.ContentItem-time")
            if await page.locator(time_sel).count() > 0:
                time_text = await page.locator(time_sel).first.inner_text()
                detail["created_time_str"] = time_text

    except Exception as e:
        logger.error(f"Error during DOM selector fallback extraction: {e}")
        
    return detail
