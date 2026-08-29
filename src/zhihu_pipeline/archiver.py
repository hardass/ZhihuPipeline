import asyncio
from typing import Union, Optional, Dict, Any
from loguru import logger
from playwright.async_api import Page, Locator

async def _open_collection_modal(page: Page, item_type: str = "answer") -> Optional[Locator]:
    """
    Locate and click the Collect/已收藏 button on the page to open the collections modal.
    """
    if item_type == "answer":
        container = page.locator(".AnswerItem").first
    else:
        container = page.locator(".Post-content, .Post-SideActions, .Post-topicsAndActions, body").first

    collect_btn = container.locator('button:has(svg.Zi--Star, svg.Zi--StarFill), button:has-text("收藏"), button:has-text("已收藏")')
    if await collect_btn.count() == 0:
        collect_btn = page.locator('button:has(svg.Zi--Star, svg.Zi--StarFill), button:has-text("收藏"), button:has-text("已收藏")').first

    if await collect_btn.count() == 0:
        logger.warning("Could not find the 'Collect' (收藏/已收藏) button on the page.")
        return None

    modal = page.locator('.Favlists-content, .Modal').filter(has_text="添加收藏").last
    
    opened = False
    for attempt in range(3):
        try:
            await collect_btn.scroll_into_view_if_needed()
            await collect_btn.click(force=True)
            # Wait up to 4 seconds for the modal to be visible
            await modal.wait_for(state="visible", timeout=4000)
            opened = True
            break
        except Exception as e:
            logger.debug(f"Attempt {attempt+1} to open modal failed: {e}")
            await asyncio.sleep(1.0)

    if not opened:
        logger.warning("Could not open collection modal dialog after 3 attempts.")
        return None

    # Scroll the list container to ensure all items are loaded
    list_container = modal.locator('.Favlists-items')
    if await list_container.count() > 0:
        try:
            await list_container.evaluate('node => node.scrollTo(0, node.scrollHeight)')
            await asyncio.sleep(0.8)
        except Exception as se:
            logger.debug(f"Failed to scroll list container: {se}")

    return modal

async def _get_modal_items_map(modal: Locator) -> Dict[str, Locator]:
    """
    Get a mapping of collection names to their respective list item locators.
    """
    items = modal.locator('.Favlists-item')
    count = await items.count()
    mapping = {}
    for i in range(count):
        item_el = items.nth(i)
        name_el = item_el.locator('.Favlists-itemNameText')
        if await name_el.count() > 0:
            name = (await name_el.inner_text()).strip()
            mapping[name] = item_el
    return mapping

async def _close_collection_modal(page: Page):
    """
    Close the open collections modal dialog.
    """
    close_btns = page.locator('button[aria-label="关闭"]')
    if await close_btns.count() > 0:
        try:
            await close_btns.last.click()
            await asyncio.sleep(0.5)
        except Exception:
            pass

async def remove_from_collection(
    page: Page,
    collection_title: str,
    item_type: str = "answer",
    item_url: Optional[str] = None
) -> bool:
    """
    Remove an item directly from a specific Zhihu collection using UI automation.
    This simulates clicking '已收藏' and unchecking the target collection in the modal,
    perfectly bypassing Zhihu API signature restrictions (x-zse-96 / 403).
    """
    logger.info(f"Removing item from collection '{collection_title}' via UI automation...")

    # Navigate to item URL if specified and not already on it
    if item_url and page.url != item_url:
        try:
            await page.goto(item_url, wait_until="domcontentloaded", timeout=20000)
            try:
                await page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass
        except Exception as ge:
            logger.warning(f"Failed to navigate to {item_url} before removal: {ge}")

    modal = await _open_collection_modal(page, item_type)
    if not modal:
        return False

    items_map = await _get_modal_items_map(modal)

    removed = False
    if collection_title in items_map:
        target_item_el = items_map[collection_title]
        btn = target_item_el.locator('button')
        btn_text = (await btn.inner_text()).strip()
        if btn_text == "已收藏":
            logger.info(f"Found '已收藏' for '{collection_title}'. Clicking to uncheck...")
            await btn.click()
            # Poll up to 3 seconds for state change to handle network lag
            for _ in range(6):
                await asyncio.sleep(0.5)
                btn_text = (await btn.inner_text()).strip()
                if btn_text == "收藏":
                    removed = True
                    logger.info(f"Successfully uncollected from '{collection_title}'.")
                    break
        elif btn_text == "收藏":
            logger.info(f"Item is already not in collection '{collection_title}'.")
            removed = True
    else:
        logger.warning(f"Collection '{collection_title}' not found in modal collection list.")

    await _close_collection_modal(page)
    return removed

