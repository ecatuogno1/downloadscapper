/* ── database.js ── browsable category UI ── */

const browseState = {
  appInfo: null,
  stats: null,
  systems: [],
  system: "",
  type: "",
  sort: "size_desc",
  limit: 50,
  offset: 0,
  total: 0,
  results: [],
  searchQuery: "",
  searchResults: null,
};

const els = {
  pathBadge: document.getElementById("database-path-badge"),
  totalBadge: document.getElementById("database-total-badge"),
  sizeBadge: document.getElementById("database-size-badge"),
  stats: document.getElementById("database-stats"),
  topDomains: document.getElementById("database-top-domains"),
  filterBar: document.getElementById("browse-filter-bar"),
  searchForm: document.getElementById("browse-search-form"),
  searchQuery: document.getElementById("browse-query"),
  searchBtn: document.getElementById("browse-search-btn"),
  sortLabel: document.getElementById("browse-sort-label"),
  sort: document.getElementById("browse-sort"),
  categories: document.getElementById("browse-categories"),
  subcategories: document.getElementById("browse-subcategories"),
  results: document.getElementById("browse-results"),
  pagination: document.getElementById("browse-pagination"),
  status: document.getElementById("browse-status"),
};

/* ─── stats (unchanged) ─── */

function renderStats() {
  const stats = browseState.stats;
  const dbPath = browseState.appInfo?.database_path || stats?.db_path || "Unavailable";
  els.pathBadge.textContent = dbPath;
  if (!stats?.available) {
    els.totalBadge.textContent = "Missing";
    els.sizeBadge.textContent = "0 B";
    els.stats.innerHTML = `
      <span>Database file: ${escapeHtml(dbPath)}</span>
      <span>Build it with: python3 downloadscapper.py downloads-db build</span>
    `;
    els.topDomains.innerHTML = '<div class="warning-banner">No SQLite index found yet. Build the database first.</div>';
    return;
  }
  els.totalBadge.textContent = String(stats.unique_downloads || 0);
  els.sizeBadge.textContent = stats.total_human || "0 B";
  els.stats.classList.remove("empty");
  els.stats.innerHTML = `
    <span>${escapeHtml(stats.unique_downloads || 0)} unique downloads</span>
    <span>${escapeHtml(stats.observed_rows || 0)} observed rows</span>
    <span>${escapeHtml(stats.source_files || 0)} source files</span>
    <span>${escapeHtml(stats.systems || 0)} systems</span>
    <span>${escapeHtml(stats.domains || 0)} domains</span>
    <span>${escapeHtml(stats.total_human || "0 B")} indexed size</span>
  `;
  if (!(stats.top_domains || []).length) {
    els.topDomains.innerHTML = '<div class="muted">No domain breakdown available.</div>';
    return;
  }
  els.topDomains.innerHTML = (stats.top_domains || []).map((item) => `
    <div class="history-card">
      <div class="history-title">
        <strong>${escapeHtml(item.site_domain)}</strong>
        <span>${escapeHtml(item.downloads)} downloads</span>
      </div>
      <div class="history-meta">
        <span>${escapeHtml(item.total_human)}</span>
      </div>
    </div>
  `).join("");
}

/* ─── browse data loading ─── */

async function loadBrowse() {
  const params = new URLSearchParams();
  if (browseState.system) params.set("system", browseState.system);
  if (browseState.type) params.set("type", browseState.type);
  params.set("sort", browseState.sort);
  params.set("limit", String(browseState.limit));
  params.set("offset", String(browseState.offset));
  try {
    const data = await requestJson(`/api/database/browse?${params}`);
    browseState.systems = data.systems || [];
    browseState.results = data.results || [];
    browseState.total = data.total || 0;
    browseState.searchResults = null;
    browseState.searchQuery = "";
    els.searchQuery.value = "";
    renderBrowse();
  } catch (error) {
    els.status.textContent = error.message;
  }
}

/* ─── render dispatcher ─── */

function renderBrowse() {
  if (browseState.searchResults) {
    renderSearchView();
    return;
  }
  if (!browseState.system) {
    renderCategoryGrid();
  } else {
    renderDrillDown();
  }
}

/* ─── category grid (no filter) ─── */

