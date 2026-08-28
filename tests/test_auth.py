import pytest
from unittest.mock import AsyncMock, patch, MagicMock, PropertyMock
from zhihu_pipeline.config import TelegramConfig
from zhihu_pipeline.auth import (
    send_telegram_message,
    send_telegram_photo,
    check_login,
    handle_qr_login,
)

import asyncio

def test_send_telegram_message_mock():
    async def _run():
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            ok = await send_telegram_message("test_token", "123456", "Hello Test")
            assert ok is True
            mock_post.assert_called_once()
            args, kwargs = mock_post.call_args
            assert kwargs["json"]["chat_id"] == "123456"
            assert kwargs["json"]["text"] == "Hello Test"
    asyncio.run(_run())


def test_send_telegram_photo_mock():
    async def _run():
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            ok = await send_telegram_photo("test_token", "123456", b"fake_png_data", "QR Caption")
            assert ok is True
            mock_post.assert_called_once()
            args, kwargs = mock_post.call_args
            assert kwargs["data"]["chat_id"] == "123456"
            assert kwargs["data"]["caption"] == "QR Caption"
    asyncio.run(_run())


def test_check_login_logged_out():
    async def _run():
        mock_page = AsyncMock()
        mock_page.url = "https://www.zhihu.com/signin"
        mock_locator = MagicMock()
        mock_locator.count = AsyncMock(return_value=0)
        mock_page.locator = MagicMock(return_value=mock_locator)

        logged_in, username = await check_login(mock_page)
        assert logged_in is False
        assert username == ""
    asyncio.run(_run())


def test_check_login_logged_in():
    async def _run():
        mock_page = AsyncMock()
        mock_page.url = "https://www.zhihu.com"
        
        def mock_locator_fn(selector):
            loc = MagicMock()
            if "登录" in selector:
                loc.count = AsyncMock(return_value=0)
            elif "Avatar" in selector or "profileAvatar" in selector:
                loc.count = AsyncMock(return_value=1)
            elif "profileName" in selector or "ProfileHeader-name" in selector:
                loc.count = AsyncMock(return_value=1)
                first_elem = MagicMock()
                first_elem.inner_text = AsyncMock(return_value="TestUser")
                loc.first = first_elem
            else:
                loc.count = AsyncMock(return_value=0)
            return loc

        mock_page.locator = MagicMock(side_effect=mock_locator_fn)

        logged_in, username = await check_login(mock_page)
        assert logged_in is True
        assert username == "TestUser"
    asyncio.run(_run())


def test_handle_qr_login_flow():
    async def _run():
        mock_page = AsyncMock()
        
        # Mock url property changing from /signin to / after scan
        type(mock_page).url = PropertyMock(side_effect=[
            "https://www.zhihu.com/signin",
            "https://www.zhihu.com/signin",
            "https://www.zhihu.com"
        ])
        
        mock_qr_elem = MagicMock()
        mock_qr_elem.screenshot = AsyncMock(return_value=b"fake_qr_png_bytes")
        mock_qr_elem.is_visible = AsyncMock(return_value=True)

        def mock_locator_fn(selector):
            loc = MagicMock()
            if "Qrcode" in selector or "img" in selector:
                loc.count = AsyncMock(return_value=1)
                loc.first = mock_qr_elem
            else:
                loc.count = AsyncMock(return_value=0)
                loc.first = MagicMock(is_visible=AsyncMock(return_value=False))
            return loc

        mock_page.locator = MagicMock(side_effect=mock_locator_fn)
        mock_page.wait_for_selector = AsyncMock(return_value=mock_qr_elem)

        with patch("zhihu_pipeline.auth.send_telegram_photo", new_callable=AsyncMock) as mock_photo, \
             patch("zhihu_pipeline.auth.send_telegram_message", new_callable=AsyncMock) as mock_msg, \
             patch("zhihu_pipeline.auth.check_login", return_value=(True, "TestUser")) as mock_check, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            
            mock_photo.return_value = True
            mock_msg.return_value = True

            config = TelegramConfig(enabled=True, bot_token="token", chat_id="123", timeout=10)
            ok, user = await handle_qr_login(mock_page, config)

            assert ok is True
            assert user == "TestUser"
            mock_photo.assert_called_once()
            mock_msg.assert_called_once()

    asyncio.run(_run())
