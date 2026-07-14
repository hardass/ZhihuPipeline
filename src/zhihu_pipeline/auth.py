import json
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from loguru import logger

async def connect_chrome(port: int) -> tuple[Browser, BrowserContext]:
    """
    Connect to a running Chrome instance via Remote Debugging Protocol (CDP).
    """
    logger.info(f"Connecting to Chrome on port {port}...")
    try:
        # Since connect_over_cdp requires an active playwright driver, we use async_playwright.
        # But we need to keep playwright running. So we might need to store the playwright manager,
        # or playwright's context manager should be kept alive.
        # Let's start async_playwright and attach the browser to it.
        # Since auth.py might be used within another async context, we want a clean way.
        # Let's start playwright inside this function, but return browser, context.
        # Note: If playwright context manager exits, the browser will close.
        # Therefore, we should return the playwright context manager object, or let the caller manage playwright.
        # But to keep auth.py API simple:
        # We can start async_playwright() and save the manager reference on the browser object or context object,
        # so it doesn't get garbage collected and closed.
        p = await async_playwright().start()
        browser = await p.chromium.connect_over_cdp(f"http://localhost:{port}")
        # Attach the playwright instance to the browser object so it stays alive as long as the browser is alive
        browser._playwright_instance = p
        
        if not browser.contexts:
            raise RuntimeError("No browser contexts found. Make sure Chrome is running and not fully headless/empty.")
        
        context = browser.contexts[0]
        logger.info("Successfully connected to Chrome via CDP.")
        return browser, context
    except Exception as e:
        logger.error(f"Failed to connect to Chrome on port {port}: {e}")
        logger.error("Please ensure you have run './start_chrome.sh' and that Chrome is running.")
        raise ConnectionError(f"Could not connect to Chrome debugging port {port}. Please run ./start_chrome.sh first.") from e

async def get_or_create_page(context: BrowserContext) -> Page:
    """
    Get the first open page in the context, or create a new one if none exist.
    """
    pages = context.pages
    if pages:
        logger.info("Reusing existing page/tab.")
        return pages[0]
    else:
        logger.info("Creating new page/tab.")
        return await context.new_page()

async def check_login(page: Page) -> tuple[bool, str]:
    """
    Verify if the user is logged into Zhihu by checking DOM indicators on the home page.
    """
    logger.info("Checking Zhihu login status via DOM...")
    try:
        # Navigate to zhihu home page
        await page.goto("https://www.zhihu.com", wait_until="domcontentloaded", timeout=20000)
        # Give it a brief moment to render dynamic content
        await page.wait_for_timeout(2000)
        
        # Check if we are on a login/signin page or if the login button is present
        current_url = page.url
        if "/signin" in current_url:
            logger.warning("Zhihu is redirecting to sign-in page. User is logged out.")
            return False, ""
            
        login_btn = page.locator("button:has-text('登录/注册'), button:has-text('登录'), a:has-text('登录')")
        if await login_btn.count() > 0:
            # Check if it's visible
            for i in range(await login_btn.count()):
                if await login_btn.nth(i).is_visible():
                    logger.warning("Login button is visible. User is logged out.")
                    return False, ""

        # Check for profile avatar or AppHeader-user to confirm login
        avatar = page.locator(".AppHeader-profileAvatar, .AppHeader-user, .AppHeader-profile, .Avatar")
        if await avatar.count() > 0:
            logger.info("Profile indicator found. User is logged in.")
            
            # Attempt to extract username from AppHeader
            username = "Zhihu User"
            try:
                name_loc = page.locator(".AppHeader-profileName, .ProfileHeader-name")
                if await name_loc.count() > 0:
                    username = await name_loc.first.inner_text()
            except Exception:
                pass
            return True, username

        # Fallback: check if standard feed tabs are present (which only show when logged in)
        tabs = page.locator("a:has-text('关注'), a:has-text('推荐'), a:has-text('热榜')")
        if await tabs.count() > 0:
            logger.info("Zhihu feed tabs found. User is logged in.")
            return True, "Zhihu User"

        logger.warning("Could not find any logged-in indicators. User is logged out.")
        return False, ""
    except Exception as e:
        logger.error(f"Error checking login status: {e}")
        return False, ""
