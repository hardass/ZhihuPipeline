import asyncio
import signal
import sys
import time
import random
from datetime import datetime, timedelta
from typing import Optional, Any
import httpx
from loguru import logger

from .config import Config, load_config
from .sync_engine import SyncEngine
from .notify import Notifier


class TelegramBotDaemon:
    """Pipeline scheduler with an optional Telegram control plane."""

    def __init__(self, config: Config):
        self.config = config
        self.bot_token = config.telegram.bot_token
        self.admin_chat_id = str(config.telegram.chat_id).strip()
        self.api_base = f"https://api.telegram.org/bot{self.bot_token}"
        self.notifier = Notifier(config.notify)
        self.engine = SyncEngine(config)
        self.sync_lock = asyncio.Lock()
        self.is_running = False
        self.last_update_id: Optional[int] = None
        self._last_alert: tuple[str, float] = ("", 0.0)

    # ------------------------------------------------------------ help/ops glue

    def reload_config(self) -> Config:
        """
        Re-read config.yaml before every scheduled pass.

        The file is bind-mounted into the container, so edits (vault path, sync
        mode, inbox policy) currently require a container restart to take effect
        mid-run. Reloading per tick keeps a repaired configuration live and
        rebuilds the engine so a moved manifest is picked up too.
        """
        try:
            new_config = load_config()
        except Exception as exc:
            logger.error(f"Cannot reload config.yaml, keeping the running one: {exc}")
            return self.config
        self.config = new_config
        self.bot_token = new_config.telegram.bot_token
        self.admin_chat_id = str(new_config.telegram.chat_id).strip()
        self.notifier = Notifier(new_config.notify)
        self.engine = SyncEngine(new_config)
        return new_config

    def publish_line(self, stats: dict) -> str:
        """Human-readable delivery verdict that never claims an unused channel worked."""
        channel = str(stats.get("publish_channel") or "none")
        published = stats.get("published")
        pending = stats.get("publish_pending") or []
        error = stats.get("publish_error")
        consumed = stats.get("inbox_consumed") or 0

        labels = {"git": "GitHub", "livesync": "LiveSync node", "none": "none (no delivery channel configured)"}
        channel_label = labels.get(channel, channel)

        if published is True:
            verdict = "confirmed delivered"
        elif published is False:
            verdict = f"delivery unconfirmed ({pending.__len__()} item(s) pending retry)" if pending else "delivery unconfirmed"
        else:
            verdict = "nothing new to deliver this pass"

        if error:
            verdict = f"{verdict} | {error}"

        return (
            f"• *Delivery channel*: `{channel_label}`\n"
            f"• *Delivery status*: `{verdict}`\n"
            f"• *Inbox consumed*: `{consumed}` item(s)"
        )

    async def notify(self, message: str, title: str = "Zhihu Pipeline") -> bool:
        """Send an undeduplicated notice, falling back to Telegram when configured."""
        sent = await self.notifier.notify_text(message, title=title)
        if (
            not sent
            and self.config.telegram.enabled
            and self.bot_token
            and self.admin_chat_id
        ):
            await self.send_message(self.admin_chat_id, f"*{title}*\n{message}")
        return sent

    async def alert(self, message: str, title: str = "⚠️ Zhihu Pipeline error") -> bool:
        """
        Notify with a one-hour dedup window per distinct message.

        A permanently broken channel must alert, but a two-hourly loop should not
        turn into an unreadable stream of the same warning.
        """
        signature = message[:200]
        now = time.time()
        last_signature, last_time = self._last_alert
        if signature == last_signature and now - last_time < 3600:
            logger.warning(f"Alert deduplicated within one hour: {signature}")
            return False
        self._last_alert = (signature, now)
        sent = await self.notifier.notify_text(message, title=title)
        if (
            not sent
            and self.config.telegram.enabled
            and self.bot_token
            and self.admin_chat_id
        ):
            await self.send_message(self.admin_chat_id, f"*{title}*\n{message}")
        return sent


    async def send_message(self, chat_id: str | int, text: str, parse_mode: str = "Markdown") -> bool:
        """Send text message to Telegram chat."""
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(
                    f"{self.api_base}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": parse_mode
                    }
                )
                if res.status_code != 200:
                    # Fallback to plain text if Markdown parsing failed
                    await client.post(
                        f"{self.api_base}/sendMessage",
                        json={
                            "chat_id": chat_id,
                            "text": text
                        }
                    )
                return res.status_code == 200
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    async def set_my_commands(self):
        """Register bot command menu in Telegram."""
        commands = [
            {"command": "sync", "description": "Trigger a Zhihu favourites sync now"},
            {"command": "status", "description": "Show system status and sync stats"},
            {"command": "help", "description": "Show help"}
        ]
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(f"{self.api_base}/setMyCommands", json={"commands": commands})
        except Exception as e:
            logger.warning(f"Failed to set bot commands: {e}")

    async def handle_command_start(self, chat_id: str):
        """Handle /start or /help."""
        interval = self.config.sync.schedule_interval_hours
        msg = (
            "🤖 *Zhihu Pipeline bot is ready*\n\n"
            "Available commands:\n"
            "• `/sync` - Trigger a Zhihu favourites sync now\n"
            "• `/status` - Show sync history and storage status\n"
            "• `/help` - Show this help menu\n\n"
            f"⏰ *Auto scan*: checks for new favourites every ~`{interval}` hours."
        )
        await self.send_message(chat_id, msg)

    async def handle_command_status(self, chat_id: str):
        """Handle /status command."""
        try:
            manifest_data = self.engine.manifest.data
            synced_items = manifest_data.get("synced_items", {})
            total_items = len(synced_items)
            
            # Count by collection
            coll_counts: dict[str, int] = {}
            for it in synced_items.values():
                coll = it.get("category") or it.get("collection", "uncategorised")
                coll_counts[coll] = coll_counts.get(coll, 0) + 1

            coll_summary = "\n".join([f"  • `{k}`: {v} item(s)" for k, v in coll_counts.items()]) if coll_counts else "  • No sync records yet"
            last_sync = manifest_data.get("last_sync", "never")

            interval = self.config.sync.schedule_interval_hours
            schedule_status = f"enabled (every ~{interval}h)" if self.config.sync.schedule_enabled else "disabled"

            stats = self.engine.manifest.get_stats()
            backlog = self.engine.delivery_backlog()
            delivery_line = f"• *Pending delivery*: `{len(backlog['waiting'])}` item(s)\n"
            if backlog["gone"]:
                delivery_line += (
                    f"• *Undeliverable (file gone from disk)*: `{len(backlog['gone'])}` item(s) (not counted as pending)\n"
                )
            status_msg = (
                "📊 *Zhihu Pipeline status*\n\n"
                f"• *Total synced*: `{total_items}` item(s)\n"
                f"{delivery_line}"
                f"• *Delivery channel*: `{self.engine.publish_channel}`\n"
                f"• *Auto scan interval*: `{schedule_status}`\n"
                f"• *Last sync*: `{last_sync}`\n"
                f"• *Notes path*: `{self.config.output.vault_path}`\n"
                f"• *State file*: `{self.engine.manifest_path}`\n\n"
                f"*Per-collection stats*:\n{coll_summary}"
            )
            await self.send_message(chat_id, status_msg)
        except Exception as e:
            logger.error(f"Failed to query status: {e}")
            await self.send_message(chat_id, f"❌ Status query failed: {e}")

    async def handle_command_sync(self, chat_id: str):
        """Handle /sync command with concurrency guard."""
        if self.sync_lock.locked():
            await self.send_message(chat_id, "⚠️ *A sync task is already running*, wait for it to finish.")
            return

        async with self.sync_lock:
            await self.send_message(chat_id, "🔄 *Zhihu favourites sync started...*\nIf a QR login is needed, the code will be pushed to this chat.")
            start_time = time.time()
            try:
                # Run the complete sync flow
                stats = (await self.engine.run()) or {}
                elapsed = int(time.time() - start_time)
                
                synced_count = stats.get("synced", 0)
                failed_count = stats.get("failed", 0)
                tagged_count = stats.get("tagged", 0)
                tag_failed_count = stats.get("tag_failed", 0)

                summary = (
                    "✅ *Zhihu favourites sync complete!*\n\n"
                    f"• *Newly synced*: `{synced_count}` item(s)\n"
                    f"• *Failed*: `{failed_count}` item(s)\n"
                    f"• *Tagged this run*: `{tagged_count}` ok / `{tag_failed_count}` failed\n"
                    f"• *Duration*: `{elapsed}` s\n"
                    f"{self.publish_line(stats)}\n"
                    f"• *Saved to*: `{self.config.output.vault_path}`"
                )
                await self.send_message(chat_id, summary)
                pending_count = len(stats.get("publish_pending") or [])
                if stats.get("published") is not True and synced_count:
                    reason = stats.get("publish_error") or f"{pending_count} item(s) not confirmed in the shared library"
                    await self.alert(
                        f"{synced_count} new note(s) this run, but the delivery channel did not confirm delivery: {reason}",
                        title="❌ Zhihu Pipeline delivery unconfirmed",
                    )
            except Exception as e:
                logger.error(f"Sync failed in bot handler: {e}")
                await self.send_message(chat_id, f"❌ *Sync failed*:\n`{str(e)}`")

    async def run_scheduled_sync_loop(self):
        """Periodic background worker with anti-crawling randomized jitter."""
        if not self.config.sync.schedule_enabled:
            logger.info("Periodic automated sync is disabled in config.")
            return

        interval_hours = self.config.sync.schedule_interval_hours
        jitter_mins = self.config.sync.schedule_jitter_minutes
        logger.info(f"Scheduled periodic sync enabled (every ~{interval_hours}h ±{jitter_mins}m jitter).")

        # Initial random startup delay (2-4 mins)
        initial_wait = random.randint(120, 240)
        logger.info(f"First periodic check scheduled in {initial_wait}s...")
        await asyncio.sleep(initial_wait)

        while self.is_running:
            try:
                if self.sync_lock.locked():
                    logger.info("Manual sync in progress; skipping scheduled tick.")
                else:
                    async with self.sync_lock:
                        logger.info("⏰ [Scheduled Sync] Starting automated periodic sync pass...")
                        self.reload_config()
                        stats = (await self.engine.run()) or {}
                        synced_count = stats.get("synced", 0)
                        published = stats.get("published")
                        pending = stats.get("publish_pending") or []

                        if synced_count == 0 and not pending and published is not False:
                            logger.info("[Scheduled Sync] Finished: nothing new to download.")
                        else:
                            summary_lines = [
                                f"• Newly synced: <b>{synced_count}</b> item(s)",
                                f"• Delivery channel: <code>{stats.get('publish_channel', 'none')}</code>",
                            ]
                            if published is True:
                                summary_lines.append(
                                    f"• Delivery status: <b>confirmed delivered</b> (inbox consumed {stats.get('inbox_consumed', 0)} item(s))"
                                )
                                title = "⏰ Scheduled sync complete"
                            else:
                                reason = stats.get("publish_error") or f"{len(pending)} item(s) unconfirmed"
                                summary_lines.append(f"• Delivery status: <b>unconfirmed</b> - {reason}")
                                summary_lines.append(
                                    "• These notes stay in the Zhihu favourites folder and are retried next cycle."
                                )
                                title = "❌ Zhihu Pipeline delivery unconfirmed"
                            text = "\n".join(summary_lines)
                            if published is True:
                                await self.notify(text, title)
                            else:
                                # Deduplicated inside alert(): a dead channel must
                                # page, but not once every two hours.
                                await self.alert(text, title=title)
            except asyncio.CancelledError:
                break
            except Exception as e:
                # A swallowed scheduler exception is how a broken delivery stayed
                # invisible last time: fail-closed aborts must reach the operator.
                logger.exception(f"[Scheduled Sync] Error during pass: {e}")
                await self.alert(
                    f"The scheduled sync pass failed outright: {type(e).__name__}: {e}\n"
                    "New notes may still be sitting on the NAS disk; check the container logs.",
                    title="❌ Zhihu Pipeline sync aborted",
                )

            # Calculate next sleep interval with randomized jitter (e.g. 2h ± 25m -> 95m to 145m)
            base_sec = interval_hours * 3600
            jitter_sec = random.uniform(-jitter_mins * 60, jitter_mins * 60)
            sleep_duration = max(1800.0, base_sec + jitter_sec)
            next_eta = datetime.now() + timedelta(seconds=sleep_duration)
            logger.info(
                f"[Scheduled Sync] Next run in {int(sleep_duration // 60)} minutes "
                f"(at ~{next_eta.strftime('%H:%M:%S')})"
            )

            try:
                await asyncio.sleep(sleep_duration)
            except asyncio.CancelledError:
                break

    async def process_update(self, update: dict[str, Any]):
        """Process a single incoming Telegram update."""
        message = update.get("message")
        if not message:
            return

        chat = message.get("chat", {})
        chat_id = str(chat.get("id"))
        text = message.get("text", "").strip()

        # Security check: only respond to admin_chat_id
        if self.admin_chat_id and chat_id != self.admin_chat_id:
            logger.warning(f"Unauthorized access attempt from chat_id={chat_id}, username={chat.get('username')}")
            await self.send_message(chat_id, "⛔ *Access denied*: you are not this bot's authorised admin.")
            return

        cmd = text.split()[0].lower() if text else ""
        logger.info(f"Received bot command '{cmd}' from admin {chat_id}")

        if cmd in ["/start", "/help"]:
            await self.handle_command_start(chat_id)
        elif cmd == "/status":
            await self.handle_command_status(chat_id)
        elif cmd == "/sync":
            # Run sync task in background so polling doesn't block
            asyncio.create_task(self.handle_command_sync(chat_id))
        else:
            await self.send_message(chat_id, "❓ Unknown command. Send `/help` for the list of commands.")

    async def run_polling(self):
        """Main long-polling loop."""
        self.is_running = True
        # Launch periodic background sync worker
        schedule_task = asyncio.create_task(self.run_scheduled_sync_loop())

        try:
            if not self.config.telegram.enabled:
                logger.info("Telegram polling is disabled; scheduled sync remains active.")
                await schedule_task
                return

            if not self.bot_token or not self.admin_chat_id:
                logger.error("Telegram bot_token or chat_id is missing; scheduled sync remains active.")
                await schedule_task
                return

            logger.info(f"Starting Telegram Bot daemon for admin chat {self.admin_chat_id}...")
            async with httpx.AsyncClient(timeout=35.0) as client:
                # A revoked or malformed token returns 404. Keep the scheduler
                # alive in degraded mode instead of retrying the same bad URL.
                preflight = await client.get(f"{self.api_base}/getMe")
                if preflight.status_code == 404:
                    logger.error(
                        "Telegram bot API returned 404; disabling Telegram polling "
                        "while keeping scheduled sync active."
                    )
                    await schedule_task
                    return
                if preflight.status_code != 200:
                    logger.error(
                        f"Telegram bot API preflight failed with status {preflight.status_code}; "
                        "scheduled sync remains active."
                    )
                    await schedule_task
                    return

                await self.set_my_commands()
                await self.send_message(
                    self.admin_chat_id,
                    "🚀 *Zhihu Pipeline daemon started!*\nSend `/sync` to start a fetch at any time.",
                )

                while self.is_running:
                    try:
                        params: dict[str, Any] = {"timeout": 30}
                        if self.last_update_id is not None:
                            params["offset"] = self.last_update_id + 1

                        response = await client.get(f"{self.api_base}/getUpdates", params=params)
                        if response.status_code == 200:
                            data = response.json()
                            updates = data.get("result", [])
                            for update in updates:
                                self.last_update_id = update.get("update_id", self.last_update_id)
                                await self.process_update(update)
                        elif response.status_code == 409:
                            logger.warning("Telegram getUpdates conflict (another bot instance running?). Retrying in 5s...")
                            await asyncio.sleep(5)
                        else:
                            logger.warning(f"getUpdates returned status {response.status_code}")
                            await asyncio.sleep(2)
                    except httpx.TimeoutException:
                        continue
                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        logger.error(f"Error in Telegram long polling loop: {e}")
                        await asyncio.sleep(3)
        finally:
            schedule_task.cancel()
            try:
                await schedule_task
            except asyncio.CancelledError:
                pass
            logger.info("Telegram Bot daemon polling stopped.")

    async def run_worker(self):
        """Run only the scheduled pipeline worker, without Telegram polling."""
        self.is_running = True
        try:
            await self.run_scheduled_sync_loop()
        finally:
            self.is_running = False
            logger.info("Scheduled pipeline worker stopped.")

    def stop(self):
        """Signal the daemon to stop."""
        self.is_running = False
