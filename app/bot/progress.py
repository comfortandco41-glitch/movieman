"""Progress reporting for Telegram.

Edits a single Telegram message to show pipeline progress.
Throttles updates to avoid hitting Telegram API rate limits.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from telegram import Bot, InlineKeyboardMarkup, Message
from telegram.error import TelegramError

from app.bot.keyboards import cancel_job_keyboard, job_completed_keyboard

logger = logging.getLogger(__name__)

# Minimum seconds between Telegram message edits
_THROTTLE_SECONDS = 3.0

# Stage emoji mapping
STAGE_EMOJIS = {
    "QUEUED": "⏳",
    "SCRAPING": "🔍",
    "RESOLVING": "🔗",
    "DOWNLOADING": "⬇️",
    "UPLOADING": "⬆️",
    "COMPLETED": "✅",
    "FAILED": "❌",
    "CANCELLED": "🚫",
}


class ProgressReporter:
    """Reports pipeline progress by editing a single Telegram message.

    Throttles updates to avoid excessive API calls.
    """

    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        job_id: str,
        title: str,
    ) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._job_id = job_id
        self._title = title
        self._message: Optional[Message] = None
        self._last_update_time: float = 0.0
        self._lock = asyncio.Lock()

    async def send_initial(self) -> None:
        """Send the initial progress message."""
        text = self._format_message("QUEUED", "Queued...", 0, 0)
        keyboard = cancel_job_keyboard(self._job_id)
        try:
            self._message = await self._bot.send_message(
                chat_id=self._chat_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        except TelegramError as e:
            logger.error(f"Failed to send initial progress message: {e}")

    async def update(
        self,
        stage: str,
        detail: str = "",
        current: int = 0,
        total: int = 0,
        force: bool = False,
    ) -> None:
        """Update the progress message.

        Throttled to avoid excessive Telegram API calls.

        Args:
            stage: Current pipeline stage (e.g., "DOWNLOADING").
            detail: Additional detail text.
            current: Current progress count.
            total: Total count for percentage calculation.
            force: Force update regardless of throttle.
        """
        now = time.monotonic()
        if not force and (now - self._last_update_time) < _THROTTLE_SECONDS:
            return

        async with self._lock:
            if not self._message:
                return

            text = self._format_message(stage, detail, current, total)
            keyboard = cancel_job_keyboard(self._job_id)

            try:
                await self._message.edit_text(
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )
                self._last_update_time = time.monotonic()
            except TelegramError as e:
                if "not modified" not in str(e).lower():
                    logger.warning(f"Failed to update progress: {e}")

    async def complete(self, success: bool = True, error_msg: str = "") -> None:
        """Mark the job as completed or failed.

        Args:
            success: Whether the job completed successfully.
            error_msg: Error message if failed.
        """
        if not self._message:
            return

        if success:
            stage = "COMPLETED"
            detail = (
                "Movie uploaded successfully!\n\n"
                f"💡 <b>To publish to Movie Store:</b>\n"
                f"Copy video link from channel & send:\n"
                f"<code>/upload {self._job_id[:6]} &lt;telegram_video_url&gt;</code>"
            )
            text = self._format_message(stage, detail, 100, 100)
            keyboard = job_completed_keyboard()
        else:
            stage = "FAILED"
            text = self._format_message(stage, error_msg or "Processing failed.", 0, 0)
            keyboard = job_completed_keyboard()

        try:
            await self._message.edit_text(
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        except TelegramError as e:
            logger.warning(f"Failed to send completion update: {e}")

    async def cancelled(self) -> None:
        """Mark the job as cancelled."""
        if not self._message:
            return

        text = self._format_message("CANCELLED", "Job was cancelled.", 0, 0)
        try:
            await self._message.edit_text(
                text=text,
                reply_markup=job_completed_keyboard(),
                parse_mode="HTML",
            )
        except TelegramError as e:
            logger.warning(f"Failed to send cancellation update: {e}")

    def _format_message(
        self, stage: str, detail: str, current: int, total: int
    ) -> str:
        """Format the progress message text.

        Args:
            stage: Pipeline stage name.
            detail: Detail text.
            current: Current progress.
            total: Total for percentage.

        Returns:
            Formatted HTML message string.
        """
        emoji = STAGE_EMOJIS.get(stage, "⏳")

        # Calculate percentage
        if total > 0:
            pct = min(int((current / total) * 100), 100)
        elif stage == "COMPLETED":
            pct = 100
        else:
            pct = 0

        # Progress bar
        filled = pct // 5
        bar = "█" * filled + "░" * (20 - filled)

        lines = [
            f"🎬 <b>Movie Man</b>",
            f"📽 {self._title}",
            "",
            f"Stage: {emoji} {stage.replace('_', ' ').title()}",
            f"[{bar}] {pct}%",
        ]

        if detail:
            lines.append(f"📋 {detail}")

        if current > 0 and total > 0:
            lines.append(f"Progress: {current} / {total}")

        lines.append("")
        lines.append(f"Job: <code>#{self._job_id[:6]}</code>")

        return "\n".join(lines)
