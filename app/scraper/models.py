"""Data models for scraped movie information."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MovieListing:
    """A movie entry from the listing/index page.

    Represents the minimal information visible on the main page
    before clicking into a movie's detail page.
    """

    title: str
    detail_url: str            # Full URL to the movie detail page
    poster_url: str = ""       # Thumbnail/poster image URL
    year: str = ""             # Release year if available
    quality: str = ""          # e.g., "HD", "CAM", "720p"
    category: str = ""         # Genre or category
    source: str = ""           # "mmsubchannel" or "homietv"

    @property
    def slug(self) -> str:
        """Extract a slug identifier from the detail URL."""
        return self.detail_url.rstrip("/").split("/")[-1] if self.detail_url else ""


@dataclass
class MovieDetail:
    """Full movie detail extracted from the detail page.

    Contains download links and extended metadata.
    """

    title: str
    detail_url: str
    poster_url: str = ""
    description: str = ""
    year: str = ""
    quality: str = ""
    category: str = ""
    duration: str = ""
    source: str = ""           # "mmsubchannel" or "homietv"

    # Download links — may contain ad/shortener URLs that need resolving
    download_links: list[DownloadLink] = field(default_factory=list)

    @property
    def best_download_link(self) -> Optional["DownloadLink"]:
        """Return the highest-quality download link available."""
        if not self.download_links:
            return None
        # Prefer links labeled as highest quality
        quality_order = ["1080p", "720p", "480p", "360p", "HD", "SD"]
        for q in quality_order:
            for link in self.download_links:
                if q.lower() in (link.label or "").lower():
                    return link
        # Fallback: return first link
        return self.download_links[0]

    @property
    def mega_link(self) -> Optional[str]:
        """Return the first Mega.nz link if any download link is already a Mega URL."""
        for link in self.download_links:
            if "mega.nz" in link.url or "mega.co.nz" in link.url:
                return link.url
        return None

    @property
    def telegram_link(self) -> Optional[str]:
        """Return the first Telegram delivery bot link if available."""
        for link in self.download_links:
            if "t.me" in link.url.lower():
                return link.url
        return None


@dataclass
class DownloadLink:
    """A single download link from a movie detail page.

    The URL may be a direct Mega link, or an ad/shortener URL
    that needs to be resolved first.
    """

    url: str
    label: str = ""           # Quality label (e.g., "720p", "1080p")
    is_mega: bool = False     # True if URL is already a mega.nz link
    server_name: str = ""     # e.g., "MegaUp", "Usersdrive", "Yoteshin", "Telegram"
    file_size: str = ""       # e.g., "2.05GB", "940MB"

    def __post_init__(self):
        """Auto-detect if this is a Mega link."""
        if "mega.nz" in self.url or "mega.co.nz" in self.url:
            self.is_mega = True

