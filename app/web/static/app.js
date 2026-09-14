/**
 * Movie Man Store — Senior Frontend UI Engine
 *
 * Implements smooth skeleton loading, cinema cards, responsive modal,
 * debounced search, genre filters, and Telegram video watch links.
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
    const $skeleton = document.getElementById("skeleton-grid");
    const $empty = document.getElementById("empty-state");
    const $emptyTitle = document.getElementById("empty-title");
    const $emptyDesc = document.getElementById("empty-desc");
    const $pagination = document.getElementById("pagination");
    const $searchInput = document.getElementById("search-input");
    const $searchClear = document.getElementById("search-clear");
    const $sectionTitle = document.getElementById("section-title");
    const $movieCount = document.getElementById("movie-count");
    const $categoriesScroll = document.getElementById("categories-scroll");

    // Hero
    const $heroBg = document.getElementById("hero-bg");
    const $heroBadge = document.getElementById("hero-badge");
    const $heroTitle = document.getElementById("hero-title");
    const $heroDesc = document.getElementById("hero-desc");
    const $heroMeta = document.getElementById("hero-meta");
    const $heroBtn = document.getElementById("hero-btn");
    const $heroInfoBtn = document.getElementById("hero-info-btn");

    // Modal
    const $modalOverlay = document.getElementById("modal-overlay");
    const $modalClose = document.getElementById("modal-close");
    const $modalPoster = document.getElementById("modal-poster");
    const $modalQuality = document.getElementById("modal-quality");
    const $modalTitle = document.getElementById("modal-title");
    const $modalMeta = document.getElementById("modal-meta");
    const $modalDesc = document.getElementById("modal-desc");
    const $modalWatchBtn = document.getElementById("modal-watch-btn");

    // Header scroll
    const $header = document.getElementById("header");

    // ── API Configuration ─────────────────────────────────────────
    const DEFAULT_REMOTE_API = "https://movieman-ohdg.onrender.com";
    const isLocalhost = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";
    
    // Support URL parameter override: ?api=https://your-service.onrender.com
    try {
        const urlParams = new URLSearchParams(window.location.search);
        const apiParam = urlParams.get("api");
        if (apiParam) {
            localStorage.setItem("API_BASE_URL", apiParam.trim().replace(/\/+$/, ""));
        }
    } catch (e) {}

    let stored = localStorage.getItem("API_BASE_URL");
    // Clear old temporary tunnel URLs so visitors use the live cloud backend
    if (stored && (stored.includes("loca.lt") || stored.includes("good-guests-film"))) {
        stored = DEFAULT_REMOTE_API;
        localStorage.setItem("API_BASE_URL", stored);
    }
    const API_BASE = (
        stored ||
        (isLocalhost ? "" : DEFAULT_REMOTE_API)
    ).replace(/\/+$/, "");

    const API_HEADERS = { "bypass-tunnel-reminder": "1" };

    // ── API Client ────────────────────────────────────────────────

    async function fetchMovies(page = 1, search = "", category = "") {
        const params = new URLSearchParams({
            page: page.toString(),
            per_page: "24",
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

    // ── Helper Utilities ──────────────────────────────────────────

    function escapeHtml(str) {
        if (!str) return "";
        const div = document.createElement("div");
        div.textContent = str;
        return div.innerHTML;
    }

    function cleanTitle(raw) {
        if (!raw) return "Untitled";
        // Clean out technical brackets like "(WEB-DL - Telegram - 1.5 GB)" for presentation
        const cleaned = raw.replace(/\s*\([^)]*(?:WEB-DL|Telegram|720p|1080p|GB|MB|HEVC)[^)]*\)/gi, "").trim();
        return cleaned || raw;
    }

    function formatQuality(rawQuality, fullTitle) {
        if (rawQuality) {
            const short = rawQuality.split(/[-–—]/)[0].trim();
            if (short) return short;
        }
        if (fullTitle && fullTitle.includes("1080p")) return "1080p";
        if (fullTitle && fullTitle.includes("720p")) return "720p";
        return "HD";
    }

    // ── Rendering ─────────────────────────────────────────────────

    function renderMovieCard(movie) {
        const card = document.createElement("div");
        card.className = "movie-card";
        card.dataset.id = movie.id;

        const displayTitle = cleanTitle(movie.title);
        const qualityText = formatQuality(movie.quality, movie.title);

        const posterHtml = movie.poster_url
            ? `<img src="${escapeHtml(movie.poster_url)}" alt="${escapeHtml(displayTitle)}" loading="lazy">`
            : `<div class="poster-placeholder">🎬</div>`;

        const qualityBadge = qualityText
            ? `<div class="card-quality-badge">${escapeHtml(qualityText)}</div>`
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
                    <div class="play-icon-wrap" aria-hidden="true">
                        <svg viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
                    </div>
                </div>
            </div>
            <div class="movie-card-info">
                <div class="movie-card-title" title="${escapeHtml(displayTitle)}">${escapeHtml(displayTitle)}</div>
                <div class="movie-card-meta">${metaHtml}</div>
            </div>
        `;

        card.addEventListener("click", () => openModal(movie));
        return card;
    }

    function renderGrid(movieList) {
        $grid.innerHTML = "";
        movieList.forEach((movie) => {
            $grid.appendChild(renderMovieCard(movie));
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
                window.scrollTo({ top: 380, behavior: "smooth" });
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
            $heroBadge.textContent = "🔥 FEATURED COLLECTION";
            $heroTitle.textContent = "Welcome to MovieMan";
            $heroDesc.textContent = "Browse and watch the latest movies with Burmese subtitles and cloud streaming.";
            $heroMeta.innerHTML = "";
            $heroBtn.style.display = "none";
            $heroInfoBtn.style.display = "none";
            return;
        }

        if (movie.poster_url) {
            $heroBg.style.backgroundImage = `url('${movie.poster_url}')`;
        }

        const displayTitle = cleanTitle(movie.title);
        $heroBadge.textContent = "🔥 LATEST RELEASE";
        $heroTitle.textContent = displayTitle;
        
        // Clean first paragraph of description
        let descSnippet = (movie.description || "").trim();
        const firstBreak = descSnippet.indexOf("\n\n");
        if (firstBreak !== -1) {
            descSnippet = descSnippet.substring(0, firstBreak);
        }
        if (descSnippet.length > 240) {
            descSnippet = descSnippet.substring(0, 240) + "…";
        }
        $heroDesc.textContent = descSnippet || "Experience this release with Burmese subtitles and full audio.";

        let metaHtml = "";
        if (movie.year) metaHtml += `<span class="meta-chip">📅 ${escapeHtml(movie.year)}</span>`;
        if (movie.quality) {
            const q = formatQuality(movie.quality, movie.title);
            metaHtml += `<span class="meta-chip quality">📊 ${escapeHtml(q)}</span>`;
        }
        if (movie.category) {
            const cats = movie.category.split(",").slice(0, 2);
            cats.forEach(c => {
                metaHtml += `<span class="meta-chip">🏷️ ${escapeHtml(c.trim())}</span>`;
            });
        }
        if (movie.duration) metaHtml += `<span class="meta-chip">⏱️ ${escapeHtml(movie.duration)}</span>`;
        $heroMeta.innerHTML = metaHtml;

        if (movie.telegram_video_url) {
            $heroBtn.style.display = "inline-flex";
            $heroBtn.onclick = () => window.open(movie.telegram_video_url, "_blank");
        } else {
            $heroBtn.style.display = "none";
        }

        $heroInfoBtn.style.display = "inline-flex";
        $heroInfoBtn.onclick = () => openModal(movie);
    }

    async function renderCategories() {
        try {
            const data = await fetchCategories();
            const cats = data.categories || [];

            $categoriesScroll.innerHTML = '<button class="chip active" data-category="">All Genres</button>';

            cats.forEach((cat) => {
                const chip = document.createElement("button");
                chip.className = "chip";
                chip.dataset.category = cat;
                chip.textContent = cat;
                $categoriesScroll.appendChild(chip);
            });

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
        const displayTitle = cleanTitle(movie.title);
        $modalPoster.src = movie.poster_url || "";
        $modalPoster.alt = displayTitle;
        $modalPoster.style.display = movie.poster_url ? "block" : "none";
        
        const q = formatQuality(movie.quality, movie.title);
        $modalQuality.textContent = q;
        $modalQuality.style.display = q ? "block" : "none";
        $modalTitle.textContent = displayTitle;

        let metaHtml = "";
        if (movie.year) metaHtml += `<span class="meta-chip">📅 ${escapeHtml(movie.year)}</span>`;
        if (movie.quality) metaHtml += `<span class="meta-chip quality">📊 ${escapeHtml(movie.quality)}</span>`;
        if (movie.duration) metaHtml += `<span class="meta-chip">⏱️ ${escapeHtml(movie.duration)}</span>`;
        if (movie.category) metaHtml += `<span class="meta-chip">🏷️ ${escapeHtml(movie.category)}</span>`;
        if (movie.source) metaHtml += `<span class="meta-chip">📡 ${escapeHtml(movie.source)}</span>`;
        $modalMeta.innerHTML = metaHtml;

        $modalDesc.textContent = (movie.description || "No review available for this movie.").trim();

        if (movie.telegram_video_url) {
            $modalWatchBtn.href = movie.telegram_video_url;
            $modalWatchBtn.style.display = "inline-flex";
        } else {
            $modalWatchBtn.style.display = "none";
        }

        $modalOverlay.classList.add("open");
        $modalOverlay.setAttribute("aria-hidden", "false");
        document.body.style.overflow = "hidden";
    }

    function closeModal() {
        $modalOverlay.classList.remove("open");
        $modalOverlay.setAttribute("aria-hidden", "true");
        document.body.style.overflow = "";
    }

    $modalClose.addEventListener("click", closeModal);
    $modalOverlay.addEventListener("click", (e) => {
        if (e.target === $modalOverlay) closeModal();
    });

    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && $modalOverlay.classList.contains("open")) {
            closeModal();
        }
    });

    // ── Search & Filter ───────────────────────────────────────────

    function updateSectionTitle() {
        if (currentSearch) {
            $sectionTitle.textContent = `🔍 Search: "${currentSearch}"`;
        } else if (currentCategory) {
            $sectionTitle.textContent = `🎬 ${currentCategory} Movies`;
        } else {
            $sectionTitle.textContent = "🎬 Available Releases";
        }
    }

    $searchInput.addEventListener("input", (e) => {
        const val = e.target.value.trim();
        $searchClear.classList.toggle("visible", val.length > 0);

        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
            currentSearch = val;
            currentPage = 1;
            updateSectionTitle();
            loadMovies();
        }, 300);
    });

    $searchClear.addEventListener("click", () => {
        $searchInput.value = "";
        $searchClear.classList.remove("visible");
        currentSearch = "";
        currentPage = 1;
        updateSectionTitle();
        loadMovies();
        $searchInput.focus();
    });

    // Nav filters
    const $navAll = document.getElementById("nav-all");
    const $navLatest = document.getElementById("nav-latest");

    if ($navAll) {
        $navAll.addEventListener("click", () => {
            $navAll.classList.add("active");
            if ($navLatest) $navLatest.classList.remove("active");
            currentCategory = "";
            currentSearch = "";
            $searchInput.value = "";
            $searchClear.classList.remove("visible");
            $categoriesScroll.querySelectorAll(".chip").forEach((c) => c.classList.remove("active"));
            const first = $categoriesScroll.querySelector(".chip");
            if (first) first.classList.add("active");
            currentPage = 1;
            updateSectionTitle();
            loadMovies();
        });
    }

    if ($navLatest) {
        $navLatest.addEventListener("click", () => {
            $navLatest.classList.add("active");
            if ($navAll) $navAll.classList.remove("active");
            currentPage = 1;
            loadMovies();
            window.scrollTo({ top: 400, behavior: "smooth" });
        });
    }

    // Header scroll blur
    window.addEventListener("scroll", () => {
        if (window.scrollY > 20) {
            $header.classList.add("scrolled");
        } else {
            $header.classList.remove("scrolled");
        }
    });

    // ── Data Loading ──────────────────────────────────────────────

    function showLoading() {
        $skeleton.style.display = "grid";
        $grid.style.display = "none";
        $empty.style.display = "none";
    }

    function hideLoading() {
        $skeleton.style.display = "none";
        $grid.style.display = "grid";
    }

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
                $movieCount.textContent = "0 releases";
                if (currentSearch || currentCategory) {
                    $emptyTitle.textContent = "No movies found";
                    $emptyDesc.textContent = "Try searching for a different title or select 'All Genres'.";
                } else {
                    $emptyTitle.textContent = "No Movies Published Yet";
                    $emptyDesc.innerHTML = "Process a movie in the Telegram bot, then send:<br><br><code>/upload &lt;job_id&gt; &lt;telegram_video_url&gt;</code>";
                }
                renderHero(null);
            } else {
                $empty.style.display = "none";
                $movieCount.textContent = `${totalMovies} movie${totalMovies !== 1 ? "s" : ""}`;
                renderGrid(movies);
                renderPagination();

                if (currentPage === 1 && !currentSearch && !currentCategory && movies.length > 0) {
                    renderHero(movies[0]);
                }
            }
        } catch (err) {
            hideLoading();
            console.error("Failed to load movies:", err);
            $empty.style.display = "block";
            $emptyTitle.textContent = "Backend Connection";
            $emptyDesc.innerHTML = `
                Could not connect to the cloud movie database.<br><br>
                <button id="set-api-btn" class="btn btn-primary">
                    🔗 Connect Backend URL
                </button>
            `;
            const btn = document.getElementById("set-api-btn");
            if (btn) {
                btn.onclick = () => {
                    const url = prompt("Enter your Render backend URL:", API_BASE);
                    if (url !== null) {
                        localStorage.setItem("API_BASE_URL", url.trim());
                        window.location.reload();
                    }
                };
            }
        }
    }

    // ── Initialize ────────────────────────────────────────────────

    async function init() {
        await Promise.all([loadMovies(), renderCategories()]);
    }

    init();
})();
