import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from zhihu_pipeline.config import NotifyConfig
from zhihu_pipeline.notify import Notifier


def test_notifier_text_success():
    async def _run():
        config = NotifyConfig(
            enabled=True,
            gateway_url="https://notify.example.test/send",
            api_key="test_api_key",
            service="ZhihuPipeline"
        )
        notifier = Notifier(config)
        assert notifier.is_configured is True

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_post.return_value = mock_resp

            ok = await notifier.notify_text("Hello World", title="Test Title")
            assert ok is True
            mock_post.assert_called_once()
            args, kwargs = mock_post.call_args
            assert args[0] == "https://notify.example.test/send"
            assert kwargs["headers"]["Authorization"] == "Bearer test_api_key"
            assert kwargs["headers"]["User-Agent"] == "NotifyClient/1.0"
            assert kwargs["json"]["message"] == "Hello World"
            assert kwargs["json"]["title"] == "Test Title"
            assert kwargs["json"]["service"] == "ZhihuPipeline"

    asyncio.run(_run())


def test_notifier_photo_success():
    async def _run():
        config = NotifyConfig(
            enabled=True,
            gateway_url="https://notify.example.test/send",
            api_key="test_api_key",
            service="ZhihuPipeline"
        )
        notifier = Notifier(config)

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_post.return_value = mock_resp

            ok = await notifier.notify_photo(b"png_bytes", caption="Scan QR", title="QR Code")
            assert ok is True
            mock_post.assert_called_once()
            args, kwargs = mock_post.call_args
            assert args[0] == "https://notify.example.test/send"
            assert kwargs["headers"]["Authorization"] == "Bearer test_api_key"
            assert kwargs["headers"]["User-Agent"] == "NotifyClient/1.0"
            assert kwargs["data"]["caption"] == "Scan QR"
            assert kwargs["data"]["service"] == "ZhihuPipeline"
            assert "photo" in kwargs["files"]

    asyncio.run(_run())


def test_notifier_disabled():
    async def _run():
        config = NotifyConfig(enabled=False, api_key="key")
        notifier = Notifier(config)
        ok = await notifier.notify_text("msg")
        assert ok is False

    asyncio.run(_run())
