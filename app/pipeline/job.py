"""Job context and state definitions.

Explicit typed state for each pipeline job. Every stage updates
the context as it progresses.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


class JobStatus(str, enum.Enum):
    """Pipeline job states."""
    QUEUED = "QUEUED"
    SCRAPING = "SCRAPING"
    RESOLVING = "RESOLVING"
    DOWNLOADING = "DOWNLOADING"
    UPLOADING = "UPLOADING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class JobContext:
    """Typed context for a pipeline job.

    Holds all state including file paths, progress, and cancellation.
    Each stage reads from and writes to this context.
    """

    # Identity
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    user_id: int = 0
    title: str = ""

    # Source
    movie_page_url: str = ""       # Source movie detail page (mmsubchannel or homietv)
    mega_url: str = ""             # Resolved mega.nz download link
    download_url: str = ""         # Direct HTTP / stream download link
    telegram_url: str = ""         # Direct Telegram delivery bot or channel URL
    selected_server: str = ""      # Selected server name (e.g. 'Usersdrive', 'MegaUp')
    candidate_urls: list[str] = field(default_factory=list)  # Candidate download links
    poster_url: str = ""           # Movie poster/thumbnail URL
    description: str = ""          # Movie description text
    year: str = ""                 # Release year
    category: str = ""             # Genres / categories
    duration: str = ""             # Duration / runtime
    quality: str = ""              # Quality label
    source: str = ""               # Source website (e.g. 'homietv', 'mmsubchannel')

    # Job directory
    job_dir: Optional[Path] = None

    # File paths — set by each stage as it completes
    downloaded_file: Optional[Path] = None
    thumbnail_file: Optional[Path] = None

    # Media metadata
    file_size: int = 0

    # State
    status: JobStatus = JobStatus.QUEUED
    progress: int = 0
    error: Optional[str] = None

    # Cancellation flag — checked by every long-running stage
    _cancelled: bool = False

    # Telegram context for progress reporting
    chat_id: int = 0

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        """Request cancellation of this job."""
        self._cancelled = True

    def is_cancelled(self) -> bool:
        """Check if cancellation was requested. Used as cancel_check callback."""
        return self._cancelled

    def setup_workspace(self, base_dir: Path) -> Path:
        """Create and return the job workspace directory.

        Args:
            base_dir: Base jobs directory (workspace/jobs/).

        Returns:
            Path to this job's workspace directory.
        """
        self.job_dir = base_dir / self.job_id
        self.job_dir.mkdir(parents=True, exist_ok=True)

        # Set default file paths
        self.downloaded_file = self.job_dir / "movie.mp4"
        self.thumbnail_file = self.job_dir / "thumbnail.jpg"

        return self.job_dir


class PipelineError(Exception):
    """Structured pipeline error with stage information.

    User-facing message is sanitized. Full details go to server logs.
    """

    def __init__(self, stage: str, message: str, detail: str = "") -> None:
        self.stage = stage
        self.message = message  # User-facing
        self.detail = detail    # Server-side detail
        super().__init__(f"[{stage}] {message}")

    @property
    def user_message(self) -> str:
        """Sanitized message safe for Telegram display."""
        return (
            f"❌ Processing failed\n\n"
            f"Stage: {self.stage}\n"
            f"Reason: {self.message}"
        )
