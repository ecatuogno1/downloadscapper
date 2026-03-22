const state = {
  csvText: "",
  fileName: "",
  preview: null,
  jobId: null,
  currentJob: null,
  poller: null,
  appInfo: null,
};

const els = {
  fileInput: document.getElementById("csv-file"),
  importStatus: document.getElementById("import-status"),
  previewWarnings: document.getElementById("preview-warnings"),
  previewMeta: document.getElementById("preview-meta"),
  previewHead: document.querySelector("#preview-table thead"),
  previewBody: document.querySelector("#preview-table tbody"),
  form: document.getElementById("job-form"),
  formError: document.getElementById("job-form-error"),
  startJobButton: document.getElementById("start-job"),
  cancelJobButton: document.getElementById("cancel-job"),
  retryFailedJobButton: document.getElementById("retry-failed-job"),
  mappingUrl: document.getElementById("mapping-url"),
  mappingFilename: document.getElementById("mapping-filename"),
  mappingMethod: document.getElementById("mapping-method"),
  mappingRequestData: document.getElementById("mapping-request-data"),
  mappingSubdir: document.getElementById("mapping-subdir"),
  mappingReferer: document.getElementById("mapping-referer"),
  outputDir: document.getElementById("output-dir"),
  concurrency: document.getElementById("concurrency"),
  collisionStrategy: document.getElementById("collision-strategy"),
  useSubdirectories: document.getElementById("use-subdirectories"),
  jobSummary: document.getElementById("job-summary"),
  jobProgressBar: document.getElementById("job-progress-bar"),
  jobTableBody: document.querySelector("#job-table tbody"),
  jobLog: document.getElementById("job-log"),
};

const mappingFields = [
  ["url", els.mappingUrl],
  ["filename", els.mappingFilename],
  ["method", els.mappingMethod],
  ["request_data", els.mappingRequestData],
  ["subdir", els.mappingSubdir],
  ["referer", els.mappingReferer],
];

function setFormError(message = "") {
  if (!message) {
    els.formError.classList.add("hidden");
    els.formError.textContent = "";
    return;
  }
  els.formError.classList.remove("hidden");
  els.formError.textContent = message;
}

function buildSelectOptions(selectEl, headers, selectedValue) {
  selectEl.innerHTML = "";
  const noneOption = document.createElement("option");
  noneOption.value = "";
  noneOption.textContent = "None";
  selectEl.appendChild(noneOption);

  headers.forEach((header) => {
    const option = document.createElement("option");
    option.value = header;
    option.textContent = header;
    if (header === selectedValue) {
      option.selected = true;
    }
    selectEl.appendChild(option);
  });
}

function renderWarnings(warnings) {
  els.previewWarnings.innerHTML = "";
  warnings.forEach((warning) => {
    const node = document.createElement("div");
    node.className = "warning-banner";
    node.textContent = warning;
    els.previewWarnings.appendChild(node);
  });
}

function renderPreview(preview) {
  const headers = preview.headers || [];
  const previewRows = preview.preview_rows || [];
  const rowCount = preview.row_count || 0;
  const validRows = preview.valid_url_rows || 0;

  els.previewMeta.classList.remove("empty");
  els.previewMeta.innerHTML = `
    <span><strong>${preview.file_name}</strong></span>
    <span>${rowCount} rows</span>
    <span>${validRows} rows with URLs</span>
    <span>${headers.length} columns</span>
  `;

  els.previewHead.innerHTML = "";
  const headRow = document.createElement("tr");
  headers.forEach((header) => {
    const th = document.createElement("th");
    th.textContent = header;
    headRow.appendChild(th);
  });
  els.previewHead.appendChild(headRow);

  els.previewBody.innerHTML = "";
  previewRows.forEach((row) => {
    const tr = document.createElement("tr");
    headers.forEach((header) => {
      const td = document.createElement("td");
      td.textContent = row[header] || "";
      tr.appendChild(td);
    });
    els.previewBody.appendChild(tr);
  });

  mappingFields.forEach(([key, selectEl]) => {
    buildSelectOptions(selectEl, headers, preview.mappings?.[key] || "");
  });
  renderWarnings(preview.warnings || []);
  els.startJobButton.disabled = false;
}

