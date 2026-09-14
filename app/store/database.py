"""SQLite database for the Movie Store frontend.

Stores published movies with poster, review, metadata, and Telegram video links.
Uses aiosqlite for async access.
"""

from __future__ import annotations

import aiosqlite
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default DB path
_DB_PATH: Path = Path("./workspace/movie_store.db")
_db: Optional[aiosqlite.Connection] = None


async def init_db(db_path: Optional[Path] = None) -> aiosqlite.Connection:
    """Initialize the database and create tables if they don't exist.

    Args:
        db_path: Path to the SQLite database file. Defaults to workspace/movie_store.db.

    Returns:
        The aiosqlite connection instance.
    """
    global _db, _DB_PATH

    if db_path:
        _DB_PATH = db_path

    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    _db = await aiosqlite.connect(str(_DB_PATH))
    _db.row_factory = aiosqlite.Row

    await _db.execute("""
        CREATE TABLE IF NOT EXISTS movies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT UNIQUE,
            title TEXT NOT NULL,
            poster_url TEXT DEFAULT '',
            description TEXT DEFAULT '',
            year TEXT DEFAULT '',
            quality TEXT DEFAULT '',
            category TEXT DEFAULT '',
            duration TEXT DEFAULT '',
            source TEXT DEFAULT '',
            telegram_video_url TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS completed_jobs (
            job_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            poster_url TEXT DEFAULT '',
            description TEXT DEFAULT '',
            year TEXT DEFAULT '',
            quality TEXT DEFAULT '',
            category TEXT DEFAULT '',
            duration TEXT DEFAULT '',
            source TEXT DEFAULT '',
            movie_page_url TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await _db.commit()

    logger.info(f"Movie store database initialized at {_DB_PATH}")
    return _db


async def get_db() -> aiosqlite.Connection:
    """Get the database connection, initializing if needed."""
    global _db
    if _db is None:
        await init_db()
    return _db


async def close_db() -> None:
    """Close the database connection."""
    global _db
    if _db:
        await _db.close()
        _db = None


async def add_movie(
    job_id: str,
    title: str,
    poster_url: str = "",
    description: str = "",
    year: str = "",
    quality: str = "",
    category: str = "",
    duration: str = "",
    source: str = "",
    telegram_video_url: str = "",
) -> int:
    """Add a movie to the store.

    Args:
        job_id: Pipeline job ID (used as unique key).
        title: Movie title.
        poster_url: Poster/thumbnail image URL.
        description: Burmese review / description text.
        year: Release year.
        quality: Video quality label.
        category: Genres / categories.
        duration: Runtime.
        source: Source website (homietv / mmsubchannel).
        telegram_video_url: Watchable Telegram video link.

    Returns:
        The inserted row ID.

    Raises:
        aiosqlite.IntegrityError: If a movie with the same job_id already exists.
    """
    db = await get_db()
    cursor = await db.execute(
        """
        INSERT INTO movies (job_id, title, poster_url, description, year,
                           quality, category, duration, source, telegram_video_url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(job_id) DO UPDATE SET
            title=excluded.title,
            poster_url=excluded.poster_url,
            description=excluded.description,
            year=excluded.year,
            quality=excluded.quality,
            category=excluded.category,
            duration=excluded.duration,
            source=excluded.source,
            telegram_video_url=excluded.telegram_video_url
        RETURNING id
        """,
        (job_id, title, poster_url, description, year,
         quality, category, duration, source, telegram_video_url),
    )
    row = await cursor.fetchone()
    await db.commit()
    movie_id = row[0] if row else cursor.lastrowid
    logger.info(f"Movie '{title}' (job={job_id}) added/updated in store with id={movie_id}")
    return movie_id


async def get_movies(
    page: int = 1,
    per_page: int = 20,
    search: str = "",
    category: str = "",
) -> dict:
    """Get paginated movie list with optional search and category filter.

    Args:
        page: Page number (1-indexed).
        per_page: Number of movies per page.
        search: Search term to filter by title.
        category: Category to filter by.

    Returns:
        Dict with 'movies' list, 'total', 'page', 'per_page', 'total_pages'.
    """
    db = await get_db()

    where_clauses = []
    params = []

    if search:
        where_clauses.append("title LIKE ?")
        params.append(f"%{search}%")

    if category:
        where_clauses.append("category LIKE ?")
        params.append(f"%{category}%")

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    # Count total
    count_row = await db.execute(f"SELECT COUNT(*) FROM movies {where_sql}", params)
    total = (await count_row.fetchone())[0]

    # Fetch page
    offset = (page - 1) * per_page
    rows = await db.execute(
        f"""
        SELECT id, job_id, title, poster_url, description, year, quality,
               category, duration, source, telegram_video_url, created_at
        FROM movies
        {where_sql}
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """,
        params + [per_page, offset],
    )
    movies = [dict(row) for row in await rows.fetchall()]

    total_pages = max(1, (total + per_page - 1) // per_page)

    return {
        "movies": movies,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
    }


async def get_movie(movie_id: int) -> Optional[dict]:
    """Get a single movie by ID.

    Args:
        movie_id: Database row ID.

    Returns:
        Movie dict or None if not found.
    """
    db = await get_db()
    row = await db.execute(
        """
        SELECT id, job_id, title, poster_url, description, year, quality,
               category, duration, source, telegram_video_url, created_at
        FROM movies WHERE id = ?
        """,
        (movie_id,),
    )
    result = await row.fetchone()
    return dict(result) if result else None


async def get_categories() -> list[str]:
    """Get all unique categories from the database.

    Returns:
        Sorted list of unique category strings.
    """
    db = await get_db()
    rows = await db.execute(
        "SELECT DISTINCT category FROM movies WHERE category != '' ORDER BY category"
    )
    results = await rows.fetchall()

    # Categories may be comma-separated, so split and deduplicate
    categories = set()
    for row in results:
        for cat in row[0].split(","):
            cat = cat.strip()
            if cat:
                categories.add(cat)

    return sorted(categories)


async def delete_movie(movie_id: int) -> bool:
    """Delete a movie from the store.

    Args:
        movie_id: Database row ID.

    Returns:
        True if deleted, False if not found.
    """
    db = await get_db()
    cursor = await db.execute("DELETE FROM movies WHERE id = ?", (movie_id,))
    await db.commit()
    return cursor.rowcount > 0


async def save_completed_job(
    job_id: str,
    title: str,
    poster_url: str = "",
    description: str = "",
    year: str = "",
    quality: str = "",
    category: str = "",
    duration: str = "",
    source: str = "",
    movie_page_url: str = "",
) -> None:
    """Persist completed job metadata permanently so /upload can always find it."""
    db = await get_db()
    await db.execute(
        """
        INSERT INTO completed_jobs (job_id, title, poster_url, description, year,
                                   quality, category, duration, source, movie_page_url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(job_id) DO UPDATE SET
            title=excluded.title,
            poster_url=excluded.poster_url,
            description=excluded.description,
            year=excluded.year,
            quality=excluded.quality,
            category=excluded.category,
            duration=excluded.duration,
            source=excluded.source,
            movie_page_url=excluded.movie_page_url
        """,
        (job_id, title, poster_url, description, year,
         quality, category, duration, source, movie_page_url),
    )
    await db.commit()
    logger.info(f"Persisted completed job #{job_id[:6]} ('{title}') to database")


async def get_completed_job(job_id_prefix: str) -> Optional[dict]:
    """Look up a completed job from the persistent database by full ID or prefix."""
    db = await get_db()
    prefix = job_id_prefix.lower().strip().lstrip("#")
    cursor = await db.execute(
        """
        SELECT * FROM completed_jobs
        WHERE lower(job_id) = ? OR lower(job_id) LIKE ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (prefix, f"{prefix}%"),
    )
    row = await cursor.fetchone()
    if row:
        return dict(row)
    return None

