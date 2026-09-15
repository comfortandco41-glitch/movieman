"""Pipeline stages — individual processing steps.

Each stage is a standalone async function that operates on a JobContext.
Stages update the context as they progress and report to ProgressReporter.
"""

from __future__ import annotations

import asyncio
import html
import logging
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from app.pipeline.job import JobContext, JobStatus, PipelineError
from app.utils.filenames import sanitize_filename, format_file_size

if TYPE_CHECKING:
    from telegram import Bot
    from app.config import Config
    from app.scraper.client import MMSubChannelScraper
    from app.resolver.link_resolver import LinkResolver
    from app.mega.downloader import MegaDownloader
    from app.media.telethon_uploader import TelethonUploader
    from app.bot.progress import ProgressReporter

logger = logging.getLogger(__name__)


# ── Stage: Scrape ──────────────────────────────────────────────────────────

async def stage_scrape(
    ctx: JobContext,
    scraper: "MMSubChannelScraper",
    progress: Optional["ProgressReporter"] = None,
) -> None:
    """Scrape movie detail page to extract download links.

    Reads ctx.movie_page_url and populates ctx with download links.
    """
    ctx.status = JobStatus.SCRAPING

    if progress:
        await progress.update("SCRAPING", "Fetching movie details...")

    if not ctx.movie_page_url:
        raise PipelineError("Scrape", "No movie page URL provided")

    detail = None
    try:
        detail = await scraper.get_movie_detail(ctx.movie_page_url)
    except Exception as e:
        logger.warning(f"Failed scraping {ctx.movie_page_url}: {e}")

    if not detail:
        if not ctx.telegram_url and not ctx.download_url and not ctx.mega_url:
            raise PipelineError(
                "Scrape",
                "Could not extract movie details from the page",
                f"URL: {ctx.movie_page_url}",
            )
        return

    # Update context with scraped data
    ctx.title = detail.title or ctx.title
    ctx.description = detail.description or ctx.description
    ctx.poster_url = detail.poster_url or ctx.poster_url
    ctx.year = getattr(detail, "year", "") or ctx.year
    ctx.category = getattr(detail, "category", "") or ctx.category
    ctx.duration = getattr(detail, "duration", "") or ctx.duration
    ctx.quality = getattr(detail, "quality", "") or ctx.quality

    if getattr(detail, "telegram_link", None) and not ctx.telegram_url:
        ctx.telegram_url = detail.telegram_link

    # Collect all candidate download links
    candidates: list[str] = []
    if detail.mega_link:
        candidates.append(detail.mega_link)
    for dl in detail.download_links:
        if dl.url not in candidates:
            candidates.append(dl.url)

    if not candidates and not ctx.telegram_url and not ctx.download_url:
        raise PipelineError(
            "Scrape",
            "No download links found on the movie page",
            f"URL: {ctx.movie_page_url}",
        )

    ctx.candidate_urls = candidates
    if candidates and not ctx.mega_url and not ctx.download_url and not ctx.telegram_url:
        ctx.mega_url = candidates[0]

    if progress:
        await progress.update(
            "SCRAPING",
            f"Found: {ctx.title}",
            force=True,
        )

    logger.info(
        f"job={ctx.job_id} scraped: title='{ctx.title}', "
        f"{len(candidates)} candidate links, primary='{ctx.mega_url[:50]}...'"
    )


# ── Stage: Resolve ─────────────────────────────────────────────────────────

