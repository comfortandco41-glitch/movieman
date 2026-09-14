"""Telethon MTProto uploader for Telegram files up to 2GB.

Uses parallel multipart upload for maximum throughput.
Adapted from 17plusone/movie-translator-bot.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import html
from pathlib import Path
from typing import Any, Callable, Optional, Union
from urllib.parse import parse_qs, urlparse

from telethon import TelegramClient, functions, types
from telethon.tl.types import (
    DocumentAttributeVideo,
    DocumentAttributeFilename,
    Message,
)

from app.utils.crypto_patch import apply_crypto_patch
from app.utils.filenames import format_file_size

logger = logging.getLogger(__name__)


class TelethonUploaderError(Exception):
    """Raised when Telethon upload fails."""
    pass


def is_bot_delivery_link(url: str) -> bool:
    """Check if URL points to a Telegram bot delivery link rather than a channel link."""
    clean_url = (url or "").strip().lower()
    if clean_url.startswith("@"):
        return clean_url.endswith("bot")
    parsed = urlparse(clean_url if "://" in clean_url else f"https://{clean_url}")
    qs = parse_qs(parsed.query)
    # If start parameter is present, it's definitely a bot deeplink
    if "start" in qs and qs["start"]:
        return True
    path_parts = parsed.path.strip("/").split("/")
    if not path_parts or not path_parts[0]:
        return False
    # If path has a numeric message ID (e.g. t.me/channel/123), it's a channel post
    if len(path_parts) >= 2 and path_parts[1].isdigit():
        return False
    username = path_parts[0]
    return username.endswith("bot")


def is_video_message(m: Any) -> bool:
    """Check if a Telethon message contains a valid video media document."""
    if not m or not getattr(m, "media", None):
        return False
    if getattr(m, "video", None) or getattr(m, "document", None):
        fsize = getattr(m.file, "size", 0) or 0
        fname = (getattr(m.file, "name", "") or "").lower()
        mime = (getattr(m.file, "mime_type", "") or "").lower()
        is_vid = mime.startswith("video/") or fname.endswith(
            (".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv")
        )
        return is_vid and fsize > 2 * 1024 * 1024
    return False


async def _fast_upload_file(
    client: TelegramClient,
    file_path: Path,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    max_workers: int = 6,
) -> Union[types.InputFile, types.InputFileBig]:
    """Upload a file using parallel multipart requests for speed.

    Args:
        client: Connected TelegramClient instance.
        file_path: Path to the file to upload.
        progress_callback: Called with (bytes_uploaded, total_bytes).
        max_workers: Number of parallel upload workers.

    Returns:
        Telethon InputFile or InputFileBig ready for send_file.
    """
    file_size = file_path.stat().st_size
    is_big = file_size > 10 * 1024 * 1024  # > 10MB uses big file upload

    part_size = 512 * 1024  # 512 KB parts
    total_parts = (file_size + part_size - 1) // part_size
    file_id = random.randrange(0, 2**63)

    uploaded_bytes = 0
    lock = asyncio.Lock()
    sem = asyncio.Semaphore(max_workers)

    async def upload_part(part_index: int, data: bytes):
        nonlocal uploaded_bytes
        async with sem:
            if is_big:
                req = functions.upload.SaveBigFilePartRequest(
                    file_id=file_id,
                    file_part=part_index,
                    file_total_parts=total_parts,
                    bytes=data,
                )
            else:
                req = functions.upload.SaveFilePartRequest(
                    file_id=file_id,
                    file_part=part_index,
                    bytes=data,
                )
            await client(req)
            async with lock:
                uploaded_bytes += len(data)
                if progress_callback:
                    try:
                        progress_callback(uploaded_bytes, file_size)
                    except Exception:
                        pass

    tasks = []
    with open(file_path, "rb") as f:
        part_index = 0
        while True:
            chunk = f.read(part_size)
            if not chunk:
                break
            tasks.append(upload_part(part_index, chunk))
            part_index += 1

    await asyncio.gather(*tasks)

    if is_big:
        return types.InputFileBig(
            id=file_id,
            parts=total_parts,
            name=file_path.name,
        )
    else:
        return types.InputFile(
            id=file_id,
            parts=total_parts,
            name=file_path.name,
            md5_checksum="",
        )


class TelethonUploader:
    """Manages Telegram MTProto client for 2GB file uploads."""

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_name: str = "telethon.session",
    ) -> None:
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_name = session_name
        self._client: Optional[TelegramClient] = None
        self._lock = asyncio.Lock()
        apply_crypto_patch()

    async def get_client(self) -> TelegramClient:
        """Get or initialize the Telethon client."""
        async with self._lock:
            if self._client is None:
                self._client = TelegramClient(
                    self.session_name,
                    self.api_id,
                    self.api_hash,
                )
            if not self._client.is_connected():
                await self._client.connect()
            return self._client

    async def is_authorized(self) -> bool:
        """Check if the client has an active authorized session."""
        try:
            client = await self.get_client()
            return await client.is_user_authorized()
        except Exception as e:
            logger.warning(f"Failed to check Telethon authorization: {e}")
            return False

    async def upload_file(
        self,
        file_path: Path,
        entity: str | int,
        caption: str = "",
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Message:
        """Upload a file up to 2GB to a chat or channel.

        Args:
            file_path: Path to the file to upload.
            entity: Username, channel string (e.g. '@channel'), or chat ID.
            caption: File caption text.
            progress_callback: Callback receiving (bytes_uploaded, total_bytes).

        Returns:
            The sent message object.

        Raises:
            TelethonUploaderError: If upload fails or client is not authorized.
        """
        if not file_path.exists():
            raise TelethonUploaderError(f"File not found: {file_path}")

        client = await self.get_client()
        if not await client.is_user_authorized():
            raise TelethonUploaderError(
                "Telethon session is not authorized. "
                "Run 'python login_telethon.py' to log in."
            )

        if isinstance(entity, str):
            try:
                entity = int(entity)
            except ValueError:
                pass

        file_size = file_path.stat().st_size
        logger.info(
            f"Uploading {file_path.name} ({file_size:,} bytes) "
            f"to {entity} via Telethon MTProto..."
        )

        # Determine if this is a video based on extension
        ext = file_path.suffix.lower()
        is_video = ext in {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv"}

        attributes = [
            DocumentAttributeFilename(file_name=file_path.name),
        ]
        if is_video:
            attributes.append(
                DocumentAttributeVideo(
                    duration=0,
                    w=0,
                    h=0,
                    supports_streaming=True,
                )
            )

        try:
            # Parallel multipart upload
            input_file = await _fast_upload_file(
                client=client,
                file_path=file_path,
                progress_callback=progress_callback,
                max_workers=6,
            )

            # Send the uploaded file
            message = await client.send_file(
                entity=entity,
                file=input_file,
                caption=caption,
                attributes=attributes,
                supports_streaming=is_video,
                parse_mode="html",
            )
            logger.info(f"Telethon upload completed: msg_id={message.id}")
            return message

        except Exception as e:
            logger.error(f"Telethon upload failed: {e}", exc_info=True)
            raise TelethonUploaderError(f"Telethon upload failed: {e}") from e

    async def _prepare_poster(
        self,
        poster_url: str,
        job_id: str = "",
    ) -> Optional[Path]:
        """Download poster image from URL to workspace if available."""
        if not poster_url:
            return None
        try:
            import httpx
            tmp_poster = Path("workspace") / f"poster_{job_id or 'tmp'}.webp"
            tmp_poster.parent.mkdir(parents=True, exist_ok=True)
            async with httpx.AsyncClient(timeout=15.0) as http_client:
                resp = await http_client.get(poster_url)
                if resp.status_code == 200 and len(resp.content) > 1024:
                    tmp_poster.write_bytes(resp.content)
                    logger.info(f"Downloaded poster from URL ({len(resp.content)} bytes)")
                    return tmp_poster
        except Exception as poster_dl_err:
            logger.warning(f"Could not download poster from URL: {poster_dl_err}")
        return None

    async def _send_poster_and_review(
        self,
        client: TelegramClient,
        target_entity: str | int,
        title: str = "",
        description: str = "",
        local_poster_path: Optional[Path] = None,
        fallback_photo_media: Any = None,
        total_items: int = 1,
        year: str = "",
        category: str = "",
        duration: str = "",
        quality: str = "",
    ) -> bool:
        """Send poster and review text to destination channel."""
        poster_caption = f"🎬 <b>{html.escape(title or 'Movie')}</b>"
        poster_sent = False

        if local_poster_path and local_poster_path.exists():
            try:
                await client.send_file(
                    entity=target_entity,
                    file=local_poster_path,
                    caption=poster_caption,
                    parse_mode="html",
                )
                poster_sent = True
                logger.info(f"Delivered poster photo image to {target_entity}")
                await asyncio.sleep(1.5)
            except Exception as p_err:
                logger.warning(f"Could not send poster post from local file: {p_err}")

        if not poster_sent and fallback_photo_media:
            try:
                await client.send_file(
                    entity=target_entity,
                    file=fallback_photo_media,
                    caption=poster_caption,
                    parse_mode="html",
                )
                poster_sent = True
                logger.info(f"Delivered poster photo from media to {target_entity}")
                await asyncio.sleep(1.5)
            except Exception as p_err:
                logger.warning(f"Could not send poster post from fallback media: {p_err}")

        # Send review as a separate text message
        if description and description.strip():
            try:
                clean_desc = description.strip()
                max_desc_len = 3600
                if len(clean_desc) > max_desc_len:
                    clean_desc = clean_desc[:max_desc_len] + "..."

                meta_lines = []
                if year:
                    meta_lines.append(f"📅 <b>Year:</b> {html.escape(year)}")
                if duration:
                    meta_lines.append(f"⏱️ <b>Runtime:</b> {html.escape(duration)}")
                if category:
                    meta_lines.append(f"🏷️ <b>Genre:</b> {html.escape(category)}")
                if quality:
                    meta_lines.append(f"📊 <b>Quality:</b> {html.escape(quality)}")

                meta_block = ("\n" + "\n".join(meta_lines) + "\n") if meta_lines else ""
                header = f"{title} ({year})" if year and year not in (title or "") else (title or "Movie")

                review_text = (
                    f"🎬 <b>{html.escape(header)}</b>\n"
                    f"{meta_block}\n"
                    f"📝 <b>Review / ဇာတ်လမ်းအညွှန်း:</b>\n"
                    f"{html.escape(clean_desc)}"
                )
                if total_items > 1:
                    review_text += f"\n\n📺 <i>{total_items} Episodes below:</i> 👇"
                else:
                    review_text += "\n\n📁 <i>Movie file below:</i> 👇"

                await client.send_message(
                    entity=target_entity,
                    message=review_text,
                    parse_mode="html",
                )
                logger.info(
                    f"Delivered full review text ({len(clean_desc)} chars) to {target_entity}"
                )
                await asyncio.sleep(1.2)
            except Exception as text_err:
                logger.warning(f"Could not send review text message: {text_err}")

        return poster_sent

    async def clone_channel_media(
        self,
        telegram_url: str,
        target_entity: str | int,
        title: str = "",
        description: str = "",
        poster_url: str = "",
        job_id: str = "",
        progress_callback: Optional[Callable[[str, str], None]] = None,
        year: str = "",
        category: str = "",
        duration: str = "",
        quality: str = "",
    ) -> list[Message]:
        """Clone video media directly from a Telegram public channel to target channel.

        1. Resolves channel username and message ID (e.g. t.me/ch003agwpcd/898).
        2. If message ID is present, fetches message. If message has media, uses it.
           If message does not have media (e.g. review post), checks neighbors or sublinks.
        3. If message ID is not present (e.g. t.me/new2025channel), searches channel by title.
        4. Sends poster and review text to target channel.
        5. Sends video media directly to target channel with streaming support.
        6. Returns list of sent messages.
        """
        clean_url = telegram_url.strip()
        if not clean_url.startswith(("http://", "https://", "tg://")):
            clean_url = "https://" + clean_url

        parsed = urlparse(clean_url)
        path_parts = parsed.path.strip("/").split("/")
        if not path_parts or not path_parts[0]:
            raise TelethonUploaderError(f"Invalid Telegram URL: {telegram_url}")

        channel_ref = path_parts[0]
        msg_id: Optional[int] = None
        if len(path_parts) >= 2 and path_parts[1].isdigit():
            msg_id = int(path_parts[1])

        client = await self.get_client()
        if not await client.is_user_authorized():
            raise TelethonUploaderError(
                "Telethon session is not authorized. Run 'python login_telethon.py' to log in."
            )

        if isinstance(target_entity, str):
            try:
                target_entity = int(target_entity)
            except ValueError:
                pass

        logger.info(
            f"Cloning channel media from @{channel_ref} (msg_id={msg_id}) to {target_entity} for job={job_id}"
        )

        video_msgs: list[Message] = []
        lead_msg: Optional[Message] = None

        if msg_id is not None:
            if progress_callback:
                progress_callback("RESOLVING", f"Locating movie media in @{channel_ref}...")
            try:
                lead_msg = await client.get_messages(channel_ref, ids=msg_id)
            except Exception as fetch_err:
                logger.warning(f"Failed to fetch msg {msg_id} from {channel_ref}: {fetch_err}")

            if lead_msg and is_video_message(lead_msg):
                video_msgs.append(lead_msg)
            else:
                # Check neighbor messages around msg_id
                for offset in [1, -1, 2, -2]:
                    try:
                        adj = await client.get_messages(channel_ref, ids=msg_id + offset)
                        if adj and is_video_message(adj):
                            video_msgs.append(adj)
                            break
                    except Exception:
                        pass

                # Check for public sublinks in lead_msg.text
                if not video_msgs and lead_msg and lead_msg.text:
                    sub_links = re.findall(r"t\.me/([a-zA-Z0-9_]+)/(\d+)", lead_msg.text)
                    for sub_chan, sub_id in sub_links:
                        if sub_chan.lower() not in ("c", "joinchat", "addlist"):
                            try:
                                sub_msg = await client.get_messages(sub_chan, ids=int(sub_id))
                                if sub_msg and is_video_message(sub_msg):
                                    video_msgs.append(sub_msg)
                                    break
                            except Exception:
                                pass
        else:
            # No msg_id provided: search channel by title
            if progress_callback:
                progress_callback("RESOLVING", f"Searching @{channel_ref} for {title or 'movie'}...")

            clean_search = re.sub(r"[\(\)\[\]\-_]", " ", title).strip() if title else ""
            if clean_search:
                try:
                    async for m in client.iter_messages(channel_ref, search=clean_search, limit=15):
                        if is_video_message(m):
                            video_msgs.append(m)
                            break
                        if m.text:
                            sub_links = re.findall(r"t\.me/([a-zA-Z0-9_]+)/(\d+)", m.text)
                            for sub_chan, sub_id in sub_links:
                                if sub_chan.lower() not in ("c", "joinchat", "addlist"):
                                    try:
                                        sub_msg = await client.get_messages(sub_chan, ids=int(sub_id))
                                        if sub_msg and is_video_message(sub_msg):
                                            video_msgs.append(sub_msg)
                                            break
                                    except Exception:
                                        pass
                            if video_msgs:
                                break
                except Exception as s_err:
                    logger.warning(f"Error searching channel {channel_ref}: {s_err}")

            if not video_msgs:
                try:
                    recent_msgs = await client.get_messages(channel_ref, limit=30)
                    words = [w.lower() for w in re.findall(r"\w+", title) if len(w) > 3] if title else []
                    for m in recent_msgs:
                        if is_video_message(m):
                            fname = (getattr(m.file, "name", "") or "").lower()
                            if not words or any(w in fname for w in words):
                                video_msgs.append(m)
                                break
                except Exception as r_err:
                    logger.warning(f"Error fetching recent messages from {channel_ref}: {r_err}")

        if not video_msgs:
            raise TelethonUploaderError(
                f"Could not locate downloadable video in Telegram channel link: {telegram_url}"
            )

        # Auto-discover poster and review description from HomieTV if missing
        if (not poster_url or not description) and video_msgs:
            try:
                fname = getattr(video_msgs[0].file, "name", "") or ""
                candidate_title = title
                if not candidate_title or candidate_title.lower() in ("movie", "telegram movie delivery", "direct mega download"):
                    candidate_title = re.split(r"(?:19|20)\d{2}|720p|1080p|480p|web-dl|bluray|amzn|rip", fname, flags=re.IGNORECASE)[0]
                    candidate_title = candidate_title.replace(".", " ").strip()

                if candidate_title:
                    from app.scraper.homietv import HomieTVScraper
                    s = HomieTVScraper()
                    try:
                        results, _ = await s.search(candidate_title)
                        if results:
                            d = await s.get_movie_detail(results[0].detail_url)
                            if d:
                                poster_url = poster_url or d.poster_url
                                description = description or d.description
                                year = year or d.year
                                category = category or d.category
                                duration = duration or d.duration
                                quality = quality or d.quality
                                if not title or title.lower() in ("movie", "telegram movie delivery"):
                                    title = d.title
                                logger.info(f"Auto-enriched metadata from HomieTV for '{candidate_title}': poster={bool(poster_url)}, desc_len={len(description)}")
                    finally:
                        await s.close()
            except Exception as enrich_err:
                logger.debug(f"Could not auto-discover metadata from HomieTV: {enrich_err}")

        # Download poster
        local_poster = await self._prepare_poster(poster_url, job_id)
        fallback_photo = None
        if lead_msg and (hasattr(lead_msg, "photo") or isinstance(getattr(lead_msg, "media", None), types.MessageMediaPhoto)):
            fallback_photo = lead_msg.media

        await self._send_poster_and_review(
            client=client,
            target_entity=target_entity,
            title=title,
            description=description,
            local_poster_path=local_poster,
            fallback_photo_media=fallback_photo,
            total_items=len(video_msgs),
            year=year,
            category=category,
            duration=duration,
            quality=quality,
        )

        sent_messages: list[Message] = []
        try:
            for idx, v_msg in enumerate(video_msgs, 1):
                fsize = getattr(v_msg.file, "size", 0) or 0
                fname = getattr(v_msg.file, "name", "") or ""

                if len(video_msgs) > 1:
                    ep_header = f"{title} — Part {idx}" if title else f"Part {idx}"
                    caption = f"🎬 <b>{html.escape(ep_header)}</b>\n"
                    if fname:
                        caption += f"📄 <code>{html.escape(fname)}</code>\n"
                    if fsize:
                        caption += f"\n📁 Size: {format_file_size(fsize)}"
                    caption += f" ({idx}/{len(video_msgs)})\n"
                    if job_id:
                        caption += f"🆔 Job: #{job_id[:6]}"
                else:
                    caption = f"🎬 <b>{html.escape(title or fname or 'Movie')}</b>\n"
                    if fname:
                        caption += f"📄 <code>{html.escape(fname)}</code>\n"
                    if fsize:
                        caption += f"\n📁 Size: {format_file_size(fsize)}"
                    if job_id:
                        caption += f"\n🆔 Job: #{job_id[:6]}"

                if progress_callback:
                    progress_callback("UPLOADING", "Cloning movie directly to Telegram channel...")

                try:
                    sent = await client.send_file(
                        entity=target_entity,
                        file=v_msg.media,
                        caption=caption,
                        thumb=local_poster if (local_poster and local_poster.exists()) else None,
                        supports_streaming=True,
                        parse_mode="html",
                    )
                except Exception as send_err:
                    logger.warning(
                        f"send_file with media failed: {send_err}. Falling back to forward_messages."
                    )
                    forwarded = await client.forward_messages(
                        entity=target_entity,
                        messages=v_msg,
                    )
                    sent = forwarded[0] if isinstance(forwarded, list) else forwarded

                sent_messages.append(sent)
                if idx < len(video_msgs):
                    await asyncio.sleep(1.5)

            logger.info(f"Successfully cloned {len(sent_messages)} video(s) to {target_entity}")
            return sent_messages
        finally:
            if local_poster and local_poster.exists():
                try:
                    local_poster.unlink()
                except Exception:
                    pass

    async def fetch_and_forward_from_bot(
        self,
        telegram_url: str,
        target_entity: str | int,
        title: str = "",
        description: str = "",
        poster_url: str = "",
        job_id: str = "",
        progress_callback: Optional[Callable[[str, str], None]] = None,
        year: str = "",
        category: str = "",
        duration: str = "",
        quality: str = "",
    ) -> list[Message]:
        """Request a movie or series from the Telegram delivery bot and transfer all episodes/files to the channel.

        1. Parses bot username and start param (e.g. t.me/MSUBKYIMAL_BOT?start=sr_...).
        2. Unblocks the bot if blocked.
        3. Sends /start <start_param> to the bot.
        4. Clicks the deeplink button.
        5. For TV series, detects Season selection buttons and clicks them to trigger all episodes.
        6. Collects all episode video files streamed by the bot until finished.
        7. Sorts episodes chronologically (E01 -> E02 -> ...) and forwards each to the channel.
        8. Returns the list of sent messages.
        """
        clean_url = telegram_url.strip()
        if not is_bot_delivery_link(clean_url):
            logger.info(f"Routing channel link '{clean_url}' to clone_channel_media")
            return await self.clone_channel_media(
                telegram_url=clean_url,
                target_entity=target_entity,
                title=title,
                description=description,
                poster_url=poster_url,
                job_id=job_id,
                progress_callback=progress_callback,
                year=year,
                category=category,
                duration=duration,
                quality=quality,
            )

        if not clean_url.startswith(("http://", "https://", "tg://")):
            clean_url = "https://" + clean_url

        parsed = urlparse(clean_url)
        bot_username = ""
        start_param = ""

        if "t.me" in parsed.netloc or "telegram.me" in parsed.netloc:
            path_parts = parsed.path.strip("/").split("/")
            if path_parts:
                bot_username = path_parts[0]
            qs = parse_qs(parsed.query)
            if "start" in qs and qs["start"]:
                start_param = qs["start"][0]
        elif parsed.scheme == "tg":
            qs = parse_qs(parsed.query)
            bot_username = qs.get("domain", [""])[0]
            start_param = qs.get("start", [""])[0]
        elif clean_url.startswith("@"):
            bot_username = clean_url[1:]

        if not bot_username:
            bot_username = "MSUBKYIMAL_BOT"

        is_series = (
            start_param.startswith("sr_") or
            "series" in start_param.lower() or
            "season" in (title or "").lower() or
            "series" in (title or "").lower()
        )

        client = await self.get_client()
        if not await client.is_user_authorized():
            raise TelethonUploaderError(
                "Telethon session is not authorized. Run 'python login_telethon.py' to log in."
            )

        if isinstance(target_entity, str):
            try:
                target_entity = int(target_entity)
            except ValueError:
                pass

        # Unblock bot in case previously blocked
        try:
            await client(functions.contacts.UnblockRequest(id=bot_username))
        except Exception as e:
            logger.debug(f"Unblock {bot_username} notice: {e}")

        bot_entity = await client.get_input_entity(bot_username)
        cmd = f"/start {start_param}".strip() if start_param else "/start"
        logger.info(f"Sending '{cmd}' to bot @{bot_username} for job={job_id} (is_series={is_series})")

        if progress_callback:
            media_type_label = "series" if is_series else "movie"
            progress_callback("REQUESTING", f"Requesting {media_type_label} from @{bot_username}...")

        sent_cmd = await client.send_message(bot_entity, cmd)

        # 1. Wait for initial reply with buttons
        reply_msg = None
        for _ in range(25):
            await asyncio.sleep(1)
            msgs = await client.get_messages(bot_entity, limit=5)
            for m in msgs:
                if m.id > sent_cmd.id:
                    reply_msg = m
                    break
            if reply_msg:
                break

        if not reply_msg:
            raise TelethonUploaderError(
                f"No response from delivery bot @{bot_username} within 25 seconds"
            )

        # 2. Click deeplink button if present
        if reply_msg.buttons:
            if progress_callback:
                progress_callback("REQUESTING", "Processing delivery link...")
            logger.info(f"Clicking inline button on message {reply_msg.id}")
            try:
                await reply_msg.click(0)
            except Exception as click_err:
                logger.warning(f"Error clicking bot inline button: {click_err}")
            await asyncio.sleep(2)

        # 3. Check for Season selection (Series flow)
        season_msg = None
        for _ in range(10):
            msgs = await client.get_messages(bot_entity, limit=6)
            for m in msgs:
                if m.id > sent_cmd.id and m.buttons:
                    for r in m.buttons:
                        for b in r:
                            b_data = (b.data or b"").decode("utf-8", errors="ignore").lower()
                            b_text = (b.text or "").lower()
                            if "season" in b_data or "season" in b_text:
                                season_msg = m
                                break
                        if season_msg:
                            break
                if season_msg:
                    break
            if season_msg:
                break
            await asyncio.sleep(1)

        if season_msg and season_msg.buttons:
            is_series = True
            logger.info(f"Found season selection message {season_msg.id}")
            if progress_callback:
                progress_callback("REQUESTING", "Selecting series season...")
            for r_idx, row in enumerate(season_msg.buttons):
                for c_idx, btn in enumerate(row):
                    b_data = (btn.data or b"").decode("utf-8", errors="ignore").lower()
                    b_text = (btn.text or "").lower()
                    if "season" in b_data or "season" in b_text:
                        logger.info(f"Clicking season [{r_idx}][{c_idx}]: {btn.text}")
                        try:
                            await season_msg.click(r_idx, c_idx)
                            await asyncio.sleep(2)
                        except Exception as s_err:
                            logger.warning(f"Error clicking season button: {s_err}")

        # 4. Collect all incoming episode/movie video files
        if progress_callback:
            progress_callback(
                "RECEIVING",
                "Receiving episodes from delivery bot..." if is_series else "Receiving movie from delivery bot..."
            )

        collected_media: dict[int, Message] = {}
        poster_photo_msg: Optional[Message] = None
        last_new_time = asyncio.get_event_loop().time()
        start_wait = asyncio.get_event_loop().time()
        max_wait = 90 if is_series else 30
        quiet_timeout = 6 if is_series else 3

        while True:
            await asyncio.sleep(1)
            now = asyncio.get_event_loop().time()

            recent = await client.get_messages(bot_entity, limit=40)
            for m in recent:
                if m.id > sent_cmd.id:
                    # 1. Capture poster photo message from bot
                    if (hasattr(m, "photo") and m.photo) or isinstance(getattr(m, "media", None), types.MessageMediaPhoto):
                        if poster_photo_msg is None or m.id > poster_photo_msg.id:
                            poster_photo_msg = m
                            logger.info(f"Captured poster photo message from bot (id={m.id})")

                    # 2. Capture video files
                    if m.id not in collected_media:
                        if m.media and (getattr(m, "video", None) or getattr(m, "document", None)):
                            fsize = getattr(m.file, "size", 0) or 0
                            fname = getattr(m.file, "name", "") or ""
                            mime = getattr(m.file, "mime_type", "") or ""
                            is_vid = (
                                mime.startswith("video/") or
                                fname.lower().endswith((".mp4", ".mkv", ".avi", ".mov", ".webm"))
                            )
                            # Filter out stickers and thumbnails
                            if is_vid and fsize > 2 * 1024 * 1024:
                                collected_media[m.id] = m
                                last_new_time = now
                                logger.info(f"Collected: {fname} ({format_file_size(fsize)})")
                                if progress_callback:
                                    if is_series or len(collected_media) > 1:
                                        progress_callback(
                                            "RECEIVING",
                                            f"Received {len(collected_media)} episode(s) from bot..."
                                        )
                                    else:
                                        progress_callback("RECEIVING", "Received movie file from bot...")

            # If we collected files and no new ones arrived within quiet_timeout
            if collected_media and (now - last_new_time) >= quiet_timeout:
                break

            # Hard safety timeout
            if (now - start_wait) >= max_wait:
                break

        if not collected_media:
            raise TelethonUploaderError(
                f"Delivery bot @{bot_username} did not return any video files"
            )

        # 5. Sort episodes chronologically
        def episode_sort_key(msg: Message) -> tuple[int, int, int]:
            fname = getattr(msg.file, "name", "") or ""
            # Check S01E02
            s_match = re.search(r"[sS](\d+)[eE](\d+)", fname)
            if s_match:
                return (int(s_match.group(1)), int(s_match.group(2)), msg.id)
            # Check E02 or Ep 2 or Episode 2
            e_match = re.search(r"(?:[eE]|ep|episode)\s*(\d+)", fname, re.IGNORECASE)
            if e_match:
                return (1, int(e_match.group(1)), msg.id)
            return (1, msg.id, msg.id)

        sorted_msgs = sorted(collected_media.values(), key=episode_sort_key)
        total_items = len(sorted_msgs)

        # Download poster image (try website URL first, then bot's photo)
        local_poster_path: Optional[Path] = await self._prepare_poster(poster_url, job_id)

        # Fallback: Download bot's photo if website poster failed or missing
        if not local_poster_path and poster_photo_msg and poster_photo_msg.media:
            try:
                tmp_poster = Path("workspace") / f"poster_{job_id or 'tmp'}.jpg"
                tmp_poster.parent.mkdir(parents=True, exist_ok=True)
                dl_path = await client.download_media(poster_photo_msg.media, file=tmp_poster)
                if dl_path and Path(dl_path).exists() and Path(dl_path).stat().st_size > 1024:
                    local_poster_path = Path(dl_path)
                    logger.info(f"Downloaded poster photo from delivery bot ({local_poster_path.stat().st_size} bytes)")
            except Exception as bot_photo_err:
                logger.warning(f"Could not download bot photo: {bot_photo_err}")

        # Send poster and review
        fallback_photo = poster_photo_msg.media if (poster_photo_msg and poster_photo_msg.media) else None
        await self._send_poster_and_review(
            client=client,
            target_entity=target_entity,
            title=title,
            description=description,
            local_poster_path=local_poster_path,
            fallback_photo_media=fallback_photo,
            total_items=total_items,
            year=year,
            category=category,
            duration=duration,
            quality=quality,
        )

        logger.info(
            f"Delivering {total_items} file(s) for job={job_id} to {target_entity}..."
        )

        sent_messages: list[Message] = []
        try:
            for idx, ep_msg in enumerate(sorted_msgs, 1):
                fsize = getattr(ep_msg.file, "size", 0) or 0
                fname = getattr(ep_msg.file, "name", "") or ""

                if total_items > 1:
                    ep_header = f"{title} — Episode {idx}" if title else f"Episode {idx}"
                    caption = f"🎬 <b>{html.escape(ep_header)}</b>\n"
                    if fname:
                        caption += f"📄 <code>{html.escape(fname)}</code>\n"
                    if fsize:
                        caption += f"\n📁 Size: {format_file_size(fsize)}"
                    caption += f" (Episode {idx}/{total_items})\n"
                    if job_id:
                        caption += f"🆔 Job: #{job_id[:6]}"

                    if progress_callback:
                        progress_callback(
                            "UPLOADING",
                            f"Delivering episode {idx}/{total_items} to channel..."
                        )
                else:
                    caption = f"🎬 <b>{html.escape(title or fname or 'Movie')}</b>\n"
                    if fname:
                        caption += f"📄 <code>{html.escape(fname)}</code>\n"
                    if fsize:
                        caption += f"\n📁 Size: {format_file_size(fsize)}"
                    if job_id:
                        caption += f"\n🆔 Job: #{job_id[:6]}"

                    if progress_callback:
                        progress_callback("UPLOADING", "Forwarding movie to target channel...")

                sent = await client.send_file(
                    entity=target_entity,
                    file=ep_msg.media,
                    caption=caption,
                    thumb=local_poster_path if (local_poster_path and local_poster_path.exists()) else None,
                    supports_streaming=True,
                    parse_mode="html",
                )
                sent_messages.append(sent)

                # Small cooldown between sending episodes
                if idx < total_items:
                    await asyncio.sleep(1.5)

            logger.info(
                f"Successfully transferred {len(sent_messages)} file(s) to {target_entity}"
            )
            return sent_messages
        finally:
            if local_poster_path and local_poster_path.exists():
                try:
                    local_poster_path.unlink()
                except Exception:
                    pass

    async def close(self) -> None:
        """Disconnect the Telethon client."""
        async with self._lock:
            if self._client and self._client.is_connected():
                await self._client.disconnect()
                self._client = None
                logger.info("Telethon client disconnected")

