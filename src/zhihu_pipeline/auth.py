import os
import asyncio
import httpx
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from loguru import logger
from zhihu_pipeline.config import TelegramConfig

async def launch_browser_context(user_data_dir: str = "~/.zhihu_pipeline/chrome_profile", headless: bool = False) -> BrowserContext:
    """
    Launch a persistent Chromium browser context.
    Persistent context preserves session state, cookies, and local storage automatically.
    """
    profile_dir = os.path.abspath(os.path.expanduser(user_data_dir))
    os.makedirs(profile_dir, exist_ok=True)
    logger.info(f"Launching persistent browser context (headless={headless}) with profile at: {profile_dir}")

    p = await async_playwright().start()
    try:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=headless,
            args=[
                "--no-default-browser-check",
                "--no-first-run",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-dev-shm-usage",
            ],
            viewport={"width": 1280, "height": 800},
        )
        context._playwright_instance = p
        logger.info("Browser context launched successfully.")
        return context
    except Exception as e:
        logger.error(f"Failed to launch persistent browser context: {e}")
        await p.stop()
        raise

async def connect_chrome(port: int = 9222) -> tuple[Browser, BrowserContext]:
    """
    Backward-compatible CDP connection method.
    """
    logger.info(f"Connecting to Chrome on port {port} via CDP...")
    p = await async_playwright().start()
    try:
        browser = await p.chromium.connect_over_cdp(f"http://localhost:{port}")
        browser._playwright_instance = p
        if not browser.contexts:
            raise RuntimeError("No browser contexts found. Make sure Chrome is running.")
        return browser, browser.contexts[0]
    except Exception as e:
        await p.stop()
        raise ConnectionError(f"Could not connect to Chrome debugging port {port}: {e}") from e

async def get_or_create_page(context: BrowserContext) -> Page:
    """
    Get the first open page in the context, or create a new one if none exist.
    """
    pages = context.pages
    if pages:
        logger.debug("Reusing existing page/tab.")
        return pages[0]
    else:
        logger.debug("Creating new page/tab.")
        return await context.new_page()

async def check_login(page: Page) -> tuple[bool, str]:
    """
    Verify if the user is logged into Zhihu by checking DOM indicators.
    """
    logger.info("Checking Zhihu login status via DOM...")
    try:
        current_url = page.url
        if "zhihu.com" not in current_url or "/signin" in current_url:
            await page.goto("https://www.zhihu.com", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2000)
        
        current_url = page.url
        if "/signin" in current_url:
            logger.warning("Zhihu redirected to sign-in page. User is logged out.")
            return False, ""
            
        login_btn = page.locator("button:has-text('登录/注册'), button:has-text('登录'), a:has-text('登录')")
        if await login_btn.count() > 0:
            for i in range(await login_btn.count()):
                if await login_btn.nth(i).is_visible():
                    logger.warning("Login button is visible. User is logged out.")
                    return False, ""

        avatar = page.locator(".AppHeader-profileAvatar, .AppHeader-user, .AppHeader-profile, .Avatar")
        if await avatar.count() > 0:
            logger.info("Profile indicator found. User is logged in.")
            username = "Zhihu User"
            try:
                name_loc = page.locator(".AppHeader-profileName, .ProfileHeader-name")
                if await name_loc.count() > 0:
                    username = await name_loc.first.inner_text()
            except Exception:
                pass
            return True, username

        tabs = page.locator("a:has-text('关注'), a:has-text('推荐'), a:has-text('热榜')")
        if await tabs.count() > 0:
            logger.info("Zhihu feed tabs found. User is logged in.")
            return True, "Zhihu User"

        logger.warning("Could not find any logged-in indicators. User is logged out.")
        return False, ""
    except Exception as e:
        logger.error(f"Error checking login status: {e}")
        return False, ""

async def send_telegram_message(bot_token: str, chat_id: str, text: str) -> bool:
    """
    Send text message to Telegram chat.
    """
    if not bot_token or not chat_id:
        logger.warning("Telegram bot_token or chat_id not configured. Skipping message notification.")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                logger.debug("Telegram message sent successfully.")
                return True
            else:
                logger.error(f"Telegram sendMessage failed with status {resp.status_code}: {resp.text}")
                return False
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return False

async def send_telegram_photo(bot_token: str, chat_id: str, photo_bytes: bytes, caption: str = "") -> bool:
    """
    Send photo to Telegram chat.
    """
    if not bot_token or not chat_id:
        logger.warning("Telegram bot_token or chat_id not configured. Skipping photo notification.")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    data = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
    files = {"photo": ("zhihu_login_qr.png", photo_bytes, "image/png")}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, data=data, files=files)
            if resp.status_code == 200:
                logger.info("Telegram QR photo sent successfully.")
                return True
            else:
                logger.error(f"Telegram sendPhoto failed with status {resp.status_code}: {resp.text}")
                return False
    except Exception as e:
        logger.error(f"Failed to send Telegram photo: {e}")
        return False