async def stage_resolve(
    ctx: JobContext,
    resolver: "LinkResolver",
    progress: Optional["ProgressReporter"] = None,
) -> None:
    """Resolve ad/shortener links to get the final Mega.nz URL.

    Iterates through candidate URLs and resolves to a direct mega.nz link.
    Fast-fails if the file has been removed by the host.
    """
    ctx.status = JobStatus.RESOLVING

    if progress:
        await progress.update("RESOLVING", "Resolving download link...")

    if not ctx.mega_url and not ctx.candidate_urls:
        raise PipelineError("Resolve", "No download URL to resolve")

    from app.resolver.link_resolver import (
        is_mega_url,
        is_direct_url,
        is_filehost_page,
        DeadLinkError,
        LinkResolver,
    )

    if resolver is None:
        logger.warning(f"job={ctx.job_id} resolver was None; instantiating fallback LinkResolver")
        resolver = LinkResolver()

    candidates = ctx.candidate_urls or [ctx.mega_url]
    resolved = None
    dead_error_msg = None

    for i, candidate in enumerate(candidates):
        # If already a Mega URL, skip resolution
        if is_mega_url(candidate):
            logger.info(f"job={ctx.job_id} URL is already a Mega link: {candidate}")
            ctx.mega_url = candidate
            if progress:
                await progress.update("RESOLVING", "Direct Mega link found!", force=True)
            return

        # If already a direct video URL, skip resolution (filehost pages must NEVER be skipped)
        if not is_filehost_page(candidate) and is_direct_url(candidate):
            logger.info(f"job={ctx.job_id} URL is already a direct download link: {candidate}")
            ctx.download_url = candidate
            ctx.mega_url = ""
            if progress:
                await progress.update("RESOLVING", "Direct download link found!", force=True)
            return

        try:
            if progress and len(candidates) > 1:
                await progress.update("RESOLVING", f"Resolving link {i + 1}/{len(candidates)}...")

            resolved = await resolver.resolve(candidate)
            if resolved:
                # Route resolved URL to the correct context field
                if is_mega_url(resolved):
                    ctx.mega_url = resolved
                    ctx.download_url = ""
                    if progress:
                        await progress.update("RESOLVING", "Mega link resolved!", force=True)
                    logger.info(f"job={ctx.job_id} resolved to Mega: {ctx.mega_url}")
                else:
                    # Direct HTTP video URL — route to HttpDownloader
                    ctx.download_url = resolved
                    ctx.mega_url = ""
                    if progress:
                        await progress.update("RESOLVING", "Direct download link resolved!", force=True)
                    logger.info(f"job={ctx.job_id} resolved to direct URL: {ctx.download_url}")
                return
        except DeadLinkError as dle:
            logger.warning(f"job={ctx.job_id} candidate {candidate} is dead: {dle}")
            dead_error_msg = str(dle)
            continue
        except Exception as e:
            logger.warning(f"job={ctx.job_id} failed resolving candidate {candidate}: {e}")
            continue

    if dead_error_msg:
        raise PipelineError(
            "Resolve",
            f"❌ The movie file on the hosting server is no longer available ({dead_error_msg}). The host deleted this file. Please select another movie.",
            f"Original URL: {ctx.mega_url}",
        )

    raise PipelineError(
        "Resolve",
        "Could not resolve download link. The link may have expired or is unavailable.",
        f"Original URL: {ctx.mega_url}",
    )


# ── Stage: Download ────────────────────────────────────────────────────────

