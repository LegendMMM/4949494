"""Playwright browser engine implementation."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from playwright_stealth import Stealth

from ticket_bot.browser.base import BrowserEngine, ElementHandle, PageWrapper

logger = logging.getLogger(__name__)

STEALTH_INIT_SCRIPT = """
(() => {
    try {
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
        });

        Object.defineProperty(navigator, 'languages', {
            get: () => ['zh-TW', 'zh', 'en-US', 'en'],
        });

        Object.defineProperty(navigator, 'platform', {
            get: () => 'Win32',
        });

        const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
        if (originalQuery) {
            window.navigator.permissions.query = (parameters) => (
                parameters && parameters.name === 'notifications'
                    ? Promise.resolve({ state: Notification.permission })
                    : originalQuery.call(window.navigator.permissions, parameters)
            );
        }

        if (window.chrome && !window.chrome.runtime) {
            window.chrome.runtime = {};
        }
    } catch (_) {
        // Ignore stealth patch failures.
    }
})();
"""


class PlaywrightElement(ElementHandle):
    def __init__(self, locator_or_handle, page: Page):
        self._el = locator_or_handle
        self._page = page
        self._cached_text = ""

    async def click(self) -> None:
        await self._el.click()

    async def send_keys(self, text: str) -> None:
        import random

        await self._el.type(text, delay=random.randint(50, 150))

    async def query_selector(self, selector: str) -> ElementHandle | None:
        child = await self._el.query_selector(selector)
        if child:
            return PlaywrightElement(child, self._page)
        return None

    @property
    def text(self) -> str:
        return self._cached_text

    def _set_text(self, text: str) -> None:
        self._cached_text = text


class PlaywrightPage(PageWrapper):
    def __init__(self, page: Page):
        self._page = page

    async def goto(self, url: str) -> None:
        await self._page.goto(url, wait_until="domcontentloaded")

    async def current_url(self) -> str:
        return self._page.url

    async def select(self, selector: str) -> ElementHandle | None:
        try:
            handle = await self._page.query_selector(selector)
            if handle:
                el = PlaywrightElement(handle, self._page)
                text = await handle.inner_text() if await handle.is_visible() else ""
                el._set_text(text)
                return el
        except Exception:
            pass
        return None

    async def select_all(self, selector: str) -> list[ElementHandle]:
        handles = await self._page.query_selector_all(selector)
        result = []
        for handle in handles:
            el = PlaywrightElement(handle, self._page)
            try:
                text = await handle.inner_text()
            except Exception:
                text = ""
            el._set_text(text)
            result.append(el)
        return result

    async def evaluate(self, expression: str) -> Any:
        return await self._page.evaluate(expression)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def get_cookies_string(self) -> str:
        return await self._page.evaluate("document.cookie")

    async def get_all_cookies(self) -> list[dict]:
        cookies = await self._page.context.cookies()
        return [
            {
                "name": cookie["name"],
                "value": cookie["value"],
                "domain": cookie.get("domain", ""),
                "path": cookie.get("path", "/"),
                "httpOnly": cookie.get("httpOnly", False),
                "secure": cookie.get("secure", False),
            }
            for cookie in cookies
        ]

    async def set_cookies(self, cookies: list[dict]) -> None:
        formatted = []
        for cookie in cookies:
            entry = {"name": cookie["name"], "value": cookie["value"]}
            if "url" in cookie:
                entry["url"] = cookie["url"]
            elif "domain" in cookie:
                entry["domain"] = cookie["domain"]
                entry["path"] = cookie.get("path", "/")
            else:
                entry["url"] = "https://tixcraft.com"
            formatted.append(entry)
        if formatted:
            await self._page.context.add_cookies(formatted)

    async def block_urls(self, patterns: list[str]) -> None:
        try:
            substrings = [pattern.strip("*") for pattern in patterns if pattern.strip("*")]

            async def _abort(route):
                await route.abort()

            await self._page.route(
                lambda url: any(substring in str(url) for substring in substrings),
                _abort,
            )
            logger.info("Blocked %d URL patterns", len(patterns))
        except Exception as exc:
            logger.warning("Failed to block URL patterns: %s", exc)

    def on_response_callback(self, url_pattern: str, callback: callable) -> None:
        import re

        regex = re.compile(url_pattern)

        async def handle_response(response):
            if regex.search(response.url):
                try:
                    body = await response.body()
                    callback(body)
                except Exception as exc:
                    logger.debug("Intercept response error: %s", exc)

        self._page.on("response", handle_response)

    def on_response_event(self, url_pattern: str, callback: callable) -> None:
        import re

        regex = re.compile(url_pattern)

        async def handle_response(response):
            if regex.search(response.url):
                try:
                    headers = await response.headers_array()
                    callback(
                        {
                            "url": response.url,
                            "status_code": response.status,
                            "method": getattr(response.request, "method", ""),
                            "headers": [(item["name"], item["value"]) for item in headers],
                        }
                    )
                except Exception as exc:
                    logger.debug("Intercept response event error: %s", exc)

        self._page.on("response", handle_response)

    async def handle_cloudflare(self, timeout: float = 15.0) -> bool:
        try:
            has_cf = await self._page.evaluate(
                """
                (() => {
                    const text = document.body?.innerText || '';
                    const hasCfText = /verify you are human|checking your browser/i.test(text);
                    const hasCfFrame = !!document.querySelector('iframe[src*="challenges.cloudflare.com"]');
                    return hasCfText || hasCfFrame;
                })()
                """
            )
            if not has_cf:
                return True

            logger.info("Cloudflare challenge detected, trying to continue")
            cf_frame = self._page.frame_locator('iframe[src*="challenges.cloudflare.com"]')
            checkbox = cf_frame.locator('input[type="checkbox"], .cb-lb')
            try:
                await checkbox.click(timeout=timeout * 1000)
            except Exception:
                iframe = self._page.locator('iframe[src*="challenges.cloudflare.com"]')
                if await iframe.count() > 0:
                    await iframe.click()

            elapsed = 0.0
            while elapsed < timeout:
                await asyncio.sleep(1.0)
                elapsed += 1.0
                still_cf = await self._page.evaluate(
                    """
                    (() => {
                        const text = document.body?.innerText || '';
                        return /verify you are human|checking your browser/i.test(text);
                    })()
                    """
                )
                if not still_cf:
                    logger.info("Cloudflare challenge cleared")
                    return True

            logger.warning("Cloudflare challenge timeout")
            return False
        except Exception as exc:
            logger.warning("Cloudflare handling failed: %s", exc)
            return False

    async def screenshot(self) -> bytes:
        try:
            return await self._page.screenshot()
        except Exception:
            return b""


class PlaywrightEngine(BrowserEngine):
    """Playwright browser engine."""

    def __init__(self):
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._stealth = Stealth()
        self._attached_via_cdp = False
        self._attach_page_url_substring = ""

    @staticmethod
    def _detect_browser_executable() -> str:
        candidates = [
            os.getenv("BROWSER_EXECUTABLE_PATH", ""),
            os.getenv("CHROME_EXECUTABLE_PATH", ""),
            os.path.join(os.getenv("ProgramFiles", ""), "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(os.getenv("ProgramFiles(x86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(os.getenv("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(os.getenv("ProgramFiles", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(os.getenv("ProgramFiles(x86)", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(os.getenv("LOCALAPPDATA", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]

        for path in candidates:
            if path and os.path.exists(path):
                return path
        return ""

    @staticmethod
    def _resolve_user_data_dir(user_data_dir: str) -> str:
        if not user_data_dir:
            return ""

        path = Path(user_data_dir).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()

        path_str = str(path)
        try:
            path_str.encode("ascii")
            path.mkdir(parents=True, exist_ok=True)
            return path_str
        except UnicodeEncodeError:
            safe_root = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "ticket-bot-public" / "profiles"
            safe_root.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha1(path_str.encode("utf-8")).hexdigest()[:10]
            safe_path = safe_root / f"profile_{digest}"
            safe_path.mkdir(parents=True, exist_ok=True)
            logger.warning(
                "Non-ASCII user_data_dir detected, remapping Playwright profile from %s to %s",
                path_str,
                safe_path,
            )
            return str(safe_path)

    @staticmethod
    def _build_proxy_config(proxy_server: str) -> dict[str, str] | None:
        if not proxy_server:
            return None

        parsed = urlsplit(proxy_server)
        if not parsed.hostname:
            return {"server": proxy_server}

        server = f"{parsed.scheme or 'http'}://{parsed.hostname}"
        if parsed.port:
            server = f"{server}:{parsed.port}"

        config: dict[str, str] = {"server": server}
        if parsed.username:
            config["username"] = parsed.username
        if parsed.password:
            config["password"] = parsed.password
        return config

    async def _install_context_stealth(self) -> None:
        if not self._context:
            return

        try:
            await self._context.add_init_script(STEALTH_INIT_SCRIPT)
        except Exception as exc:
            logger.debug("Failed to add context init script: %s", exc)

        for page in list(self._context.pages):
            await self._apply_page_stealth(page)

        def _handle_new_page(page: Page) -> None:
            asyncio.create_task(self._apply_page_stealth(page))

        self._context.on("page", _handle_new_page)

    async def _apply_page_stealth(self, page: Page) -> None:
        try:
            await page.add_init_script(STEALTH_INIT_SCRIPT)
        except Exception:
            pass

        try:
            await self._stealth.apply_stealth_async(page)
        except Exception as exc:
            logger.debug("Failed to apply page stealth: %s", exc)

    async def _select_existing_page(self) -> Page | None:
        if not self._context:
            return None

        pages = [page for page in self._context.pages if page.url not in ("", "about:blank")]
        if not pages:
            return None

        if self._attach_page_url_substring:
            matched = [page for page in pages if self._attach_page_url_substring in page.url]
            if not matched:
                return None
            pages = matched

        for page in reversed(pages):
            try:
                if await page.evaluate("() => document.hasFocus()"):
                    await page.bring_to_front()
                    return page
            except Exception:
                continue

        page = pages[-1]
        try:
            await page.bring_to_front()
        except Exception:
            pass
        return page

    async def launch(
        self,
        *,
        headless: bool = False,
        user_data_dir: str = "",
        executable_path: str = "",
        lang: str = "zh-TW",
        proxy_server: str = "",
        extra_args: list[str] | None = None,
        attach_cdp_url: str = "",
        attach_page_url_substring: str = "",
    ) -> None:
        self._playwright = await async_playwright().start()
        self._attached_via_cdp = False
        self._attach_page_url_substring = attach_page_url_substring.strip()

        if attach_cdp_url:
            self._browser = await self._playwright.chromium.connect_over_cdp(attach_cdp_url)
            if self._browser.contexts:
                self._context = self._browser.contexts[0]
            else:
                self._context = await self._browser.new_context(
                    locale=lang,
                    viewport={"width": 1280, "height": 800},
                    timezone_id="Asia/Taipei",
                )
            self._attached_via_cdp = True
            await self._install_context_stealth()
            logger.info("Playwright attached over CDP: %s", attach_cdp_url)
            return

        if not executable_path:
            executable_path = self._detect_browser_executable()
            if executable_path:
                logger.info("Using system browser executable: %s", executable_path)

        launch_args = list(extra_args or [])
        launch_args.append("--disable-blink-features=AutomationControlled")

        kwargs: dict[str, Any] = {
            "headless": headless,
            "args": launch_args,
            "ignore_default_args": ["--enable-automation"],
        }
        if executable_path:
            kwargs["executable_path"] = executable_path

        proxy_config = self._build_proxy_config(proxy_server) if proxy_server else None

        if user_data_dir:
            resolved_user_data_dir = self._resolve_user_data_dir(user_data_dir)
            context_kwargs: dict[str, Any] = {
                "user_data_dir": resolved_user_data_dir,
                "locale": lang,
                "viewport": {"width": 1280, "height": 800},
                "timezone_id": "Asia/Taipei",
                **kwargs,
            }
            if proxy_config:
                context_kwargs["proxy"] = proxy_config
            try:
                self._context = await self._playwright.chromium.launch_persistent_context(
                    **context_kwargs,
                )
                self._browser = None
            except Exception as exc:
                logger.warning(
                    "Persistent Playwright context failed for %s, falling back to non-persistent mode: %s",
                    resolved_user_data_dir,
                    exc,
                )
                fallback_profile = tempfile.mkdtemp(prefix="ticket-bot-pw-")
                fallback_kwargs = dict(context_kwargs)
                fallback_kwargs["user_data_dir"] = fallback_profile
                try:
                    self._context = await self._playwright.chromium.launch_persistent_context(
                        **fallback_kwargs,
                    )
                    self._browser = None
                except Exception as fallback_exc:
                    logger.warning(
                        "Temporary persistent context failed, falling back to ephemeral browser: %s",
                        fallback_exc,
                    )
                    if proxy_config:
                        kwargs["proxy"] = proxy_config
                    self._browser = await self._playwright.chromium.launch(**kwargs)
                    self._context = await self._browser.new_context(
                        locale=lang,
                        viewport={"width": 1280, "height": 800},
                        timezone_id="Asia/Taipei",
                    )
        else:
            if proxy_config:
                kwargs["proxy"] = proxy_config
            self._browser = await self._playwright.chromium.launch(**kwargs)
            self._context = await self._browser.new_context(
                locale=lang,
                viewport={"width": 1280, "height": 800},
                timezone_id="Asia/Taipei",
            )

        await self._install_context_stealth()
        logger.info("Playwright browser launched")

    async def new_page(self, url: str = "") -> PageWrapper:
        if not self._context:
            raise RuntimeError("Browser engine is not launched")

        page = None
        if self._attached_via_cdp:
            page = await self._select_existing_page()
            if page is None:
                target = self._attach_page_url_substring or "the current browser tab"
                raise RuntimeError(f"找不到可接手的既有分頁：{target}")

        existing_pages = list(self._context.pages)
        if page is None:
            for existing in existing_pages:
                if existing.url in ("", "about:blank"):
                    page = existing
                    break
        if page is None and len(existing_pages) == 1:
            page = existing_pages[0]
        if page is None:
            page = await self._context.new_page()

        await self._apply_page_stealth(page)

        if url:
            await page.goto(url, wait_until="domcontentloaded")

        return PlaywrightPage(page)

    async def close(self) -> None:
        if self._context and not self._attached_via_cdp:
            await self._context.close()
        self._context = None
        if self._browser and not self._attached_via_cdp:
            await self._browser.close()
        self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        self._attached_via_cdp = False
        self._attach_page_url_substring = ""
        logger.info("Playwright browser closed")
