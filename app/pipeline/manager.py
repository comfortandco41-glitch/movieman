"""Pipeline manager — orchestrates job creation, execution, and cleanup.

Manages job lifecycle, concurrency control via semaphore,
and background task execution.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from typing import Optional, TYPE_CHECKING

from app.pipeline.job import JobContext, JobStatus, PipelineError
from app.pipeline.stages import (
    stage_scrape,
    stage_resolve,
    stage_download,
    stage_upload,
    stage_telegram_transfer,
)
from app.media.cleanup import cleanup_job

if TYPE_CHECKING:
    from telegram import Bot
    from app.config import Config
    from app.scraper.client import MMSubChannelScraper
    from app.resolver.link_resolver import LinkResolver
    from app.mega.downloader import MegaDownloader
    from app.media.telethon_uploader import TelethonUploader
    from app.bot.progress import ProgressReporter

logger = logging.getLogger(__name__)


class PipelineManager:
    """Orchestrates the movie download pipeline.

    Manages job creation, execution, concurrency control, and cleanup.
    """

    def __init__(
        self,
        config: "Config",
        scraper: Any,
        resolver: "LinkResolver",
        mega_downloader: "MegaDownloader",
        telethon_uploader: Optional["TelethonUploader"] = None,
        http_downloader: Optional[Any] = None,
    ) -> None:
        self._config = config
        self._scraper = scraper
        self._resolver = resolver
        self._mega_downloader = mega_downloader
        self._telethon_uploader = telethon_uploader
        from app.media.http_downloader import HttpDownloader
        self._http_downloader = http_downloader or HttpDownloader()
        self._semaphore = asyncio.Semaphore(config.max_concurrent_jobs)
        self._active_jobs: dict[str, JobContext] = {}  # job_id -> context
        self._user_jobs: dict[int, str] = {}  # user_id -> job_id
        self._completed_jobs: dict[str, JobContext] = {}  # Keep last 50 completed jobs
        self._max_completed = 50

    async def create_job(
        self,
        user_id: int,
        movie_page_url: str,
        title: str,
        bot: "Bot",
        chat_id: int,
        mega_url: str = "",
        telegram_url: str = "",
        download_url: str = "",
        selected_server: str = "",
        poster_url: str = "",
        description: str = "",
        year: str = "",
        category: str = "",
        duration: str = "",
        quality: str = "",
    ) -> str:
        """Create and start a new download job.

        Args:
            user_id: Telegram user ID.
            movie_page_url: URL of the movie detail page.
            title: Movie title for display.
            bot: Telegram bot instance.
            chat_id: Telegram chat ID for progress.
            mega_url: Direct Mega URL (skips scrape + resolve if provided).
            telegram_url: Direct Telegram delivery bot or channel URL.
            download_url: Direct HTTP/stream download URL.
            selected_server: Name of the download server.
            poster_url: Movie poster image URL.
            description: Movie review / description text.
            year: Release year.
            category: Movie genres/categories.
            duration: Runtime duration.
            quality: Video quality label.

        Returns:
            Job ID string.
        """
        ctx = JobContext(
            user_id=user_id,
            movie_page_url=movie_page_url,
            title=title,
            chat_id=chat_id,
            mega_url=mega_url,
            telegram_url=telegram_url,
            download_url=download_url,
            selected_server=selected_server,
            poster_url=poster_url,
            description=description,
            year=year,
            category=category,
            duration=duration,
            quality=quality,
        )

        # Setup workspace
        ctx.setup_workspace(self._config.jobs_dir)

        # Track job
        self._active_jobs[ctx.job_id] = ctx
        self._user_jobs[user_id] = ctx.job_id

        # Start processing in background
        asyncio.create_task(
            self._run_pipeline(ctx, bot),
            name=f"pipeline-{ctx.job_id}",
        )

        return ctx.job_id

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a job by ID.

        Args:
            job_id: Job identifier.

        Returns:
            True if job was found and cancelled.
        """
        ctx = self._active_jobs.get(job_id)
        if not ctx:
            return False

        ctx.cancel()
        logger.info(f"job={job_id} cancellation requested")
        return True

    async def cancel_user_job(self, user_id: int) -> bool:
        """Cancel the active job for a user.

        Args:
            user_id: Telegram user ID.

        Returns:
            True if a job was found and cancelled.
        """
        job_id = self._user_jobs.get(user_id)
        if job_id:
            return await self.cancel_job(job_id)
        return False

    def has_active_job(self, user_id: int) -> bool:
        """Check if a user has an active job."""
        job_id = self._user_jobs.get(user_id)
        if job_id and job_id in self._active_jobs:
            ctx = self._active_jobs[job_id]
            return ctx.status not in (
                JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED
            )
        return False

    def is_user_job(self, user_id: int, job_id: str) -> bool:
        """Check if a job belongs to a user."""
        return self._user_jobs.get(user_id) == job_id

    def get_job_by_id(self, job_id_prefix: str) -> Optional[JobContext]:
        """Look up a job by ID or ID prefix (first 6 chars).

        Searches active jobs first, then completed jobs cache.

        Args:
            job_id_prefix: Full job ID or first 6+ characters.

        Returns:
            JobContext if found, None otherwise.
        """
        # Exact match first
        if job_id_prefix in self._active_jobs:
            return self._active_jobs[job_id_prefix]
        if job_id_prefix in self._completed_jobs:
            return self._completed_jobs[job_id_prefix]

        # Prefix match
        prefix = job_id_prefix.lower().strip()
        for jid, ctx in self._active_jobs.items():
            if jid.startswith(prefix):
                return ctx
        for jid, ctx in self._completed_jobs.items():
            if jid.startswith(prefix):
                return ctx

        return None


    async def _run_pipeline(self, ctx: JobContext, bot: "Bot") -> None:
        """Execute the full pipeline for a job.

        Runs each stage sequentially with concurrency control.
        Handles errors and cleanup.
        """
        from app.bot.progress import ProgressReporter

        progress = ProgressReporter(
            bot=bot,
            chat_id=ctx.chat_id,
            job_id=ctx.job_id,
            title=ctx.title,
        )

        try:
            async with self._semaphore:
                await progress.send_initial()

                # Step 1: Ensure we have website metadata (poster, review, details)
                needs_scrape = False
                if ctx.movie_page_url and (not ctx.description or not ctx.poster_url):
                    needs_scrape = True
                elif not ctx.mega_url and not ctx.telegram_url and not ctx.download_url:
                    needs_scrape = True

                if needs_scrape and ctx.movie_page_url:
                    if ctx.cancelled:
                        ctx.status = JobStatus.CANCELLED
                        await progress.cancelled()
                        return
                    logger.info(f"job={ctx.job_id} stage=scrape started for {ctx.movie_page_url}")
                    await stage_scrape(ctx, self._scraper, progress)
                    logger.info(f"job={ctx.job_id} stage=scrape completed (has_poster={bool(ctx.poster_url)}, desc_len={len(ctx.description)})")

                # Auto-enrich missing poster/review via scraper search if title is known
                if (not ctx.poster_url or not ctx.description) and ctx.title:
                    import re
                    clean_q = re.split(r"(?:19|20)\d{2}|720p|1080p|480p|web-dl|bluray|\(", ctx.title, flags=re.IGNORECASE)[0]
                    clean_q = clean_q.replace(".", " ").strip()
                    if clean_q and clean_q.lower() not in ("telegram movie delivery", "direct mega download", "movie from url"):
                        try:
                            results = await self._scraper.search(clean_q)
                            if results:
                                matched = results[0]
                                detail = await self._scraper.get_movie_detail(matched.detail_url)
                                if detail:
                                    ctx.title = detail.title or ctx.title
                                    ctx.poster_url = ctx.poster_url or detail.poster_url
                                    ctx.description = ctx.description or detail.description
                                    ctx.year = ctx.year or getattr(detail, "year", "")
                                    ctx.category = ctx.category or getattr(detail, "category", "")
                                    ctx.duration = ctx.duration or getattr(detail, "duration", "")
                                    ctx.quality = ctx.quality or getattr(detail, "quality", "")
                                    logger.info(f"Enriched job={ctx.job_id} metadata via search for '{clean_q}': poster={bool(ctx.poster_url)}, desc_len={len(ctx.description)}")
                        except Exception as enrich_err:
                            logger.debug(f"Could not auto-enrich metadata for '{clean_q}': {enrich_err}")

                # Step 2: If we have a telegram_url and telethon is enabled, use direct Telegram cloud transfer
                if ctx.telegram_url and self._telethon_uploader and self._config.use_telethon:
                    if ctx.cancelled:
                        ctx.status = JobStatus.CANCELLED
                        await progress.cancelled()
                        return
                    try:
                        if await self._telethon_uploader.is_authorized():
                            logger.info(
                                f"job={ctx.job_id} stage=telegram_transfer started: {ctx.telegram_url}"
                            )
                            await stage_telegram_transfer(
                                ctx=ctx,
                                config=self._config,
                                bot=bot,
                                telethon_uploader=self._telethon_uploader,
                                progress=progress,
                            )
                            logger.info(f"job={ctx.job_id} stage=telegram_transfer completed")
                            ctx.status = JobStatus.COMPLETED
                            await progress.complete(success=True)
                            logger.info(
                                f"job={ctx.job_id} pipeline completed successfully via Telegram delivery"
                            )
                            return
                        else:
                            logger.warning(
                                "Telethon not authorized, falling back to Mega resolver pipeline"
                            )
                            if not ctx.download_url and not ctx.mega_url and not ctx.candidate_urls:
                                raise PipelineError(
                                    "Telegram Transfer",
                                    "Telethon session is not authorized on Render. Please add the TELETHON_SESSION string in Render Environment Variables."
                                )
                    except PipelineError:
                        raise
                    except Exception as tg_err:
                        logger.warning(
                            f"job={ctx.job_id} telegram_transfer failed: {tg_err}. Falling back to standard pipeline."
                        )
                        if not ctx.download_url and not ctx.mega_url and not ctx.candidate_urls:
                            raise PipelineError("Telegram Transfer", f"Telegram transfer failed: {tg_err}")

                # Step 3: Fallback standard pipeline (Resolve -> Download -> Upload)
                stages = []
                from app.resolver.link_resolver import is_mega_url, is_filehost_page

                # If download_url is a known file-host page (e.g. UsersDrive HTML page),
                # move it into the candidate list so the resolver can click the download
                # button and extract the real direct URL via Playwright.
                if ctx.download_url and is_filehost_page(ctx.download_url):
                    logger.info(
                        f"job={ctx.job_id} download_url is a filehost page, routing through resolver: {ctx.download_url}"
                    )
                    ctx.candidate_urls = ctx.candidate_urls or []
                    if ctx.download_url not in ctx.candidate_urls:
                        ctx.candidate_urls.append(ctx.download_url)
                    ctx.mega_url = ctx.mega_url or ctx.download_url
                    ctx.download_url = ""

                # Only resolve if we don't already have a direct download_url and mega_url is not direct
                if not ctx.download_url and (not ctx.mega_url or not is_mega_url(ctx.mega_url)):
                    if self._resolver is None:
                        from app.resolver.link_resolver import LinkResolver
                        self._resolver = LinkResolver(scraper=self._scraper)
                    res_inst = self._resolver
                    stages.append(
                        ("resolve", lambda: stage_resolve(ctx, res_inst, progress))
                    )


                stages.append(
                    ("download", lambda: stage_download(
                        ctx, self._mega_downloader, progress, self._http_downloader
                    ))
                )
                stages.append(
                    ("upload", lambda: stage_upload(
                        ctx, self._config, bot, progress, self._telethon_uploader
                    ))
                )

                for stage_name, stage_fn in stages:
                    # Check cancellation
                    if ctx.cancelled:
                        ctx.status = JobStatus.CANCELLED
                        await progress.cancelled()
                        logger.info(f"job={ctx.job_id} cancelled before {stage_name}")
                        return

                    logger.info(f"job={ctx.job_id} stage={stage_name} started")

                    try:
                        await stage_fn()
                    except asyncio.CancelledError:
                        ctx.status = JobStatus.CANCELLED
                        await progress.cancelled()
                        logger.info(f"job={ctx.job_id} cancelled during {stage_name}")
                        return

                    logger.info(f"job={ctx.job_id} stage={stage_name} completed")

                # All stages complete
                ctx.status = JobStatus.COMPLETED
                await progress.complete(success=True)
                logger.info(f"job={ctx.job_id} pipeline completed successfully")

        except PipelineError as e:
            ctx.status = JobStatus.FAILED
            ctx.error = e.message
            logger.error(
                f"job={ctx.job_id} stage={e.stage} failed: {e.message}\n"
                f"Detail: {e.detail}"
            )
            await progress.complete(
                success=False,
                error_msg=f"Stage: {e.stage}\n{e.message}",
            )

        except Exception as e:
            ctx.status = JobStatus.FAILED
            ctx.error = str(e)
            logger.error(
                f"job={ctx.job_id} unexpected error:\n{traceback.format_exc()}"
            )
            await progress.complete(
                success=False,
                error_msg="An unexpected error occurred. Check server logs.",
            )

        finally:
            # Archive to completed jobs cache before cleanup
            self._completed_jobs[ctx.job_id] = ctx
            # Trim cache if too large
            if len(self._completed_jobs) > self._max_completed:
                oldest_key = next(iter(self._completed_jobs))
                del self._completed_jobs[oldest_key]

            # Permanently persist to SQLite database
            try:
                from app.store.database import save_completed_job
                await save_completed_job(
                    job_id=ctx.job_id,
                    title=ctx.title,
                    poster_url=ctx.poster_url or "",
                    description=ctx.description or "",
                    year=ctx.year or "",
                    quality=ctx.quality or "",
                    category=ctx.category or "",
                    duration=ctx.duration or "",
                    source=getattr(ctx, "selected_server", ""),
                    movie_page_url=ctx.movie_page_url or "",
                )
            except Exception as e:
                logger.warning(f"job={ctx.job_id} failed to persist to database: {e}")

            # Cleanup
            try:
                if ctx.job_dir:
                    cleanup_job(ctx.job_dir)
            except Exception as e:
                logger.warning(f"job={ctx.job_id} cleanup error: {e}")

            # Remove from active tracking
            self._active_jobs.pop(ctx.job_id, None)
            if self._user_jobs.get(ctx.user_id) == ctx.job_id:
                self._user_jobs.pop(ctx.user_id, None)
