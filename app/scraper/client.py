"""MMSubChannel website scraper using Playwright.

The site is a JavaScript SPA (React-based, rendered into <div id="root">)
so we must use a headless browser to render the content.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Optional
from urllib.parse import urljoin

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from app.scraper.models import MovieListing, MovieDetail, DownloadLink

logger = logging.getLogger(__name__)

# Default timeout for page loads and element waits (ms)
_PAGE_TIMEOUT_MS = 30_000
_ELEMENT_TIMEOUT_MS = 15_000


class ScraperError(Exception):
    """Raised when scraping fails."""
    pass


class MMSubChannelScraper:
    """Scrapes movie listings and details from mmsubchannel.com.

    Uses Playwright headless browser to render the JavaScript SPA.
    Manages a shared browser instance for efficiency.
    """

    def __init__(
        self,
        base_url: str = "https://mmsubchannel.com",
        browser_type: str = "chromium",
        headless: bool = True,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._browser_type = browser_type
        self._headless = headless
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._lock = asyncio.Lock()

    async def _ensure_browser(self) -> BrowserContext:
        """Ensure the browser is initialized and return a context."""
        async with self._lock:
            if self._context is None:
                self._playwright = await async_playwright().start()
                launcher = getattr(self._playwright, self._browser_type)

                # Check for Google Chrome channel on Windows/Linux/Mac
                channel = None
                if self._browser_type == "chromium":
                    import os
                    chrome_paths = [
                        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                    ]
                    for cp in chrome_paths:
                        if os.path.exists(cp):
                            channel = "chrome" if "Chrome" in cp else "msedge"
                            break

                profile_dir = os.environ.get("BROWSER_PROFILE_DIR") or os.path.join(os.getcwd(), ".browser_profile")
                os.makedirs(profile_dir, exist_ok=True)

                launch_kwargs = {
                    "user_data_dir": profile_dir,
                    "headless": self._headless,
                    "args": [
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                    ],
                    "viewport": {"width": 1280, "height": 800},
                    "accept_downloads": True,
                }
                if channel:
                    launch_kwargs["channel"] = channel

                try:
                    self._context = await launcher.launch_persistent_context(**launch_kwargs)
                except Exception as e:
                    logger.warning(f"Failed to launch with channel {channel}: {e}. Retrying with default chromium...")
                    launch_kwargs.pop("channel", None)
                    self._context = await launcher.launch_persistent_context(**launch_kwargs)

                await self._context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                logger.info(f"Browser launched: {self._browser_type} (channel={channel}, headless={self._headless}) with persistent context")

            return self._context

    async def get_latest_movies(self, max_items: int = 20) -> list[MovieListing]:
        """Scrape the latest movie listings from the homepage.

        Args:
            max_items: Maximum number of movies to return.

        Returns:
            List of MovieListing objects.
        """
        context = await self._ensure_browser()
        page = await context.new_page()

        try:
            logger.info(f"Scraping latest movies from {self._base_url}")
            await page.goto(self._base_url, wait_until="domcontentloaded", timeout=_PAGE_TIMEOUT_MS)

            # Wait dynamically for React SPA cards to render
            try:
                await page.wait_for_selector("a[href*='/video/'], [class*='card'], main", timeout=3000)
            except Exception:
                await page.wait_for_timeout(1000)

            # Extract movie cards from the rendered DOM
            movies = await self._extract_movie_listings(page, max_items)
            logger.info(f"Found {len(movies)} movie listings")
            return movies

        except Exception as e:
            logger.error(f"Failed to scrape latest movies: {e}")
            raise ScraperError(f"Failed to scrape latest movies: {e}") from e
        finally:
            await page.close()

    async def get_movie_detail(self, detail_url: str) -> Optional[MovieDetail]:
        """Scrape full movie details from a detail page.

        Args:
            detail_url: URL of the movie detail page.

        Returns:
            MovieDetail object, or None if extraction fails.
        """
        context = await self._ensure_browser()
        page = await context.new_page()

        try:
            logger.info(f"Scraping movie detail: {detail_url}")
            await page.goto(detail_url, wait_until="domcontentloaded", timeout=_PAGE_TIMEOUT_MS)

            # Wait dynamically for movie detail elements
            try:
                await page.wait_for_selector("a[href*='mega'], [class*='download'], h1, h2, main", timeout=3000)
            except Exception:
                await page.wait_for_timeout(1000)

            detail = await self._extract_movie_detail(page, detail_url)
            if detail:
                logger.info(
                    f"Extracted detail: '{detail.title}' with "
                    f"{len(detail.download_links)} download links"
                )
            return detail

        except Exception as e:
            logger.error(f"Failed to scrape movie detail: {e}")
            return None
        finally:
            await page.close()

    async def _extract_movie_listings(
        self, page: Page, max_items: int
    ) -> list[MovieListing]:
        """Extract movie listing cards from a rendered page.

        Prioritizes MMSubChannel /video/ cards and extracts clean titles, years,
        and posters. Falls back to generic card patterns.
        """
        movies: list[MovieListing] = []
        seen_urls: set[str] = set()

        # Strategy 1: Targeted /video/ links (MMSubChannel standard layout)
        try:
            elements = await page.query_selector_all("a[href*='/video/']")
            for elem in elements:
                if len(movies) >= max_items:
                    break
                try:
                    href = await elem.get_attribute("href")
                    if not href:
                        continue
                    full_url = urljoin(self._base_url, href)
                    if full_url in seen_urls:
                        continue

                    # Extract title from h3 or img alt or text
                    title = ""
                    h3_elem = await elem.query_selector("h3")
                    if h3_elem:
                        title = (await h3_elem.text_content() or "").strip()

                    img = await elem.query_selector("img")
                    poster_url = ""
                    if img:
                        if not title:
                            title = (await img.get_attribute("alt") or "").strip()
                        poster_url = (
                            await img.get_attribute("src")
                            or await img.get_attribute("data-src")
                            or ""
                        )

                    # Extract year if present
                    year_elem = await elem.query_selector("span[class*='blue'], span.font-medium")
                    if year_elem:
                        year = (await year_elem.text_content() or "").strip()
                        if year and year.isdigit() and year not in title:
                            title = f"{title} ({year})"

                    if not title:
                        title = (await elem.text_content() or "").strip()[:80]

                    if title:
                        seen_urls.add(full_url)
                        movies.append(MovieListing(
                            title=title,
                            detail_url=full_url,
                            poster_url=poster_url,
                        ))
                except Exception as card_err:
                    logger.debug(f"Failed to parse /video/ card: {card_err}")
        except Exception as e:
            logger.debug(f"Failed querying /video/ elements: {e}")

        if movies:
            return movies[:max_items]

        # Strategy 2 fallback: Look for common card/grid patterns
        selectors = [
            "[class*='movie-card']",
            "[class*='film-card']",
            "[class*='card'] a[href*='/movie']",
            ".grid a:has(img)",
            "a:has(img)",
        ]

        for selector in selectors:
            try:
                elements = await page.query_selector_all(selector)
                for elem in elements[:max_items]:
                    href = await elem.get_attribute("href")
                    if not href or href in {"/", "#"} or "filter=" in href:
                        continue
                    full_url = urljoin(self._base_url, href)
                    if full_url in seen_urls:
                        continue

                    title_elem = await elem.query_selector("h2, h3, h4, [class*='title'], span, p")
                    title = (await title_elem.text_content() or "").strip() if title_elem else ""
                    if not title:
                        title = (await elem.text_content() or "").strip()[:80]
                    if not title:
                        continue

                    img = await elem.query_selector("img")
                    poster = await img.get_attribute("src") if img else ""

                    seen_urls.add(full_url)
                    movies.append(MovieListing(
                        title=title,
                        detail_url=full_url,
                        poster_url=poster or "",
                    ))
                if movies:
                    break
            except Exception:
                continue

        return movies[:max_items]

    async def _extract_movie_detail(
        self, page: Page, detail_url: str
    ) -> Optional[MovieDetail]:
        """Extract full movie details from a rendered detail page."""

        # Extract title
        title = ""
        for sel in ["h1", "[class*='title']", "[class*='heading']"]:
            try:
                elem = await page.query_selector(sel)
                if elem:
                    title = (await elem.text_content() or "").strip()
                    if title:
                        break
            except Exception:
                continue

        if not title:
            raw_title = await page.title() or "Unknown Movie"
            if " - " in raw_title:
                title = raw_title.split(" - ")[0].strip()
            elif raw_title != "MM SUB CHANNEL":
                title = raw_title
            else:
                title = "Movie"

        # Extract review / description text
        description = ""
        for sel in [
            "p.text-muted-foreground",
            "p.leading-relaxed",
            "main p",
            "[class*='description']",
            "[class*='synopsis']",
            "[class*='overview']",
            "article p",
        ]:
            try:
                elems = await page.query_selector_all(sel)
                parts = []
                for el in elems:
                    t = (await el.text_content() or "").strip()
                    if len(t) > 25 and t not in parts:
                        parts.append(t)
                if parts:
                    description = "\n\n".join(parts)
                    break
            except Exception:
                continue

        # Extract poster image
        poster_url = ""
        for sel in [
            "img.glow",
            "img[class*='glow']",
            "img[class*='flex-shrink-0']",
            "img[src*='wsrv.nl']",
            "img[src*='bot-hosting']",
            "main img",
            "[class*='poster'] img",
            "[class*='cover'] img",
            "img",
        ]:
            try:
                elems = await page.query_selector_all(sel)
                for elem in elems:
                    src = (
                        await elem.get_attribute("src")
                        or await elem.get_attribute("data-src")
                        or ""
                    )
                    if src and not any(ic in src.lower() for ic in ["logo", "icon", "avatar", "banner", ".svg"]):
                        poster_url = src
                        break
                if poster_url:
                    break
            except Exception:
                continue

        # Extract download links
        download_links = await self._extract_download_links(page)

        return MovieDetail(
            title=title,
            detail_url=detail_url,
            poster_url=poster_url,
            description=description[:2000] if description else "",
            download_links=download_links,
        )

    async def _extract_download_links(self, page: Page) -> list[DownloadLink]:
        """Extract download links from a movie detail page.

        Looks for links to Mega.nz (direct or via ad/shortener wrappers).
        """
        links = []

        # Strategy 1: Look for explicit download buttons/links and base64 redirects
        download_selectors = [
            "a[href*='redirect?to=']",
            "a[href*='mega.nz']",
            "a[href*='mega.co.nz']",
            "a[href*='megaup']",
            "a[class*='download']",
            "a[class*='btn'][href]",
            "[class*='download'] a",
            "a:has-text('Download')",
            "a:has-text('download')",
            "a:has-text('MEGA')",
            "a:has-text('Mega')",
            "a:has-text('Open')",
            # Common shortener domains
            "a[href*='linkvertise']",
            "a[href*='ouo.io']",
            "a[href*='ouo.press']",
            "a[href*='bit.ly']",
            "a[href*='tinyurl']",
            "a[href*='shorturl']",
            "a[href*='exe.io']",
            "a[href*='gplinks']",
        ]

        seen_urls = set()

        for selector in download_selectors:
            try:
                elements = await page.query_selector_all(selector)
                for elem in elements:
                    href = await elem.get_attribute("href")
                    if not href or href.startswith("#"):
                        continue

                    # If this is a redirect link with base64 'to=' param, decode it
                    if "redirect?to=" in href or ("to=" in href and "redirect" in href):
                        b64_part = href.split("to=")[-1]
                        try:
                            decoded = base64.b64decode(b64_part).decode("utf-8", errors="ignore")
                            href = decoded
                        except Exception:
                            pass

                    if href in seen_urls:
                        continue

                    seen_urls.add(href)
                    label = (await elem.text_content() or "").strip()
                    if not label or label.lower() in {"open", "click here", "download"}:
                        if "t.me" in href.lower() and "start=mv_" in href.lower():
                            label = "Telegram Movie Delivery"
                        elif "mega.nz" in href or "mega.co.nz" in href:
                            label = "Mega.nz Link"
                        elif "megaup" in href:
                            label = "MegaUp Link"
                        else:
                            label = "Download Link"

                    links.append(DownloadLink(
                        url=href,
                        label=label,
                    ))
            except Exception as e:
                logger.debug(f"Download selector '{selector}' failed: {e}")
                continue

        # Strategy 2: Look for links in onclick handlers or data attributes
        try:
            all_anchors = await page.query_selector_all("a[href]")
            for anchor in all_anchors:
                href = await anchor.get_attribute("href") or ""
                if not href or href.startswith("#"):
                    continue

                if "redirect?to=" in href or ("to=" in href and "redirect" in href):
                    b64_part = href.split("to=")[-1]
                    try:
                        decoded = base64.b64decode(b64_part).decode("utf-8", errors="ignore")
                        href = decoded
                    except Exception:
                        pass

                if href in seen_urls:
                    continue

                # Check if the href or surrounding text mentions mega/download/telegram
                text = (await anchor.text_content() or "").lower()
                if any(kw in href.lower() or kw in text for kw in [
                    "mega.nz", "mega.co.nz", "megaup", "download", "mega", "start=mv_"
                ]):
                    seen_urls.add(href)
                    label = (await anchor.text_content() or "").strip()
                    if not label or label.lower() in {"open", "click here", "download"}:
                        if "start=mv_" in href:
                            label = "Telegram Movie Delivery"
                        else:
                            label = "Download Link"
                    links.append(DownloadLink(
                        url=href,
                        label=label,
                    ))
        except Exception as e:
            logger.debug(f"Anchor scan failed: {e}")

        logger.info(f"Found {len(links)} download links")
        return links

    async def close(self) -> None:
        """Close the browser and cleanup resources."""
        async with self._lock:
            if self._context:
                await self._context.close()
                self._context = None
            if self._browser:
                await self._browser.close()
                self._browser = None
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None
            logger.info("Browser closed")