async def stage_download(
    ctx: JobContext,
    mega_downloader: "MegaDownloader",
    progress: Optional["ProgressReporter"] = None,
    http_downloader: Optional["HttpDownloader"] = None,
) -> None:
    """Download the movie file to the job workspace directory.

    Automatically routes to HttpDownloader for direct HTTP/stream links
    or MegaDownloader for mega.nz URLs.
    """
    ctx.status = JobStatus.DOWNLOADING

    if not ctx.job_dir:
        raise PipelineError("Download", "Job workspace not initialized")

    def dl_progress(downloaded: int, total: int):
        """Synchronous progress callback."""
        if total > 0:
            pct = int((downloaded / total) * 100)
            if pct % 10 == 0:
                logger.debug(f"job={ctx.job_id} download {pct}%")

    target_url = ctx.download_url or ctx.mega_url
    if not target_url:
        raise PipelineError("Download", "No download URL available")

    is_mega = "mega.nz" in target_url or "mega.co.nz" in target_url

    if is_mega:
        if progress:
            await progress.update("DOWNLOADING", "Starting Mega download...")

        try:
            downloaded_path = await mega_downloader.download(
                mega_url=target_url,
                dest_dir=ctx.job_dir,
                progress_callback=dl_progress,
                cancel_check=ctx.is_cancelled,
            )
            ctx.downloaded_file = downloaded_path
            ctx.file_size = downloaded_path.stat().st_size

            if progress:
                await progress.update(
                    "DOWNLOADING",
                    f"Downloaded: {format_file_size(ctx.file_size)}",
                    force=True,
                )

            logger.info(
                f"job={ctx.job_id} downloaded: {downloaded_path.name} "
                f"({format_file_size(ctx.file_size)})"
            )

        except Exception as e:
            raise PipelineError(
                "Download",
                f"Mega download failed: {str(e)[:200]}",
                str(e),
            )
    else:
        # HTTP direct / streaming download
        if progress:
            server_label = f" from {ctx.selected_server}" if ctx.selected_server else ""
            await progress.update("DOWNLOADING", f"Starting HTTP download{server_label}...")

        from app.media.http_downloader import HttpDownloader
        downloader = http_downloader or HttpDownloader()

        try:
            downloaded_path = await downloader.download(
                url=target_url,
                dest_dir=ctx.job_dir,
                progress_callback=dl_progress,
                cancel_check=ctx.is_cancelled,
            )
            ctx.downloaded_file = downloaded_path
            ctx.file_size = downloaded_path.stat().st_size

            if progress:
                await progress.update(
                    "DOWNLOADING",
                    f"Downloaded: {format_file_size(ctx.file_size)}",
                    force=True,
                )

            logger.info(
                f"job={ctx.job_id} downloaded: {downloaded_path.name} "
                f"({format_file_size(ctx.file_size)})"
            )

        except Exception as e:
            raise PipelineError(
                "Download",
                f"HTTP download failed: {str(e)[:200]}",
                str(e),
            )


# ── Stage: Upload ──────────────────────────────────────────────────────────

