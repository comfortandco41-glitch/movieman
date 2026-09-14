"""Link resolver — follows ad/shortener redirects to extract Mega.nz URLs.

Many movie sites wrap their Mega links in ad shorteners (linkvertise,
ouo.io, etc.) or custom redirect pages. This module uses Playwright
to follow the full redirect chain and extract the final Mega link.
"""

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
        and looks for Mega links in the page content and network requests.
        """
        page = await self._context.new_page()
        mega_url = None

        try:
            # Monitor network requests for Mega URLs
            captured_urls = []

            def on_request(request):
                req_url = request.url
                if is_mega_url(req_url):
                    captured_urls.append(req_url)

            def on_response(response):
                # Check redirect locations
                resp_url = response.url
                if is_mega_url(resp_url):
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

            # Check if we landed on a Mega page directly
            current_url = page.url
            if is_mega_url(current_url):
                return current_url

            # Check captured network requests
            if captured_urls:
                return captured_urls[0]

            # Wait and check for delayed redirects
            for wait_step in range(min(timeout, 30)):
                await page.wait_for_timeout(1000)

                # Check current URL
                current_url = page.url
                if is_mega_url(current_url):
                    return current_url

                # Check captured URLs
                if captured_urls:
                    return captured_urls[0]

                # Try to find Mega links in page content
                mega_url = await self._scan_page_for_mega(page)
                if mega_url:
                    return mega_url

                # Try clicking "continue" or "skip ad" buttons
                await self._try_skip_ad(page)

            # Final scan of page content
            return await self._scan_page_for_mega(page)

        finally:
            await page.close()

    async def _scan_page_for_mega(self, page: Page) -> Optional[str]:
        """Scan the current page content for Mega.nz URLs.

        Checks:
        - All anchor href attributes
        - Page body text content
        - Meta refresh tags
        - JavaScript variables
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
