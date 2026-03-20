const databaseState = {
  appInfo: null,
  stats: null,
};

const databaseEls = {
  pathBadge: document.getElementById("database-path-badge"),
  totalBadge: document.getElementById("database-total-badge"),
  sizeBadge: document.getElementById("database-size-badge"),
  stats: document.getElementById("database-stats"),
  topDomains: document.getElementById("database-top-domains"),
  searchForm: document.getElementById("database-search-form"),
  query: document.getElementById("database-query"),
  limit: document.getElementById("database-limit"),
  searchStatus: document.getElementById("database-search-status"),
  results: document.getElementById("database-results"),
};

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || "Request failed.");
  }
  return payload;
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderStats() {
  const stats = databaseState.stats;
  const dbPath = databaseState.appInfo?.database_path || stats?.db_path || "Unavailable";
  databaseEls.pathBadge.textContent = dbPath;
  if (!stats?.available) {
    databaseEls.totalBadge.textContent = "Missing";
    databaseEls.sizeBadge.textContent = "0 B";
    databaseEls.stats.classList.remove("empty");
    databaseEls.stats.innerHTML = `
      <span>Database file: ${escapeHtml(dbPath)}</span>
      <span>Build it with: python3 downloadscapper.py downloads-db build</span>
    `;
    databaseEls.topDomains.innerHTML = '<div class="warning-banner">No SQLite index found yet. Build the database first.</div>';
    return;
  }

  databaseEls.totalBadge.textContent = String(stats.unique_downloads || 0);
  databaseEls.sizeBadge.textContent = stats.total_human || "0 B";
  databaseEls.stats.classList.remove("empty");
  databaseEls.stats.innerHTML = `
    <span>${escapeHtml(stats.unique_downloads || 0)} unique downloads</span>
    <span>${escapeHtml(stats.observed_rows || 0)} observed rows</span>
    <span>${escapeHtml(stats.source_files || 0)} source files</span>
    <span>${escapeHtml(stats.systems || 0)} systems</span>
    <span>${escapeHtml(stats.domains || 0)} domains</span>
    <span>${escapeHtml(stats.total_human || "0 B")} indexed size</span>
  `;
  if (!(stats.top_domains || []).length) {
    databaseEls.topDomains.innerHTML = '<div class="muted">No domain breakdown available.</div>';
    return;
  }
  databaseEls.topDomains.innerHTML = (stats.top_domains || []).map((item) => `
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

function renderResults(payload) {
  if (!payload.available) {
    databaseEls.results.innerHTML = '<div class="warning-banner">Build the download index first. Search is unavailable until the SQLite file exists.</div>';
    return;
  }
  if (!(payload.results || []).length) {
    databaseEls.results.innerHTML = '<div class="muted">No matches for that query.</div>';
    return;
  }
  databaseEls.results.innerHTML = payload.results.map((item) => `
    <section class="history-card">
      <div class="history-title">
        <strong>${escapeHtml(item.filename)}</strong>
        <span>${escapeHtml(item.size_human || "unknown")}</span>
      </div>
      <div class="history-meta">
        <span>${escapeHtml(item.system_name || "Unknown system")}</span>
        <span>${escapeHtml(item.site_domain || "unknown-domain")}</span>
        <span>${escapeHtml(item.observations || 0)} observations</span>
      </div>
      <div class="path-note">
        <div>Method: ${escapeHtml(item.method || "GET")}</div>
        <div>Final URL: ${escapeHtml(item.final_url || "")}</div>
        <div>Source page: ${escapeHtml(item.source_page || "")}</div>
      </div>
    </section>
  `).join("");
}

async function runSearch(event) {
  if (event) {
    event.preventDefault();
  }
  const query = databaseEls.query.value.trim();
  if (!query) {
    databaseEls.searchStatus.textContent = "Enter a query to search the index.";
    databaseEls.results.innerHTML = '<div class="muted">No search yet.</div>';
    return;
  }
  const limit = Number(databaseEls.limit.value || 25);
  databaseEls.searchStatus.textContent = `Searching for "${query}"…`;
  try {
    const payload = await requestJson(`/api/database/search?q=${encodeURIComponent(query)}&limit=${encodeURIComponent(limit)}`);
    databaseEls.searchStatus.textContent = `Found ${payload.results.length} result(s) for "${query}".`;
    renderResults(payload);
  } catch (error) {
    databaseEls.searchStatus.textContent = "Search failed.";
    databaseEls.results.innerHTML = `<div class="error-banner">${escapeHtml(error.message)}</div>`;
  }
}

async function initDatabasePage() {
  try {
    const [appInfo, stats] = await Promise.all([
      requestJson("/api/app-info"),
      requestJson("/api/database/stats"),
    ]);
    databaseState.appInfo = appInfo;
    databaseState.stats = stats;
    renderStats();
  } catch (error) {
    const message = `<div class="error-banner">${escapeHtml(error.message)}</div>`;
    databaseEls.stats.innerHTML = message;
    databaseEls.results.innerHTML = message;
  }
}

databaseEls.searchForm.addEventListener("submit", runSearch);
initDatabasePage();
