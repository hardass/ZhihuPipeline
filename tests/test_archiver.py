import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from zhihu_pipeline.archiver import remove_from_collection, archive_item

def test_remove_from_collection_success():
    async def _run():
        mock_page = AsyncMock()
        mock_page.url = "https://www.zhihu.com/question/1/answer/2"

        # Mock collect button
        mock_collect_btn = MagicMock()
        mock_collect_btn.first = mock_collect_btn
        mock_collect_btn.count = AsyncMock(return_value=1)
        mock_collect_btn.scroll_into_view_if_needed = AsyncMock()
        mock_collect_btn.click = AsyncMock()

        # Mock modal
        mock_modal = MagicMock()
        mock_modal.wait_for = AsyncMock()

        # Mock items in modal
        mock_item_el = MagicMock()
        mock_name_el = MagicMock()
        mock_name_el.count = AsyncMock(return_value=1)
        mock_name_el.inner_text = AsyncMock(return_value="我的收藏")

        # Mock button state transition: "已收藏" -> "收藏"
        mock_btn_el = MagicMock()
        mock_btn_el.inner_text = AsyncMock(side_effect=["已收藏", "收藏"])
        mock_btn_el.click = AsyncMock()

        mock_item_el.locator = MagicMock(side_effect=lambda s: mock_name_el if "itemNameText" in s else mock_btn_el)

        mock_items = MagicMock()
        mock_items.count = AsyncMock(return_value=1)
        mock_items.nth = MagicMock(return_value=mock_item_el)

        mock_list_container = MagicMock()
        mock_list_container.count = AsyncMock(return_value=0)

        def mock_modal_locator(sel):
            if ".Favlists-item" in sel:
                return mock_items
            if ".Favlists-items" in sel:
                return mock_list_container
            return MagicMock(count=AsyncMock(return_value=0))

        mock_modal.locator = MagicMock(side_effect=mock_modal_locator)

        # Mock container
        mock_container = MagicMock()
        mock_container.first = mock_container
        mock_container.locator = MagicMock(return_value=mock_collect_btn)

        # Mock page locator
        def mock_page_locator(sel):
            if ".AnswerItem" in sel or "Post-content" in sel:
                return mock_container
            if "Favlists-content" in sel:
                loc = MagicMock()
                loc.filter = MagicMock(return_value=MagicMock(last=mock_modal))
                return loc
            if 'button[aria-label="关闭"]' in sel:
                close_btn = MagicMock()
                close_btn.count = AsyncMock(return_value=1)
                close_btn.last = MagicMock(click=AsyncMock())
                return close_btn
            mock_default = MagicMock()
            mock_default.first = mock_default
            mock_default.count = AsyncMock(return_value=0)
            return mock_default

        mock_page.locator = MagicMock(side_effect=mock_page_locator)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            removed = await remove_from_collection(mock_page, "我的收藏", item_type="answer")
            assert removed is True
            mock_btn_el.click.assert_called_once()

    asyncio.run(_run())


def test_remove_from_collection_already_uncollected():
    async def _run():
        mock_page = AsyncMock()
        mock_page.url = "https://www.zhihu.com/question/1/answer/2"

        mock_collect_btn = MagicMock()
        mock_collect_btn.first = mock_collect_btn
        mock_collect_btn.count = AsyncMock(return_value=1)
        mock_collect_btn.scroll_into_view_if_needed = AsyncMock()
        mock_collect_btn.click = AsyncMock()

        mock_modal = MagicMock()
        mock_modal.wait_for = AsyncMock()

        mock_item_el = MagicMock()
        mock_name_el = MagicMock()
        mock_name_el.count = AsyncMock(return_value=1)
        mock_name_el.inner_text = AsyncMock(return_value="我的收藏")

        mock_btn_el = MagicMock()
        mock_btn_el.inner_text = AsyncMock(return_value="收藏")

        mock_item_el.locator = MagicMock(side_effect=lambda s: mock_name_el if "itemNameText" in s else mock_btn_el)

        mock_items = MagicMock()
        mock_items.count = AsyncMock(return_value=1)
        mock_items.nth = MagicMock(return_value=mock_item_el)

        mock_modal.locator = MagicMock(side_effect=lambda s: mock_items if ".Favlists-item" in s else MagicMock(count=AsyncMock(return_value=0)))

        mock_container = MagicMock()
        mock_container.first = mock_container
        mock_container.locator = MagicMock(return_value=mock_collect_btn)

        def mock_page_locator(sel):
            if ".AnswerItem" in sel or "Post-content" in sel:
                return mock_container
            if "Favlists-content" in sel:
                loc = MagicMock()
                loc.filter = MagicMock(return_value=MagicMock(last=mock_modal))
                return loc
            if 'button[aria-label="关闭"]' in sel:
                close_btn = MagicMock()
                close_btn.count = AsyncMock(return_value=1)
                close_btn.last = MagicMock(click=AsyncMock())
                return close_btn
            mock_default = MagicMock()
            mock_default.first = mock_default
            mock_default.count = AsyncMock(return_value=0)
            return mock_default

        mock_page.locator = MagicMock(side_effect=mock_page_locator)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            removed = await remove_from_collection(mock_page, "我的收藏", item_type="answer")
            assert removed is True
            mock_btn_el.click.assert_not_called()

    asyncio.run(_run())