async def stage_upload(
    ctx: JobContext,
    config: "Config",
    bot: "Bot",
    progress: Optional["ProgressReporter"] = None,
    telethon_uploader: Optional["TelethonUploader"] = None,
) -> None:
    """Upload the downloaded movie to the Telegram channel.

    Prefers Telethon MTProto for files up to 2GB.
    Falls back to standard Bot API for smaller files.
    """
    ctx.status = JobStatus.UPLOADING

    if progress:
        await progress.update("UPLOADING", "Preparing upload...")

    if not ctx.downloaded_file or not ctx.downloaded_file.exists():
        raise PipelineError("Upload", "No downloaded file to upload")

    output_file = ctx.downloaded_file
    file_size = output_file.stat().st_size

    # Build caption
    caption = (
        f"🎬 <b>{html.escape(ctx.title or 'Movie')}</b>\n"
    )
    if ctx.description:
        # Truncate description to keep caption reasonable
        desc = ctx.description[:300]
        if len(ctx.description) > 300:
            desc += "..."
        caption += f"\n{html.escape(desc)}\n"
    caption += (
        f"\n📁 Size: {format_file_size(file_size)}\n"
        f"🆔 Job: #{ctx.job_id[:6]}"
    )

    # Determine upload target
    target: str | int = config.telegram_channel_id or ctx.chat_id
    if isinstance(target, str):
        try:
            target = int(target)
        except ValueError:
            pass

    # 1. Send poster and review to destination channel first
    if telethon_uploader and config.use_telethon:
        try:
            if await telethon_uploader.is_authorized():
                client = await telethon_uploader.get_client()
                local_poster = await telethon_uploader._prepare_poster(ctx.poster_url, ctx.job_id)
                await telethon_uploader._send_poster_and_review(
                    client=client,
                    target_entity=target,
                    title=ctx.title,
                    description=ctx.description,
                    local_poster_path=local_poster,
                    total_items=1,
                    year=ctx.year,
                    category=ctx.category,
                    duration=ctx.duration,
                    quality=ctx.quality,
                )
                if local_poster and local_poster.exists():
                    try:
                        local_poster.unlink()
                    except Exception:
                        pass
        except Exception as pre_err:
            logger.warning(f"Failed sending poster/review before upload: {pre_err}")
    else:
        # Bot API fallback for poster & review
        if ctx.poster_url:
            try:
                await bot.send_photo(
                    chat_id=target,
                    photo=ctx.poster_url,
                    caption=f"🎬 <b>{html.escape(ctx.title or 'Movie')}</b>",
                    parse_mode="HTML",
                )
            except Exception as pe:
                logger.warning(f"Could not send poster via bot: {pe}")
        if ctx.description:
            try:
                meta_lines = []
                if ctx.year:
                    meta_lines.append(f"📅 <b>Year:</b> {html.escape(ctx.year)}")
                if ctx.duration:
                    meta_lines.append(f"⏱️ <b>Runtime:</b> {html.escape(ctx.duration)}")
                if ctx.category:
                    meta_lines.append(f"🏷️ <b>Genre:</b> {html.escape(ctx.category)}")
                if ctx.quality:
                    meta_lines.append(f"📊 <b>Quality:</b> {html.escape(ctx.quality)}")
                if ctx.source:
                    s_lower = ctx.source.lower()
                    if "homie" in s_lower:
                        meta_lines.append('🌐 <b>Credit:</b> <a href="https://www.homietv.com">HomieTV</a>')
                    elif "mmsub" in s_lower:
                        meta_lines.append('🌐 <b>Credit:</b> <a href="https://mmsubchannel.com">MMSubChannel</a>')
                    else:
                        meta_lines.append(f"🌐 <b>Credit:</b> {html.escape(ctx.source)}")
                meta_block = ("\n" + "\n".join(meta_lines) + "\n") if meta_lines else ""
                review_text = (
                    f"🎬 <b>{html.escape(ctx.title or 'Movie')}</b>\n"
                    f"{meta_block}\n"
                    f"📝 <b>Review / ဇာတ်လမ်းအညွှန်း:</b>\n"
                    f"{html.escape(ctx.description[:3600])}\n\n"
                    f"📁 <i>Movie file below:</i> 👇"
                )
                await bot.send_message(chat_id=target, text=review_text, parse_mode="HTML")
            except Exception as de:
                logger.warning(f"Could not send review via bot: {de}")

    # 2. Try Telethon MTProto upload (supports 2GB)
    if telethon_uploader and config.use_telethon:
        try:
            if await telethon_uploader.is_authorized():
                if progress:
                    await progress.update("UPLOADING", "Uploading via Telethon MTProto...")

                def upload_progress(uploaded: int, total: int):
                    pct = int((uploaded / total) * 100) if total > 0 else 0
                    if pct % 10 == 0:
                        logger.debug(f"job={ctx.job_id} upload {pct}%")

                msg = await telethon_uploader.upload_file(
                    file_path=output_file,
                    entity=target,
                    caption=caption,
                    progress_callback=upload_progress,
                )

                logger.info(f"Telethon upload succeeded: msg_id={msg.id}")

                # Also notify the user's private chat if we uploaded to channel
                if ctx.chat_id and str(ctx.chat_id) != str(target):
                    try:
                        await bot.send_message(
                            chat_id=ctx.chat_id,
                            text=(
                                f"✅ Movie uploaded to channel!\n\n"
                                f"🎬 {ctx.title}\n"
                                f"📁 Size: {format_file_size(file_size)}\n\n"
                                f"Job: #{ctx.job_id[:6]}"
                            ),
                        )
                    except Exception as notify_err:
                        logger.warning(f"Could not notify user: {notify_err}")

                if progress:
                    await progress.update("UPLOADING", "Upload complete!", force=True)
                return
            else:
                logger.info(
                    "Telethon configured but not logged in. "
                    "Run 'python login_telethon.py'. Falling back to Bot API."
                )
        except Exception as e:
            logger.warning(f"Telethon upload failed: {e}. Falling back to Bot API.")

    # 2. Standard Bot API upload fallback (50MB limit without local server)
    standard_max_bytes = 50 * 1024 * 1024

    if file_size > standard_max_bytes:
        raise PipelineError(
            "Upload",
            f"File too large ({format_file_size(file_size)}) for standard Bot API. "
            "Run 'python login_telethon.py' to enable 2GB uploads via Telethon.",
            f"File size: {file_size} bytes, limit: {standard_max_bytes} bytes",
        )

    try:
        if progress:
            await progress.update("UPLOADING", "Uploading via Bot API...")

        with open(output_file, "rb") as f:
            kwargs = {
                "chat_id": target,
                "document": f,
                "caption": caption,
                "parse_mode": "HTML",
                "read_timeout": 300,
                "write_timeout": 300,
                "connect_timeout": 60,
            }
            await bot.send_document(**kwargs)

        # Notify user if uploaded to channel
        if ctx.chat_id and str(ctx.chat_id) != str(target):
            try:
                await bot.send_message(
                    chat_id=ctx.chat_id,
                    text=(
                        f"✅ Movie uploaded to channel!\n\n"
                        f"🎬 {ctx.title}\n"
                        f"📁 Size: {format_file_size(file_size)}\n\n"
                        f"Job: #{ctx.job_id[:6]}"
                    ),
                )
            except Exception as notify_err:
                logger.warning(f"Could not notify user: {notify_err}")

    except Exception as e:
        raise PipelineError(
            "Upload",
            f"Upload failed: {str(e)[:200]}",
            str(e),
        )

    if progress:
        await progress.update("UPLOADING", "Upload complete!", force=True)


