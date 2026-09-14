"""Mega.nz file downloader with progress, retry, and cancellation.

Downloads files from Mega.nz public links using the mega.py library.
Supports progress callbacks, retry with exponential backoff, and cancellation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Callable, Optional

from app.utils.compat import apply_compat_patches

apply_compat_patches()

from mega import Mega

logger = logging.getLogger(__name__)

# Download configuration
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 3.0  # seconds


class MegaDownloadError(Exception):
    """Raised when a Mega download fails after all retries."""
    pass


class MegaDownloader:
    """Downloads files from Mega.nz with retry and progress support.

    Can optionally authenticate with a Mega account for higher
    download quotas (anonymous has ~5GB/day limit).
    """

    def __init__(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        self._email = email
        self._password = password
        self._mega: Optional[Mega] = None
        self._api = None

    def _ensure_client(self):
        """Initialize the Mega client (lazy initialization)."""
        if self._api is None:
            self._mega = Mega()
            if self._email and self._password:
                logger.info(f"Logging into Mega as {self._email}")
                self._api = self._mega.login(self._email, self._password)
            else:
                logger.info("Using Mega anonymously (quota limits apply)")
                self._api = self._mega.login()

    async def download(
        self,
        mega_url: str,
        dest_dir: Path,
        dest_filename: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        max_retries: int = _MAX_RETRIES,
    ) -> Path:
        """Download a file from Mega.nz.

        Args:
            mega_url: Mega.nz file URL.
            dest_dir: Directory to save the downloaded file.
            dest_filename: Override filename (uses Mega's filename if None).
            progress_callback: Called with (bytes_downloaded, total_bytes).
            cancel_check: Returns True if download should be cancelled.
            max_retries: Maximum retry attempts.

        Returns:
            Path to the downloaded file.

        Raises:
            MegaDownloadError: If download fails after all retries.
            asyncio.CancelledError: If cancelled via cancel_check.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)

        for attempt in range(max_retries + 1):
            try:
                # Check cancellation before attempt
                if cancel_check and cancel_check():
                    raise asyncio.CancelledError("Download cancelled")

                result = await self._download_attempt(
                    mega_url, dest_dir, dest_filename, progress_callback, cancel_check
                )
                return result

            except asyncio.CancelledError:
                raise

            except Exception as e:
                if attempt < max_retries:
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        f"Mega download attempt {attempt + 1} failed: {e}. "
                        f"Retrying in {delay}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    raise MegaDownloadError(
                        f"Mega download failed after {max_retries + 1} attempts: {e}"
                    ) from e

        raise MegaDownloadError("Download failed unexpectedly")

    async def _download_attempt(
        self,
        mega_url: str,
        dest_dir: Path,
        dest_filename: Optional[str],
        progress_callback: Optional[Callable],
        cancel_check: Optional[Callable],
    ) -> Path:
        """Single download attempt.

        mega.py is synchronous, so we run it in a thread executor.
        """
        loop = asyncio.get_event_loop()

        def _sync_download():
            self._ensure_client()

            # Get file info first
            logger.info(f"Getting file info from: {mega_url}")

            # Download the file
            # mega.py downloads to the specified directory
            downloaded_path = self._api.download_url(
                mega_url,
                dest_path=str(dest_dir),
            )

            return downloaded_path

        # Run the synchronous mega download in a thread
        downloaded_path = await loop.run_in_executor(None, _sync_download)

        if not downloaded_path:
            raise MegaDownloadError("mega.py returned None — download may have failed")

        downloaded_path = Path(downloaded_path)

        if not downloaded_path.exists():
            raise MegaDownloadError(f"Downloaded file does not exist: {downloaded_path}")

        file_size = downloaded_path.stat().st_size
        if file_size == 0:
            downloaded_path.unlink(missing_ok=True)
            raise MegaDownloadError("Downloaded file is empty")

        # Rename if a custom filename was requested
        if dest_filename:
            final_path = dest_dir / dest_filename
            if final_path != downloaded_path:
                shutil.move(str(downloaded_path), str(final_path))
                downloaded_path = final_path

        # Report final progress
        if progress_callback:
            try:
                progress_callback(file_size, file_size)
            except Exception:
                pass

        logger.info(f"Mega download complete: {downloaded_path} ({file_size:,} bytes)")
        return downloaded_path

    async def get_file_info(self, mega_url: str) -> dict:
        """Get file information from a Mega.nz URL without downloading.

        Args:
            mega_url: Mega.nz file URL.

        Returns:
            Dictionary with file metadata (name, size, etc.).
        """
        loop = asyncio.get_event_loop()

        def _sync_info():
            self._ensure_client()
            # mega.py can get file info from a URL
            try:
                info = self._api.get_public_url_info(mega_url)
                return info
            except Exception:
                return {}

        return await loop.run_in_executor(None, _sync_info)
