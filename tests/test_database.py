"""Tests for the Movie Store database and web API."""

from __future__ import annotations

import pytest
import pytest_asyncio
from pathlib import Path
from httpx import AsyncClient, ASGITransport

from app.store import database as db
from app.web.server import app


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db(tmp_path: Path):
    """Use an isolated temporary SQLite database for each test."""
    test_db_path = tmp_path / "test_movie_store.db"
    await db.init_db(test_db_path)
    yield
    await db.close_db()


@pytest.mark.asyncio
async def test_add_and_get_movie():
    """Verify adding a movie and retrieving by ID."""
    movie_id = await db.add_movie(
        job_id="job_001",
        title="Interstellar",
        poster_url="https://example.com/poster.jpg",
        description="A team of explorers travel through a wormhole in space.",
        year="2014",
        quality="1080p",
        category="Sci-Fi, Adventure",
        duration="169 min",
        source="homietv",
        telegram_video_url="https://t.me/c/12345/100",
    )
    assert movie_id > 0

    movie = await db.get_movie(movie_id)
    assert movie is not None
    assert movie["title"] == "Interstellar"
    assert movie["job_id"] == "job_001"
    assert movie["year"] == "2014"
    assert movie["telegram_video_url"] == "https://t.me/c/12345/100"


@pytest.mark.asyncio
async def test_add_movie_upsert():
    """Verify that adding with the same job_id updates rather than fails."""
    id1 = await db.add_movie(
        job_id="job_dup",
        title="Original Title",
        telegram_video_url="https://t.me/c/1/10",
    )
    id2 = await db.add_movie(
        job_id="job_dup",
        title="Updated Title",
        telegram_video_url="https://t.me/c/1/20",
    )
    assert id1 == id2

    movie = await db.get_movie(id1)
    assert movie["title"] == "Updated Title"
    assert movie["telegram_video_url"] == "https://t.me/c/1/20"


@pytest.mark.asyncio
async def test_completed_jobs_persistence():
    """Verify completed jobs are permanently persisted and retrieved by prefix."""
    await db.save_completed_job(
        job_id="70937ba1c89f",
        title="Gladiator II",
        poster_url="https://example.com/poster.jpg",
        description="A epic historical drama film.",
        year="2024",
        quality="1080p",
        category="Action",
    )

    # Prefix lookup
    job = await db.get_completed_job("70937b")
    assert job is not None
    assert job["title"] == "Gladiator II"
    assert job["year"] == "2024"

    # Exact lookup
    job_exact = await db.get_completed_job("70937ba1c89f")
    assert job_exact is not None
    assert job_exact["title"] == "Gladiator II"

    # Non-existent
    assert await db.get_completed_job("999999") is None


@pytest.mark.asyncio
async def test_get_movies_filtering_and_pagination():
    """Verify search, category filtering, and pagination."""
    await db.add_movie(
        job_id="m1",
        title="The Dark Knight",
        category="Action, Crime",
        year="2008",
    )
    await db.add_movie(
        job_id="m2",
        title="Inception",
        category="Sci-Fi, Action",
        year="2010",
    )
    await db.add_movie(
        job_id="m3",
        title="The Prestige",
        category="Drama, Mystery",
        year="2006",
    )

    # All movies
    res = await db.get_movies(page=1, per_page=10)
    assert res["total"] == 3
    assert len(res["movies"]) == 3

    # Search
    res_search = await db.get_movies(search="Dark")
    assert res_search["total"] == 1
    assert res_search["movies"][0]["title"] == "The Dark Knight"

    # Category filter
    res_cat = await db.get_movies(category="Sci-Fi")
    assert res_cat["total"] == 1
    assert res_cat["movies"][0]["title"] == "Inception"

    # Pagination
    res_page = await db.get_movies(page=1, per_page=2)
    assert len(res_page["movies"]) == 2
    assert res_page["total_pages"] == 2


@pytest.mark.asyncio
async def test_get_categories():
    """Verify categories are extracted and deduped."""
    await db.add_movie(job_id="c1", title="M1", category="Action, Drama")
    await db.add_movie(job_id="c2", title="M2", category="Drama, Sci-Fi")

    cats = await db.get_categories()
    assert "Action" in cats
    assert "Drama" in cats
    assert "Sci-Fi" in cats


@pytest.mark.asyncio
async def test_web_api_endpoints():
    """Verify FastAPI API endpoints work properly."""
    await db.add_movie(
        job_id="api_01",
        title="Spider-Man",
        category="Action",
        telegram_video_url="https://t.me/c/123/99",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # GET /api/movies
        resp = await ac.get("/api/movies")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["movies"][0]["title"] == "Spider-Man"

        # GET /api/movies/{id}
        movie_id = data["movies"][0]["id"]
        resp_single = await ac.get(f"/api/movies/{movie_id}")
        assert resp_single.status_code == 200
        assert resp_single.json()["title"] == "Spider-Man"

        # GET /api/categories
        resp_cats = await ac.get("/api/categories")
        assert resp_cats.status_code == 200
        assert "Action" in resp_cats.json()["categories"]

        # GET / (Frontend HTML)
        resp_html = await ac.get("/")
        assert resp_html.status_code == 200
        assert "Movie Man" in resp_html.text