# ── Stage: Telegram Direct Transfer ───────────────────────────────────────

async def stage_telegram_transfer(
    ctx: JobContext,
    config: "Config",
    bot: "Bot",
    telethon_uploader: "TelethonUploader",
    progress: Optional["ProgressReporter"] = None,
) -> None:
    """Transfer movie directly from Telegram public channel or delivery bot to channel via Telethon.

    Eliminates local downloading/uploading and Mega/HTTP bandwidth usage.
    """
    ctx.status = JobStatus.UPLOADING

    if progress:
        action_desc = (
            "Requesting movie from Telegram delivery bot..."
            if "start=" in (ctx.telegram_url or "") or "bot" in (ctx.telegram_url or "").lower()
            else "Cloning movie directly from Telegram channel..."
        )
        await progress.update("UPLOADING", action_desc)

    target: str | int = config.telegram_channel_id or ctx.chat_id

    def on_progress(stage_label: str, text: str):
        if progress:
            asyncio.create_task(progress.update("UPLOADING", text))

    sent_items = await telethon_uploader.fetch_and_forward_from_bot(
        telegram_url=ctx.telegram_url,
        target_entity=target,
        title=ctx.title,
        description=ctx.description,
        poster_url=ctx.poster_url,
        job_id=ctx.job_id,
        progress_callback=on_progress,
        year=ctx.year,
        category=ctx.category,
        duration=ctx.duration,
        quality=ctx.quality,
        source=getattr(ctx, "source", ""),
    )

    count = len(sent_items) if isinstance(sent_items, list) else 1

    # If file was delivered to channel, notify user's private chat if different
    if ctx.chat_id and str(ctx.chat_id) != str(target):
        try:
            if count > 1:
                notify_text = (
                    f"✅ All {count} episodes delivered to channel!\n\n"
                    f"🎬 {ctx.title}\n\n"
                    f"Job: #{ctx.job_id[:6]}"
                )
            else:
                notify_text = (
                    f"✅ Movie delivered to channel!\n\n"
                    f"🎬 {ctx.title}\n\n"
                    f"Job: #{ctx.job_id[:6]}"
                )
            await bot.send_message(
                chat_id=ctx.chat_id,
                text=notify_text,
            )
        except Exception as notify_err:
            logger.warning(f"Could not notify user: {notify_err}")

    if progress:
        done_text = f"Delivered {count} episodes!" if count > 1 else "Transfer complete!"
        await progress.update("UPLOADING", done_text, force=True)
