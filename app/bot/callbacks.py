"""Callback query handler for inline keyboard interactions."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.keyboards import (
    main_menu_keyboard,
    movie_list_keyboard,
    movie_detail_keyboard,
    download_options_keyboard,
    source_choice_keyboard,
    job_completed_keyboard,
)

if TYPE_CHECKING:
    from app.pipeline.manager import PipelineManager

logger = logging.getLogger(__name__)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all callback queries to appropriate handlers."""
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = query.from_user.id

    if data == "menu":
        await _handle_menu(query, context)

    elif data == "latest":
        await _handle_choose_source(query, context)

    elif data.startswith("src:"):
        source = data.split(":")[1]
        await _handle_fetch_by_source(query, context, source)

    elif data == "submit_url":
        await _handle_submit_url(query, context)

    elif data == "help":
        await _handle_help(query, context)

    elif data.startswith("movie:"):
        index = int(data.split(":")[1])
        await _handle_movie_detail(query, context, index)

    elif data.startswith("download:"):
        index = int(data.split(":")[1])
        await _handle_start_download(query, context, index, user_id)

    elif data.startswith("dl_opt:"):
        parts = data.split(":")
        movie_idx = int(parts[1])
        opt_idx = int(parts[2])
        await _handle_start_download_option(query, context, movie_idx, opt_idx, user_id)

    elif data.startswith("cancel:"):
        job_id = data.split(":")[1]
        await _handle_cancel_job(query, context, job_id, user_id)

    else:
        logger.warning(f"Unknown callback data: {data}")


async def _handle_menu(query, context) -> None:
    """Show the main menu."""
    await query.message.edit_text(
        "🎬 <b>Movie Man Bot</b>\n\n"
        "Download movies from MMSubChannel and upload to Telegram.\n\n"
        "Choose an action below:",
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )


async def _handle_choose_source(query, context) -> None:
    """Prompt user to select movie source (HomieTV or MMSubChannel)."""
    await query.message.edit_text(
        "🎬 <b>Select Movie Source</b>\n\n"
        "Choose which site you'd like to browse:",
        reply_markup=source_choice_keyboard(),
        parse_mode="HTML",
    )


async def _handle_fetch_by_source(query, context, source: str) -> None:
    """Fetch latest movies from chosen source."""
    scraper = context.bot_data["scraper"]
    src_title = "HomieTV" if source == "homietv" else ("MMSubChannel" if source == "mmsubchannel" else "All Sources")

    await query.message.edit_text(f"🔍 Fetching latest movies from {src_title}...")

    try:
        if hasattr(scraper, "get_latest_movies"):
            import inspect
            sig = inspect.signature(scraper.get_latest_movies)
            if "source" in sig.parameters:
                movies = await scraper.get_latest_movies(source=source, max_items=10)
            else:
                movies = await scraper.get_latest_movies(max_items=10)
        else:
            movies = []
    except Exception as e:
        logger.error(f"Failed to fetch latest movies from {source}: {e}")
        await query.message.edit_text(
            f"❌ Failed to fetch movies from {src_title}. Please try again later.",
            reply_markup=main_menu_keyboard(),
            parse_mode="HTML",
        )
        return

    if not movies:
        await query.message.edit_text(
            f"😕 No movies found on {src_title} right now.",
            reply_markup=main_menu_keyboard(),
            parse_mode="HTML",
        )
        return

    # Cache the movie list for callback reference
    context.user_data["movie_list"] = movies

    text = (
        f"🎬 <b>Latest Movies ({src_title})</b>\n"
        f"Found {len(movies)} releases\n\n"
        "Select a movie to download:"
    )

    await query.message.edit_text(
        text,
        reply_markup=movie_list_keyboard(movies),
        parse_mode="HTML",
    )


