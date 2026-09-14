"""Job workspace cleanup utilities."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def cleanup_job(job_dir: Path) -> None:
    """Remove a job's workspace directory and all contents.

    Args:
        job_dir: Path to the job directory to clean up.
    """
    if not job_dir or not job_dir.exists():
        return

    try:
        shutil.rmtree(job_dir, ignore_errors=True)
        logger.info(f"Cleaned up job directory: {job_dir}")
    except Exception as e:
        logger.warning(f"Failed to clean up {job_dir}: {e}")
