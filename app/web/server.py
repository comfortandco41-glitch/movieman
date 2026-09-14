"""FastAPI web server for the Movie Store frontend.

Serves the static HTML/CSS/JS frontend and provides REST API
endpoints for movie data. Designed to run alongside the Telegram bot.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.store import database as db

logger = logging.getLogger(__name__)

# Paths
STATIC_DIR = Path(__file__).parent / "static"

# FastAPI app
app = FastAPI(title="Movie Man Store", docs_url=None, redoc_url=None)

# Allow CORS for all origins (so Vercel frontend can talk to this backend)
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── API Routes ─────────────────────────────────────────────────────────────

@app.get("/api/movies", response_class=JSONResponse)
async def api_get_movies(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: str = Query("", max_length=200),
    category: str = Query("", max_length=100),
):
    """Get paginated movie list with optional search and category filter."""
    result = await db.get_movies(
        page=page,
        per_page=per_page,
        search=search,
        category=category,
    )
    return result


@app.get("/api/movies/{movie_id}", response_class=JSONResponse)
async def api_get_movie(movie_id: int):
    """Get a single movie by ID."""
    movie = await db.get_movie(movie_id)
    if not movie:
        return JSONResponse({"error": "Movie not found"}, status_code=404)
    return movie


@app.get("/api/categories", response_class=JSONResponse)
async def api_get_categories():
    """Get all unique categories."""
    categories = await db.get_categories()
    return {"categories": categories}


@app.get("/health", response_class=JSONResponse)
async def health_check():
    """Health check endpoint for Render keep-alive and uptime monitoring."""
    return {"status": "ok", "service": "movie-man"}


# ── Static Files & Frontend ───────────────────────────────────────────────

# Mount static files (CSS, JS, images)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """Serve the main frontend HTML page."""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text(encoding="utf-8"))
    return HTMLResponse(
        content="<h1>Movie Man Store</h1><p>Frontend not found. Check app/web/static/</p>",
        status_code=500,
    )


@app.get("/style.css")
async def serve_css():
    from fastapi.responses import FileResponse
    return FileResponse(STATIC_DIR / "style.css", media_type="text/css")


@app.get("/app.js")
async def serve_js():
    from fastapi.responses import FileResponse
    return FileResponse(STATIC_DIR / "app.js", media_type="application/javascript")


async def start_server(host: str = "0.0.0.0", port: int = 8080) -> None:
    """Start the FastAPI server in the background.

    This is called from the main app startup to run alongside the Telegram bot.

    Args:
        host: Bind host address.
        port: Bind port number.
    """
    import uvicorn

    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    logger.info(f"Starting Movie Store web server on http://{host}:{port}")
    await server.serve()