async def _handle_submit_url(query, context) -> None:
    """Prompt user to submit a URL."""
    context.user_data["waiting_url"] = True
    await query.message.edit_text(
        "🔗 <b>Submit Movie URL</b>\n\n"
        "Please paste a movie page URL from <b>HomieTV</b> or <b>MMSubChannel</b>:\n\n"
        "<i>Examples:</i>\n"
        "• <code>https://www.homietv.com/movie/...</code>\n"
        "• <code>https://www.homietv.com/tv-show/...</code>\n"
        "• <code>https://mmsubchannel.com/movie/...</code>\n"
        "• <code>https://mega.nz/file/...</code>\n\n"
        "Send the URL as a text message.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Cancel", callback_data="menu")]
        ]),
        parse_mode="HTML",
    )


async def _handle_help(query, context) -> None:
    """Show help message."""
    await query.message.edit_text(
        "ℹ️ <b>Movie Man — Help</b>\n\n"
        "<b>Commands:</b>\n"
        "/start — Show main menu\n"
        "/latest — List latest movies\n"
        "/fetch &lt;url&gt; — Process a specific URL\n"
        "/search &lt;query&gt; — Search movies\n"
        "/cancel — Cancel active job\n\n"
        "<b>Supported sources:</b>\n"
        "• <b>HomieTV</b> (https://www.homietv.com) — Ultra-fast downloads via MegaUp, Usersdrive, Telegram\n"
        "• <b>MMSubChannel</b> (https://mmsubchannel.com) — Mega.nz direct releases\n"
        "• Direct Mega.nz & Telegram channel links",
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )


async def _handle_movie_detail(query, context, index: int) -> None:
    """Show movie detail with download and server options."""
    movies = context.user_data.get("movie_list", [])

    if index >= len(movies):
        await query.message.edit_text(
            "❌ Movie not found. Please search again.",
            reply_markup=main_menu_keyboard(),
            parse_mode="HTML",
        )
        return

    movie = movies[index]
    scraper = context.bot_data["scraper"]

    await query.message.edit_text("🔍 Loading movie download options...")

    detail = None
    try:
        detail = await scraper.get_movie_detail(movie.detail_url)
    except Exception as e:
        logger.warning(f"Could not load extended detail for {movie.detail_url}: {e}")

    text = f"🎬 <b>{movie.title}</b>\n\n"
    if movie.quality:
        text += f"📊 Quality: {movie.quality}\n"
    if movie.year:
        text += f"📅 Year: {movie.year}\n"
    if movie.category:
        text += f"🏷️ Genre: {movie.category}\n"
    if detail and detail.duration:
        text += f"⏱️ Runtime: {detail.duration}\n"

    # If multiple download links (servers/qualities) exist, let user pick
    if detail and len(detail.download_links) > 1:
        context.user_data[f"detail_{index}"] = detail
        text += f"\nFound <b>{len(detail.download_links)}</b> download options.\nSelect your preferred server/quality below:"
        await query.message.edit_text(
            text,
            reply_markup=download_options_keyboard(index, detail.download_links),
            parse_mode="HTML",
        )
        return

    text += (
        f"\n🔗 <code>{movie.detail_url}</code>\n\n"
        "Click below to start downloading and uploading to the channel:"
    )

    await query.message.edit_text(
        text,
        reply_markup=movie_detail_keyboard(index),
        parse_mode="HTML",
    )