async def archive_item(
    page: Page,
    item_type: str,
    item_id: str,
    current_collection_title: str,
    archive_collection_title: str
) -> bool:
    """
    Archive a synced item:
    1. Locate and click the 'Collect' button on the page with retries.
    2. Add the item to the 'archive' collection (creating it if absent).
    3. Remove the item from the original collection.
    4. Close the modal dialog.
    """
    logger.info(f"Archiving {item_type} {item_id}: moving from '{current_collection_title}' to '{archive_collection_title}'...")

    modal = await _open_collection_modal(page, item_type)
    if not modal:
        return False

    create_btn = modal.locator('button:has-text("创建收藏"), button:has-text("创建")').first
    items_map = await _get_modal_items_map(modal)

    # If archive collection is not found, automatically create it
    if archive_collection_title not in items_map:
        logger.info(f"Archive collection '{archive_collection_title}' not found in list. Creating it...")
        if await create_btn.count() > 0:
            try:
                await create_btn.click()
                title_input = page.locator('input.Input[placeholder="收藏标题"]')
                await title_input.wait_for(state="visible", timeout=3000)
                await title_input.fill(archive_collection_title)

                # Set to Private (私密) for privacy
                private_radio = page.locator('input[name="isPublic"][value="false"]')
                if await private_radio.count() > 0:
                    await private_radio.click()

                # Confirm creation
                confirm_btn = page.locator('button[type="submit"]:has-text("确认创建")')
                await confirm_btn.wait_for(state="visible", timeout=3000)
                await confirm_btn.click()
                
                # Wait for the creation modal to close and return to selection modal
                await asyncio.sleep(2.0)
                
                # Refresh modal and items map
                modal = page.locator('.Favlists-content, .Modal').filter(has_text="添加收藏").last
                list_container = modal.locator('.Favlists-items')
                if await list_container.count() > 0:
                    await list_container.evaluate('node => node.scrollTo(0, node.scrollHeight)')
                    await asyncio.sleep(0.8)
                items_map = await _get_modal_items_map(modal)
            except Exception as ce:
                logger.warning(f"Failed to create new collection: {ce}")
        else:
            logger.warning("Could not find '创建收藏夹' button.")

    # Add to archive collection
    archive_success = False
    if archive_collection_title in items_map:
        archive_item_el = items_map[archive_collection_title]
        btn = archive_item_el.locator('button')
        btn_text = (await btn.inner_text()).strip()
        if btn_text == "收藏":
            logger.info(f"Adding item to archive: '{archive_collection_title}'")
            await btn.click()
            for _ in range(6):
                await asyncio.sleep(0.5)
                btn_text = (await btn.inner_text()).strip()
                if btn_text == "已收藏":
                    break
        
        if btn_text == "已收藏":
            archive_success = True
    else:
        logger.warning(f"Archive collection '{archive_collection_title}' still not found/created.")

    # Remove from original collection
    if archive_success and current_collection_title in items_map:
        if current_collection_title != archive_collection_title:
            original_item_el = items_map[current_collection_title]
            btn = original_item_el.locator('button')
            btn_text = (await btn.inner_text()).strip()
            if btn_text == "已收藏":
                logger.info(f"Removing item from original collection: '{current_collection_title}'")
                await btn.click()
                for _ in range(6):
                    await asyncio.sleep(0.5)
                    btn_text = (await btn.inner_text()).strip()
                    if btn_text == "收藏":
                        break

    await _close_collection_modal(page)
    return archive_success
