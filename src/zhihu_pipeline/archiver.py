import asyncio
from loguru import logger
from playwright.async_api import Page

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
    2. Add the item to the 'archive' collection.
    3. Remove the item from the original collection.
    4. Close the modal dialog.
    """
    logger.info(f"Archiving {item_type} {item_id}: moving from '{current_collection_title}' to '{archive_collection_title}'...")

    # 1. Locate the Collect button
    # Try to find the button inside the target container first to avoid clicking buttons of other answers
    if item_type == "answer":
        container = page.locator(".AnswerItem").first
    else:
        container = page.locator(".Post-content, .Post-SideActions, .Post-topicsAndActions, body").first

    collect_btn = container.locator('button:has(svg.Zi--Star, svg.Zi--StarFill), button:has-text("收藏")')
    if await collect_btn.count() == 0:
        collect_btn = page.locator('button:has(svg.Zi--Star, svg.Zi--StarFill), button:has-text("收藏")').first

    if await collect_btn.count() == 0:
        logger.warning("Could not find the 'Collect' button on the page.")
        return False

    # 2. Click the Collect button and wait for the modal with retries
    modal = page.locator('.Favlists-content').last
    opened = False
    for attempt in range(3):
        try:
            await collect_btn.scroll_into_view_if_needed()
            await collect_btn.click(force=True)
            # Wait up to 3 seconds for the modal to be visible
            await modal.wait_for(state="visible", timeout=3000)
            opened = True
            break
        except Exception as e:
            logger.debug(f"Attempt {attempt+1} to open modal failed: {e}")
            await page.wait_for_timeout(1000)

    if not opened:
        logger.warning("Could not open collection modal dialog after 3 attempts.")
        return False

    # 3. Locate and toggle collections
    async def get_items_map(modal_el):
        items = modal_el.locator('.Favlists-item')
        count = await items.count()
        mapping = {}
        for i in range(count):
            item_el = items.nth(i)
            name_el = item_el.locator('.Favlists-itemNameText')
            if await name_el.count() > 0:
                name = (await name_el.inner_text()).strip()
                mapping[name] = item_el
        return mapping

    items_map = await get_items_map(modal)

    # If archive collection is not found, automatically create it
    if archive_collection_title not in items_map:
        logger.info(f"Archive collection '{archive_collection_title}' not found. Creating it...")
        create_btn = modal.locator('button:has-text("创建收藏夹")')
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
                await page.wait_for_timeout(2000)
                
                # Refresh the items map
                modal = page.locator('.Favlists-content').last
                items_map = await get_items_map(modal)
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
            await page.wait_for_timeout(1000)
            btn_text = (await btn.inner_text()).strip()
        
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
                await page.wait_for_timeout(1000)

    # 4. Close the modal
    close_btn = page.locator('button[aria-label="关闭"]').last
    if await close_btn.count() > 0:
        await close_btn.click()
        await page.wait_for_timeout(1000)
        
    return archive_success
