import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from zhihu_pipeline.config import Config, TelegramConfig, OutputConfig
from zhihu_pipeline.bot import TelegramBotDaemon


@pytest.fixture
def dummy_config():
    cfg = Config()
    cfg.telegram = TelegramConfig(
        enabled=True,
        bot_token="test_token_12345",
        chat_id="123456789"
    )
    cfg.output = OutputConfig(vault_path="/tmp/test_vault")
    return cfg


def test_bot_unauthorized_chat_id(dummy_config):
    async def _run():
        daemon = TelegramBotDaemon(dummy_config)
        daemon.send_message = AsyncMock(return_value=True)

        # Message from unknown chat
        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 999999999},
                "text": "/sync"
            }
        }
        await daemon.process_update(update)

        # Should send unauthorized error
        daemon.send_message.assert_called_once()
        assert "无权访问" in daemon.send_message.call_args[0][1]

    asyncio.run(_run())


def test_bot_start_command(dummy_config):
    async def _run():
        daemon = TelegramBotDaemon(dummy_config)
        daemon.send_message = AsyncMock(return_value=True)

        update = {
            "update_id": 2,
            "message": {
                "chat": {"id": 123456789},
                "text": "/start"
            }
        }
        await daemon.process_update(update)

        daemon.send_message.assert_called_once()
        assert "Zhihu Pipeline 机器人已就绪" in daemon.send_message.call_args[0][1]

    asyncio.run(_run())


def test_bot_status_command(dummy_config):
    async def _run():
        daemon = TelegramBotDaemon(dummy_config)
        daemon.send_message = AsyncMock(return_value=True)
        daemon.engine.manifest.data = {
            "last_sync": "2026-08-28 22:00:00",
            "synced_items": {
                "item_1": {"category": "技术", "status": "synced"},
                "item_2": {"category": "商业", "status": "synced"}
            }
        }
        daemon.engine.manifest.get_untagged_items = MagicMock(return_value=[])

        update = {
            "update_id": 3,
            "message": {
                "chat": {"id": 123456789},
                "text": "/status"
            }
        }
        await daemon.process_update(update)

        daemon.send_message.assert_called_once()
        status_text = daemon.send_message.call_args[0][1]
        assert "已同步总篇数*: `2`" in status_text
        assert "技术" in status_text

    asyncio.run(_run())


def test_bot_sync_locked_concurrency(dummy_config):
    async def _run():
        daemon = TelegramBotDaemon(dummy_config)
        daemon.send_message = AsyncMock(return_value=True)
        
        # Simulate already locked
        await daemon.sync_lock.acquire()
        
        update = {
            "update_id": 4,
            "message": {
                "chat": {"id": 123456789},
                "text": "/sync"
            }
        }
        await daemon.process_update(update)
        # Give event loop a microtick to process task
        await asyncio.sleep(0.01)

        daemon.send_message.assert_called_with("123456789", "⚠️ *当前已有一个同步任务正在运行中*，请稍候完成。")
        daemon.sync_lock.release()

    asyncio.run(_run())
