"""HTTP chunked file downloader with progress, retry, and cancellation.

Downloads media files from direct HTTP/HTTPS URLs (including resolved filehost links)
using streaming chunks to maintain minimal memory consumption.
"""

from __future__ import annotations

import asyncio
import email.message
import logging
import os
import re
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import unquote, urlparse

import httpx

from app.utils.filenames import sanitize_filename

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 512 * 1024  # 512 KB
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0


class HttpDownloadError(Exception):
    """Raised when an HTTP download fails."""
    pass


class HttpDownloader:
    """Async HTTP streaming downloader with progress callbacks and cancellation."""

    def __init__(self, timeout: float = 60.0) -> None:
        self.timeout = timeout

    @staticmethod
    def _extract_filename(response: httpx.Response, fallback_url: str) -> str:
        """Extract filename from Content-Disposition header or URL path."""
        # 1. Content-Disposition header
        cd = response.headers.get("content-disposition", "")
        if cd:
            msg = email.message.EmailMessage()
            msg["content-disposition"] = cd
            fname = msg.get_filename()
            if fname:
                return sanitize_filename(fname)

        # 2. URL path
        parsed = urlparse(fallback_url)
        path = unquote(parsed.path)
        base = os.path.basename(path)
        if base and "." in base:
            return sanitize_filename(base)

        return "downloaded_video.mp4"

    async def download(
        self,
        url: str,
        dest_dir: Path,
        dest_filename: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        max_retries: int = _MAX_RETRIES,
        headers: Optional[dict[str, str]] = None,
    ) -> Path:
        """Download a file via HTTP streaming.

        Args:
            url: Direct HTTP/HTTPS download link.
            dest_dir: Target directory to save file.
            dest_filename: Custom filename override.
            progress_callback: Called with (bytes_downloaded, total_bytes).
            cancel_check: Returns True if download should be cancelled.
            max_retries: Retry attempts on transient errors.
            headers: Optional custom HTTP request headers.

        Returns:
            Path to downloaded file.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)

        req_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
        }
        if headers:
            req_headers.update(headers)

        for attempt in range(max_retries + 1):
            if cancel_check and cancel_check():
                raise asyncio.CancelledError("Download cancelled")

            try:
                return await self._download_attempt(
                    url=url,
                    dest_dir=dest_dir,
                    dest_filename=dest_filename,
                    progress_callback=progress_callback,
                    cancel_check=cancel_check,
                    headers=req_headers,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if attempt < max_retries:
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        f"HTTP download attempt {attempt + 1} failed: {e}. Retrying in {delay}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    raise HttpDownloadError(
                        f"HTTP download failed after {max_retries + 1} attempts: {e}"
                    ) from e

        raise HttpDownloadError("HTTP download failed unexpectedly")

    async def _download_attempt(
        self,
        url: str,
        dest_dir: Path,
        dest_filename: Optional[str],
        progress_callback: Optional[Callable[[int, int], None]],
        cancel_check: Optional[Callable[[], bool]],
        headers: dict[str, str],
    ) -> Path:
        target_path: Optional[Path] = None
        try:
            async with httpx.AsyncClient(
                headers=headers,
                follow_redirects=True,
                timeout=httpx.Timeout(self.timeout, read=120.0),
            ) as client:
                    resp.raise_for_status()

                    # Validate content type to avoid downloading HTML cache pages as video files
                    content_type = resp.headers.get("content-type", "").lower()
                    if "text/html" in content_type:
                        raise HttpDownloadError(
                            f"Download URL returned HTML content ({content_type}) instead of a media stream. "
                            "The file host may have served an ad or expired landing page."
                        )

                    total_bytes = int(resp.headers.get("content-length", 0))

                    fname = dest_filename or self._extract_filename(resp, str(resp.url))
                    target_path = dest_dir / fname

                    # Ensure video extension fallback
                    if not target_path.suffix:
                        target_path = target_path.with_suffix(".mp4")

                    downloaded = 0
                    logger.info(
                        f"Streaming HTTP download to {target_path.name} (size={total_bytes} bytes)..."
                    )

                    with open(target_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=_CHUNK_SIZE):
                            if cancel_check and cancel_check():
                                raise asyncio.CancelledError("Download cancelled by user")

                            f.write(chunk)
                            downloaded += len(chunk)

                            if progress_callback:
                                progress_callback(downloaded, total_bytes)

                    logger.info(
                        f"HTTP download complete: {target_path.name} ({downloaded} bytes)"
                    )
                    return target_path

        except Exception:
            if target_path and target_path.exists():
                try:
                    target_path.unlink()
                except OSError:
                    pass
            raise
