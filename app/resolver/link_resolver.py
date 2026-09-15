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
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Page

logger = logging.getLogger(__name__)

# Regex patterns that match Mega.nz URLs
_MEGA_URL_PATTERNS = [
    re.compile(r"https?://mega\.nz/(?:file|folder)/[A-Za-z0-9_-]+(?:#[A-Za-z0-9_-]+)?"),
    re.compile(r"https?://mega\.co\.nz/(?:#!?)?[A-Za-z0-9_-]+(?:![A-Za-z0-9_-]+)?"),
    re.compile(r"https?://mega\.nz/#![A-Za-z0-9_-]+(?:![A-Za-z0-9_-]+)?"),
]

# Regex patterns that match direct video/file download URLs
_DIRECT_URL_PATTERNS = [
    re.compile(
        r"https?://[^\s'\"]+\.(?:mp4|mkv|avi|mov|wmv|flv|webm|m4v|ts|m2ts)(\?[^\s'\"]*)?$",
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
    return any(pattern.search(url) for pattern in _MEGA_URL_PATTERNS)


def is_direct_url(url: str) -> bool:
    """Check if a URL is a direct video/file download link.

    Args:
        url: URL string to check.

    Returns:
        True if the URL points directly to a video file.
    """
    return any(pattern.search(url) for pattern in _DIRECT_URL_PATTERNS)


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
            return match.group(0)
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

        UsersDrive serves an HTML page at the .html URL. Clicking the
        'Create Download Link' button triggers a POST request that returns
        the real direct download URL. This method:
          1. Navigates to the page in a new tab
          2. Detects dead/removed files
          3. Clicks the download button
          4. Intercepts the resulting network request for the direct file URL
          5. Handles pop-up ad tabs by closing them immediately

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
            # Capture any video file URL or known CDN download URLs
            if is_direct_url(req_url) or is_mega_url(req_url):
                captured_urls.append(req_url)
                logger.debug(f"[UsersDrive] Captured download URL from request: {req_url}")

        def on_response(response):
            resp_url = response.url
            if is_direct_url(resp_url) or is_mega_url(resp_url):
                if resp_url not in captured_urls:
                    captured_urls.append(resp_url)
                    logger.debug(f"[UsersDrive] Captured download URL from response: {resp_url}")

        def on_popup(popup_page):
            """Close pop-up ad tabs immediately."""
            popup_pages.append(popup_page)
            logger.debug("[UsersDrive] Pop-up tab detected, will close it")

        page.on("request", on_request)
        page.on("response", on_response)
        self._context.on("page", on_popup)

        try:
            # Navigate to the UsersDrive page
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            except Exception as nav_err:
                logger.debug(f"[UsersDrive] Navigation error (may be expected): {nav_err}")

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
                    "404",
                    "not found",
                    "deleted",
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

            # Already captured a URL during page load (unlikely but possible)
            if captured_urls:
                return captured_urls[0]

            # Close any pop-ups that opened during navigation
            for pp in popup_pages:
                try:
                    await pp.close()
                except Exception:
                    pass
            popup_pages.clear()

            # Click the download button — UsersDrive typically has a form with a
            # 'Create Download Link' or 'Download' submit button.
            _USERSDRIVE_BUTTON_SELECTORS = [
                "form[action*='download'] input[type='submit']",
                "form[action*='download'] button[type='submit']",
                "input[type='submit'][value*='Download']",
                "input[type='submit'][value*='Create']",
                "button:has-text('Create Download Link')",
                "button:has-text('Download')",
                "a:has-text('Download')",
                "a[href*='download']",
                "#download",
                ".download-btn",
                "[id*='download']",
            ]

            clicked = False
            for selector in _USERSDRIVE_BUTTON_SELECTORS:
                try:
                    elem = await page.query_selector(selector)
                    if elem and await elem.is_visible():
                        logger.debug(f"[UsersDrive] Clicking button: {selector}")
                        await elem.click()
                        clicked = True
                        break
                except Exception:
                    continue

            if not clicked:
                logger.warning("[UsersDrive] Could not find download button — scanning page for links")

            # Wait up to remaining timeout for a download URL to appear
            max_wait = min(timeout, 30)
            for _ in range(max_wait):
                await page.wait_for_timeout(1000)

                # Close pop-up ads
                for pp in popup_pages:
                    try:
                        await pp.close()
                    except Exception:
                        pass
                popup_pages.clear()

                if captured_urls:
                    logger.info(f"[UsersDrive] Resolved direct URL: {captured_urls[0]}")
                    return captured_urls[0]

                # Check if a new download link appeared on the page
                found = await self._scan_page_for_download_url(page)
                if found:
                    logger.info(f"[UsersDrive] Extracted URL from page: {found}")
                    return found

                # Try clicking again if the first click opened a pop-up
                if not captured_urls:
                    for selector in _USERSDRIVE_BUTTON_SELECTORS:
                        try:
                            elem = await page.query_selector(selector)
                            if elem and await elem.is_visible():
                                await elem.click()
                                break
                        except Exception:
                            continue

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
                    return href

            body_text = await page.content()

            # Check for Mega URLs first
            mega_url = extract_mega_url_from_text(body_text)
            if mega_url:
                return mega_url

            # Check for direct video URLs
            direct_url = extract_direct_url_from_text(body_text)
            if direct_url:
                return direct_url

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
