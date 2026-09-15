"""Link resolver — follows ad/shortener redirects to extract Mega.nz or direct download URLs.

Many movie sites wrap their Mega links in ad shorteners (linkvertise,
ouo.io, etc.) or serve files through file-hosting pages (UsersDrive,
MegaUp, etc.) that require browser interaction. This module uses Playwright
to follow the full redirect chain, click download buttons, and intercept
the final direct download or Mega link."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Optional
from urllib.parse import quote, unquote, urlparse, urlsplit, urlunsplit

from playwright.async_api import BrowserContext, Page, Playwright

logger = logging.getLogger(__name__)

# Regex patterns that match Mega.nz URLs
_MEGA_URL_PATTERNS = [
    re.compile(r"https?://mega\.nz/(?:file|folder)/[A-Za-z0-9_-]+(?:#[A-Za-z0-9_-]+)?"),
    re.compile(r"https?://mega\.co\.nz/(?:#!?)?[A-Za-z0-9_-]+(?:![A-Za-z0-9_-]+)?"),
    re.compile(r"https?://mega\.nz/#![A-Za-z0-9_-]+(?:![A-Za-z0-9_-]+)?"),
]

# Supported video extensions
_VIDEO_EXTENSIONS = (
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv",
    ".webm", ".m4v", ".ts", ".m2ts",
)

# Regex patterns that match direct video/file download URLs
_DIRECT_URL_PATTERNS = [
    re.compile(
        r"https?://[^\r\n'\"<>]+\.(?:mp4|mkv|avi|mov|wmv|flv|webm|m4v|ts|m2ts)(?:\?[^\r\n'\"<>]*)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"https?://[^\r\n'\"<>]*?userdrive\.org[^\r\n'\"<>]*",
        re.IGNORECASE,
    ),
]

# Known ad/shortener domains to handle
_SHORTENER_DOMAINS = {
    "linkvertise.com", "linkvertise.net",
    "ouo.io", "ouo.press",
    "exe.io", "exey.io",
    "gplinks.co", "gplinks.in",
    "bit.ly", "tinyurl.com",
    "shorturl.at",
    "shrinkme.io",
    "za.gl", "za.gg",
}

# Known file-hosting domains that serve an HTML page and require
# browser interaction to generate the real download link.
_FILEHOST_DOMAINS = {
    "usersdrive.com",
    "www.usersdrive.com",
    "megaup.net",
    "www.megaup.net",
}

# Maximum time to follow redirects (seconds)
_RESOLVE_TIMEOUT = 60
_MAX_RETRIES = 3


class ResolverError(Exception):
    """Raised when link resolution fails."""
    pass


class DeadLinkError(ResolverError):
    """Raised when a hosting site explicitly indicates the file was deleted or not found."""
    pass


def is_mega_url(url: str) -> bool:
    """Check if a URL is a Mega.nz link.

    Args:
        url: URL string to check.

    Returns:
        True if the URL matches a known Mega.nz pattern.
    """
    if not url or not isinstance(url, str):
        return False
    return any(pattern.search(url) for pattern in _MEGA_URL_PATTERNS)


def is_direct_url(url: str) -> bool:
    """Check if a URL is a direct video/file download link.

    Handles unencoded spaces in URLs, query parameters, and CDN domains.

    Args:
        url: URL string to check.

    Returns:
        True if the URL points directly to a video file.
    """
    if not url or not isinstance(url, str):
        return False

    clean = url.strip().strip("'\"")
    if not clean.startswith(("http://", "https://")):
        return False

    # Never treat an HTML page as a direct file download
    if ".html" in clean.lower():
        return False

    try:
        parsed = urlparse(clean)
        netloc = parsed.netloc.lower()
        path = unquote(parsed.path).lower()

        # Known direct CDN file hosts or download paths
        if "userdrive.org" in netloc:
            return True
        if ("usersdrive" in netloc or "megaup" in netloc) and "/d/" in path:
            return True

        # Check path extension (e.g. /path/video.mp4 or /path/The%20Movie.mp4)
        if any(path.endswith(ext) for ext in _VIDEO_EXTENSIONS):
            return True

        # Check path before query parameters
        clean_no_query = unquote(clean.split("?")[0]).lower()
        if any(clean_no_query.endswith(ext) for ext in _VIDEO_EXTENSIONS):
            return True

        return any(pattern.search(clean) for pattern in _DIRECT_URL_PATTERNS)
    except Exception:
        return False


def normalize_download_url(url: str) -> str:
    """Properly quote spaces in a download URL so HTTP clients can fetch it cleanly."""
    if not url:
        return url
    clean = url.strip()
    if " " in clean:
        parts = urlsplit(clean)
        quoted_path = quote(parts.path, safe="/:@=+$,")
        quoted_query = quote(parts.query, safe="=&+$,")
        return urlunsplit((parts.scheme, parts.netloc, quoted_path, quoted_query, parts.fragment))
    return clean


def is_filehost_page(url: str) -> bool:
    """Check if a URL is a known file-hosting page that needs browser resolution.

    These are HTML pages (not direct files) that require clicking a download
    button to obtain the real download URL.

    Args:
        url: URL string to check.

    Returns:
        True if the URL is a known file-host page.
    """
    try:
        domain = urlparse(url).netloc.lower().lstrip("www.")
        return domain in {d.lstrip("www.") for d in _FILEHOST_DOMAINS}
    except Exception:
        return False


def extract_mega_url_from_text(text: str) -> Optional[str]:
    """Extract a Mega.nz URL from arbitrary text.

    Args:
        text: Text that may contain a Mega URL.

    Returns:
        The first Mega URL found, or None.
    """
    for pattern in _MEGA_URL_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def extract_direct_url_from_text(text: str) -> Optional[str]:
    """Extract a direct video download URL from arbitrary text.

    Args:
        text: Text that may contain a direct video URL.

    Returns:
        The first direct video URL found, or None.
    """
    for pattern in _DIRECT_URL_PATTERNS:
        for match in pattern.finditer(text):
            found_url = match.group(0)
            if ".html" in found_url or "usersdrive.com" in found_url or "]" in found_url:
                continue
            return normalize_download_url(found_url)
    return None


class LinkResolver:
    """Resolves ad/shortener links to extract final Mega.nz URLs.

    Uses a Playwright browser context (shared with the scraper)
    to follow JavaScript-based redirects and ad pages.

    For UsersDrive specifically, a separate NON-HEADLESS browser context
    is spawned so Cloudflare Turnstile can auto-solve. Without a visible
    browser, Turnstile blocks the form submission entirely.
    """

    def __init__(
        self,
        browser_context: Optional[BrowserContext] = None,
        playwright: Optional[Playwright] = None,
        scraper: Optional[Any] = None,
    ) -> None:
        self._context = browser_context
        self._playwright = playwright  # Used to spawn context for UsersDrive
        self._scraper = scraper

    def set_browser_context(
        self,
        browser_context: BrowserContext,
        playwright: Optional[Playwright] = None,
    ) -> None:
        """Update the active shared browser context."""
        self._context = browser_context
        if playwright is not None:
            self._playwright = playwright

    async def _get_context(self) -> Optional[BrowserContext]:
        """Get or lazily initialize the shared browser context."""
        if self._context is not None:
            return self._context
        if self._scraper is not None and hasattr(self._scraper, "_ensure_browser"):
            try:
                self._context = await self._scraper._ensure_browser()
                if hasattr(self._scraper, "_playwright") and self._scraper._playwright:
                    self._playwright = self._scraper._playwright
                return self._context
            except Exception as e:
                logger.warning(f"Failed to obtain browser context from scraper: {e}")
        return None

    async def _open_nonheadless_context(self) -> Optional[BrowserContext]:
        """Open a dedicated browser context for CAPTCHA-protected pages (UsersDrive).

        Uses a visible window on desktop OS (Windows/macOS/Linux with DISPLAY)
        so Cloudflare Turnstile auto-solves, or stealth headless with --headless=new
        on headless Linux servers (e.g. Render/Docker).
        """
        try:
            pw = self._playwright
            if pw is None:
                if self._scraper and hasattr(self._scraper, "_playwright") and self._scraper._playwright:
                    pw = self._scraper._playwright
                else:
                    from playwright.async_api import async_playwright as _apw
                    pw = await _apw().start()
                    self._playwright = pw

            launcher = pw.chromium

            # Detect real Chrome installation (Windows + Linux)
            channel = None
            chrome_paths = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                "/usr/bin/google-chrome",
                "/usr/bin/google-chrome-stable",
                "/usr/bin/chromium",
                "/usr/bin/chromium-browser",
            ]
            for cp in chrome_paths:
                if os.path.exists(cp):
                    channel = "chrome" if ("chrome" in cp.lower() or "Chrome" in cp) else "chromium"
                    break

            # Use non-headless only if display is available (Windows, macOS, or Linux with DISPLAY)
            has_display = bool(os.environ.get("DISPLAY"))
            can_run_headed = sys.platform in ("win32", "darwin") or has_display
            use_headless = not can_run_headed

            profile_dir = os.path.join(os.getcwd(), ".browser_profile_resolver")
            os.makedirs(profile_dir, exist_ok=True)

            args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--window-size=1280,800",
            ]
            if use_headless:
                args.append("--headless=new")

            kwargs = {
                "user_data_dir": profile_dir,
                "headless": use_headless,
                "args": args,
                "viewport": {"width": 1280, "height": 800},
                "accept_downloads": True,
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            }
            if channel:
                kwargs["channel"] = channel

            try:
                ctx = await launcher.launch_persistent_context(**kwargs)
            except Exception as launch_err:
                logger.warning(
                    f"[UsersDrive] Launch with channel={channel} failed: {launch_err}. Retrying standard..."
                )
                kwargs.pop("channel", None)
                kwargs["headless"] = True
                ctx = await launcher.launch_persistent_context(**kwargs)

            await ctx.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            logger.info(f"[UsersDrive] Context launched (channel={channel}, headless={use_headless})")
            return ctx
        except Exception as e:
            logger.error(f"[UsersDrive] Failed to launch context: {e}")
            return None

    async def resolve(
        self,
        url: str,
        timeout: int = _RESOLVE_TIMEOUT,
        max_retries: int = _MAX_RETRIES,
    ) -> Optional[str]:
        """Resolve a URL to its final Mega.nz link.

        If the URL is already a Mega link, returns it directly.
        Otherwise, follows redirects and ad pages to find the Mega link.

        Args:
            url: Starting URL (may be a shortener, ad wrapper, or direct Mega link).
            timeout: Maximum seconds to spend resolving.
            max_retries: Maximum retry attempts.

        Returns:
            The resolved Mega.nz URL, or None if resolution fails.
        """
        # Already a Mega link
        if is_mega_url(url):
            logger.info(f"URL is already a Mega link: {url}")
            return url

        for attempt in range(max_retries):
            try:
                result = await self._resolve_attempt(url, timeout)
                if result:
                    logger.info(f"Resolved to Mega URL: {result}")
                    return result
            except DeadLinkError:
                # Do NOT retry dead links — fail immediately
                raise
            except Exception as e:
                logger.warning(
                    f"Resolve attempt {attempt + 1}/{max_retries} failed: {e}"
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 * (attempt + 1))

        logger.error(f"Failed to resolve URL after {max_retries} attempts: {url}")
        return None

    async def _resolve_attempt(self, url: str, timeout: int) -> Optional[str]:
        """Single resolve attempt using Playwright.

        Opens the URL in a new tab, watches for redirects,
        and looks for Mega or direct video links in page content and network requests.
        For known file-hosting pages (e.g. UsersDrive), uses a specialised handler
        that clicks the download button and intercepts the resulting file request.
        """
        # Route known file-host pages to their dedicated handler
        try:
            domain = urlparse(url).netloc.lower().lstrip("www.")
        except Exception:
            domain = ""

        if "usersdrive.com" in domain:
            return await self._resolve_usersdrive(url, timeout)

        ctx = await self._get_context()
        if ctx is None:
            logger.error("No browser context available to resolve link")
            return None
        page = await ctx.new_page()

        try:
            # Monitor network requests for Mega OR direct video URLs
            captured_urls: list[str] = []

            def on_request(request):
                req_url = request.url
                if is_mega_url(req_url) or is_direct_url(req_url):
                    captured_urls.append(req_url)

            def on_response(response):
                resp_url = response.url
                if is_mega_url(resp_url) or is_direct_url(resp_url):
                    captured_urls.append(resp_url)

            page.on("request", on_request)
            page.on("response", on_response)

            # Navigate to the URL
            logger.debug(f"Navigating to: {url}")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            except Exception as nav_err:
                # Navigation might fail if it redirects to mega:// or triggers download
                logger.debug(f"Navigation exception (may be expected): {nav_err}")

            # Check if page indicates a dead or deleted file on the host
            try:
                title = (await page.title() or "").lower()
                body_snippet = (await page.inner_text("body", timeout=1500) or "").lower()
                dead_markers = [
                    "file not found",
                    "no longer available",
                    "file has been deleted",
                    "file was deleted",
                    "file removed",
                    "error - megaup",
                    "the page you are looking for doesn't exist",
                    "404 not found",
                ]
                for marker in dead_markers:
                    if marker in title or marker in body_snippet:
                        logger.warning(f"Dead link detected at {url}: '{marker}'")
                        raise DeadLinkError(f"File is no longer available on hosting site ({marker.title()})")
            except DeadLinkError:
                raise
            except Exception:
                pass

            # Check if we landed on a Mega or direct video page directly
            current_url = page.url
            if is_mega_url(current_url) or is_direct_url(current_url):
                return current_url

            # Check captured network requests
            if captured_urls:
                return captured_urls[0]

            # Wait and check for delayed redirects
            for _wait_step in range(min(timeout, 30)):
                await page.wait_for_timeout(1000)

                # Check current URL
                current_url = page.url
                if is_mega_url(current_url) or is_direct_url(current_url):
                    return current_url

                # Check captured URLs
                if captured_urls:
                    return captured_urls[0]

                # Try to find Mega/direct links in page content
                found = await self._scan_page_for_download_url(page)
                if found:
                    return found

                # Try clicking "continue" or "skip ad" buttons
                await self._try_skip_ad(page)

            # Final scan of page content
            return await self._scan_page_for_download_url(page)

        finally:
            await page.close()

    async def _resolve_usersdrive(self, url: str, timeout: int) -> Optional[str]:
        """Specialised resolver for UsersDrive file-hosting pages.

        ROOT CAUSE: UsersDrive uses Cloudflare Turnstile which ONLY auto-solves
        in a visible (non-headless) browser. In headless mode the token is never
        populated and the server rejects the form submission.

        Fix: Open a dedicated NON-HEADLESS Chrome context just for this resolution,
        completely separate from the scraper's headless context. After getting the
        download URL we close the context.

        Flow:
        1. Open non-headless Chrome context
        2. Navigate to UsersDrive URL
        3. Wait up to 30s for countdown + Turnstile to auto-solve
        4. Submit the download form (op=download2, adblock_detected=0)
        5. Wait for the "Click To Download" page
        6. Strategy A: read a.btn-download href from DOM (no click needed)
        7. Strategy B: click button, capture popup tab URL
        8. Strategies C-F: network/listener/scan fallbacks
        """
        nh_context = None
        owns_context = False
        try:
            nh_context = await self._open_nonheadless_context()
            if nh_context is not None:
                owns_context = True
            else:
                logger.warning("[UsersDrive] Dedicated context unavailable, falling back to shared context")
                nh_context = await self._get_context()

            if nh_context is None:
                logger.error("[UsersDrive] No browser context available for UsersDrive")
                return None

            page = await nh_context.new_page()
            captured_urls: list[str] = []
            popup_urls: list[str] = []

            def on_request(request):
                req_url = request.url
                if is_direct_url(req_url) or is_mega_url(req_url):
                    if req_url not in captured_urls:
                        captured_urls.append(req_url)
                        logger.debug(f"[UsersDrive] Captured from request: {req_url}")

            def on_response(response):
                resp_url = response.url
                if is_direct_url(resp_url) or is_mega_url(resp_url):
                    if resp_url not in captured_urls:
                        captured_urls.append(resp_url)
                        logger.debug(f"[UsersDrive] Captured from response: {resp_url}")

            def on_download(download):
                d_url = download.url
                if d_url and d_url not in captured_urls:
                    captured_urls.append(d_url)
                    logger.info(f"[UsersDrive] Captured from download event: {d_url}")

            async def on_popup(popup_page):
                """Capture URL from any popup tab then close it."""
                try:
                    try:
                        await popup_page.wait_for_load_state("domcontentloaded", timeout=8000)
                    except Exception:
                        pass
                    p_url = popup_page.url or ""
                    logger.debug(f"[UsersDrive] Popup tab URL: {p_url}")
                    if p_url and (is_direct_url(p_url) or is_mega_url(p_url)):
                        popup_urls.append(p_url)
                        logger.info(f"[UsersDrive] Captured from popup URL: {p_url}")
                    else:
                        try:
                            for lnk in await popup_page.query_selector_all("a[href]"):
                                href = (await lnk.get_attribute("href") or "").strip()
                                if href and (is_direct_url(href) or is_mega_url(href)):
                                    popup_urls.append(href)
                                    logger.info(f"[UsersDrive] Captured from popup link: {href}")
                                    break
                        except Exception:
                            pass
                    try:
                        await popup_page.close()
                    except Exception:
                        pass
                except Exception:
                    try:
                        await popup_page.close()
                    except Exception:
                        pass

            page.on("request", on_request)
            page.on("response", on_response)
            page.on("download", on_download)
            page.on("popup", on_popup)

            # Step 1: Navigate
            logger.info(f"[UsersDrive] Navigating to {url}")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            except Exception as nav_err:
                logger.debug(f"[UsersDrive] Navigation warning: {nav_err}")

            # Check for dead file
            try:
                pg_title = (await page.title() or "").lower()
                body_text = (await page.inner_text("body", timeout=2000) or "").lower()
                for marker in ["file not found", "no longer available", "file has been deleted",
                               "file was deleted", "file removed"]:
                    if marker in pg_title or marker in body_text[:500]:
                        logger.warning(f"[UsersDrive] Dead link: '{marker}'")
                        raise DeadLinkError(
                            f"File is no longer available on UsersDrive ({marker.title()})"
                        )
            except DeadLinkError:
                raise
            except Exception:
                pass

            # Step 2: Wait for countdown + Turnstile auto-solve (works in non-headless)
            logger.info("[UsersDrive] Waiting for countdown + Turnstile (non-headless)...")
            for sec in range(30):
                await page.wait_for_timeout(1000)
                try:
                    status = await page.evaluate("""() => {
                        const btn = document.getElementById('downloadbtn');
                        const cf = document.querySelector('input[name="cf-turnstile-response"]');
                        const disabled = btn
                            ? (btn.disabled || btn.classList.contains('disabled'))
                            : true;
                        const cfLen = cf && cf.value ? cf.value.length : 0;
                        return { disabled: disabled, cf_len: cfLen };
                    }""")
                except Exception:
                    status = {"disabled": True, "cf_len": 0}

                logger.debug(
                    f"[UsersDrive] sec={sec} btn_disabled={status.get('disabled')} "
                    f"cf_token_len={status.get('cf_len')}"
                )

                # If Turnstile didn't auto-solve (rare in non-headless), click it
                if sec >= 10 and status.get("cf_len", 0) == 0:
                    try:
                        for f in page.frames:
                            if "challenges.cloudflare.com" in f.url:
                                box = await f.query_selector(
                                    "input[type='checkbox'], .ctp-checkbox-label, body"
                                )
                                if box:
                                    await box.click()
                                    logger.debug("[UsersDrive] Clicked Turnstile checkbox")
                                    break
                    except Exception:
                        pass

                if not status.get("disabled") and (status.get("cf_len", 0) > 0 or sec >= 22):
                    logger.info(
                        f"[UsersDrive] Ready at {sec}s (token_len={status.get('cf_len')})"
                    )
                    break

            await page.wait_for_timeout(500)

            # Step 3: Submit the download form
            logger.info("[UsersDrive] Submitting download form...")
            try:
                await page.evaluate("""() => {
                    const btn = document.getElementById('downloadbtn');
                    const form = btn
                        ? btn.form
                        : (document.querySelector('form:has(#downloadbtn)')
                            || document.querySelector('input[value="download2"]')?.form);
                    if (form) {
                        const ab = form.querySelector('#adblock_detected')
                            || document.getElementById('adblock_detected');
                        if (ab) ab.value = '0';
                        if (btn) { btn.disabled = false; btn.removeAttribute('disabled'); }
                        form.submit();
                    }
                }""")
            except Exception as js_err:
                logger.debug(f"[UsersDrive] JS submit error, clicking button: {js_err}")
                try:
                    btn = await page.query_selector("#downloadbtn")
                    if btn:
                        await btn.evaluate(
                            "el => { el.disabled = false; el.removeAttribute('disabled'); }"
                        )
                        await btn.click()
                except Exception:
                    pass

            # Step 4: Wait for "Click To Download" page
            logger.info("[UsersDrive] Waiting for Click To Download page...")
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            await page.wait_for_timeout(2000)
            logger.debug(f"[UsersDrive] URL after form submit: {page.url}")

            # Strategy A: Read href directly from DOM (fastest - URL is in the HTML)
            try:
                await page.wait_for_selector(
                    "a.btn-download, a[href*='userdrive.org'], a:has-text('Click To Download')",
                    timeout=12000,
                )
            except Exception:
                pass

            try:
                dl_el = await page.query_selector(
                    "a.btn-download, a[href*='userdrive.org'], a:has-text('Click To Download')"
                )
                if dl_el:
                    href = (await dl_el.get_attribute("href") or "").strip()
                    if href and ("userdrive.org" in href or is_direct_url(href)):
                        final_url = normalize_download_url(href)
                        logger.info(f"[UsersDrive] Strategy A: {final_url}")
                        return final_url
            except Exception:
                pass

            # Strategy B: Click -> popup tab
            logger.info("[UsersDrive] Strategy B: click button for popup tab...")
            try:
                dl_el = await page.query_selector(
                    "a.btn-download, a:has-text('Click To Download'), a[href*='userdrive.org']"
                )
                if dl_el:
                    try:
                        async with page.expect_popup(timeout=15000) as popup_info:
                            await dl_el.click()
                        new_tab = await popup_info.value
                        try:
                            await new_tab.wait_for_load_state("domcontentloaded", timeout=10000)
                        except Exception:
                            pass
                        await asyncio.sleep(1)
                        tab_url = new_tab.url or ""
                        if tab_url and is_direct_url(tab_url):
                            await new_tab.close()
                            final_url = normalize_download_url(tab_url)
                            logger.info(f"[UsersDrive] Strategy B popup URL: {final_url}")
                            return final_url
                        try:
                            for lnk in await new_tab.query_selector_all("a[href]"):
                                href = (await lnk.get_attribute("href") or "").strip()
                                if href and is_direct_url(href):
                                    await new_tab.close()
                                    final_url = normalize_download_url(href)
                                    logger.info(f"[UsersDrive] Strategy B popup link: {final_url}")
                                    return final_url
                        except Exception:
                            pass
                        try:
                            await new_tab.close()
                        except Exception:
                            pass
                    except Exception as popup_err:
                        logger.debug(f"[UsersDrive] expect_popup failed: {popup_err}")
                        try:
                            await dl_el.click()
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"[UsersDrive] Strategy B error: {e}")

            # Strategy C: popup listener captured URL
            await asyncio.sleep(2)
            if popup_urls:
                final_url = normalize_download_url(popup_urls[0])
                logger.info(f"[UsersDrive] Strategy C: {final_url}")
                return final_url

            # Strategy D: network captured URLs
            if captured_urls:
                final_url = normalize_download_url(captured_urls[0])
                logger.info(f"[UsersDrive] Strategy D: {final_url}")
                return final_url

            # Strategy E: scan page links
            try:
                for link in await page.query_selector_all("a[href]"):
                    href = (await link.get_attribute("href") or "").strip()
                    if href and not href.endswith(".html") and (
                        is_direct_url(href) or is_mega_url(href)
                    ):
                        final_url = normalize_download_url(href)
                        logger.info(f"[UsersDrive] Strategy E: {final_url}")
                        return final_url
            except Exception:
                pass

            # Strategy F: full content scan
            found = await self._scan_page_for_download_url(page)
            if found and not found.endswith(".html"):
                final_url = normalize_download_url(found)
                logger.info(f"[UsersDrive] Strategy F: {final_url}")
                return final_url

            logger.error(f"[UsersDrive] All strategies exhausted for: {url}")
            return None

        finally:
            if owns_context and nh_context is not None:
                try:
                    await nh_context.close()
                    logger.debug("[UsersDrive] Context closed")
                except Exception:
                    pass

    async def _scan_page_for_mega(self, page: Page) -> Optional[str]:
        """Scan the current page content for Mega.nz URLs.

        Checks:
        - All anchor href attributes
        - Page body text content
        """
        try:
            # Check all links on the page
            links = await page.query_selector_all("a[href]")
            for link in links:
                href = await link.get_attribute("href") or ""
                if is_mega_url(href):
                    return href

            # Check page body text for Mega URLs
            body_text = await page.content()
            mega_url = extract_mega_url_from_text(body_text)
            if mega_url:
                return mega_url

        except Exception as e:
            logger.debug(f"Page scan error: {e}")

        return None

    async def _scan_page_for_download_url(self, page: Page) -> Optional[str]:
        """Scan the current page content for Mega.nz or direct video download URLs.

        Checks:
        - All anchor href attributes
        - Page body HTML content
        """
        try:
            links = await page.query_selector_all("a[href]")
            for link in links:
                href = await link.get_attribute("href") or ""
                if is_mega_url(href):
                    return href
                if is_direct_url(href):
                    return normalize_download_url(href)

            body_text = await page.content()

            # Check for Mega URLs first
            mega_url = extract_mega_url_from_text(body_text)
            if mega_url:
                return mega_url

            # Check for direct video URLs
            direct_url = extract_direct_url_from_text(body_text)
            if direct_url:
                return normalize_download_url(direct_url)

        except Exception as e:
            logger.debug(f"Page scan error: {e}")

        return None

    async def _try_skip_ad(self, page: Page) -> None:
        """Try to click common "skip ad" or "continue" buttons.

        Many ad/shortener pages have buttons like:
        - "Get Link", "Continue", "Skip", "Click here to continue"
        - Countdown timers that reveal a button
        """
        skip_selectors = [
            # Common skip/continue button patterns
            "button:has-text('Continue')",
            "button:has-text('Get Link')",
            "button:has-text('Skip')",
            "button:has-text('Go to link')",
            "a:has-text('Continue')",
            "a:has-text('Get Link')",
            "a:has-text('Skip')",
            "a:has-text('Go to link')",
            "a:has-text('Click here')",
            "#skip-btn",
            "#continue-btn",
            ".skip-btn",
            ".continue-btn",
            "[class*='skip']",
            "[class*='continue']",
            "[id*='skip']",
            "[id*='getlink']",
            "[id*='get-link']",
        ]

        for selector in skip_selectors:
            try:
                elem = await page.query_selector(selector)
                if elem and await elem.is_visible():
                    await elem.click()
                    logger.debug(f"Clicked skip/continue button: {selector}")
                    await page.wait_for_timeout(2000)
                    return
            except Exception:
                continue
