import os
import asyncio
from typing import Optional, Union
import httpx
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from loguru import logger
from zhihu_pipeline.config import TelegramConfig, NotifyConfig
from zhihu_pipeline.notify import Notifier


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
            await page.goto("https://www.zhihu.com", wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_timeout(1000)
        
        current_url = page.url
        if "/signin" in current_url:
            logger.warning("Zhihu redirected to sign-in page. User is logged out.")
            return False, ""

        # Step 1: Wait for SPA hydration.
        # Check for either logged-in indicators (avatar, profile) or explicit login button in AppHeader.
        logged_in_selectors = [
            ".AppHeader-profileAvatar",
            ".AppHeader-user",
            ".AppHeader-profile",
            ".Avatar",
            ".AppHeader-notifications",
            ".AppHeader-messages",
            "button.AppHeader-profile",
            ".TopstoryTabs",
        ]
        logged_out_selectors = [
            "button.AppHeader-login",
            ".AppHeader button:has-text('登录')",
            ".SignContainer",
            ".SignFlow",
        ]

        combined_wait_selector = ", ".join(logged_in_selectors + logged_out_selectors)
        try:
            # Wait up to 6 seconds for header elements to hydrate
            await page.wait_for_selector(combined_wait_selector, timeout=6000)
        except Exception:
            # Slower network/NAS fallback wait
            await page.wait_for_timeout(2000)

        # Step 2: Positive indicators check FIRST.
        # If user avatar or profile header exists, user is definitely logged in!
        avatar = page.locator(", ".join(logged_in_selectors[:4]))
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

        tabs = page.locator("nav.AppHeader-Tabs a:has-text('关注'), nav.AppHeader-Tabs a:has-text('推荐'), .TopstoryTabs")
        if await tabs.count() > 0:
            logger.info("Zhihu feed tabs found. User is logged in.")
            return True, "Zhihu User"

        # Step 3: Check if explicitly logged out.
        # Strictly scoped to AppHeader or SignContainer to avoid matching article links or text
        login_btn = page.locator("button.AppHeader-login, .AppHeader button:has-text('登录/注册'), .AppHeader button:has-text('登录'), .SignContainer, .SignFlow-submitButton")
        if await login_btn.count() > 0:
            for i in range(await login_btn.count()):
                if await login_btn.nth(i).is_visible():
                    logger.warning("Header login button/modal is visible. User is logged out.")
                    return False, ""

        # Step 4: Cookie verification fallback.
        # If DOM hasn't rendered either clearly, check if z_c0 auth cookie exists in browser profile
        try:
            cookies = await page.context.cookies("https://www.zhihu.com")
            has_auth_cookie = any(c.get("name") == "z_c0" and c.get("value") for c in cookies)
            if has_auth_cookie:
                logger.info("Auth cookie (z_c0) found in context. Waiting for DOM to finish hydration...")
                await page.wait_for_timeout(3000)
                if await avatar.count() > 0:
                    logger.info("Profile indicator found after cookie wait. User is logged in.")
                    return True, "Zhihu User"
        except Exception as e:
            logger.debug(f"Cookie check error: {e}")

        logger.warning("Could not find any logged-in indicators. User is logged out.")
        return False, ""
    except Exception as e:
        logger.error(f"Error checking login status: {e}")
        return False, ""

async def send_telegram_message(bot_token: str = "", chat_id: str = "", text: str = "", title: str = "") -> bool:
    """
    Send text message via Unified Notifier (notify-gateway) with direct Telegram fallback.
    """
    notifier = Notifier()
    if notifier.is_configured:
        return await notifier.notify_text(message=text, title=title)

    if not bot_token or not chat_id:
        logger.warning("Neither NotifyGateway nor Telegram credentials configured. Skipping message.")
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

async def send_telegram_photo(bot_token: str = "", chat_id: str = "", photo_bytes: bytes = b"", caption: str = "", title: str = "") -> bool:
    """
    Send photo via Unified Notifier (notify-gateway) with direct Telegram fallback.
    """
    notifier = Notifier()
    if notifier.is_configured:
        return await notifier.notify_photo(photo_bytes=photo_bytes, caption=caption, title=title)

    if not bot_token or not chat_id:
        logger.warning("Neither NotifyGateway nor Telegram credentials configured. Skipping photo.")
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

async def handle_qr_login(
    page: Page,
    notify_config: Optional[Union[NotifyConfig, TelegramConfig, Notifier]] = None
) -> tuple[bool, str]:
    """
    Handle automated QR Code login flow with push notifications via the unified notification gateway.
    Captures Zhihu login QR code, pushes to notification gateway, and waits for user to scan and complete login.
    """
    logger.info("Initiating QR code login flow...")
    if isinstance(notify_config, Notifier):
        notifier = notify_config
    elif isinstance(notify_config, NotifyConfig):
        notifier = Notifier(notify_config)
    else:
        # Fallback to default Notifier
        notifier = Notifier()

    timeout_sec = getattr(notify_config, 'timeout', 300) or 300
    
    try:
        if "/signin" not in page.url:
            await page.goto("https://www.zhihu.com/signin", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2000)
            # If navigating to /signin redirected away (user is already logged in)
            if "/signin" not in page.url:
                ok, username = await check_login(page)
                if ok:
                    logger.info(f"User is already logged in (redirected from /signin). Active user: {username}")
                    return True, username

        # Look for QR code tab if password tab is active
        qr_tab = page.locator("div.SignFlow-tab:has-text('二维码登录'), button:has-text('二维码登录'), .SignFlow-tabs button:first-child, .SignFlow-qrcodeMode")
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
                qr_element = await page.wait_for_selector("img.Qrcode-qrcode, .Qrcode-img img, .Qrcode-container", timeout=6000)
            except Exception:
                pass

        if not qr_element:
            logger.error("Could not find Zhihu QR code element on signin page.")
            # Double check if user is actually logged in
            ok, username = await check_login(page)
            if ok:
                return True, username
            
            # Avoid sending random full-page screenshot as a fake "QR code"
            await notifier.notify_text(
                "检测到登录失效，但在登录页面未找到二维码。请检查知乎账号状态。",
                title="⚠️ 【知乎登录提示】"
            )
            return False, ""

        photo_bytes = await qr_element.screenshot()

        # Save a local backup image
        local_qr_path = "zhihu_qr.png"
        with open(local_qr_path, "wb") as f:
            f.write(photo_bytes)
        logger.info(f"QR code screenshot saved locally to {local_qr_path}")

        # Push to notification gateway only when valid QR photo is captured
        caption = (
            "请在手机上打开 <b>知乎 App</b> 扫描上方二维码完成登录。\n"
            f"二维码有效期约 5 分钟，登录后系统将自动恢复同步任务。"
        )
        qr_sent = await notifier.notify_photo(photo_bytes, caption=caption, title="🔔 【知乎登录已过期】")

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
                    if qr_sent:
                        success_msg = f"当前账号：<b>{username}</b>\nPipeline 正在继续执行同步任务。"
                        await notifier.notify_text(success_msg, title="✅ 知乎扫码登录成功！")
                    return True, username

            # Check for avatar or other indicators directly
            avatar = page.locator(".AppHeader-profileAvatar, .AppHeader-user, .AppHeader-profile, .Avatar")
            if await avatar.count() > 0:
                ok, username = await check_login(page)
                if ok:
                    logger.info(f"QR Login successful! User: {username}")
                    if qr_sent:
                        success_msg = f"当前账号：<b>{username}</b>\nPipeline 正在继续执行同步任务。"
                        await notifier.notify_text(success_msg, title="✅ 知乎扫码登录成功！")
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
                            if qr_sent:
                                await notifier.notify_photo(
                                    new_bytes,
                                    caption="请扫描最新的二维码完成登录：",
                                    title="🔄 二维码已刷新"
                                )
                    except Exception as e:
                        logger.debug(f"Refresh QR error: {e}")

        # Timeout reached
        logger.error("QR Code login timed out.")
        if qr_sent:
            await notifier.notify_text(
                "未在 5 分钟内完成扫码，任务已暂停。请稍后重试。",
                title="❌ 知乎扫码登录超时"
            )
        return False, ""

    except Exception as e:
        logger.error(f"Error during QR login flow: {e}")
        return False, ""


