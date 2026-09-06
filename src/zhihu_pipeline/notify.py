import os
from typing import Optional
import httpx
from loguru import logger
from zhihu_pipeline.config import NotifyConfig


class Notifier:
    """
    Unified client for Cloudflare Worker Notification Gateway (notify-gateway).
    Decouples Telegram credentials from the application by routing notifications
    through https://notify.perilcrosser.com/send.
    """

    def __init__(self, config: Optional[NotifyConfig] = None):
        self.config = config or NotifyConfig()
        self.gateway_url = self.config.gateway_url or "https://notify.perilcrosser.com/send"
        self.service_name = self.config.service or "ZhihuPipeline"
        self.api_key = self._resolve_api_key(self.config.api_key)

    def _resolve_api_key(self, configured_key: str) -> str:
        """Resolve API key from config, env var, or local key file."""
        if configured_key:
            return configured_key.strip()
        
        # 1. Check environment variable
        env_key = os.environ.get("NOTIFY_GATEWAY_KEY", "").strip()
        if env_key:
            return env_key

        # 2. Check local key files (~/.notify_key, ~/vibe/notify-gateway/.api_key)
        candidate_paths = [
            os.path.expanduser("~/.notify_key"),
            os.path.expanduser("~/vibe/notify-gateway/.api_key"),
            "/etc/sing-box/.notify_key",
        ]
        for path in candidate_paths:
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        key = f.read().strip()
                        if key:
                            logger.debug(f"Loaded notification gateway API key from {path}")
                            return key
                except Exception as e:
                    logger.debug(f"Failed to read key file {path}: {e}")

        return ""

    @property
    def is_configured(self) -> bool:
        """Check if notification is enabled and API key is present."""
        return bool(self.config.enabled and self.api_key and self.gateway_url)

    async def notify_text(self, message: str, title: str = "", silent: bool = False) -> bool:
        """
        Send text notification via the unified gateway.
        """
        if not self.config.enabled:
            logger.debug("Notification disabled in config. Skipping notify_text.")
            return False

        if not self.api_key:
            logger.warning("No Notification Gateway API Key configured. Skipping notification.")
            return False

        payload = {
            "service": self.service_name,
            "title": title,
            "message": message,
            "silent": silent
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "NotifyClient/1.0",
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(self.gateway_url, json=payload, headers=headers)
                if resp.status_code == 200:
                    logger.debug("Notification text sent successfully via gateway.")
                    return True
                else:
                    logger.error(f"Gateway notify_text failed with status {resp.status_code}: {resp.text}")
                    return False
        except Exception as e:
            logger.error(f"Failed to send text notification via gateway: {e}")
            return False

    async def notify_photo(self, photo_bytes: bytes, caption: str = "", title: str = "") -> bool:
        """
        Send photo (e.g. login QR code) via the unified gateway.
        """
        if not self.config.enabled:
            logger.debug("Notification disabled in config. Skipping notify_photo.")
            return False

        if not self.api_key:
            logger.warning("No Notification Gateway API Key configured. Skipping photo notification.")
            return False

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "NotifyClient/1.0",
        }

        data = {
            "service": self.service_name,
            "caption": caption,
        }
        if title:
            data["title"] = title

        files = {
            "photo": ("zhihu_login_qr.png", photo_bytes, "image/png")
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(self.gateway_url, data=data, files=files, headers=headers)
                if resp.status_code == 200:
                    logger.info("Notification photo sent successfully via gateway.")
                    return True
                else:
                    logger.error(f"Gateway notify_photo failed with status {resp.status_code}: {resp.text}")
                    return False
        except Exception as e:
            logger.error(f"Failed to send photo notification via gateway: {e}")
            return False
