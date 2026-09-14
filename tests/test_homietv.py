"""Tests for HomieTV scraper."""

from __future__ import annotations

import pytest
from app.scraper.homietv import HomieTVScraper, HomieTVError


def test_extract_slug():
    scraper = HomieTVScraper()
    assert scraper.extract_slug("https://www.homietv.com/movie/resident-dead-pwsvjbnq") == ("movie", "resident-dead-pwsvjbnq")
    assert scraper.extract_slug("https://www.homietv.com/tv-show/love-on-the-menu-ykiuckme") == ("tv-show", "love-on-the-menu-ykiuckme")
    assert scraper.extract_slug("resident-dead-pwsvjbnq") == ("movie", "resident-dead-pwsvjbnq")
    assert scraper.extract_slug("  https://www.homietv.com/movies/pages/1  ") == ("movie", "pages")


@pytest.mark.asyncio
async def test_homietv_live_api():
    """Verify live HomieTV API endpoints."""
    scraper = HomieTVScraper()
    try:
        # 1. Latest movies
        movies = await scraper.get_latest_movies(page=1, limit=5)
        assert len(movies) > 0
        assert movies[0].title != ""
        assert movies[0].source == "homietv"
        assert movies[0].detail_url.startswith("https://www.homietv.com/")

        # 2. Movie detail
        slug = movies[0].slug
        detail = await scraper.get_movie_detail(slug)
        assert detail is not None
        assert detail.title != ""
        assert detail.source == "homietv"
        assert len(detail.download_links) >= 0

        # 3. Search
        results, total = await scraper.search("President", page=1)
        assert len(results) > 0
        assert total > 0
    finally:
        await scraper.close()


def test_multisource_scraper_identify_source():
    from app.scraper.manager import MultiSourceScraper

    assert MultiSourceScraper.identify_source("https://www.homietv.com/movie/test") == "homietv"
    assert MultiSourceScraper.identify_source("https://mmsubchannel.com/movie/test") == "mmsubchannel"
    assert MultiSourceScraper.identify_source("https://other.com/test") == "unknown"


@pytest.mark.asyncio
async def test_http_downloader_stream(tmp_path):
    """Test HttpDownloader against an online test file or mock."""
    from app.media.http_downloader import HttpDownloader

    downloader = HttpDownloader()
    # Test downloading a small online asset (e.g. HomieTV favicon)
    url = "https://www.homietv.com/images/logo/favicon.png"
    downloaded = await downloader.download(
        url=url,
        dest_dir=tmp_path,
        dest_filename="test_fav.png",
    )

    assert downloaded.exists()
    assert downloaded.stat().st_size > 0


def test_telegram_channel_cloning_url_detection():
    """Verify distinction between bot delivery links and public channel cloning links."""
    from app.media.telethon_uploader import is_bot_delivery_link

    # Delivery bots
    assert is_bot_delivery_link("https://t.me/MSUBKYIMAL_BOT?start=sr_56417") is True
    assert is_bot_delivery_link("https://t.me/MyAwesomeBot?start=mov_123") is True
    assert is_bot_delivery_link("https://t.me/MSUBKYIMAL_BOT") is True
    assert is_bot_delivery_link("@some_delivery_bot") is True
    assert is_bot_delivery_link("tg://resolve?domain=MSUBKYIMAL_BOT&start=sr_1") is True

    # Public channels and post links for cloning
    assert is_bot_delivery_link("https://t.me/ch003agwpcd/898") is False
    assert is_bot_delivery_link("https://t.me/bcm12345678/1030") is False
    assert is_bot_delivery_link("https://t.me/hmtv20/538") is False
    assert is_bot_delivery_link("https://t.me/new2025channel") is False
    assert is_bot_delivery_link("https://t.me/mw2025newchannel") is False
    assert is_bot_delivery_link("@mychannel") is False


@pytest.mark.asyncio
async def test_website_metadata_enrichment():
    """Verify movie detail extraction correctly captures poster, Burmese overview, and metadata."""
    scraper = HomieTVScraper()
    try:
        results, total = await scraper.search("Resident Dead")
        assert len(results) > 0
        detail = await scraper.get_movie_detail(results[0].detail_url)
        assert detail is not None
        assert detail.poster_url.startswith("http")
        assert len(detail.description) > 50  # Contains full Burmese review
        assert detail.year != ""
        assert detail.category != ""
    finally:
        await scraper.close()



