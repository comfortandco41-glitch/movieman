/**
 * Movie Man Store — Frontend Application
 *
 * Fetches movies from the API and renders the movie grid,
 * hero section, category chips, search, pagination, and detail modal.
 */

(function () {
    "use strict";

    // ── State ─────────────────────────────────────────────────────
    let currentPage = 1;
    let currentSearch = "";
    let currentCategory = "";
    let movies = [];
    let totalPages = 1;
    let totalMovies = 0;
    let debounceTimer = null;

    // ── DOM Elements ──────────────────────────────────────────────
    const $grid = document.getElementById("movie-grid");
    const $spinner = document.getElementById("loading-spinner");
    const $empty = document.getElementById("empty-state");
    const $pagination = document.getElementById("pagination");
    const $searchInput = document.getElementById("search-input");
    const $searchClear = document.getElementById("search-clear");
    const $sectionTitle = document.getElementById("section-title");
    const $movieCount = document.getElementById("movie-count");
    const $categoriesScroll = document.getElementById("categories-scroll");

    // Hero
    const $heroBg = document.getElementById("hero-bg");
    const $heroTitle = document.getElementById("hero-title");
    const $heroDesc = document.getElementById("hero-desc");
    const $heroMeta = document.getElementById("hero-meta");
    const $heroBtn = document.getElementById("hero-btn");

    // Modal
    const $modalOverlay = document.getElementById("modal-overlay");
    const $modalClose = document.getElementById("modal-close");
    const $modalPoster = document.getElementById("modal-poster");
    const $modalQuality = document.getElementById("modal-quality");
    const $modalTitle = document.getElementById("modal-title");
    const $modalMeta = document.getElementById("modal-meta");
    const $modalDesc = document.getElementById("modal-desc");
    const $modalWatchBtn = document.getElementById("modal-watch-btn");

    // Header scroll effect
    const $header = document.getElementById("header");

    // ── API ───────────────────────────────────────────────────────
    const DEFAULT_REMOTE_API = "https://movieman-store-api.loca.lt";
    const isLocalhost = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";
    let stored = localStorage.getItem("API_BASE_URL");
    if (stored && stored.includes("good-guests-film")) {
        stored = DEFAULT_REMOTE_API;
        localStorage.setItem("API_BASE_URL", stored);
    }
    const API_BASE = (
        stored ||
        (isLocalhost ? "" : DEFAULT_REMOTE_API)
    ).replace(/\/+$/, "");

    const API_HEADERS = { "bypass-tunnel-reminder": "1" };

    async function fetchMovies(page = 1, search = "", category = "") {
        const params = new URLSearchParams({
            page: page.toString(),
            per_page: "20",
        });
        if (search) params.set("search", search);
        if (category) params.set("category", category);

        const response = await fetch(`${API_BASE}/api/movies?${params}`, {
            headers: API_HEADERS,
        });
        if (!response.ok) throw new Error("Failed to fetch movies");
        return response.json();
    }

    async function fetchCategories() {
        const response = await fetch(`${API_BASE}/api/categories`, {
            headers: API_HEADERS,
        });
        if (!response.ok) return { categories: [] };
        return response.json();
    }

    async function fetchMovie(id) {
        const response = await fetch(`${API_BASE}/api/movies/${id}`, {
            headers: API_HEADERS,
        });
        if (!response.ok) throw new Error("Movie not found");
        return response.json();
    }

    // ── Rendering ─────────────────────────────────────────────────

    function renderMovieCard(movie, index) {
        const card = document.createElement("div");
        card.className = "movie-card";
        card.style.animationDelay = `${Math.min(index * 0.05, 0.5)}s`;
        card.dataset.id = movie.id;

        const posterHtml = movie.poster_url
            ? `<img src="${escapeHtml(movie.poster_url)}" alt="${escapeHtml(movie.title)}" loading="lazy">`
            : `<div class="poster-placeholder">🎬</div>`;

        const qualityBadge = movie.quality
            ? `<div class="quality-badge">${escapeHtml(movie.quality)}</div>`
            : "";

        const yearText = movie.year || "";
        const categoryText = movie.category ? movie.category.split(",")[0].trim() : "";

        let metaHtml = "";
        if (yearText) metaHtml += `<span>${escapeHtml(yearText)}</span>`;
        if (yearText && categoryText) metaHtml += `<span class="dot"></span>`;
        if (categoryText) metaHtml += `<span>${escapeHtml(categoryText)}</span>`;

        card.innerHTML = `
            <div class="movie-card-poster">
                ${posterHtml}
                ${qualityBadge}
                <div class="play-overlay">
                    <div class="play-icon">
                        <svg viewBox="0 0 24 24"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
                    </div>
                </div>
            </div>
            <div class="movie-card-info">
                <div class="movie-card-title" title="${escapeHtml(movie.title)}">${escapeHtml(movie.title)}</div>
                <div class="movie-card-meta">${metaHtml}</div>
            </div>
        `;

        card.addEventListener("click", () => openModal(movie));
        return card;
    }

    function renderGrid(movieList) {
        $grid.innerHTML = "";
        movieList.forEach((movie, i) => {
            $grid.appendChild(renderMovieCard(movie, i));
        });
    }

    function renderPagination() {
        $pagination.innerHTML = "";
        if (totalPages <= 1) return;

        // Previous button
        const prevBtn = createPageBtn("‹", currentPage > 1, currentPage - 1);
        $pagination.appendChild(prevBtn);

        // Page numbers
        const range = getPageRange(currentPage, totalPages);
        range.forEach((p) => {
            if (p === "...") {
                const dots = document.createElement("span");
                dots.className = "page-btn";
                dots.textContent = "…";
                dots.style.cursor = "default";
                $pagination.appendChild(dots);
            } else {
                const btn = createPageBtn(p.toString(), true, p);
                if (p === currentPage) btn.classList.add("active");
                $pagination.appendChild(btn);
            }
        });

        // Next button
        const nextBtn = createPageBtn("›", currentPage < totalPages, currentPage + 1);
        $pagination.appendChild(nextBtn);
    }

    function createPageBtn(label, enabled, targetPage) {
        const btn = document.createElement("button");
        btn.className = "page-btn";
        btn.textContent = label;
        btn.disabled = !enabled;
        if (enabled) {
            btn.addEventListener("click", () => {
                currentPage = targetPage;
                loadMovies();
                window.scrollTo({ top: 400, behavior: "smooth" });
            });
        }
        return btn;
    }

    function getPageRange(current, total) {
        if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
        const pages = [];
        pages.push(1);
        if (current > 3) pages.push("...");
        for (let i = Math.max(2, current - 1); i <= Math.min(total - 1, current + 1); i++) {
            pages.push(i);
        }
        if (current < total - 2) pages.push("...");
        pages.push(total);
        return pages;
    }

    function renderHero(movie) {
        if (!movie) {
            $heroBg.style.backgroundImage = "";
            $heroTitle.textContent = "Welcome to MovieMan";
            $heroDesc.textContent = "Browse and watch the latest movies with Burmese subtitles";
            $heroMeta.innerHTML = "";
            $heroBtn.style.display = "none";
            return;
        }
        if (movie.poster_url) {
            $heroBg.style.backgroundImage = `url('${movie.poster_url}')`;
        }
        $heroTitle.textContent = movie.title;
        $heroDesc.textContent = movie.description
            ? movie.description.substring(0, 200) + (movie.description.length > 200 ? "…" : "")
            : "Browse and watch the latest movies with Burmese subtitles";

        let metaHtml = "";
        if (movie.year) metaHtml += `<span class="meta-tag">📅 ${escapeHtml(movie.year)}</span>`;
        if (movie.quality) metaHtml += `<span class="meta-tag">📊 ${escapeHtml(movie.quality)}</span>`;
        if (movie.category) {
            const cat = movie.category.split(",")[0].trim();
            metaHtml += `<span class="meta-tag">🏷️ ${escapeHtml(cat)}</span>`;
        }
        if (movie.duration) metaHtml += `<span class="meta-tag">⏱️ ${escapeHtml(movie.duration)}</span>`;
        $heroMeta.innerHTML = metaHtml;

        if (movie.telegram_video_url) {
            $heroBtn.style.display = "inline-flex";
            $heroBtn.onclick = () => window.open(movie.telegram_video_url, "_blank");
        } else {
            $heroBtn.style.display = "none";
        }
    }

    async function renderCategories() {
        try {
            const data = await fetchCategories();
            const cats = data.categories || [];

            // Keep the "All Genres" chip
            $categoriesScroll.innerHTML = '<button class="chip active" data-category="">All Genres</button>';

            cats.forEach((cat) => {
                const chip = document.createElement("button");
                chip.className = "chip";
                chip.dataset.category = cat;
                chip.textContent = cat;
                $categoriesScroll.appendChild(chip);
            });

            // Click handlers
            $categoriesScroll.querySelectorAll(".chip").forEach((chip) => {
                chip.addEventListener("click", () => {
                    $categoriesScroll.querySelectorAll(".chip").forEach((c) => c.classList.remove("active"));
                    chip.classList.add("active");
                    currentCategory = chip.dataset.category;
                    currentPage = 1;
                    updateSectionTitle();
                    loadMovies();
                });
            });
        } catch (e) {
            console.error("Failed to load categories:", e);
        }
    }

    // ── Modal ─────────────────────────────────────────────────────

    function openModal(movie) {
        $modalPoster.src = movie.poster_url || "";
        $modalPoster.alt = movie.title;
        $modalPoster.style.display = movie.poster_url ? "block" : "none";
        $modalQuality.textContent = movie.quality || "";
        $modalQuality.style.display = movie.quality ? "block" : "none";
        $modalTitle.textContent = movie.title;

        let metaHtml = "";
        if (movie.year) metaHtml += `<span class="meta-tag">📅 ${escapeHtml(movie.year)}</span>`;
        if (movie.quality) metaHtml += `<span class="meta-tag">📊 ${escapeHtml(movie.quality)}</span>`;
        if (movie.duration) metaHtml += `<span class="meta-tag">⏱️ ${escapeHtml(movie.duration)}</span>`;
        if (movie.category) metaHtml += `<span class="meta-tag">🏷️ ${escapeHtml(movie.category)}</span>`;
        if (movie.source) metaHtml += `<span class="meta-tag">📡 ${escapeHtml(movie.source)}</span>`;
        $modalMeta.innerHTML = metaHtml;

        $modalDesc.textContent = movie.description || "No review available.";

        if (movie.telegram_video_url) {
            $modalWatchBtn.href = movie.telegram_video_url;
            $modalWatchBtn.style.display = "inline-flex";
        } else {
            $modalWatchBtn.style.display = "none";
        }

        $modalOverlay.classList.add("open");
        document.body.style.overflow = "hidden";
    }

    function closeModal() {
        $modalOverlay.classList.remove("open");
        document.body.style.overflow = "";
    }

    $modalClose.addEventListener("click", closeModal);
    $modalOverlay.addEventListener("click", (e) => {
        if (e.target === $modalOverlay) closeModal();
    });
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") closeModal();
    });

    // ── Search ────────────────────────────────────────────────────

    $searchInput.addEventListener("input", () => {
        const val = $searchInput.value.trim();
        $searchClear.classList.toggle("visible", val.length > 0);

        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
            currentSearch = val;
            currentPage = 1;
            updateSectionTitle();
            loadMovies();
        }, 350);
    });

    $searchClear.addEventListener("click", () => {
        $searchInput.value = "";
        $searchClear.classList.remove("visible");
        currentSearch = "";
        currentPage = 1;
        updateSectionTitle();
        loadMovies();
    });

    // ── Header scroll effect ──────────────────────────────────────

    window.addEventListener("scroll", () => {
        $header.classList.toggle("scrolled", window.scrollY > 50);
    });

    // ── Helpers ───────────────────────────────────────────────────

    function escapeHtml(str) {
        const div = document.createElement("div");
        div.textContent = str;
        return div.innerHTML;
    }

    function updateSectionTitle() {
        if (currentSearch) {
            $sectionTitle.textContent = `🔍 Results for "${currentSearch}"`;
        } else if (currentCategory) {
            $sectionTitle.textContent = `🏷️ ${currentCategory}`;
        } else {
            $sectionTitle.textContent = "🎬 All Movies";
        }
    }

    function showLoading() {
        $grid.innerHTML = "";
        $pagination.innerHTML = "";
        $empty.style.display = "none";
        $spinner.style.display = "flex";
    }

    function hideLoading() {
        $spinner.style.display = "none";
    }

    // ── Main Load ─────────────────────────────────────────────────

    async function loadMovies() {
        showLoading();
        try {
            const data = await fetchMovies(currentPage, currentSearch, currentCategory);
            movies = data.movies || [];
            totalPages = data.total_pages || 1;
            totalMovies = data.total || 0;

            hideLoading();

            if (movies.length === 0) {
                $empty.style.display = "block";
                $movieCount.textContent = "";
                const emptyHeading = $empty.querySelector("h3");
                const emptyText = $empty.querySelector("p");
                if (currentSearch || currentCategory) {
                    if (emptyHeading) emptyHeading.textContent = "No movies found";
                    if (emptyText) emptyText.textContent = "Try a different search or filter.";
                } else {
                    if (emptyHeading) emptyHeading.textContent = "No Movies Published Yet";
                    if (emptyText) emptyText.innerHTML = "Process a movie in the Telegram bot, then send:<br><br><code>/upload &lt;job_id&gt; &lt;telegram_video_url&gt;</code>";
                }
                renderHero(null);
            } else {
                $empty.style.display = "none";
                $movieCount.textContent = `${totalMovies} movie${totalMovies !== 1 ? "s" : ""}`;
                renderGrid(movies);
                renderPagination();

                // Update hero with the latest movie (only on first page, no filter)
                if (currentPage === 1 && !currentSearch && !currentCategory && movies.length > 0) {
                    renderHero(movies[0]);
                }
            }
        } catch (err) {
            hideLoading();
            console.error("Failed to load movies:", err);
            $empty.style.display = "block";
            const emptyHeading = $empty.querySelector("h3");
            const emptyText = $empty.querySelector("p");
            if (emptyHeading) emptyHeading.textContent = "Backend Not Connected";
            if (emptyText) {
                emptyText.innerHTML = `
                    The frontend is live, but it cannot reach your bot backend yet.<br><br>
                    <button id="set-api-btn" style="padding:10px 20px;background:#6366f1;color:#fff;border:none;border-radius:10px;cursor:pointer;font-weight:600;font-size:14px;box-shadow:0 4px 14px rgba(99,102,241,0.4);">
                        🔗 Connect Backend URL
                    </button>
                `;
                const btn = document.getElementById("set-api-btn");
                if (btn) {
                    btn.onclick = () => {
                        const url = prompt("Enter your public backend URL (e.g. from Cloudflare Tunnel or Render):", API_BASE);
                        if (url !== null) {
                            localStorage.setItem("API_BASE_URL", url.trim());
                            window.location.reload();
                        }
                    };
                }
            }
        }
    }

    // ── Initialize ────────────────────────────────────────────────

    async function init() {
        await Promise.all([loadMovies(), renderCategories()]);
    }

    init();
})();
