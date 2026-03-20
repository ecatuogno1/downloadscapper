const SESSION_OUTPUT_DIR_KEY = "downloadscapper.output_dir";

const MEDIA_OPTIONS = {
  video: {
    formats: [
      { value: "any", label: "Best available" },
      { value: "mp4", label: "MP4" },
    ],
    qualities: [
      { value: "best", label: "Best" },
      { value: "1080", label: "1080p or less" },
      { value: "720", label: "720p or less" },
      { value: "480", label: "480p or less" },
    ],
  },
  audio: {
    formats: [
      { value: "mp3", label: "MP3" },
      { value: "m4a", label: "M4A" },
      { value: "opus", label: "Opus" },
    ],
    qualities: [
      { value: "320", label: "320 kbps target" },
      { value: "192", label: "192 kbps target" },
      { value: "128", label: "128 kbps target" },
      { value: "best", label: "Best" },
    ],
  },
};

const state = {
  appInfo: null,
  activeStep: 1,
  sourceType: "url",
  importedCsvText: "",
  importedCsvFileName: "",
  discoveryJobId: null,
  discoveryJob: null,
  discoveryPollTimer: null,
  selectedRecordIds: new Set(),
  filters: {
    search: "",
    knownOnly: false,
  },
  downloadJobId: null,
  currentJob: null,
  downloadPollTimer: null,
  historyJobs: [],
};

