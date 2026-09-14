"""Runtime compatibility patches for modern Python versions (3.11, 3.12, 3.13+).

Ensures legacy dependencies (like mega.py/tenacity < 8) work seamlessly
on Python 3.11+ where asyncio.coroutine was deprecated and removed.
"""

from __future__ import annotations

import asyncio
import types


def apply_compat_patches() -> None:
    """Apply runtime monkeypatches for Python 3.11+ backward compatibility."""
    # Python 3.11 deprecated and 3.13 removed asyncio.coroutine.
    # Legacy packages like tenacity 5.x (pinned by mega.py) decorate methods
    # with @asyncio.coroutine. We map it to types.coroutine so they continue working.
    if not hasattr(asyncio, "coroutine"):
        asyncio.coroutine = types.coroutine  # type: ignore[attr-defined]


# Apply immediately on module import
apply_compat_patches()
