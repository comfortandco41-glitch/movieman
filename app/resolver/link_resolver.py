"""Link resolver — follows ad/shortener redirects to extract Mega.nz or direct download URLs.

Many movie sites wrap their Mega links in ad shorteners (linkvertise,
ouo.io, etc.) or serve files through file-hosting pages (UsersDrive,
MegaUp, etc.) that require browser interaction. This module uses Playwright
to follow the full redirect chain, click download buttons, and intercept
the final direct download or Mega link."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional
from urllib.parse import quote, unquote, urlparse, urlsplit, urlunsplit

from playwright.async_api import BrowserContext, Page

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
        match = pattern.search(text)
        if match:
            return normalize_download_url(match.group(0))
    return None



class LinkResolver:
    """Resolves ad/shortener links to extract final Mega.nz URLs.

    Uses a Playwright browser context (shared with the scraper)
    to follow JavaScript-based redirects and ad pages.
    """

    def __init__(self, browser_context: BrowserContext) -> None:
        self._context = browser_context

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

        page = await self._context.new_page()

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

        UsersDrive serves an HTML page at the .html URL.
        1. Navigates to the page and waits for countdown timer (~5-10s) to finish.
        2. Clicks 'Create Download Link' (#downloadbtn) to submit the form.
        3. The second page displays the 'Click To Download' button (a.btn-download).
        4. Extracts the direct CDN video URL from its href or captured downloads.

        Args:
            url: UsersDrive .html page URL.
            timeout: Maximum seconds to wait.

        Returns:
            The direct download URL, or None if not found.
        """
        logger.info(f"Using UsersDrive resolver for: {url}")
        page = await self._context.new_page()
        captured_urls: list[str] = []
        popup_pages: list = []

        def on_request(request):
            req_url = request.url
            if is_direct_url(req_url) or is_mega_url(req_url):
                if req_url not in captured_urls:
                    captured_urls.append(req_url)
                    logger.debug(f"[UsersDrive] Captured download URL from request: {req_url}")

        def on_response(response):
            resp_url = response.url
            if is_direct_url(resp_url) or is_mega_url(resp_url):
                if resp_url not in captured_urls:
                    captured_urls.append(resp_url)
                    logger.debug(f"[UsersDrive] Captured download URL from response: {resp_url}")

        def on_download(download):
            d_url = download.url
            if d_url and d_url not in captured_urls:
                captured_urls.append(d_url)
                logger.info(f"[UsersDrive] Captured download URL from browser download event: {d_url}")

        def on_popup(popup_page):
            """Close pop-up ad tabs immediately."""
            popup_pages.append(popup_page)
            logger.debug("[UsersDrive] Pop-up tab detected, queued for closure")

        page.on("request", on_request)
        page.on("response", on_response)
        page.on("download", on_download)
        self._context.on("page", on_popup)

        try:
            # Navigate to the UsersDrive page
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            except Exception as nav_err:
                logger.debug(f"[UsersDrive] Navigation warning: {nav_err}")

            # Check for dead/removed file
            try:
                title = (await page.title() or "").lower()
                body_text = (await page.inner_text("body", timeout=2000) or "").lower()
                dead_markers = [
                    "file not found",
                    "no longer available",
                    "file has been deleted",
                    "file was deleted",
                    "file removed",
                ]
                for marker in dead_markers:
                    if marker in title or marker in body_text[:500]:
                        logger.warning(f"[UsersDrive] Dead link detected: '{marker}'")
                        raise DeadLinkError(
                            f"File is no longer available on UsersDrive ({marker.title()})"
                        )
            except DeadLinkError:
                raise
            except Exception:
                pass

            # Close any ad popups that opened on initial load
            for pp in popup_pages:
                try:
                    await pp.close()
                except Exception:
                    pass
            popup_pages.clear()

            # Step 1: Wait for countdown timer (~5-10 seconds) to complete.
            # When countdown hits 0, UsersDrive JS removes disabled attribute/class from #downloadbtn.
            logger.info("[UsersDrive] Waiting for countdown timer to complete...")
            try:
                await page.wait_for_selector(
                    "#downloadbtn:not([disabled]):not(.disabled)",
                    timeout=15000,
                )
                logger.debug("[UsersDrive] Countdown finished, #downloadbtn enabled")
            except Exception:
                logger.debug("[UsersDrive] Selector wait timed out, waiting fixed delay...")
                await page.wait_for_timeout(10000)

            # Extra 1.5s for Cloudflare Turnstile verification callback to fire
            await page.wait_for_timeout(1500)

            # Step 2: Click the 'Create Download Link' button / submit form
            logger.info("[UsersDrive] Submitting 'Create Download Link' form...")
            try:
                await page.evaluate("""() => {
                    const btn = document.getElementById('downloadbtn');
                    if (btn) {
                        btn.click();
                    } else if (document.forms['F1']) {
                        document.forms['F1'].submit();
                    }
                }""")
            except Exception as click_err:
                logger.debug(f"[UsersDrive] JS submit error: {click_err}")
                try:
                    btn = await page.query_selector("#downloadbtn, button:has-text('Create Download Link')")
                    if btn:
                        await btn.click()
                except Exception:
                    pass

            # Step 3: Wait for the download page to load with the 'Click To Download' link
            logger.info("[UsersDrive] Waiting for download link to appear...")
            try:
                await page.wait_for_selector(
                    "a.btn-download, a[href*='userdrive.org'], a:has-text('Click To Download'), a:has-text('Download')",
                    timeout=20000,
                )
            except Exception:
                pass

            # Step 4: Scan for the download link across the page and network
            max_wait = min(timeout, 30)
            for _step in range(max_wait):
                # Close any popups that opened
                for pp in popup_pages:
                    try:
                        await pp.close()
                    except Exception:
                        pass
                popup_pages.clear()

                # 1. Check network captured URLs
                if captured_urls:
                    final_url = normalize_download_url(captured_urls[0])
                    logger.info(f"[UsersDrive] Resolved direct URL from network: {final_url}")
                    return final_url

                # 2. Check anchor links on page
                try:
                    links = await page.query_selector_all("a[href]")
                    for link in links:
                        href = await link.get_attribute("href") or ""
                        if is_direct_url(href) or is_mega_url(href):
                            final_url = normalize_download_url(href)
                            logger.info(f"[UsersDrive] Resolved direct URL from link: {final_url}")
                            return final_url
                except Exception:
                    pass

                # 3. Specifically inspect .btn-download element
                try:
                    dl_btn = await page.query_selector("a.btn-download, a:has-text('Click To Download')")
                    if dl_btn:
                        href = await dl_btn.get_attribute("href") or ""
                        if href and (is_direct_url(href) or is_mega_url(href) or "http" in href):
                            final_url = normalize_download_url(href)
                            logger.info(f"[UsersDrive] Resolved URL from download button href: {final_url}")
                            return final_url
                except Exception:
                    pass

                # 4. Check page body text / regex
                found = await self._scan_page_for_download_url(page)
                if found:
                    final_url = normalize_download_url(found)
                    logger.info(f"[UsersDrive] Resolved URL from page scan: {final_url}")
                    return final_url

                # If still not found after 5s, try clicking the button in case it needs activation
                if _step == 5:
                    try:
                        dl_btn = await page.query_selector("a.btn-download, a:has-text('Click To Download')")
                        if dl_btn:
                            await dl_btn.click()
                    except Exception:
                        pass

                await page.wait_for_timeout(1000)

            logger.error(f"[UsersDrive] Failed to capture download URL for: {url}")
            return None

        finally:
            # Cleanup pop-up tracking listener
            try:
                self._context.remove_listener("page", on_popup)
            except Exception:
                pass
            # Close any remaining pop-ups
            for pp in popup_pages:
                try:
                    await pp.close()
                except Exception:
                    pass
            await page.close()

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
