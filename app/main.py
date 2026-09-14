"""Application entry point.

Creates all dependencies, wires them together via DI,
and starts the Telegram bot.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from app.utils.compat import apply_compat_patches

apply_compat_patches()

from app.config import load_config
from app.utils.logging import setup_logging, get_logger


logger = get_logger(__name__)


def main() -> None:
    """Main entry point — configure, build, and run the bot."""
    # Load configuration (fails fast if required env vars are missing)
    try:
        config = load_config()
    except Exception as e:
        print(f"❌ Configuration error: {e}", file=sys.stderr)
        print("   Make sure you have a valid .env file.", file=sys.stderr)
        print("   See .env.example for reference.", file=sys.stderr)
        sys.exit(1)

    # Setup logging
    setup_logging(config.log_level)

    # Initialize reliable public DNS resolver
    from app.utils.dns import setup_dns_resolver
    setup_dns_resolver()

    # Accelerate Telethon crypto with OpenSSL
    from app.utils.crypto_patch import apply_crypto_patch
    apply_crypto_patch()

    logger.info("Starting Movie Man Bot")
    logger.info(config.safe_repr())

    # Create dependencies
    from app.scraper.client import MMSubChannelScraper
    from app.scraper.homietv import HomieTVScraper
    from app.scraper.manager import MultiSourceScraper
    from app.resolver.link_resolver import LinkResolver
    from app.mega.downloader import MegaDownloader
    from app.media.http_downloader import HttpDownloader
    from app.pipeline.manager import PipelineManager
    from app.bot.handlers import create_bot

    # Scrapers
    mmsub_scraper = MMSubChannelScraper(
        base_url=config.mmsubchannel_base_url,
        browser_type=config.browser_type,
        headless=config.browser_headless,
    )
    homietv_scraper = HomieTVScraper()
    scraper = MultiSourceScraper(
        mmsub_scraper=mmsub_scraper,
        homietv_scraper=homietv_scraper,
    )

    # Downloaders
    mega_downloader = MegaDownloader(
        email=config.mega_email,
        password=config.mega_password,
    )
    http_downloader = HttpDownloader()

    # Telethon 2GB Uploader (optional MTProto client)
    telethon_uploader = None
    if config.use_telethon:
        from app.media.telethon_uploader import TelethonUploader
        telethon_uploader = TelethonUploader(
            api_id=config.telegram_api_id,
            api_hash=config.telegram_api_hash,
            session_name=config.telethon_session or "telethon.session",
        )
        logger.info("Telethon 2GB MTProto Uploader enabled")

    # Link resolver needs a browser context from the scraper
    # We'll initialize it lazily after the scraper's browser is ready
    resolver = None

    # Pipeline manager
    pipeline = PipelineManager(
        config=config,
        scraper=scraper,
        resolver=resolver,  # Will be set after browser init
        mega_downloader=mega_downloader,
        telethon_uploader=telethon_uploader,
        http_downloader=http_downloader,
    )

    # We need to initialize the resolver with the scraper's browser context
    # This is done via a startup hook
    async def on_startup(app):
        """Initialize browser-dependent components on bot startup."""
        nonlocal resolver
        try:
            browser_context = await mmsub_scraper._ensure_browser()
            resolver = LinkResolver(browser_context)
            pipeline._resolver = resolver
            logger.info("Link resolver initialized with browser context")
            bot_user = await app.bot.get_me()
            logger.info(f"Bot @{bot_user.username} is fully online and ready!")
            logger.info("Open Telegram and send /start to your bot to begin.")
        except Exception as e:
            logger.error(f"Failed to initialize browser: {e}")
            logger.warning("Link resolution will not work until browser is available")

        # Initialize movie store database
        try:
            from app.store.database import init_db
            db_path = config.workspace_dir / "movie_store.db"
            await init_db(db_path)
            logger.info(f"Movie store database ready at {db_path}")
        except Exception as e:
            logger.error(f"Failed to initialize movie store database: {e}")

        # Start FastAPI web server in background
        try:
            from app.web.server import start_server
            asyncio.create_task(
                start_server(host=config.web_host, port=config.web_port),
                name="web-server",
            )
            logger.info(f"Movie Store frontend starting on http://{config.web_host}:{config.web_port}")
        except Exception as e:
            logger.error(f"Failed to start web server: {e}")

    async def on_shutdown(app):
        """Cleanup on bot shutdown."""
        try:
            await scraper.close()
        except Exception as e:
            logger.warning(f"Scraper cleanup error: {e}")

        if telethon_uploader:
            try:
                await telethon_uploader.close()
            except Exception as e:
                logger.warning(f"Telethon cleanup error: {e}")

        # Close movie store database
        try:
            from app.store.database import close_db
            await close_db()
        except Exception as e:
            logger.warning(f"Database cleanup error: {e}")

    # Telegram bot
    app = create_bot(config, scraper, pipeline)

    # Register lifecycle hooks
    app.post_init = on_startup
    app.post_shutdown = on_shutdown

    logger.info("Bot is starting... Press Ctrl+C to stop.")

    # Run the bot
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"],
    )


if __name__ == "__main__":
    main()

