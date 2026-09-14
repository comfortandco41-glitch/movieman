"""Telegram bot command handlers and message routing."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.bot.callbacks import handle_callback
from app.bot.keyboards import main_menu_keyboard

if TYPE_CHECKING:
    from app.config import Config
    from app.pipeline.manager import PipelineManager
    from app.scraper.client import MMSubChannelScraper

logger = logging.getLogger(__name__)


# ── Command Handlers ───────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command — show the main menu."""
    await update.message.reply_text(
        "🎬 <b>Movie Man Bot</b>\n\n"
        "Download movies from MMSubChannel and upload to Telegram.\n\n"
        "Choose an action below:",
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )


async def latest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /latest command — fetch and display latest movies."""
    scraper: MMSubChannelScraper = context.bot_data["scraper"]

    msg = await update.message.reply_text("🔍 Fetching latest movies...")

    try:
        movies = await scraper.get_latest_movies(max_items=10)
    except Exception as e:
        logger.error(f"Failed to fetch latest movies: {e}")
        await msg.edit_text(
            "❌ Failed to fetch movies. Please try again later.",
            reply_markup=main_menu_keyboard(),
            parse_mode="HTML",
        )
        return

    if not movies:
        await msg.edit_text(
            "😕 No movies found on the site right now.",
            reply_markup=main_menu_keyboard(),
            parse_mode="HTML",
        )
        return

    # Cache the movie list
    context.user_data["movie_list"] = movies

    from app.bot.keyboards import movie_list_keyboard

    text = (
        f"🎬 <b>Latest Movies</b>\n"
        f"Found {len(movies)} movies\n\n"
        "Select a movie to download:"
    )

    await msg.edit_text(
        text,
        reply_markup=movie_list_keyboard(movies),
        parse_mode="HTML",
    )


def is_telegram_delivery_url(url: str) -> bool:
    """Check if URL points to a Telegram bot or channel link."""
    low = url.lower()
    return "t.me" in low or "telegram.me" in low


async def fetch_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /fetch <url> command — process a specific URL.

    Accepts HomieTV URLs, MMSubChannel URLs, direct Mega.nz links, or Telegram links.
    """
    from app.pipeline.manager import PipelineManager
    from app.resolver.link_resolver import is_mega_url
    from app.scraper.models import MovieListing

    pipeline: PipelineManager = context.bot_data["pipeline"]
    scraper = context.bot_data["scraper"]
    user_id = update.effective_user.id

    # Check for active job
    if pipeline.has_active_job(user_id):
        await update.message.reply_text(
            "⚠️ You already have an active job running.\n"
            "Please wait for it to finish or /cancel it first.",
            parse_mode="HTML",
        )
        return

    # Get URL from command args
    if not context.args:
        await update.message.reply_text(
            "📋 <b>Usage:</b> <code>/fetch &lt;url&gt;</code>\n\n"
            "Examples:\n"
            "• <code>/fetch https://www.homietv.com/movie/...</code>\n"
            "• <code>/fetch https://www.homietv.com/tv-show/...</code>\n"
            "• <code>/fetch https://mmsubchannel.com/movie/...</code>\n"
            "• <code>/fetch https://mega.nz/file/...</code>",
            parse_mode="HTML",
        )
        return

    url = context.args[0].strip()

    msg = await update.message.reply_text(f"⚙️ Inspecting URL...")

    # 1. HomieTV URLs — fetch details and offer server/quality choices if available
    if "homietv.com" in url.lower():
        try:
            detail = await scraper.get_movie_detail(url)
            if detail:
                if len(detail.download_links) > 1:
                    context.user_data["movie_list"] = [
                        MovieListing(
                            title=detail.title,
                            detail_url=detail.detail_url,
                            poster_url=detail.poster_url,
                            year=detail.year,
                            quality=detail.quality,
                            category=detail.category,
                            source="homietv",
                        )
                    ]
                    context.user_data["detail_0"] = detail
                    from app.bot.keyboards import download_options_keyboard
                    text = (
                        f"🎬 <b>{detail.title}</b>\n\n"
                        f"Found <b>{len(detail.download_links)}</b> download options.\n"
                        "Select your preferred server / quality below:"
                    )
                    await msg.edit_text(
                        text,
                        reply_markup=download_options_keyboard(0, detail.download_links),
                        parse_mode="HTML",
                    )
                    return
                elif len(detail.download_links) == 1:
                    opt = detail.download_links[0]
                    is_tg = "telegram" in opt.server_name.lower() or "t.me" in opt.url.lower()
                    is_mega = opt.is_mega or "mega.nz" in opt.url or "mega.co.nz" in opt.url
                    job_id = await pipeline.create_job(
                        user_id=user_id,
                        movie_page_url=detail.detail_url,
                        title=detail.title,
                        bot=context.bot,
                        chat_id=update.message.chat_id,
                        telegram_url=opt.url if is_tg else "",
                        mega_url=opt.url if is_mega else "",
                        download_url=opt.url if not is_tg and not is_mega else "",
                        selected_server=opt.server_name,
                        poster_url=detail.poster_url or "",
                        description=detail.description or "",
                        year=detail.year or "",
                        category=detail.category or "",
                        duration=detail.duration or "",
                        quality=detail.quality or opt.label or "",
                    )
                    await msg.edit_text(
                        f"✅ Job <code>#{job_id[:6]}</code> created!\n\n"
                        f"🎬 <b>{detail.title}</b>\n"
                        f"⚡ Source: <b>{opt.server_name}</b>\n\n"
                        "Processing will begin shortly...",
                        parse_mode="HTML",
                    )
                    return
        except Exception as e:
            logger.warning(f"Failed to pre-fetch HomieTV download links for {url}: {e}")

    # 2. Telegram link
    if is_telegram_delivery_url(url):
        job_id = await pipeline.create_job(
            user_id=user_id,
            movie_page_url="",
            title="Telegram Movie Delivery",
            bot=context.bot,
            chat_id=update.message.chat_id,
            telegram_url=url,
        )
    # 3. Direct Mega link
    elif is_mega_url(url):
        job_id = await pipeline.create_job(
            user_id=user_id,
            movie_page_url="",
            title="Direct Mega Download",
            bot=context.bot,
            chat_id=update.message.chat_id,
            mega_url=url,
        )
    # 4. Standard Movie detail URL
    else:
        job_id = await pipeline.create_job(
            user_id=user_id,
            movie_page_url=url,
            title="Movie from URL",
            bot=context.bot,
            chat_id=update.message.chat_id,
        )

    await msg.edit_text(
        f"✅ Job <code>#{job_id[:6]}</code> created!\n\n"
        f"🔗 {url[:60]}...\n\n"
        "Processing will begin shortly...",
        parse_mode="HTML",
    )


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /search <query> command — search across movie sources."""
    if not context.args:
        await update.message.reply_text(
            "📋 <b>Usage:</b> <code>/search &lt;movie or series name&gt;</code>\n\n"
            "<i>Example:</i> <code>/search Resident</code>",
            parse_mode="HTML",
        )
        return

    query = " ".join(context.args).strip()
    scraper = context.bot_data["scraper"]

    msg = await update.message.reply_text(f"🔍 Searching for '<b>{query}</b>'...", parse_mode="HTML")

    try:
        if hasattr(scraper, "search"):
            movies = await scraper.search(query)
        else:
            movies = await scraper.search_movies(query)
    except Exception as e:
        logger.error(f"Search error for '{query}': {e}")
        await msg.edit_text("❌ Search failed. Please try again later.")
        return

    if not movies:
        await msg.edit_text(f"😕 No movies found matching '<b>{query}</b>'.", parse_mode="HTML")
        return

    context.user_data["movie_list"] = movies
    from app.bot.keyboards import movie_list_keyboard

    await msg.edit_text(
        f"🎬 <b>Search Results for '{query}'</b>\n"
        f"Found {len(movies)} results:\n\n"
        "Select a title below to view download options:",
        reply_markup=movie_list_keyboard(movies),
        parse_mode="HTML",
    )


async def upload_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /upload <job_id> <telegram_video_url> — publish movie to the frontend store.

    Looks up the completed job's metadata (poster, review, title, year, etc.)
    and saves it to the movie store database along with the Telegram video link.

    Usage:
        /upload <job_id> <telegram_video_url>
        /upload abc123 https://t.me/channel/456
    """
    from app.pipeline.manager import PipelineManager
    from app.store.database import add_movie

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "📋 <b>Usage:</b> <code>/upload &lt;job_id&gt; &lt;telegram_video_url&gt;</code>\n\n"
            "<i>Example:</i>\n"
            "<code>/upload abc123 https://t.me/channel/456</code>\n\n"
            "The job ID is the 6-character code shown when a job completes.\n"
            "The Telegram video URL is the link to the watchable video in your channel.",
            parse_mode="HTML",
        )
        return

    job_id_input = context.args[0].strip().lstrip("#")
    telegram_video_url = context.args[1].strip()

    # Validate the Telegram video URL
    if "t.me" not in telegram_video_url.lower() and "telegram" not in telegram_video_url.lower():
        await update.message.reply_text(
            "❌ Invalid Telegram video URL.\n"
            "Please provide a valid <code>t.me</code> link.",
            parse_mode="HTML",
        )
        return

    pipeline: PipelineManager = context.bot_data["pipeline"]
    movie_meta = None

    # Strategy 1: In-memory pipeline manager cache
    ctx = pipeline.get_job_by_id(job_id_input)
    if ctx:
        movie_meta = {
            "job_id": ctx.job_id,
            "title": ctx.title,
            "poster_url": ctx.poster_url or "",
            "description": ctx.description or "",
            "year": ctx.year or "",
            "quality": ctx.quality or "",
            "category": ctx.category or "",
            "duration": ctx.duration or "",
            "source": getattr(ctx, "selected_server", ""),
        }

    # Strategy 2: Persistent SQLite completed_jobs table
    if not movie_meta:
        from app.store.database import get_completed_job
        db_job = await get_completed_job(job_id_input)
        if db_job:
            movie_meta = db_job

    # Strategy 3: If job_id_input is a website URL (HomieTV or MMSubChannel)
    if not movie_meta and (job_id_input.startswith("http://") or job_id_input.startswith("https://")):
        scraper = context.bot_data["scraper"]
        try:
            detail = await scraper.get_movie_detail(job_id_input)
            if detail:
                import uuid
                movie_meta = {
                    "job_id": f"url_{uuid.uuid4().hex[:8]}",
                    "title": detail.title,
                    "poster_url": detail.poster_url or "",
                    "description": detail.description or "",
                    "year": getattr(detail, "year", "") or "",
                    "quality": getattr(detail, "quality", "") or "",
                    "category": getattr(detail, "category", "") or "",
                    "duration": getattr(detail, "duration", "") or "",
                    "source": getattr(detail, "source", "web"),
                }
        except Exception as e:
            logger.warning(f"Failed to scrape movie URL in /upload: {e}")

    # Strategy 4: Inspect Telegram video link directly via Telethon
    if not movie_meta:
        uploader = getattr(pipeline, "_telethon_uploader", None)
        if uploader and getattr(uploader, "_client", None):
            try:
                client = uploader._client
                if await uploader.is_authorized():
                    import re
                    channel_target = None
                    msg_id = None
                    m_priv = re.search(r"t\.me/c/(\d+)/(\d+)", telegram_video_url)
                    m_pub = re.search(r"t\.me/([a-zA-Z0-9_]+)/(\d+)", telegram_video_url)
                    if m_priv:
                        channel_target = int("-100" + m_priv.group(1))
                        msg_id = int(m_priv.group(2))
                    elif m_pub:
                        channel_target = m_pub.group(1)
                        msg_id = int(m_pub.group(2))

                    if channel_target and msg_id:
                        tg_msg = await client.get_messages(channel_target, ids=msg_id)
                        if tg_msg:
                            extracted_title = ""
                            raw_text = tg_msg.text or ""
                            title_m = re.search(r"🎬\s*<b>(.*?)</b>", raw_text)
                            if title_m:
                                extracted_title = title_m.group(1).strip()
                            elif raw_text:
                                extracted_title = raw_text.split("\n")[0].replace("🎬", "").strip()
                            elif getattr(tg_msg, "file", None) and getattr(tg_msg.file, "name", None):
                                clean_f = re.split(r"(?:19|20)\d{2}|720p|1080p|480p|\.", tg_msg.file.name)[0]
                                extracted_title = clean_f.replace(".", " ").strip()

                            if extracted_title:
                                scraper = context.bot_data["scraper"]
                                search_res = await scraper.search(extracted_title)
                                if search_res:
                                    det = await scraper.get_movie_detail(search_res[0].detail_url)
                                    if det:
                                        import uuid
                                        movie_meta = {
                                            "job_id": job_id_input or f"tg_{uuid.uuid4().hex[:8]}",
                                            "title": det.title or extracted_title,
                                            "poster_url": det.poster_url or "",
                                            "description": det.description or "",
                                            "year": getattr(det, "year", "") or "",
                                            "quality": getattr(det, "quality", "") or "",
                                            "category": getattr(det, "category", "") or "",
                                            "duration": getattr(det, "duration", "") or "",
                                            "source": getattr(det, "source", "telegram"),
                                        }
                                else:
                                    import uuid
                                    movie_meta = {
                                        "job_id": job_id_input or f"tg_{uuid.uuid4().hex[:8]}",
                                        "title": extracted_title,
                                        "poster_url": "",
                                        "description": raw_text,
                                        "year": "",
                                        "quality": "",
                                        "category": "",
                                        "duration": "",
                                        "source": "telegram",
                                    }
            except Exception as tg_err:
                logger.warning(f"Error inspecting telegram message in /upload: {tg_err}")

    # Strategy 5: Title search fallback
    if not movie_meta and len(job_id_input) > 2 and not job_id_input.startswith("http"):
        scraper = context.bot_data["scraper"]
        try:
            search_res = await scraper.search(job_id_input)
            if search_res:
                det = await scraper.get_movie_detail(search_res[0].detail_url)
                if det:
                    import uuid
                    movie_meta = {
                        "job_id": f"search_{uuid.uuid4().hex[:8]}",
                        "title": det.title,
                        "poster_url": det.poster_url or "",
                        "description": det.description or "",
                        "year": getattr(det, "year", "") or "",
                        "quality": getattr(det, "quality", "") or "",
                        "category": getattr(det, "category", "") or "",
                        "duration": getattr(det, "duration", "") or "",
                        "source": getattr(det, "source", "search"),
                    }
        except Exception as e:
            logger.debug(f"Title search fallback failed: {e}")

    if not movie_meta:
        await update.message.reply_text(
            f"❌ Job <code>#{job_id_input}</code> not found in cache.\n\n"
            "This can happen if the bot restarted before the job was saved.\n\n"
            "💡 <b>You can still publish this movie easily using:</b>\n"
            "1. <b>The movie webpage URL:</b>\n"
            f"<code>/upload &lt;movie_website_url&gt; {telegram_video_url}</code>\n\n"
            "2. <b>Or the movie name:</b>\n"
            f"<code>/upload \"Movie Name\" {telegram_video_url}</code>",
            parse_mode="HTML",
        )
        return

    # Save to the movie store database
    try:
        movie_id = await add_movie(
            job_id=movie_meta["job_id"],
            title=movie_meta["title"],
            poster_url=movie_meta.get("poster_url", "") or "",
            description=movie_meta.get("description", "") or "",
            year=movie_meta.get("year", "") or "",
            quality=movie_meta.get("quality", "") or "",
            category=movie_meta.get("category", "") or "",
            duration=movie_meta.get("duration", "") or "",
            source=movie_meta.get("source", "") or "",
            telegram_video_url=telegram_video_url,
        )
    except Exception as e:
        logger.error(f"Failed to add movie to store: {e}")
        await update.message.reply_text(
            "❌ Failed to publish movie to the store. Please try again.",
            parse_mode="HTML",
        )
        return

    config = context.bot_data.get("config")
    web_port = getattr(config, "web_port", 8080) if config else 8080

    await update.message.reply_text(
        f"✅ <b>Published to Movie Store!</b>\n\n"
        f"🎬 <b>{movie_meta['title']}</b>\n"
        f"📎 Store ID: <code>#{movie_id}</code>\n"
        f"🔗 Video: {telegram_video_url}\n\n"
        f"🌐 View at: <code>http://localhost:{web_port}</code>",
        parse_mode="HTML",
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /cancel command — cancel the user's active job."""
    from app.pipeline.manager import PipelineManager

    pipeline: PipelineManager = context.bot_data["pipeline"]
    user_id = update.effective_user.id

    cancelled = await pipeline.cancel_user_job(user_id)
    if cancelled:
        await update.message.reply_text("🚫 Job cancellation requested.")
    else:
        await update.message.reply_text("ℹ️ No active job to cancel.")


# ── Message Handler ────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle plain text messages.

    Routes to URL processing if user is in "waiting_url" state,
    or treats the text as a search query / URL to fetch.
    """
    text = update.message.text.strip()

    if not text:
        return

    # Check if user is waiting to submit a URL
    if context.user_data.get("waiting_url"):
        context.user_data["waiting_url"] = False
        await _handle_url_input(update, context, text)
        return

    # Check if the text looks like a URL
    if text.startswith("http://") or text.startswith("https://"):
        await _handle_url_input(update, context, text)
        return

    # Default: show help
    await update.message.reply_text(
        "💡 Send a movie URL or use the menu:\n\n"
        "/start — Main menu\n"
        "/latest — Latest movies\n"
        "/fetch <url> — Process a URL",
        parse_mode="HTML",
    )


async def _handle_url_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
) -> None:
    """Process a submitted URL (movie page or Mega link)."""
    from app.pipeline.manager import PipelineManager
    from app.resolver.link_resolver import is_mega_url

    pipeline: PipelineManager = context.bot_data["pipeline"]
    user_id = update.effective_user.id

    if pipeline.has_active_job(user_id):
        await update.message.reply_text(
            "⚠️ You already have an active job running.\n"
            "Please wait for it to finish or /cancel it first.",
            parse_mode="HTML",
        )
        return

    msg = await update.message.reply_text("⚙️ Processing URL...")

    if is_telegram_delivery_url(url):
        job_id = await pipeline.create_job(
            user_id=user_id,
            movie_page_url="",
            title="Telegram Movie Delivery",
            bot=context.bot,
            chat_id=update.message.chat_id,
            telegram_url=url,
        )
    elif is_mega_url(url):
        job_id = await pipeline.create_job(
            user_id=user_id,
            movie_page_url="",
            title="Direct Mega Download",
            bot=context.bot,
            chat_id=update.message.chat_id,
            mega_url=url,
        )
    else:
        job_id = await pipeline.create_job(
            user_id=user_id,
            movie_page_url=url,
            title="Movie from URL",
            bot=context.bot,
            chat_id=update.message.chat_id,
        )

    await msg.edit_text(
        f"✅ Job <code>#{job_id[:6]}</code> created!\n\n"
        f"🔗 {url[:60]}{'...' if len(url) > 60 else ''}\n\n"
        "Processing will begin shortly...",
        parse_mode="HTML",
    )


# ── Bot Factory ────────────────────────────────────────────────────────────

def create_bot(
    config: "Config",
    scraper: "MMSubChannelScraper",
    pipeline: "PipelineManager",
) -> Application:
    """Create and configure the Telegram bot application.

    Args:
        config: Application configuration.
        scraper: MMSubChannel scraper instance.
        pipeline: Pipeline manager instance.

    Returns:
        Configured Application ready to run.
    """
    builder = Application.builder().token(config.telegram_bot_token)
    app = builder.build()

    # Store shared dependencies in bot_data
    app.bot_data["scraper"] = scraper
    app.bot_data["pipeline"] = pipeline
    app.bot_data["config"] = config

    # Register handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("latest", latest_command))
    app.add_handler(CommandHandler("fetch", fetch_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("upload", upload_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    # Register error handler
    async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle errors cleanly."""
        from telegram.error import Conflict
        if isinstance(context.error, Conflict):
            logger.warning(
                "Telegram Conflict: Another bot instance with the same token is running."
            )
            return
        logger.error(f"Update {update} caused error {context.error}", exc_info=context.error)

    app.add_error_handler(_error_handler)

    return app
