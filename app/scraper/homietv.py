"""HomieTV scraper and API client using httpx.

HomieTV (https://www.homietv.com) is built on Next.js with a public JSON REST API.
This scraper directly queries the REST endpoints, providing sub-second response times
without the overhead of a headless browser.
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlparse

import httpx

from app.scraper.models import DownloadLink, MovieDetail, MovieListing

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 25.0
_BASE_URL = "https://www.homietv.com"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


class HomieTVError(Exception):
    """Raised when HomieTV API request or parsing fails."""
    pass


class HomieTVScraper:
    """Async scraper and client for HomieTV."""

    def __init__(
        self,
        base_url: str = _BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazily initialize an httpx.AsyncClient."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Referer": f"{self.base_url}/",
                    "Accept": "application/json, text/plain, */*",
                },
                timeout=self.timeout,
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def extract_slug(url_or_slug: str) -> tuple[str, str]:
        """Extract media type ('movie' or 'tv-show') and slug from a URL or slug.

        Examples:
            'https://www.homietv.com/movie/resident-dead-pwsvjbnq' -> ('movie', 'resident-dead-pwsvjbnq')
            'https://www.homietv.com/tv-show/love-on-the-menu-ykiuckme' -> ('tv-show', 'love-on-the-menu-ykiuckme')
            'resident-dead-pwsvjbnq' -> ('movie', 'resident-dead-pwsvjbnq')
        """
        clean = url_or_slug.strip()
        if clean.startswith(("http://", "https://")):
            parsed = urlparse(clean)
            parts = [p for p in parsed.path.strip("/").split("/") if p]
            if len(parts) >= 2:
                media_type = "tv-show" if parts[0] in ("tv-show", "tv-shows") else "movie"
                return media_type, parts[1]
            elif len(parts) == 1:
                return "movie", parts[0]
            return "movie", ""
        return "movie", clean

    async def get_latest_movies(self, page: int = 1, limit: int = 20) -> list[MovieListing]:
        """Fetch latest movies list from HomieTV.

        If page == 1, also considers /api/home/movies or /api/movies?page=1.
        """
        client = await self._get_client()
        try:
            url = f"/api/movies?page={page}"
            resp = await client.get(url)
            resp.raise_for_status()
            payload = resp.json()
            items = payload.get("data", [])
            results: list[MovieListing] = []
            for item in items[:limit]:
                listing = self._parse_movie_listing(item)
                if listing:
                    results.append(listing)
            return results
        except Exception as e:
            logger.error(f"Failed to fetch latest movies from HomieTV (page={page}): {e}")
            raise HomieTVError(f"HomieTV latest movies error: {e}") from e

    async def get_trending_movies(self, limit: int = 20) -> list[MovieListing]:
        """Fetch trending movies from HomieTV."""
        client = await self._get_client()
        try:
            resp = await client.get("/api/trending/movies")
            resp.raise_for_status()
            payload = resp.json()
            items = payload.get("data", [])
            results: list[MovieListing] = []
            for item in items[:limit]:
                listing = self._parse_movie_listing(item)
                if listing:
                    results.append(listing)
            return results
        except Exception as e:
            logger.error(f"Failed to fetch trending movies from HomieTV: {e}")
            raise HomieTVError(f"HomieTV trending movies error: {e}") from e

    async def search(self, keyword: str, page: int = 1) -> tuple[list[MovieListing], int]:
        """Search HomieTV by keyword.

        Returns:
            Tuple of (list[MovieListing], total_results_count).
        """
        client = await self._get_client()
        try:
            resp = await client.get(f"/api/search?keyword={keyword}&page={page}")
            resp.raise_for_status()
            payload = resp.json()
            items = payload.get("data", [])
            meta = payload.get("meta", {})
            total = meta.get("total", len(items))

            results: list[MovieListing] = []
            for item in items:
                listing = self._parse_movie_listing(item)
                if listing:
                    results.append(listing)
            return results, total
        except Exception as e:
            logger.error(f"Search failed on HomieTV for keyword='{keyword}': {e}")
            raise HomieTVError(f"HomieTV search error: {e}") from e

    async def get_movie_detail(self, slug_or_url: str) -> Optional[MovieDetail]:
        """Fetch full movie details and download links by slug or URL."""
        media_type, slug = self.extract_slug(slug_or_url)
        if not slug:
            return None

        if media_type == "tv-show":
            return await self.get_tv_show_detail(slug)

        client = await self._get_client()
        try:
            resp = await client.get(f"/api/movies/{slug}")
            if resp.status_code == 404:
                # Try tv-show as fallback
                return await self.get_tv_show_detail(slug)
            resp.raise_for_status()
            data = resp.json().get("data", {})
            if not data:
                return None

            title = data.get("title") or data.get("original_title") or slug
            year = str(data.get("year") or "")
            overview = data.get("overview") or ""
            poster = data.get("poster") or ""
            runtime = f"{data.get('runtime')} min" if data.get("runtime") else ""
            quality = data.get("resolution") or ""

            categories = [c.get("name") for c in (data.get("categories") or []) if isinstance(c, dict) and c.get("name")]
            category_str = ", ".join(categories)

            # Parse download links
            raw_links = data.get("movie_download_links") or []
            download_links: list[DownloadLink] = []

            for rl in raw_links:
                url = (rl.get("url") or "").strip()
                if not url:
                    continue

                server_name = (rl.get("server_name") or "").strip()
                # Skip promo app links
                if "bioscopeapp" in url.lower() or server_name.lower() == "app":
                    continue

                quality_label = (rl.get("quality") or rl.get("resolution") or "").strip()
                size = (rl.get("size") or "").strip()

                full_label_parts = []
                if quality_label:
                    full_label_parts.append(quality_label)
                if server_name:
                    full_label_parts.append(server_name)
                if size:
                    full_label_parts.append(size)
                label = " - ".join(full_label_parts) if full_label_parts else (server_name or "Download")

                download_links.append(
                    DownloadLink(
                        url=url,
                        label=label,
                        server_name=server_name,
                        file_size=size,
                    )
                )

            detail_url = f"{self.base_url}/movie/{slug}"

            return MovieDetail(
                title=title,
                detail_url=detail_url,
                poster_url=poster,
                description=overview,
                year=year,
                quality=quality,
                category=category_str,
                duration=runtime,
                source="homietv",
                download_links=download_links,
            )
        except Exception as e:
            logger.error(f"Failed to get movie detail for slug='{slug}': {e}")
            raise HomieTVError(f"HomieTV detail error for {slug}: {e}") from e

    async def get_tv_show_detail(self, slug_or_url: str) -> Optional[MovieDetail]:
        """Fetch TV show details and aggregate episode download links."""
        _, slug = self.extract_slug(slug_or_url)
        if not slug:
            return None

        client = await self._get_client()
        try:
            resp = await client.get(f"/api/tv-shows/{slug}")
            resp.raise_for_status()
            data = resp.json().get("data", {})
            if not data:
                return None

            title = data.get("title") or slug
            year = str(data.get("year") or "")
            overview = data.get("overview") or ""
            poster = data.get("poster") or ""
            categories = [c.get("name") for c in (data.get("categories") or []) if isinstance(c, dict) and c.get("name")]
            category_str = ", ".join(categories)

            # Aggregate download links across episodes
            download_links: list[DownloadLink] = []
            seasons = data.get("seasons") or []

            for s in seasons:
                for ep in (s.get("episodes") or []):
                    ep_num = ep.get("episode_number") or ""
                    ep_links = ep.get("tvshow_download_links") or []
                    for el in ep_links:
                        url = (el.get("url") or "").strip()
                        if not url or "bioscopeapp" in url.lower():
                            continue
                        server_name = (el.get("server_name") or "").strip()
                        qual = el.get("resolution") or el.get("quality") or ""
                        size = el.get("size") or ""
                        label = f"Ep {ep_num} - {qual} ({server_name})" if qual else f"Ep {ep_num} ({server_name})"
                        download_links.append(
                            DownloadLink(
                                url=url,
                                label=label,
                                server_name=server_name,
                                file_size=size,
                            )
                        )

            detail_url = f"{self.base_url}/tv-show/{slug}"

            return MovieDetail(
                title=title,
                detail_url=detail_url,
                poster_url=poster,
                description=overview,
                year=year,
                quality="Series",
                category=category_str,
                duration="",
                source="homietv",
                download_links=download_links,
            )
        except Exception as e:
            logger.error(f"Failed to get TV show detail for slug='{slug}': {e}")
            raise HomieTVError(f"HomieTV TV show error for {slug}: {e}") from e

    def _parse_movie_listing(self, item: dict) -> Optional[MovieListing]:
        """Convert a raw API movie dictionary to a MovieListing."""
        slug = item.get("slug")
        if not slug:
            return None

        title = item.get("title") or slug
        media_type = item.get("type", "movie")
        detail_url = f"{self.base_url}/{media_type}/{slug}" if media_type in ("tv-show", "tv-shows") else f"{self.base_url}/movie/{slug}"
        poster = item.get("poster") or ""
        year = str(item.get("year") or "")
        quality = item.get("resolution") or ""

        categories = [c.get("name") for c in (item.get("categories") or []) if isinstance(c, dict) and c.get("name")]
        category_str = ", ".join(categories)

        return MovieListing(
            title=title,
            detail_url=detail_url,
            poster_url=poster,
            year=year,
            quality=quality,
            category=category_str,
            source="homietv",
        )

