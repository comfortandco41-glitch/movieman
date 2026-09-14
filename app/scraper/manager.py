"""Unified Multi-Source Scraper Manager.

Provides a single interface to query either MMSubChannel or HomieTV,
automatically routing URLs and managing underlying browser and HTTP client lifecycles.
"""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urlparse

from app.scraper.models import MovieDetail, MovieListing
from app.scraper.client import MMSubChannelScraper
from app.scraper.homietv import HomieTVScraper

logger = logging.getLogger(__name__)


class MultiSourceScraper:
    """Dispatches scraping and searching across MMSubChannel and HomieTV."""

    def __init__(
        self,
        mmsub_scraper: Optional[MMSubChannelScraper] = None,
        homietv_scraper: Optional[HomieTVScraper] = None,
    ) -> None:
        self.mmsub = mmsub_scraper or MMSubChannelScraper()
        self.homietv = homietv_scraper or HomieTVScraper()

    @staticmethod
    def identify_source(url_or_slug: str) -> str:
        """Identify which source a URL belongs to.

        Returns 'homietv', 'mmsubchannel', or 'unknown'.
        """
        clean = url_or_slug.strip().lower()
        if "homietv.com" in clean:
            return "homietv"
        if "mmsubchannel.com" in clean:
            return "mmsubchannel"
        # If no domain, check pattern or default
        return "unknown"

    async def get_latest_movies(
        self,
        source: str = "all",
        max_items: int = 10,
    ) -> list[MovieListing]:
        """Fetch latest movies from specified source or both."""
        source = source.lower().strip()
        results: list[MovieListing] = []

        if source in ("all", "homietv"):
            try:
                homie_items = await self.homietv.get_latest_movies(limit=max_items)
                results.extend(homie_items)
            except Exception as e:
                logger.warning(f"Failed to fetch latest from HomieTV: {e}")

        if source in ("all", "mmsubchannel"):
            try:
                mmsub_items = await self.mmsub.get_latest_movies(max_items=max_items)
                results.extend(mmsub_items)
            except Exception as e:
                logger.warning(f"Failed to fetch latest from MMSubChannel: {e}")

        return results[:max_items] if source != "all" else results

    async def search(
        self,
        keyword: str,
        source: str = "all",
    ) -> list[MovieListing]:
        """Search movies across sources."""
        source = source.lower().strip()
        results: list[MovieListing] = []

        if source in ("all", "homietv"):
            try:
                homie_results, _ = await self.homietv.search(keyword)
                results.extend(homie_results)
            except Exception as e:
                logger.warning(f"HomieTV search failed for '{keyword}': {e}")

        if source in ("all", "mmsubchannel"):
            try:
                mmsub_results = await self.mmsub.search_movies(keyword)
                results.extend(mmsub_results)
            except Exception as e:
                logger.warning(f"MMSubChannel search failed for '{keyword}': {e}")

        return results

    async def get_movie_detail(self, url: str) -> Optional[MovieDetail]:
        """Fetch full movie details, auto-detecting the source from URL."""
        source = self.identify_source(url)

        if source == "homietv":
            return await self.homietv.get_movie_detail(url)
        elif source == "mmsubchannel":
            return await self.mmsub.get_movie_detail(url)
        else:
            # Fallback: try HomieTV first (fast), then MMSubChannel
            try:
                detail = await self.homietv.get_movie_detail(url)
                if detail:
                    return detail
            except Exception:
                pass
            return await self.mmsub.get_movie_detail(url)

    async def close(self) -> None:
        """Close resources for all underlying scrapers."""
        try:
            await self.homietv.close()
        except Exception as e:
            logger.warning(f"Error closing HomieTV scraper: {e}")

        try:
            await self.mmsub.close()
        except Exception as e:
            logger.warning(f"Error closing MMSubChannel scraper: {e}")