const els = {
  stepperItems: Array.from(document.querySelectorAll(".stepper-item")),
  stepPanels: Array.from(document.querySelectorAll(".wizard-step")),
  globalError: document.getElementById("global-error"),
  discoveryForm: document.getElementById("discovery-form"),
  startDiscoveryButton: document.getElementById("start-discovery"),
  sourceUrlButton: document.getElementById("source-url"),
  sourceCsvButton: document.getElementById("source-csv"),
  sourceMediaButton: document.getElementById("source-media"),
  urlSourceFields: document.getElementById("url-source-fields"),
  csvSourceFields: document.getElementById("csv-source-fields"),
  mediaSourceFields: document.getElementById("media-source-fields"),
  startUrl: document.getElementById("start-url"),
  scanMode: document.getElementById("scan-mode"),
  depthWrap: document.getElementById("depth-wrap"),
  depthLimit: document.getElementById("depth-limit"),
  scrapeCsvFile: document.getElementById("scrape-csv-file"),
  scrapeCsvStatus: document.getElementById("scrape-csv-status"),
  mediaUrl: document.getElementById("media-url"),
  mediaType: document.getElementById("media-type"),
  mediaFormat: document.getElementById("media-format"),
  mediaQuality: document.getElementById("media-quality"),
  mediaPlaylistLimit: document.getElementById("media-playlist-limit"),
  mediaSubdir: document.getElementById("media-subdir"),
  profileBadge: document.getElementById("profile-badge"),
  scrapeSaveDir: document.getElementById("scrape-save-dir"),
  discoveryStatus: document.getElementById("discovery-status"),
  discoverySummary: document.getElementById("discovery-summary"),
  selectionSearch: document.getElementById("selection-search"),
  filterKnownSize: document.getElementById("filter-known-size"),
  selectAllVisible: document.getElementById("select-all-visible"),
  clearAllVisible: document.getElementById("clear-all-visible"),
  selectionSummary: document.getElementById("selection-summary"),
  selectionGroups: document.getElementById("selection-groups"),
  toSettings: document.getElementById("to-settings"),
  downloadSettingsForm: document.getElementById("download-settings-form"),
  outputDir: document.getElementById("wizard-output-dir"),
  concurrency: document.getElementById("wizard-concurrency"),
  collisionStrategy: document.getElementById("wizard-collision-strategy"),
  settingsSelectionCount: document.getElementById("settings-selection-count"),
  jobSummary: document.getElementById("job-summary"),
  jobProgressBar: document.getElementById("job-progress-bar"),
  cancelJobButton: document.getElementById("cancel-job"),
  retryFailedJobButton: document.getElementById("retry-failed-job"),
  jobTableBody: document.querySelector("#job-table tbody"),
  jobLog: document.getElementById("job-log"),
  reviewSummary: document.getElementById("review-summary"),
  reviewManifest: document.getElementById("review-manifest"),
  reviewFailures: document.getElementById("review-failures"),
  reviewRetryFailed: document.getElementById("review-retry-failed"),
  historyList: document.getElementById("history-list"),
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

function setGlobalError(message = "") {
  if (!message) {
    els.globalError.classList.add("hidden");
    els.globalError.textContent = "";
    return;
  }
  els.globalError.classList.remove("hidden");
  els.globalError.textContent = message;
}

function setActiveStep(step) {
  state.activeStep = step;
  els.stepperItems.forEach((item) => {
    const itemStep = Number(item.dataset.step);
    item.classList.toggle("is-active", itemStep === step);
    item.classList.toggle("is-complete", itemStep < step);
  });
  els.stepPanels.forEach((panel) => {
    const panelStep = Number(panel.dataset.stepPanel);
    panel.classList.toggle("hidden", panelStep !== step);
  });
}

function detectProfileLabel(url) {
  const text = (url || "").toLowerCase();
  return text.includes("vimm.net") || text.includes("/vault") ? "Vimm" : "Generic";
}

function updateStartButtonLabel() {
  if (state.sourceType === "media") {
    els.startDiscoveryButton.textContent = "Start Media Download";
    return;
  }
  if (state.sourceType === "csv") {
    els.startDiscoveryButton.textContent = "Import Scrape CSV";
    return;
  }
  els.startDiscoveryButton.textContent = "Run Discovery";
}

function updateProfileBadge() {
  if (state.sourceType === "csv") {
    els.profileBadge.textContent = "Imported CSV";
    return;
  }
  if (state.sourceType === "media") {
    els.profileBadge.textContent = "yt-dlp";
    return;
  }
  els.profileBadge.textContent = detectProfileLabel(els.startUrl.value);
}

function updateDepthVisibility() {
  const isDeepDive = els.scanMode.value === "deep_dive";
  els.depthWrap.classList.toggle("hidden", !isDeepDive);
}

function setSelectOptions(selectEl, options, selectedValue) {
  selectEl.innerHTML = "";
  options.forEach((option) => {
    const node = document.createElement("option");
    node.value = option.value;
    node.textContent = option.label;
    if (option.value === selectedValue) {
      node.selected = true;
    }
    selectEl.appendChild(node);
  });
}

function syncMediaSelectors() {
  const mediaType = els.mediaType.value === "audio" ? "audio" : "video";
  const config = MEDIA_OPTIONS[mediaType];
  const currentFormat = els.mediaFormat.value;
  const currentQuality = els.mediaQuality.value;
  const defaultFormat = config.formats[0]?.value || "";
  const defaultQuality = config.qualities[0]?.value || "";
  const selectedFormat = config.formats.some((item) => item.value === currentFormat) ? currentFormat : defaultFormat;
  const selectedQuality = config.qualities.some((item) => item.value === currentQuality) ? currentQuality : defaultQuality;
  setSelectOptions(els.mediaFormat, config.formats, selectedFormat);
  setSelectOptions(els.mediaQuality, config.qualities, selectedQuality);
}

function setSourceType(sourceType) {
  state.sourceType = sourceType;
  const urlMode = sourceType === "url";
  const csvMode = sourceType === "csv";
  const mediaMode = sourceType === "media";
  els.urlSourceFields.classList.toggle("hidden", !urlMode);
  els.csvSourceFields.classList.toggle("hidden", !csvMode);
  els.mediaSourceFields.classList.toggle("hidden", !mediaMode);
  els.sourceUrlButton.classList.toggle("is-selected", urlMode);
  els.sourceUrlButton.classList.toggle("ghost", !urlMode);
  els.sourceCsvButton.classList.toggle("is-selected", csvMode);
  els.sourceCsvButton.classList.toggle("ghost", !csvMode);
  els.sourceMediaButton.classList.toggle("is-selected", mediaMode);
  els.sourceMediaButton.classList.toggle("ghost", !mediaMode);
  updateProfileBadge();
  updateStartButtonLabel();
}

function stopDiscoveryPolling() {
  if (state.discoveryPollTimer) {
    window.clearTimeout(state.discoveryPollTimer);
    state.discoveryPollTimer = null;
  }
}

function stopDownloadPolling() {
  if (state.downloadPollTimer) {
    window.clearTimeout(state.downloadPollTimer);
    state.downloadPollTimer = null;
  }
}

async function loadImportedCsv(file) {
  state.importedCsvFileName = file.name;
  state.importedCsvText = await file.text();
  els.scrapeCsvStatus.textContent = `Loaded ${file.name}.`;
  updateProfileBadge();
}

function renderDiscoveryStatus(job) {
  const summary = job?.summary || {};
  const lines = [];
  if (job?.status === "running") {
    lines.push(`<div class="warning-banner">Discovery is running for <strong>${escapeHtml(job.start_url)}</strong>.</div>`);
  }
  if (job?.status === "failed") {
    lines.push(`<div class="error-banner">${job.error || "Discovery failed."}</div>`);
  }
  if (summary.download_links_found !== undefined) {
    lines.push(`
      <div class="stat-strip">
        <span>${summary.pages_scanned || 0} pages scanned</span>
        <span>${summary.download_links_found || 0} downloads found</span>
        <span>${summary.inspected_count ?? 0}/${summary.candidate_count ?? summary.download_links_found ?? 0} inspected</span>
        <span>${escapeHtml(summary.total_known_human || "0 B")} known size</span>
        <span>Profile: ${escapeHtml(summary.detected_profile || job.profile)}</span>
      </div>
    `);
  }
  if (job?.logs?.length) {
    lines.push(`<pre class="log-box compact-log">${escapeHtml(job.logs.join("\n"))}</pre>`);
  }
  if (job?.artifacts?.csv_path || job?.artifacts?.directory) {
    lines.push(`
      <div class="stat-strip">
        <span>Archive folder: ${escapeHtml(job.artifacts.directory || "")}</span>
        <span>CSV: ${escapeHtml(job.artifacts.csv_path || "")}</span>
      </div>
    `);
  }
  els.discoveryStatus.innerHTML = lines.join("") || '<div class="muted">Run discovery to build the site map.</div>';
}

function renderDiscoverySummary(job) {
  const summary = job?.summary || {};
  const selectedCount = state.selectedRecordIds.size;
  els.discoverySummary.classList.remove("empty");
  els.discoverySummary.innerHTML = `
    <div class="summary-chip"><span>Status</span><strong>${job.status}</strong></div>
    <div class="summary-chip"><span>Pages scanned</span><strong>${summary.pages_scanned ?? 0}</strong></div>
    <div class="summary-chip"><span>Downloads found</span><strong>${summary.download_links_found ?? 0}</strong></div>
    <div class="summary-chip"><span>Inspected</span><strong>${summary.inspected_count ?? 0}/${summary.candidate_count ?? summary.download_links_found ?? 0}</strong></div>
    <div class="summary-chip"><span>Known size</span><strong>${summary.total_known_human || "0 B"}</strong></div>
    <div class="summary-chip"><span>Selected</span><strong>${selectedCount}</strong></div>
    <div class="summary-chip"><span>Profile</span><strong>${summary.detected_profile || job.profile}</strong></div>
    <div class="summary-chip"><span>Saved CSV</span><strong>${job.artifacts?.csv_path || "pending"}</strong></div>
  `;
}

function getFilteredRecords() {
  const records = state.discoveryJob?.records || [];
  const search = state.filters.search.trim().toLowerCase();
  return records.filter((record) => {
    if (state.filters.knownOnly && record.size_bytes == null) {
      return false;
    }
    if (!search) {
      return true;
    }
    const haystack = [
      record.filename,
      record.final_url,
      record.source_page,
      record.reason,
      record.content_type,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return haystack.includes(search);
  });
}

function getVisibleRecordIds() {
  return new Set(getFilteredRecords().map((record) => record.id));
}

function renderSelectionSummary() {
  const records = state.discoveryJob?.records || [];
  const discoveryComplete = state.discoveryJob?.status === "completed";
  if (!records.length) {
    els.selectionSummary.classList.add("empty");
    els.selectionSummary.innerHTML = "<span>No records discovered yet.</span>";
    els.toSettings.disabled = true;
    return;
  }
  const selectedRecords = records.filter((record) => state.selectedRecordIds.has(record.id));
  const selectedKnownBytes = selectedRecords.reduce((sum, record) => sum + (record.size_bytes || 0), 0);
  const visibleCount = getFilteredRecords().length;
  els.selectionSummary.classList.remove("empty");
  els.selectionSummary.innerHTML = `
    <span>${selectedRecords.length} selected</span>
    <span>${visibleCount} visible</span>
    <span>${humanSize(selectedKnownBytes)} selected known size</span>
    <span>${discoveryComplete ? "Selection ready" : "Streaming results, selection unlocks when crawl finishes"}</span>
  `;
  els.toSettings.disabled = !discoveryComplete || selectedRecords.length === 0;
  els.settingsSelectionCount.textContent = `${selectedRecords.length} selected`;
}

function pageGroupVisibleRecords(page) {
  const visibleIds = getVisibleRecordIds();
  const recordMap = new Map((state.discoveryJob?.records || []).map((record) => [record.id, record]));
  return page.record_ids
    .filter((recordId) => visibleIds.has(recordId))
    .map((recordId) => recordMap.get(recordId))
    .filter(Boolean);
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderSelectionGroups() {
  const job = state.discoveryJob;
  const discoveryComplete = job?.status === "completed";
  if (!job?.pages?.length) {
    els.selectionGroups.innerHTML = '<div class="warning-banner">No downloadable content was discovered for this URL.</div>';
    return;
  }

  const groupHtml = job.pages
    .map((page) => {
      const records = pageGroupVisibleRecords(page);
      if (!records.length) {
        return "";
      }
      const allChecked = records.every((record) => state.selectedRecordIds.has(record.id));
      const rows = records
        .map((record) => `
          <label class="record-row">
            <input type="checkbox" data-record-checkbox="${record.id}" ${state.selectedRecordIds.has(record.id) ? "checked" : ""} ${discoveryComplete ? "" : "disabled"} />
            <div class="record-main">
              <strong>${escapeHtml(record.filename || record.final_url)}</strong>
              <span>${escapeHtml(record.final_url)}</span>
            </div>
            <div class="record-meta">
              <span>${escapeHtml(record.method)}</span>
              <span>${escapeHtml(record.content_type || "unknown type")}</span>
              <span>${escapeHtml(record.size_human || "unknown")}</span>
              <span>${escapeHtml(record.reason || "discovered")}</span>
              <span>${escapeHtml(record.inspection_status === "pending" ? "Inspecting…" : "Ready")}</span>
            </div>
          </label>
        `)
        .join("");

      return `
        <section class="selection-group">
          <div class="selection-group-head">
            <label class="group-check">
              <input type="checkbox" data-page-checkbox="${escapeHtml(page.source_page)}" ${allChecked ? "checked" : ""} ${discoveryComplete ? "" : "disabled"} />
              <span>Select page</span>
            </label>
            <div>
              <strong>${escapeHtml(page.source_page)}</strong>
              <p>${page.item_count} item(s) • ${escapeHtml(page.known_total_human)}</p>
            </div>
          </div>
          <div class="selection-records">${rows}</div>
        </section>
      `;
    })
    .join("");

  els.selectionGroups.innerHTML = groupHtml || '<div class="warning-banner">No visible records match the current filters.</div>';
}

function renderReview(job) {
  const summary = job?.summary || {};
  const discoveryArtifacts = state.discoveryJob?.artifacts || {};
  const source = job?.source || {};
  const isMediaJob = job?.job_kind === "media";

  els.reviewSummary.classList.remove("empty");
  els.reviewSummary.innerHTML = `
    <div class="summary-chip"><span>Status</span><strong>${job.status}</strong></div>
    <div class="summary-chip"><span>Completed</span><strong>${summary.completed ?? 0}</strong></div>
    <div class="summary-chip"><span>Failed</span><strong>${summary.failed ?? 0}</strong></div>
    <div class="summary-chip"><span>Skipped</span><strong>${summary.skipped ?? 0}</strong></div>
    <div class="summary-chip"><span>Downloaded</span><strong>${summary.bytes_downloaded_human || "0 B"}</strong></div>
    <div class="summary-chip"><span>Output</span><strong>${job.output_dir}</strong></div>
  `;

  els.reviewManifest.classList.remove("empty");
  if (isMediaJob) {
    els.reviewManifest.innerHTML = `
      <span>Destination: ${escapeHtml(job.output_dir || "")}</span>
      <span>Manifest: ${escapeHtml(job.manifest_path || "Not written yet")}</span>
      <span>Media URL: ${escapeHtml(source.source_url || job.file_name || "")}</span>
      <span>Profile: ${escapeHtml(`${source.download_type || "video"} / ${source.format || "any"} / ${source.quality || "best"}`)}</span>
    `;
  } else {
    els.reviewManifest.innerHTML = `
      <span>Destination: ${escapeHtml(job.output_dir || "")}</span>
      <span>Manifest: ${escapeHtml(job.manifest_path || "Not written yet")}</span>
      <span>Scrape CSV: ${escapeHtml(discoveryArtifacts.csv_path || "Not available")}</span>
      <span>Scrape Folder: ${escapeHtml(discoveryArtifacts.directory || "Not available")}</span>
    `;
  }

  const failedRows = job.failed_rows || [];
  els.reviewRetryFailed.disabled = failedRows.length === 0;
  if (!failedRows.length) {
    els.reviewFailures.innerHTML = '<div class="warning-banner">No failed rows in this session.</div>';
    return;
  }
  els.reviewFailures.innerHTML = failedRows
    .map((row) => `
      <div class="error-banner review-failure">
        <strong>Row ${row.row_number}</strong>
        <div>${escapeHtml(row.message || "Failed")}</div>
        <div>${escapeHtml(row.source_page || row.download_url || "")}</div>
      </div>
    `)
    .join("");
}

function renderJob(job) {
  state.currentJob = job;
  const summary = job.summary || {};
  const percent = Math.round((summary.progress_fraction || 0) * 100);
  els.jobSummary.classList.remove("empty");
  els.jobSummary.innerHTML = `
    <div class="summary-chip"><span>Status</span><strong>${job.status}</strong></div>
    <div class="summary-chip"><span>Total rows</span><strong>${summary.total_rows ?? 0}</strong></div>
    <div class="summary-chip"><span>Completed</span><strong>${summary.completed ?? 0}</strong></div>
    <div class="summary-chip"><span>Failed</span><strong>${summary.failed ?? 0}</strong></div>
    <div class="summary-chip"><span>Skipped</span><strong>${summary.skipped ?? 0}</strong></div>
    <div class="summary-chip"><span>Downloaded</span><strong>${summary.bytes_downloaded_human || "0 B"}</strong></div>
    <div class="summary-chip"><span>Output</span><strong>${job.output_dir}</strong></div>
  `;
  els.jobProgressBar.style.width = `${percent}%`;

  els.jobTableBody.innerHTML = "";
  (job.recent_rows || []).forEach((row) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${row.row_number}</td>
      <td><span class="status-pill status-${row.status}">${row.status}</span></td>
      <td>${escapeHtml(row.message || "")}</td>
      <td>${escapeHtml(row.output_path || row.filename_hint || row.final_url || row.download_url || "")}</td>
    `;
    els.jobTableBody.appendChild(tr);
  });

  els.jobLog.textContent = (job.logs || []).join("\n") || "No log lines yet.";

  const active = summary.active || 0;
  const queued = summary.queued || 0;
  const jobActive = ["queued", "running"].includes(job.status) || active > 0 || queued > 0;
  els.cancelJobButton.disabled = !jobActive;
  els.retryFailedJobButton.disabled = jobActive || (summary.failed || 0) === 0;
  els.retryFailedJobButton.textContent = (summary.failed || 0) > 0
    ? `Retry Failed Rows (${summary.failed || 0})`
    : "Retry Failed Rows";
}

function renderHistory() {
  if (!state.historyJobs.length) {
    els.historyList.innerHTML = '<div class="muted">No persisted sessions yet.</div>';
    return;
  }
  els.historyList.innerHTML = state.historyJobs
    .map((job) => {
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
            <span>${escapeHtml(job.summary?.bytes_downloaded_human || "0 B")}</span>
            <span>${escapeHtml(job.output_dir || "")}</span>
          </div>
          <div class="actions">
            <button type="button" class="ghost" data-open-history="${job.job_id}">Open Session</button>
          </div>
        </section>
      `;
    })
    .join("");
}

function humanSize(value) {
  if (value == null) {
    return "unknown";
  }
  let size = Number(value);
  const units = ["B", "KB", "MB", "GB", "TB"];
  for (const unit of units) {
    if (size < 1024 || unit === units[units.length - 1]) {
      return unit === "B" ? `${Math.round(size)} B` : `${size.toFixed(2)} ${unit}`;
    }
    size /= 1024;
  }
  return `${value} B`;
}

async function loadHistory() {
  try {
    const payload = await requestJson("/api/history");
    state.historyJobs = payload.jobs || [];
    renderHistory();
  } catch (error) {
    els.historyList.innerHTML = `<div class="error-banner">${escapeHtml(error.message)}</div>`;
  }
}

async function pollDiscovery() {
  if (!state.discoveryJobId) {
    return;
  }
  try {
    const job = await requestJson(`/api/discovery-jobs/${state.discoveryJobId}`);
    state.discoveryJob = job;
    renderDiscoveryStatus(job);
    if ((job.records || []).length) {
      renderDiscoverySummary(job);
      renderSelectionSummary();
      renderSelectionGroups();
      if (state.activeStep < 2) {
        setActiveStep(2);
      }
    }
    if (job.status === "completed") {
      state.selectedRecordIds = new Set((job.records || []).map((record) => record.id));
      renderDiscoverySummary(job);
      renderSelectionSummary();
      renderSelectionGroups();
      setActiveStep(2);
      stopDiscoveryPolling();
      return;
    }
    if (job.status === "failed") {
      stopDiscoveryPolling();
      setGlobalError(job.error || "Discovery failed.");
      return;
    }
    state.discoveryPollTimer = window.setTimeout(pollDiscovery, 1000);
  } catch (error) {
    setGlobalError(error.message);
  }
}

async function pollDownloadJob() {
  if (!state.downloadJobId) {
    return;
  }
  try {
    const job = await requestJson(`/api/jobs/${state.downloadJobId}`);
    renderJob(job);
    const done = ["completed", "completed_with_errors", "cancelled"].includes(job.status);
    if (!done) {
      state.downloadPollTimer = window.setTimeout(pollDownloadJob, 1000);
      return;
    }
    stopDownloadPolling();
    renderReview(job);
    setActiveStep(5);
    await loadHistory();
  } catch (error) {
    setGlobalError(error.message);
  }
}

function syncSelectionFromPage(pageSource, checked) {
  const page = (state.discoveryJob?.pages || []).find((item) => item.source_page === pageSource);
  if (!page) {
    return;
  }
  const visibleIds = getVisibleRecordIds();
  page.record_ids
    .filter((recordId) => visibleIds.has(recordId))
    .forEach((recordId) => {
      if (checked) {
        state.selectedRecordIds.add(recordId);
      } else {
        state.selectedRecordIds.delete(recordId);
      }
    });
  renderSelectionSummary();
  renderSelectionGroups();
}

function syncSelectionFromVisible(checked) {
  getFilteredRecords().forEach((record) => {
    if (checked) {
      state.selectedRecordIds.add(record.id);
    } else {
      state.selectedRecordIds.delete(record.id);
    }
  });
  renderSelectionSummary();
  renderSelectionGroups();
}

async function startDiscovery(event) {
  event.preventDefault();
  setGlobalError("");
  stopDiscoveryPolling();

  try {
    if (state.sourceType === "csv") {
      if (!state.importedCsvText) {
        setGlobalError("Choose a scrape CSV first.");
        return;
      }
      const result = await requestJson("/api/discovery-imports", {
        method: "POST",
        body: JSON.stringify({
          file_name: state.importedCsvFileName,
          csv_text: state.importedCsvText,
        }),
      });
      state.discoveryJobId = result.job_id;
      const job = await requestJson(`/api/discovery-jobs/${result.job_id}`);
      state.discoveryJob = job;
      state.selectedRecordIds = new Set((job.records || []).map((record) => record.id));
      renderDiscoveryStatus(job);
      renderDiscoverySummary(job);
      renderSelectionSummary();
      renderSelectionGroups();
      setActiveStep(2);
      return;
    }

    if (state.sourceType === "media") {
      const sourceUrl = els.mediaUrl.value.trim();
      if (!sourceUrl) {
        setGlobalError("Enter a media URL first.");
        return;
      }
      const outputDir = els.outputDir.value.trim() || state.appInfo?.default_output_dir || "";
      window.sessionStorage.setItem(SESSION_OUTPUT_DIR_KEY, outputDir);
      const result = await requestJson("/api/media-jobs", {
        method: "POST",
        body: JSON.stringify({
          source_url: sourceUrl,
          options: {
            output_dir: outputDir,
            download_type: els.mediaType.value,
            format: els.mediaFormat.value,
            quality: els.mediaQuality.value,
            playlist_limit: Number(els.mediaPlaylistLimit.value || 0),
            subdir: els.mediaSubdir.value.trim(),
          },
        }),
      });
      state.downloadJobId = result.job_id;
      setActiveStep(4);
      stopDownloadPolling();
      pollDownloadJob();
      return;
    }

    const startUrl = els.startUrl.value.trim();
    if (!startUrl) {
      setGlobalError("Enter a start URL first.");
      return;
    }

    const payload = {
      start_url: startUrl,
      scan_mode: els.scanMode.value,
      depth_limit: Number(els.depthLimit.value || 2),
      profile: "auto",
    };
    const result = await requestJson("/api/discovery-jobs", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.discoveryJobId = result.job_id;
    state.discoveryJob = {
      job_id: result.job_id,
      status: result.status,
      start_url: startUrl,
      profile: result.profile,
      logs: [],
    };
    renderDiscoveryStatus(state.discoveryJob);
    setActiveStep(1);
    pollDiscovery();
  } catch (error) {
    setGlobalError(error.message);
  }
}

async function startDownloads(event) {
  event.preventDefault();
  setGlobalError("");
  if (!state.discoveryJobId || state.selectedRecordIds.size === 0) {
    setGlobalError("Pick at least one file before starting downloads.");
    return;
  }

  const outputDir = els.outputDir.value.trim() || state.appInfo?.default_output_dir || "";
  window.sessionStorage.setItem(SESSION_OUTPUT_DIR_KEY, outputDir);

  try {
    const result = await requestJson("/api/download-jobs/from-discovery", {
      method: "POST",
      body: JSON.stringify({
        discovery_job_id: state.discoveryJobId,
        selected_record_ids: Array.from(state.selectedRecordIds),
        options: {
          output_dir: outputDir,
          concurrency: Number(els.concurrency.value || 1),
          collision_strategy: els.collisionStrategy.value,
        },
      }),
    });
    state.downloadJobId = result.job_id;
    setActiveStep(4);
    stopDownloadPolling();
    pollDownloadJob();
  } catch (error) {
    setGlobalError(error.message);
  }
}

async function cancelJob() {
  if (!state.downloadJobId) {
    return;
  }
  try {
    await requestJson(`/api/jobs/${state.downloadJobId}/cancel`, {
      method: "POST",
      body: JSON.stringify({}),
    });
  } catch (error) {
    setGlobalError(error.message);
  }
}

async function retryFailedJob() {
  if (!state.downloadJobId || !state.currentJob) {
    return;
  }
  try {
    const result = await requestJson(`/api/jobs/${state.downloadJobId}/retry-failed`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    state.downloadJobId = result.job_id;
    setActiveStep(4);
    stopDownloadPolling();
    pollDownloadJob();
  } catch (error) {
    setGlobalError(error.message);
  }
}

async function openHistoryJob(jobId) {
  try {
    const job = await requestJson(`/api/jobs/${jobId}`);
    state.downloadJobId = jobId;
    renderJob(job);
    renderReview(job);
    const active = ["queued", "running"].includes(job.status) || (job.summary?.active || 0) > 0;
    if (active) {
      setActiveStep(4);
      stopDownloadPolling();
      pollDownloadJob();
      return;
    }
    setActiveStep(5);
  } catch (error) {
    setGlobalError(error.message);
  }
}

function restoreSessionOutputDir() {
  const sessionValue = window.sessionStorage.getItem(SESSION_OUTPUT_DIR_KEY);
  els.outputDir.value = sessionValue || state.appInfo?.default_output_dir || "";
}

function bindEvents() {
  els.discoveryForm.addEventListener("submit", startDiscovery);
  els.sourceUrlButton.addEventListener("click", () => setSourceType("url"));
  els.sourceCsvButton.addEventListener("click", () => setSourceType("csv"));
  els.sourceMediaButton.addEventListener("click", () => setSourceType("media"));
  els.scanMode.addEventListener("change", updateDepthVisibility);
  els.startUrl.addEventListener("input", updateProfileBadge);
  els.mediaUrl.addEventListener("input", updateProfileBadge);
  els.mediaType.addEventListener("change", syncMediaSelectors);
  els.scrapeCsvFile.addEventListener("change", async (event) => {
    const [file] = event.target.files || [];
    if (!file) {
      return;
    }
    try {
      await loadImportedCsv(file);
    } catch (error) {
      setGlobalError(error.message);
      els.scrapeCsvStatus.textContent = "Import failed.";
    }
  });

  els.selectionSearch.addEventListener("input", () => {
    state.filters.search = els.selectionSearch.value;
    renderSelectionSummary();
    renderSelectionGroups();
  });
  els.filterKnownSize.addEventListener("change", () => {
    state.filters.knownOnly = els.filterKnownSize.checked;
    renderSelectionSummary();
    renderSelectionGroups();
  });
  els.selectAllVisible.addEventListener("click", () => syncSelectionFromVisible(true));
  els.clearAllVisible.addEventListener("click", () => syncSelectionFromVisible(false));
  els.toSettings.addEventListener("click", () => {
    restoreSessionOutputDir();
    setActiveStep(3);
  });

  els.selectionGroups.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) {
      return;
    }
    if (target.dataset.recordCheckbox) {
      if (target.checked) {
        state.selectedRecordIds.add(target.dataset.recordCheckbox);
      } else {
        state.selectedRecordIds.delete(target.dataset.recordCheckbox);
      }
      renderSelectionSummary();
      renderSelectionGroups();
      return;
    }
    if (target.dataset.pageCheckbox) {
      syncSelectionFromPage(target.dataset.pageCheckbox, target.checked);
    }
  });

  els.downloadSettingsForm.addEventListener("submit", startDownloads);
  els.outputDir.addEventListener("input", () => {
    window.sessionStorage.setItem(SESSION_OUTPUT_DIR_KEY, els.outputDir.value.trim());
  });
  els.cancelJobButton.addEventListener("click", cancelJob);
  els.retryFailedJobButton.addEventListener("click", retryFailedJob);
  els.reviewRetryFailed.addEventListener("click", retryFailedJob);
  els.historyList.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    const button = target.closest("[data-open-history]");
    if (!(button instanceof HTMLElement)) {
      return;
    }
    openHistoryJob(button.dataset.openHistory);
  });
}

async function init() {
  setActiveStep(1);
  syncMediaSelectors();
  setSourceType("url");
  updateDepthVisibility();
  updateProfileBadge();
  bindEvents();
  try {
    state.appInfo = await requestJson("/api/app-info");
    els.scrapeSaveDir.textContent = state.appInfo.scrape_save_dir || "";
    restoreSessionOutputDir();
    if (!state.appInfo.yt_dlp_available) {
      els.sourceMediaButton.disabled = true;
      if (state.sourceType === "media") {
        setSourceType("url");
      }
    }
    await loadHistory();
  } catch (error) {
    setGlobalError(error.message);
  }
}

init();