function renderIdlePreview() {
  els.previewMeta.classList.add("empty");
  els.previewMeta.innerHTML = "<span>Upload a CSV to inspect its columns.</span>";
  els.previewHead.innerHTML = "";
  els.previewBody.innerHTML = "";
  els.previewWarnings.innerHTML = "";
  mappingFields.forEach(([, selectEl]) => buildSelectOptions(selectEl, [], ""));
  els.startJobButton.disabled = true;
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
    <div class="summary-chip"><span>Invalid</span><strong>${summary.invalid ?? 0}</strong></div>
    <div class="summary-chip"><span>Downloaded</span><strong>${summary.bytes_downloaded_human || "0 B"}</strong></div>
    <div class="summary-chip"><span>Output</span><strong>${job.output_dir}</strong></div>
  `;
  els.jobProgressBar.style.width = `${percent}%`;
  els.jobProgressBar.classList.toggle("is-active", ["queued", "running"].includes(job.status));

  els.jobTableBody.innerHTML = "";
  (job.recent_rows || []).forEach((row) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${row.row_number}</td>
      <td><span class="status-pill status-${row.status}">${row.status}</span></td>
      <td>${row.message || ""}</td>
      <td>${row.output_path || row.filename_hint || row.final_url || row.download_url || ""}</td>
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

function resetJobPanel() {
  state.currentJob = null;
  els.jobSummary.classList.add("empty");
  els.jobSummary.innerHTML = emptyStateHtml(EMPTY_SVG.job, "No job running yet.");
  els.jobProgressBar.style.width = "0%";
  els.jobTableBody.innerHTML = "";
  els.jobLog.textContent = "Waiting for a job.";
  els.cancelJobButton.disabled = true;
  els.retryFailedJobButton.disabled = true;
  els.retryFailedJobButton.textContent = "Retry Failed Rows";
}

async function loadPreview(file) {
  setFormError("");
  validateFileUpload(file);
  els.importStatus.textContent = `Reading ${file.name}...`;
  state.fileName = file.name;
  state.csvText = await file.text();
  const preview = await requestJson("/api/preview", {
    method: "POST",
    body: JSON.stringify({
      file_name: file.name,
      csv_text: state.csvText,
    }),
  });
  state.preview = preview;
  els.importStatus.textContent = `Loaded ${file.name}.`;
  renderPreview(preview);
}

function collectMappings() {
  return {
    url: els.mappingUrl.value || null,
    filename: els.mappingFilename.value || null,
    method: els.mappingMethod.value || null,
    request_data: els.mappingRequestData.value || null,
    subdir: els.mappingSubdir.value || null,
    referer: els.mappingReferer.value || null,
  };
}

function stopPolling() {
  if (state.poller) {
    state.poller.stop();
    state.poller = null;
  }
}

function pollJob() {
  if (!state.jobId) return;
  stopPolling();
  state.poller = createPoller(
    () => requestJson(`/api/jobs/${state.jobId}`),
    {
      onData(job) {
        renderJob(job);
        const done = ["completed", "completed_with_errors", "cancelled"].includes(job.status);
        if (done) {
          showToast(job.status === "cancelled" ? "Job cancelled" : "Downloads finished", job.status === "cancelled" ? "warning" : "success");
        }
        return done;
      },
      onDone() { state.poller = null; },
      onError(err) { setFormError(err.message); },
    }
  );
}

async function startJob(event) {
  event.preventDefault();
  setFormError("");
  if (!state.csvText) {
    setFormError("Choose a CSV file first.");
    return;
  }
  const mappings = collectMappings();
  if (!mappings.url) {
    setFormError("Pick the URL column before starting the job.");
    return;
  }

  try {
    const result = await requestJson("/api/jobs", {
      method: "POST",
      body: JSON.stringify({
        file_name: state.fileName,
        csv_text: state.csvText,
        mappings,
        options: {
          output_dir: els.outputDir.value.trim(),
          concurrency: clampInt(els.concurrency.value, 1, 16, 4),
          collision_strategy: els.collisionStrategy.value,
          use_subdirectories: els.useSubdirectories.checked,
        },
      }),
    });
    state.jobId = result.job_id;
    resetJobPanel();
    stopPolling();
    pollJob();
  } catch (error) {
    setFormError(error.message);
  }
}

async function cancelJob() {
  if (!state.jobId) {
    return;
  }
  if (!(await confirmAction("Cancel Job", "This will stop all active downloads. Are you sure?"))) {
    return;
  }
  try {
    await requestJson(`/api/jobs/${state.jobId}/cancel`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    showToast("Job cancellation requested", "info");
  } catch (error) {
    setFormError(error.message);
  }
}

async function retryFailedJob() {
  if (!state.jobId || !state.currentJob) {
    return;
  }
  setFormError("");
  try {
    const result = await requestJson(`/api/jobs/${state.jobId}/retry-failed`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    state.jobId = result.job_id;
    resetJobPanel();
    stopPolling();
    pollJob();
  } catch (error) {
    setFormError(error.message);
    if (state.currentJob) {
      renderJob(state.currentJob);
    }
  }
}

async function init() {
  renderIdlePreview();
  resetJobPanel();
  try {
    state.appInfo = await requestJson("/api/app-info", { method: "GET" });
    els.outputDir.value = state.appInfo.default_output_dir || "";
  } catch (error) {
    setFormError(error.message);
  }

  els.fileInput.addEventListener("change", async (event) => {
    const [file] = event.target.files || [];
    if (!file) {
      return;
    }
    try {
      await loadPreview(file);
    } catch (error) {
      state.csvText = "";
      state.preview = null;
      renderIdlePreview();
      setFormError(error.message);
      els.importStatus.textContent = "Import failed.";
    }
  });

  els.form.addEventListener("submit", withLoading(els.startJobButton, startJob));
  els.cancelJobButton.addEventListener("click", withLoading(els.cancelJobButton, cancelJob));
  els.retryFailedJobButton.addEventListener("click", withLoading(els.retryFailedJobButton, retryFailedJob));

  enableDragDrop(document.querySelector(".file-picker"), els.fileInput);
}

init();