async def handle_qr_login(page: Page, telegram_config: TelegramConfig) -> tuple[bool, str]:
    """
    Handle automated QR Code login flow with Telegram push notifications.
    Captures Zhihu login QR code, pushes to Telegram, and waits for user to scan and complete login.
    """
    logger.info("Initiating QR code login flow...")
    timeout_sec = telegram_config.timeout if telegram_config else 300
    
    try:
        if "/signin" not in page.url:
            await page.goto("https://www.zhihu.com/signin", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2000)

        # Look for QR code tab if password tab is active
        qr_tab = page.locator("div.SignFlow-tab:has-text('二维码登录'), button:has-text('二维码登录'), .SignFlow-tabs button:first-child")
        if await qr_tab.count() > 0 and await qr_tab.first.is_visible():
            try:
                await qr_tab.first.click()
                await page.wait_for_timeout(1500)
            except Exception as e:
                logger.debug(f"Click QR tab warning: {e}")

        # Locate QR image element
        qr_selectors = [
            "img.Qrcode-qrcode",
            ".Qrcode-img img",
            ".SignFlow-qrcodeContainer img",
            ".SignContainer-content img",
            "canvas.Qrcode-qrcode",
            ".Qrcode-container"
        ]
        
        qr_element = None
        for sel in qr_selectors:
            loc = page.locator(sel)
            if await loc.count() > 0 and await loc.first.is_visible():
                qr_element = loc.first
                break

        if not qr_element:
            # Try waiting for the default selector
            try:
                qr_element = await page.wait_for_selector("img.Qrcode-qrcode, .Qrcode-img img, .Qrcode-container", timeout=8000)
            except Exception:
                pass

        if not qr_element:
            logger.error("Could not find Zhihu QR code element on signin page.")
            # Fallback: take full page screenshot
            photo_bytes = await page.screenshot()
        else:
            photo_bytes = await qr_element.screenshot()

        # Save a local backup image
        local_qr_path = "zhihu_qr.png"
        with open(local_qr_path, "wb") as f:
            f.write(photo_bytes)
        logger.info(f"QR code screenshot saved locally to {local_qr_path}")

        # Push to Telegram
        if telegram_config.enabled and telegram_config.bot_token and telegram_config.chat_id:
            caption = (
                "🔔 <b>【知乎登录已过期】</b>\n\n"
                "请在手机上打开 <b>知乎 App</b> 扫描上方二维码完成登录。\n"
                f"二维码有效期约 5 分钟，登录后系统将自动恢复同步任务。"
            )
            await send_telegram_photo(telegram_config.bot_token, telegram_config.chat_id, photo_bytes, caption)
        else:
            logger.warning("Telegram notification not enabled. Please check zhihu_qr.png manually.")

        print("\n" + "="*60)
        print("【提示】知乎登录已过期！")
        print("已将登录二维码推送至 Telegram（本地已保存至 zhihu_qr.png）。")
        print("请在手机知乎 App 中扫码，系统将自动检测登录状态...")
        print("="*60 + "\n")

        # Polling loop waiting for login
        start_time = asyncio.get_event_loop().time()
        last_refresh_check = start_time

        while (asyncio.get_event_loop().time() - start_time) < timeout_sec:
            await asyncio.sleep(3.0)

            # Check if logged in
            current_url = page.url
            if "/signin" not in current_url:
                # Page navigated away from signin, verify login
                ok, username = await check_login(page)
                if ok:
                    logger.info(f"QR Login successful! User: {username}")
                    if telegram_config.enabled and telegram_config.bot_token:
                        success_msg = f"✅ <b>知乎扫码登录成功！</b>\n当前账号：<b>{username}</b>\nPipeline 正在继续执行同步任务。"
                        await send_telegram_message(telegram_config.bot_token, telegram_config.chat_id, success_msg)
                    return True, username

            # Check for avatar or other indicators directly
            avatar = page.locator(".AppHeader-profileAvatar, .AppHeader-user, .AppHeader-profile, .Avatar")
            if await avatar.count() > 0:
                ok, username = await check_login(page)
                if ok:
                    logger.info(f"QR Login successful! User: {username}")
                    if telegram_config.enabled and telegram_config.bot_token:
                        success_msg = f"✅ <b>知乎扫码登录成功！</b>\n当前账号：<b>{username}</b>\nPipeline 正在继续执行同步任务。"
                        await send_telegram_message(telegram_config.bot_token, telegram_config.chat_id, success_msg)
                    return True, username

            # Check if QR code needs refresh (every 45s)
            now = asyncio.get_event_loop().time()
            if now - last_refresh_check > 45.0:
                last_refresh_check = now
                refresh_btn = page.locator("button:has-text('刷新'), .Qrcode-mask, .Qrcode-refresh")
                if await refresh_btn.count() > 0 and await refresh_btn.first.is_visible():
                    logger.info("QR code expired on page. Clicking refresh and re-sending...")
                    try:
                        await refresh_btn.first.click()
                        await page.wait_for_timeout(2000)
                        if qr_element:
                            new_bytes = await qr_element.screenshot()
                            if telegram_config.enabled and telegram_config.bot_token:
                                await send_telegram_photo(
                                    telegram_config.bot_token,
                                    telegram_config.chat_id,
                                    new_bytes,
                                    "🔄 <b>二维码已刷新</b>，请扫描最新的二维码："
                                )
                    except Exception as e:
                        logger.debug(f"Refresh QR error: {e}")

        # Timeout reached
        logger.error("QR Code login timed out.")
        if telegram_config.enabled and telegram_config.bot_token:
            await send_telegram_message(
                telegram_config.bot_token,
                telegram_config.chat_id,
                "❌ <b>知乎扫码登录超时</b>\n未在 5 分钟内完成扫码，任务已暂停。请稍后重试。"
            )
        return False, ""

    except Exception as e:
        logger.error(f"Error during QR login flow: {e}")
        return False, ""