async def _handle_start_download_option(query, context, movie_idx: int, opt_idx: int, user_id: int) -> None:
    """Start job with the user's specifically chosen server/quality."""
    from app.pipeline.manager import PipelineManager

    pipeline: PipelineManager = context.bot_data["pipeline"]
    detail = context.user_data.get(f"detail_{movie_idx}")

    if not detail or opt_idx >= len(detail.download_links):
        await query.message.edit_text(
            "❌ Option expired. Please browse again.",
            reply_markup=main_menu_keyboard(),
            parse_mode="HTML",
        )
        return

    if pipeline.has_active_job(user_id):
        await query.message.edit_text(
            "⚠️ You already have an active job running.\n"
            "Please wait for it to finish or /cancel it first.",
            parse_mode="HTML",
        )
        return

    opt = detail.download_links[opt_idx]
    s_name = opt.server_name or "Direct"
    await query.message.edit_text(f"⚙️ Selected: {opt.label}\nStarting pipeline...")

    is_tg = "telegram" in s_name.lower() or "t.me" in opt.url.lower()
    is_mega = opt.is_mega or "mega.nz" in opt.url or "mega.co.nz" in opt.url

    job_id = await pipeline.create_job(
        user_id=user_id,
        movie_page_url=detail.detail_url,
        title=f"{detail.title} ({opt.label})",
        bot=context.bot,
        chat_id=query.message.chat_id,
        telegram_url=opt.url if is_tg else "",
        mega_url=opt.url if is_mega else "",
        download_url=opt.url if not is_tg and not is_mega else "",
        selected_server=s_name,
        poster_url=detail.poster_url or "",
        description=detail.description or "",
        year=detail.year or "",
        category=detail.category or "",
        duration=detail.duration or "",
        quality=detail.quality or opt.label or "",
    )

    await query.message.edit_text(
        f"✅ Job <code>#{job_id[:6]}</code> created!\n\n"
        f"🎬 <b>{detail.title}</b>\n"
        f"⚡ Source: <b>{s_name}</b> ({opt.label})\n\n"
        "Processing will begin shortly...",
        parse_mode="HTML",
    )


async def _handle_start_download(query, context, index: int, user_id: int) -> None:
    """Start a download job for the selected movie."""
    from app.pipeline.manager import PipelineManager

    pipeline: PipelineManager = context.bot_data["pipeline"]
    movies = context.user_data.get("movie_list", [])

    if index >= len(movies):
        await query.message.edit_text(
            "❌ Movie not found. Please search again.",
            reply_markup=main_menu_keyboard(),
            parse_mode="HTML",
        )
        return

    # Check if user already has an active job
    if pipeline.has_active_job(user_id):
        await query.message.edit_text(
            "⚠️ You already have an active job running.\n"
            "Please wait for it to finish or /cancel it first.",
            parse_mode="HTML",
        )
        return

    movie = movies[index]
    cached_detail = context.user_data.get(f"detail_{index}")
    poster_url = cached_detail.poster_url if cached_detail else getattr(movie, "poster_url", "")
    description = cached_detail.description if cached_detail else ""
    year = cached_detail.year if cached_detail else getattr(movie, "year", "")
    category = cached_detail.category if cached_detail else getattr(movie, "category", "")
    duration = cached_detail.duration if cached_detail else ""
    quality = cached_detail.quality if cached_detail else getattr(movie, "quality", "")

    await query.message.edit_text(f"⚙️ Starting download: {movie.title}...")

    job_id = await pipeline.create_job(
        user_id=user_id,
        movie_page_url=movie.detail_url,
        title=movie.title,
        bot=context.bot,
        chat_id=query.message.chat_id,
        poster_url=poster_url or "",
        description=description or "",
        year=year or "",
        category=category or "",
        duration=duration or "",
        quality=quality or "",
    )

    await query.message.edit_text(
        f"✅ Job <code>#{job_id[:6]}</code> created!\n\n"
        f"🎬 {movie.title}\n\n"
        "Processing will begin shortly...",
        parse_mode="HTML",
    )

    logger.info(f"Job {job_id} created for user {user_id}, movie: {movie.title}")


async def _handle_cancel_job(query, context, job_id: str, user_id: int) -> None:
    """Cancel a running job. Validates ownership."""
    from app.pipeline.manager import PipelineManager

    pipeline: PipelineManager = context.bot_data["pipeline"]

    if not pipeline.is_user_job(user_id, job_id):
        await query.answer("⚠️ This is not your job.", show_alert=True)
        return

    cancelled = await pipeline.cancel_job(job_id)
    if cancelled:
        logger.info(f"Job {job_id} cancelled by user {user_id}")
    else:
        await query.answer("Job has already finished.", show_alert=True)