function renderCategoryGrid() {
  els.filterBar.classList.add("hidden");
  els.subcategories.classList.add("hidden");
  els.results.classList.add("hidden");
  els.pagination.classList.add("hidden");
  els.sortLabel.classList.add("hidden");
  els.categories.classList.remove("hidden");
  els.status.textContent = "";

  if (!browseState.systems.length) {
    els.categories.innerHTML = emptyStateHtml(EMPTY_SVG.folder, "No systems found in the database.");
    return;
  }

  els.categories.innerHTML = browseState.systems.map((sys) => {
    const typeList = sys.base_types
      .filter((t) => t.base_type !== "other" || sys.base_types.length === 1)
      .slice(0, 3)
      .map((t) => t.base_type)
      .join(", ");
    return `
      <button class="history-card browse-system-card" data-system="${escapeHtml(sys.system_name)}">
        <div class="history-title">
          <strong>${escapeHtml(sys.system_name)}</strong>
          <span>${escapeHtml(sys.count)} files</span>
        </div>
        <div class="history-meta">
          <span>${escapeHtml(sys.total_human)}</span>
          ${typeList ? `<span>${escapeHtml(typeList)}</span>` : ""}
        </div>
      </button>
    `;
  }).join("");
}

/* ─── drill-down view (system selected) ─── */

function renderDrillDown() {
  els.categories.classList.add("hidden");
  els.filterBar.classList.remove("hidden");
  els.results.classList.remove("hidden");
  els.sortLabel.classList.remove("hidden");

  renderFilterBar();
  renderSubcategories();
  renderResultsList();
  renderPagination();
}

/* ─── filter bar breadcrumb ─── */

function renderFilterBar() {
  const sys = browseState.systems.find((s) => s.system_name === browseState.system);
  const sysCount = sys ? sys.count : browseState.total;

  let html = `<button class="summary-chip" data-action="clear-all">All Systems</button>`;
  html += `<span class="browse-separator">\u203a</span>`;
  html += `<button class="summary-chip" data-action="clear-type"><strong>${escapeHtml(browseState.system)}</strong>&nbsp; ${escapeHtml(sysCount)}</button>`;

  if (browseState.type) {
    html += `<span class="browse-separator">\u203a</span>`;
    html += `<span class="summary-chip"><strong>${escapeHtml(browseState.type)}</strong>&nbsp; ${escapeHtml(browseState.total)}</span>`;
  }
  els.filterBar.innerHTML = html;
}

/* ─── subcategory type chips ─── */

function renderSubcategories() {
  const sys = browseState.systems.find((s) => s.system_name === browseState.system);
  if (!sys || sys.base_types.length <= 1) {
    els.subcategories.classList.add("hidden");
    return;
  }
  els.subcategories.classList.remove("hidden");

  const allCount = sys.count;
  let html = `<button class="summary-chip browse-type-chip${!browseState.type ? " is-active" : ""}" data-type="">
    <strong>All</strong>&nbsp; ${escapeHtml(allCount)}
  </button>`;

  html += sys.base_types.map((bt) => `
    <button class="summary-chip browse-type-chip${browseState.type === bt.base_type ? " is-active" : ""}" data-type="${escapeHtml(bt.base_type)}">
      <strong>${escapeHtml(bt.base_type)}</strong>&nbsp; ${escapeHtml(bt.count)}
    </button>
  `).join("");

  els.subcategories.innerHTML = html;
}

/* ─── results list ─── */

function renderDownloadCard(item) {
  return `
    <section class="history-card">
      <div class="history-title">
        <strong>${escapeHtml(item.filename)}</strong>
        <span>${escapeHtml(item.size_human || "unknown")}</span>
      </div>
      <div class="history-meta">
        <span>${escapeHtml(item.system_name || "Unknown system")}</span>
        ${item.base_type ? `<span>${escapeHtml(item.base_type)}</span>` : ""}
        <span>${escapeHtml(item.site_domain || "unknown")}</span>
        <span>${escapeHtml(item.observations || 0)} obs</span>
      </div>
      <div class="path-note">
        <div>Method: ${escapeHtml(item.method || "GET")}</div>
        <div>URL: ${escapeHtml(item.final_url || "")}</div>
        ${item.source_page ? `<div>Source: ${escapeHtml(item.source_page)}</div>` : ""}
      </div>
    </section>
  `;
}

function renderResultsList() {
  if (!browseState.results.length) {
    els.results.innerHTML = emptyStateHtml(EMPTY_SVG.search, "No downloads found.");
    els.status.textContent = "";
    return;
  }
  els.results.innerHTML = browseState.results.map(renderDownloadCard).join("");
  els.status.textContent = "";
}

/* ─── pagination ─── */

function renderPagination() {
  if (browseState.total <= browseState.limit) {
    els.pagination.classList.add("hidden");
    return;
  }
  els.pagination.classList.remove("hidden");

  const start = browseState.offset + 1;
  const end = Math.min(browseState.offset + browseState.limit, browseState.total);
  const hasPrev = browseState.offset > 0;
  const hasNext = end < browseState.total;

  els.pagination.innerHTML = `
    <span class="browse-count">Showing ${start}\u2013${end} of ${browseState.total.toLocaleString()}</span>
    <div class="browse-page-buttons">
      <button class="ghost" id="browse-prev" ${hasPrev ? "" : "disabled"}>Previous</button>
      <button class="ghost" id="browse-next" ${hasNext ? "" : "disabled"}>Next</button>
    </div>
  `;
}

