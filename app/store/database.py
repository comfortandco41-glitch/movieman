"""Database layer for Movie Store frontend.

Supports both:
1. Turso Cloud SQLite (libsql-client) for 24/7 cloud persistence.
2. Local SQLite (aiosqlite) for local development and testing.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Default local DB path
_DB_PATH: Path = Path("./workspace/movie_store.db")


class BaseBackend:
    """Base interface for database operations."""

    async def init_tables(self) -> None:
        raise NotImplementedError

    async def execute(self, sql: str, params: Optional[list | tuple] = None) -> Any:
        raise NotImplementedError

    async def fetchone(self, sql: str, params: Optional[list | tuple] = None) -> Optional[dict]:
        raise NotImplementedError

    async def fetchall(self, sql: str, params: Optional[list | tuple] = None) -> list[dict]:
        raise NotImplementedError

    async def close(self) -> None:
        pass


class AioSqliteBackend(BaseBackend):
    """Local SQLite backend using aiosqlite."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._db: Optional[Any] = None

    async def get_connection(self) -> Any:
        if self._db is None:
            import aiosqlite
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = await aiosqlite.connect(str(self.db_path))
            self._db.row_factory = aiosqlite.Row
        return self._db

    async def init_tables(self) -> None:
        db = await self.get_connection()
        await db.execute("""
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
        await db.execute("""
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
        await db.commit()

    async def execute(self, sql: str, params: Optional[list | tuple] = None) -> Any:
        db = await self.get_connection()
        cursor = await db.execute(sql, params or ())
        await db.commit()
        return cursor

    async def fetchone(self, sql: str, params: Optional[list | tuple] = None) -> Optional[dict]:
        db = await self.get_connection()
        cursor = await db.execute(sql, params or ())
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def fetchall(self, sql: str, params: Optional[list | tuple] = None) -> list[dict]:
        db = await self.get_connection()
        cursor = await db.execute(sql, params or ())
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None


class TursoBackend(BaseBackend):
    """Cloud SQLite backend using Turso (libsql-client)."""

    def __init__(self, url: str, auth_token: str) -> None:
        self.url = url
        self.auth_token = auth_token
        self._client: Optional[Any] = None

    def get_client(self) -> Any:
        if self._client is None:
            import libsql_client
            # Normalise url for HTTP/libsql
            client_url = self.url
            if client_url.startswith("libsql://"):
                client_url = "https://" + client_url[len("libsql://"):]
            self._client = libsql_client.create_client(client_url, auth_token=self.auth_token)
        return self._client

    async def init_tables(self) -> None:
        client = self.get_client()
        await client.execute("""
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
        await client.execute("""
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

    async def execute(self, sql: str, params: Optional[list | tuple] = None) -> Any:
        client = self.get_client()
        return await client.execute(sql, list(params) if params else None)

    async def fetchone(self, sql: str, params: Optional[list | tuple] = None) -> Optional[dict]:
        client = self.get_client()
        res = await client.execute(sql, list(params) if params else None)
        if res.rows:
            return dict(zip(res.columns, res.rows[0]))
        return None

    async def fetchall(self, sql: str, params: Optional[list | tuple] = None) -> list[dict]:
        client = self.get_client()
        res = await client.execute(sql, list(params) if params else None)
        return [dict(zip(res.columns, r)) for r in res.rows]

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None


# Global active backend
_backend: Optional[BaseBackend] = None


async def init_db(
    db_path: Optional[Path] = None,
    turso_url: Optional[str] = None,
    turso_token: Optional[str] = None,
) -> BaseBackend:
    """Initialize the database (Turso cloud if configured, otherwise local SQLite)."""
    global _backend, _DB_PATH

    if db_path:
        _DB_PATH = db_path

    # Check environment/config if turso credentials not passed explicitly
    if not turso_url:
        turso_url = os.getenv("TURSO_DATABASE_URL")
    if not turso_token:
        turso_token = os.getenv("TURSO_AUTH_TOKEN")

    if not turso_url or not turso_token:
        try:
            from app.config import load_config
            cfg = load_config()
            turso_url = turso_url or cfg.turso_database_url
            turso_token = turso_token or cfg.turso_auth_token
        except Exception:
            pass

    if turso_url and turso_token:
        _backend = TursoBackend(turso_url, turso_token)
        logger.info(f"Connecting to Turso Cloud SQLite: {turso_url}")
    else:
        _backend = AioSqliteBackend(_DB_PATH)
        logger.info(f"Using local SQLite database at: {_DB_PATH}")

    await _backend.init_tables()

    # Clean up any legacy "Telegram Cloned" or clone labels
    try:
        await _backend.execute(
            "UPDATE movies SET quality = '1080p' WHERE (quality LIKE '%clone%' OR quality LIKE '%telegram%') AND title LIKE '%1080p%'"
        )
        await _backend.execute(
            "UPDATE movies SET quality = '720p' WHERE (quality LIKE '%clone%' OR quality LIKE '%telegram%') AND title LIKE '%720p%'"
        )
        await _backend.execute(
            "UPDATE movies SET quality = 'HD' WHERE quality LIKE '%clone%' OR quality LIKE '%telegram%'"
        )
        await _backend.execute(
            "UPDATE movies SET source = '' WHERE source LIKE '%clone%'"
        )
        await _backend.execute(
            "UPDATE completed_jobs SET quality = 'HD' WHERE quality LIKE '%clone%' OR quality LIKE '%telegram%'"
        )
        await _backend.execute(
            "UPDATE completed_jobs SET source = '' WHERE source LIKE '%clone%'"
        )
    except Exception as e:
        logger.debug(f"Data cleanup notice: {e}")

    return _backend


async def get_db() -> BaseBackend:
    """Get active database backend, initializing if needed."""
    global _backend
    if _backend is None:
        await init_db()
    return _backend


async def close_db() -> None:
    """Close active database connection."""
    global _backend
    if _backend:
        await _backend.close()
        _backend = None


# ── Movies API ─────────────────────────────────────────────────────────────

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
    """Add or update a movie in the store."""
    backend = await get_db()
    sql = """
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
    """
    params = (job_id, title, poster_url, description, year,
              quality, category, duration, source, telegram_video_url)

    row = await backend.fetchone(sql, params)
    movie_id = row["id"] if row and "id" in row else 0

    if not movie_id:
        existing = await backend.fetchone("SELECT id FROM movies WHERE job_id = ?", (job_id,))
        movie_id = existing["id"] if existing else 0

    logger.info(f"Movie '{title}' (job={job_id}) saved with id={movie_id}")
    return movie_id


async def get_movies(
    page: int = 1,
    per_page: int = 20,
    search: str = "",
    category: str = "",
) -> dict:
    """Get paginated movie list with optional search and category filter."""
    backend = await get_db()

    where_clauses = []
    params: list[Any] = []

    if search:
        where_clauses.append("LOWER(title) LIKE ?")
        params.append(f"%{search.lower()}%")

    if category:
        where_clauses.append("LOWER(category) LIKE ?")
        params.append(f"%{category.lower()}%")

    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # Total count
    count_sql = f"SELECT COUNT(*) as cnt FROM movies{where_sql}"
    count_row = await backend.fetchone(count_sql, params)
    total = count_row["cnt"] if count_row else 0

    total_pages = max(1, (total + per_page - 1) // per_page)
    offset = (page - 1) * per_page

    # Query items
    query_sql = f"""
        SELECT id, job_id, title, poster_url, description, year,
               quality, category, duration, source, telegram_video_url, created_at
        FROM movies
        {where_sql}
        ORDER BY id DESC
        LIMIT ? OFFSET ?
    """
    query_params = list(params) + [per_page, offset]
    movies = await backend.fetchall(query_sql, query_params)

    return {
        "movies": movies,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
    }


async def get_movie(movie_id: int) -> Optional[dict]:
    """Get a single movie by ID."""
    backend = await get_db()
    return await backend.fetchone("SELECT * FROM movies WHERE id = ?", (movie_id,))


async def get_categories() -> list[str]:
    """Get all unique categories from the database."""
    backend = await get_db()
    rows = await backend.fetchall(
        "SELECT DISTINCT category FROM movies WHERE category != '' ORDER BY category"
    )

    categories = set()
    for row in rows:
        cat_str = row.get("category", "")
        if cat_str:
            for cat in cat_str.split(","):
                cat = cat.strip()
                if cat:
                    categories.add(cat)

    return sorted(categories)


async def delete_movie(movie_id: int) -> bool:
    """Delete a movie from the store."""
    backend = await get_db()
    res = await backend.execute("DELETE FROM movies WHERE id = ?", (movie_id,))
    return True


# ── Completed Jobs API ─────────────────────────────────────────────────────

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
    backend = await get_db()
    sql = """
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
    """
    params = (job_id, title, poster_url, description, year,
              quality, category, duration, source, movie_page_url)
    await backend.execute(sql, params)
    logger.info(f"Persisted completed job #{job_id[:6]} ('{title}') to database")


async def get_completed_job(job_id_prefix: str) -> Optional[dict]:
    """Look up a completed job from the persistent database by full ID or prefix."""
    backend = await get_db()
    prefix = job_id_prefix.lower().strip().lstrip("#")
    sql = """
        SELECT * FROM completed_jobs
        WHERE lower(job_id) = ? OR lower(job_id) LIKE ?
        ORDER BY created_at DESC LIMIT 1
    """
    return await backend.fetchone(sql, (prefix, f"{prefix}%"))


async def sync_local_to_cloud(local_db_path: Optional[Path] = None) -> int:
    """Copy all local movies into the active cloud database.

    Useful when first migrating to Turso Cloud SQLite.

    Returns:
        Number of movies copied.
    """
    import aiosqlite
    path = local_db_path or _DB_PATH
    if not path.exists():
        return 0

    backend = await get_db()
    if isinstance(backend, AioSqliteBackend):
        logger.info("Active backend is already local SQLite; skipping sync.")
        return 0

    copied = 0
    async with aiosqlite.connect(str(path)) as local_db:
        local_db.row_factory = aiosqlite.Row
        cursor = await local_db.execute("SELECT * FROM movies")
        rows = await cursor.fetchall()
        for r in rows:
            await add_movie(
                job_id=r["job_id"],
                title=r["title"],
                poster_url=r["poster_url"],
                description=r["description"],
                year=r["year"],
                quality=r["quality"],
                category=r["category"],
                duration=r["duration"],
                source=r["source"],
                telegram_video_url=r["telegram_video_url"],
            )
            copied += 1

    logger.info(f"Synced {copied} movies from local SQLite to cloud database.")
    return copied
