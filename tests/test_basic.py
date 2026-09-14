"""Tests for runtime compatibility and configuration."""

from __future__ import annotations

import asyncio
from app.utils.compat import apply_compat_patches
from app.config import load_config


def test_compat_coroutine_patch():
    """Verify asyncio.coroutine is available for legacy packages."""
    apply_compat_patches()
    assert hasattr(asyncio, "coroutine")


def test_config_loader():
    """Verify configuration loads and sanitizes secrets."""
    config = load_config()
    safe_str = config.safe_repr()
    assert "telegram_bot_token" in safe_str
    assert "8622162960:AAGNw0Pbk3rypG-xvS27OlG7o1QmxJgoUaY" not in safe_str  # Token is masked


def test_telegram_delivery_detection():
    """Verify detection of Telegram delivery bot links."""
    from app.bot.handlers import is_telegram_delivery_url
    from app.scraper.models import MovieDetail, DownloadLink

    url = "https://t.me/MSUBKYIMAL_BOT?start=mv_Toy_Story"
    assert is_telegram_delivery_url(url) is True
    assert is_telegram_delivery_url("https://megaup.net/123/movie.mp4") is False
    assert is_telegram_delivery_url("https://mmsubchannel.com/video/123") is False

    detail = MovieDetail(
        title="Test Movie",
        detail_url="https://mmsubchannel.com/video/test",
        download_links=[
            DownloadLink(url="https://megaup.net/placeholder", label="MegaUp"),
            DownloadLink(url="https://t.me/MSUBKYIMAL_BOT?start=mv_test", label="Telegram"),
        ],
    )
    assert detail.telegram_link == "https://t.me/MSUBKYIMAL_BOT?start=mv_test"


def test_job_context_telegram_url():
    """Verify JobContext accepts and tracks telegram_url."""
    from app.pipeline.job import JobContext

    ctx = JobContext(
        movie_page_url="https://mmsubchannel.com/video/test",
        telegram_url="https://t.me/MSUBKYIMAL_BOT?start=mv_test",
    )
    assert ctx.telegram_url == "https://t.me/MSUBKYIMAL_BOT?start=mv_test"


async def test_pipeline_telegram_transfer_flow():
    """Verify PipelineManager executes stage_telegram_transfer when telegram_url is present."""
    from unittest.mock import AsyncMock, MagicMock
    from app.pipeline.manager import PipelineManager
    from app.pipeline.job import JobContext, JobStatus

    config = MagicMock()
    config.max_concurrent_jobs = 1
    config.jobs_dir = MagicMock()
    config.use_telethon = True
    config.telegram_channel_id = -1004385468716

    scraper = MagicMock()
    scraper.get_movie_detail = AsyncMock(return_value=None)
    resolver = MagicMock()
    downloader = MagicMock()
    uploader = MagicMock()
    uploader.is_authorized = AsyncMock(return_value=True)
    uploader.fetch_and_forward_from_bot = AsyncMock(return_value=MagicMock(id=123))

    mgr = PipelineManager(
        config=config,
        scraper=scraper,
        resolver=resolver,
        mega_downloader=downloader,
        telethon_uploader=uploader,
    )

    ctx = JobContext(
        user_id=1,
        title="Test Movie",
        movie_page_url="https://mmsubchannel.com/video/test",
        telegram_url="https://t.me/MSUBKYIMAL_BOT?start=mv_test",
        chat_id=1,
    )

    bot = AsyncMock()
    await mgr._run_pipeline(ctx, bot)

    assert ctx.status == JobStatus.COMPLETED
    uploader.fetch_and_forward_from_bot.assert_called_once()


