"""Inline keyboard builders for the Telegram bot."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.scraper.models import MovieListing


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Build the main menu keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Latest Movies", callback_data="latest")],
        [InlineKeyboardButton("🔗 Submit URL", callback_data="submit_url")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="help")],
    ])


def movie_list_keyboard(movies: list[MovieListing]) -> InlineKeyboardMarkup:
    """Build a keyboard listing movies for selection.

    Args:
        movies: List of MovieListing objects.

    Returns:
        InlineKeyboardMarkup with one button per movie.
    """
    buttons = []
    for i, movie in enumerate(movies):
        # Truncate long titles for button display
        label = movie.title[:50]
        if len(movie.title) > 50:
            label += "..."

        if movie.quality:
            label = f"[{movie.quality}] {label}"

        buttons.append([
            InlineKeyboardButton(
                text=f"🎬 {label}",
                callback_data=f"movie:{i}",
            )
        ])

    # Add back button
    buttons.append([
        InlineKeyboardButton("🔙 Back to Menu", callback_data="menu"),
    ])

    return InlineKeyboardMarkup(buttons)


def movie_detail_keyboard(movie_index: int) -> InlineKeyboardMarkup:
    """Build keyboard for a movie detail view with download action.

    Args:
        movie_index: Index of the movie in the cached list.

    Returns:
        InlineKeyboardMarkup with download and back buttons.
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⬇️ Download & Upload",
                callback_data=f"download:{movie_index}",
            ),
        ],
        [
            InlineKeyboardButton("🔙 Back to List", callback_data="latest"),
            InlineKeyboardButton("🏠 Menu", callback_data="menu"),
        ],
    ])


def download_options_keyboard(movie_index: int, links: list) -> InlineKeyboardMarkup:
    """Build keyboard allowing user to select a specific server and quality to download.

    Args:
        movie_index: Index of the movie in cached list.
        links: List of DownloadLink objects.

    Returns:
        InlineKeyboardMarkup with server buttons.
    """
    buttons = []
    for i, link in enumerate(links):
        s_name = link.server_name or "Server"
        label = link.label or s_name
        if link.file_size and link.file_size not in label:
            label = f"{label} ({link.file_size})"

        icon = "✈️" if "telegram" in s_name.lower() or "t.me" in link.url.lower() else "⚡"
        btn_text = f"{icon} {label}"[:45]

        buttons.append([
            InlineKeyboardButton(
                text=btn_text,
                callback_data=f"dl_opt:{movie_index}:{i}",
            )
        ])

    buttons.append([
        InlineKeyboardButton("🔙 Back to List", callback_data="latest"),
        InlineKeyboardButton("🏠 Menu", callback_data="menu"),
    ])
    return InlineKeyboardMarkup(buttons)


def source_choice_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard to choose movie source."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌐 HomieTV (Fast)", callback_data="src:homietv"),
            InlineKeyboardButton("🍿 MMSubChannel", callback_data="src:mmsubchannel"),
        ],
        [InlineKeyboardButton("🔀 All Sources", callback_data="src:all")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu")],
    ])


def cancel_job_keyboard(job_id: str) -> InlineKeyboardMarkup:
    """Build keyboard with a cancel button for an active job.

    Args:
        job_id: The job ID to cancel.

    Returns:
        InlineKeyboardMarkup with cancel button.
    """
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel Job", callback_data=f"cancel:{job_id}")],
    ])


def job_completed_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard shown after job completion."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu")],
    ])

