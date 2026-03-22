const historyState = {
  appInfo: null,
  jobs: [],
  discoveryJobs: [],
};

const historyEls = {
  overview: document.getElementById("history-overview"),
  downloadCount: document.getElementById("download-history-count"),
  discoveryCount: document.getElementById("discovery-history-count"),
  stateDirBadge: document.getElementById("state-dir-badge"),
  downloadList: document.getElementById("download-history-list"),
  discoveryList: document.getElementById("discovery-history-list"),
};

function renderOverview() {
  if (!historyState.appInfo) {
    historyEls.overview.classList.add("empty");
    historyEls.overview.innerHTML = "<span>Unable to load app info.</span>";
    return;
  }
  historyEls.overview.classList.remove("empty");
  historyEls.overview.innerHTML = `
    <span>State: ${escapeHtml(historyState.appInfo.state_dir)}</span>
    <span>Scrapes: ${escapeHtml(historyState.appInfo.scrape_save_dir)}</span>
    <span>Database: ${escapeHtml(historyState.appInfo.database_path)}</span>
    <span>Server started: ${escapeHtml(historyState.appInfo.started_at || "")}</span>
  `;
  historyEls.stateDirBadge.textContent = historyState.appInfo.state_dir || "Unavailable";
}

function renderDownloadJobs() {
  historyEls.downloadCount.textContent = String(historyState.jobs.length);
  if (!historyState.jobs.length) {
    historyEls.downloadList.innerHTML = emptyStateHtml(EMPTY_SVG.folder, "No persisted download jobs yet.");
    return;
  }
  historyEls.downloadList.innerHTML = historyState.jobs.map((job) => {
    const sourceLabel = job.job_kind === "media"
      ? (job.source?.source_url || job.file_name || "Media job")
      : (job.file_name || job.job_id);
    return `
      <section class="history-card">
        <div class="history-title">
          <strong>${escapeHtml(sourceLabel)}</strong>
          <span class="status-pill status-${escapeHtml(job.status)}">${escapeHtml(job.status)}</span>
        </div>
        <div class="history-meta">
          <span>${escapeHtml(job.created_at || "")}</span>
          <span>${escapeHtml(job.summary?.total_rows ?? 0)} rows</span>
          <span>${escapeHtml(job.summary?.bytes_downloaded_human || "0 B")}</span>
        </div>
        <div class="path-note">
          <div>Output: ${escapeHtml(job.output_dir || "")}</div>
          <div>Manifest: ${escapeHtml(job.manifest_path || "Not written")}</div>
        </div>
      </section>
    `;
  }).join("");
}

function renderDiscoveryJobs() {
  historyEls.discoveryCount.textContent = String(historyState.discoveryJobs.length);
  if (!historyState.discoveryJobs.length) {
    historyEls.discoveryList.innerHTML = emptyStateHtml(EMPTY_SVG.search, "No persisted discovery runs yet.");
    return;
  }
  historyEls.discoveryList.innerHTML = historyState.discoveryJobs.map((job) => `
    <section class="history-card">
      <div class="history-title">
        <strong>${escapeHtml(job.start_url || job.source_file_name || job.job_id)}</strong>
        <span class="status-pill status-${escapeHtml(job.status)}">${escapeHtml(job.status)}</span>
      </div>
      <div class="history-meta">
        <span>${escapeHtml(job.created_at || "")}</span>
        <span>${escapeHtml(job.summary?.download_links_found ?? 0)} downloads</span>
        <span>${escapeHtml(job.summary?.total_known_human || "0 B")}</span>
      </div>
      <div class="path-note">
        <div>Artifacts: ${escapeHtml(job.artifacts?.directory || "Not saved")}</div>
        <div>CSV: ${escapeHtml(job.artifacts?.csv_path || "Not saved")}</div>
      </div>
    </section>
  `).join("");
}

async function initHistoryPage() {
  try {
    const [appInfo, history] = await Promise.all([
      requestJson("/api/app-info"),
      requestJson("/api/history"),
    ]);
    historyState.appInfo = appInfo;
    historyState.jobs = history.jobs || [];
    historyState.discoveryJobs = history.discovery_jobs || [];
    renderOverview();
    renderDownloadJobs();
    renderDiscoveryJobs();
  } catch (error) {
    const message = `<div class="error-banner">${escapeHtml(error.message)}</div>`;
    historyEls.overview.innerHTML = message;
    historyEls.downloadList.innerHTML = message;
    historyEls.discoveryList.innerHTML = message;
  }
}

initHistoryPage();