/* ─── search ─── */

async function runSearch(event) {
  if (event) event.preventDefault();
  const query = els.searchQuery.value.trim();
  if (!query) {
    browseState.searchResults = null;
    browseState.searchQuery = "";
    renderBrowse();
    return;
  }

  try {
    const payload = await requestJson(`/api/database/search?q=${encodeURIComponent(query)}&limit=100`);
    let results = payload.results || [];
    if (browseState.system) {
      results = results.filter((r) => r.system_name === browseState.system);
    }
    browseState.searchResults = results;
    browseState.searchQuery = query;
    renderSearchView();
  } catch (error) {
    els.status.textContent = error.message;
  }
}

function renderSearchView() {
  els.categories.classList.add("hidden");
  els.subcategories.classList.add("hidden");
  els.pagination.classList.add("hidden");
  els.sortLabel.classList.add("hidden");
  els.results.classList.remove("hidden");

  // Show filter bar with search context + clear button
  els.filterBar.classList.remove("hidden");
  let breadcrumb = `<button class="summary-chip" data-action="clear-all">All Systems</button>`;
  if (browseState.system) {
    breadcrumb += `<span class="browse-separator">\u203a</span>`;
    breadcrumb += `<button class="summary-chip" data-action="clear-type"><strong>${escapeHtml(browseState.system)}</strong></button>`;
  }
  breadcrumb += `<span class="browse-separator">\u203a</span>`;
  breadcrumb += `<span class="summary-chip">Search: "${escapeHtml(browseState.searchQuery)}" \u2014 ${browseState.searchResults.length} results</span>`;
  breadcrumb += `<button class="summary-chip" data-action="clear-search">Clear search</button>`;
  els.filterBar.innerHTML = breadcrumb;

  if (!browseState.searchResults.length) {
    els.results.innerHTML = emptyStateHtml(EMPTY_SVG.search, `No matches for "${escapeHtml(browseState.searchQuery)}".`);
  } else {
    els.results.innerHTML = browseState.searchResults.map(renderDownloadCard).join("");
  }
  els.status.textContent = "";
}

/* ─── event handlers ─── */

els.categories.addEventListener("click", (e) => {
  const card = e.target.closest("[data-system]");
  if (!card) return;
  browseState.system = card.dataset.system;
  browseState.type = "";
  browseState.offset = 0;
  loadBrowse();
});

els.subcategories.addEventListener("click", (e) => {
  const chip = e.target.closest("[data-type]");
  if (!chip) return;
  browseState.type = chip.dataset.type;
  browseState.offset = 0;
  loadBrowse();
});

els.filterBar.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-action]");
  if (!btn) return;
  const action = btn.dataset.action;
  if (action === "clear-all") {
    browseState.system = "";
    browseState.type = "";
    browseState.searchResults = null;
    browseState.searchQuery = "";
    els.searchQuery.value = "";
  } else if (action === "clear-type") {
    browseState.type = "";
  } else if (action === "clear-search") {
    browseState.searchResults = null;
    browseState.searchQuery = "";
    els.searchQuery.value = "";
  }
  browseState.offset = 0;
  loadBrowse();
});

els.sort.addEventListener("change", () => {
  browseState.sort = els.sort.value;
  browseState.offset = 0;
  loadBrowse();
});

els.pagination.addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  if (btn.id === "browse-prev" && browseState.offset > 0) {
    browseState.offset = Math.max(0, browseState.offset - browseState.limit);
    loadBrowse();
  } else if (btn.id === "browse-next" && browseState.offset + browseState.limit < browseState.total) {
    browseState.offset += browseState.limit;
    loadBrowse();
  }
});

els.searchForm.addEventListener("submit", withLoading(els.searchBtn, runSearch));

/* ─── init ─── */

async function initDatabasePage() {
  try {
    const [appInfo, stats, browse] = await Promise.all([
      requestJson("/api/app-info"),
      requestJson("/api/database/stats"),
      requestJson("/api/database/browse"),
    ]);
    browseState.appInfo = appInfo;
    browseState.stats = stats;
    browseState.systems = browse.systems || [];
    renderStats();
    renderBrowse();
  } catch (error) {
    els.stats.innerHTML = `<div class="error-banner">${escapeHtml(error.message)}</div>`;
  }
}

initDatabasePage();
