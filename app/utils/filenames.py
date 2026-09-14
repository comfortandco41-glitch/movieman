"""Filename sanitization utilities.

Ensures filenames are safe for Windows, macOS, and Linux filesystems.
"""

from __future__ import annotations

import re
import unicodedata


# Characters not allowed in filenames on Windows
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Multiple spaces/underscores collapse
_MULTI_SPACE = re.compile(r"[\s_]+")

# Maximum filename length (Windows NTFS limit)
_MAX_FILENAME_LEN = 200


def sanitize_filename(name: str, max_length: int = _MAX_FILENAME_LEN) -> str:
    """Sanitize a string for use as a safe filename.

    Args:
        name: Raw filename string.
        max_length: Maximum allowed length.

    Returns:
        Sanitized filename safe for all major filesystems.
    """
    if not name:
        return "untitled"

    # Normalize unicode
    name = unicodedata.normalize("NFKD", name)

    # Remove unsafe characters
    name = _UNSAFE_CHARS.sub("_", name)

    # Collapse whitespace/underscores
    name = _MULTI_SPACE.sub("_", name)

    # Strip leading/trailing underscores and dots
    name = name.strip("_. ")

    # Truncate to max length
    if len(name) > max_length:
        name = name[:max_length].rstrip("_. ")

    return name or "untitled"


def format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format.

    Args:
        size_bytes: File size in bytes.

    Returns:
        Formatted string like "1.5 GB" or "350 MB".
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
