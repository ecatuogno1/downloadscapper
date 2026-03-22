/* ── shared.js ── utilities shared across all DownloadScapper pages ── */

/* ─── request helper ─── */

async function requestJson(url, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30000);
  let response;
  try {
    response = await fetch(url, {
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
      ...options,
      signal: controller.signal,
    });
  } catch (err) {
    if (err.name === "AbortError") {
      throw new Error("Request timed out.");
    }
    throw new Error("Network error \u2014 is the server running?");
  } finally {
    clearTimeout(timeout);
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || "Request failed.");
  }
  return payload;
}

/* ─── html escaping ─── */

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

/* ─── dark mode ─── */

(function initTheme() {
  const btn = document.querySelector(".theme-toggle");
  if (!btn) return;

  function apply(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("downloadscapper.theme", theme);
    btn.setAttribute("aria-label", theme === "dark" ? "Switch to light mode" : "Switch to dark mode");
  }

  btn.addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme") || "light";
    apply(current === "dark" ? "light" : "dark");
  });

  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
    if (!localStorage.getItem("downloadscapper.theme")) {
      document.documentElement.setAttribute("data-theme", e.matches ? "dark" : "light");
    }
  });
})();

/* ─── toast notifications ─── */

const _toastContainer = document.createElement("div");
_toastContainer.className = "toast-container";
document.body.appendChild(_toastContainer);

function showToast(message, type = "info", duration = 4000) {
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  _toastContainer.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add("is-visible"));
  setTimeout(() => {
    toast.classList.remove("is-visible");
    toast.addEventListener("transitionend", () => toast.remove(), { once: true });
  }, duration);
}

/* ─── confirmation dialog ─── */

function confirmAction(title, message) {
  return new Promise((resolve) => {
    const backdrop = document.createElement("div");
    backdrop.className = "modal-backdrop";
    backdrop.innerHTML = `
      <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
        <h3 id="modal-title">${escapeHtml(title)}</h3>
        <p>${escapeHtml(message)}</p>
        <div class="modal-actions">
          <button class="ghost modal-cancel" type="button">Cancel</button>
          <button class="modal-confirm" type="button">Confirm</button>
        </div>
      </div>`;
    document.body.appendChild(backdrop);
    requestAnimationFrame(() => {
      backdrop.classList.add("is-visible");
      backdrop.querySelector(".modal-confirm").focus();
    });

    function close(result) {
      backdrop.classList.remove("is-visible");
      backdrop.addEventListener("transitionend", () => backdrop.remove(), { once: true });
      resolve(result);
    }
    backdrop.querySelector(".modal-cancel").addEventListener("click", () => close(false));
    backdrop.querySelector(".modal-confirm").addEventListener("click", () => close(true));
    backdrop.addEventListener("click", (e) => {
      if (e.target === backdrop) close(false);
    });
    backdrop.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        close(false);
        return;
      }
      if (e.key === "Tab") {
        const btns = backdrop.querySelectorAll("button");
        const first = btns[0];
        const last = btns[btns.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    });
  });
}

/* ─── button loading wrapper ─── */

function withLoading(button, asyncFn) {
  let running = false;
  return async function (...args) {
    if (running) return;
    running = true;
    button.classList.add("is-loading");
    button.disabled = true;
    try {
      return await asyncFn.apply(this, args);
    } finally {
      button.classList.remove("is-loading");
      button.disabled = false;
      running = false;
    }
  };
}

/* ─── resilient poller ─── */

function createPoller(fetchFn, { onData, onDone, onError, interval = 1000, maxBackoff = 15000 } = {}) {
  let timer = null;
  let backoff = interval;
  let hadError = false;
  let stopped = false;

  async function tick() {
    if (stopped) return;
    try {
      const data = await fetchFn();
      if (stopped) return;
      if (hadError) {
        showToast("Reconnected", "success");
        hadError = false;
      }
      backoff = interval;
      const terminal = onData(data);
      if (terminal) {
        stopped = true;
        if (onDone) onDone(data);
        return;
      }
    } catch (err) {
      if (stopped) return;
      if (!hadError) {
        showToast("Connection lost \u2014 retrying\u2026", "warning", 3000);
        hadError = true;
      }
      backoff = Math.min(backoff * 2, maxBackoff);
      if (onError) onError(err);
    }
    if (!stopped) {
      timer = setTimeout(tick, backoff);
    }
  }

  timer = setTimeout(tick, 0);

  return {
    stop() {
      stopped = true;
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    },
  };
}

/* ─── input validation helpers ─── */

function validateFileUpload(file, { maxSizeMB = 10, allowedExtensions = [".csv", ".tsv"] } = {}) {
  const ext = file.name.substring(file.name.lastIndexOf(".")).toLowerCase();
  if (!allowedExtensions.includes(ext)) {
    throw new Error(`Invalid file type "${ext}". Allowed: ${allowedExtensions.join(", ")}`);
  }
  const maxBytes = maxSizeMB * 1024 * 1024;
  if (file.size > maxBytes) {
    throw new Error(`File is too large (${(file.size / 1024 / 1024).toFixed(1)} MB). Maximum: ${maxSizeMB} MB.`);
  }
}

function validateHttpUrl(value, fieldName = "URL") {
  if (!value) {
    throw new Error(`${fieldName} is required.`);
  }
  if (!/^https?:\/\/.+/i.test(value)) {
    throw new Error(`${fieldName} must start with http:// or https://`);
  }
}

function clampInt(value, min, max, fallback) {
  const n = parseInt(value, 10);
  if (isNaN(n)) return fallback;
  return Math.max(min, Math.min(max, n));
}

/* ─── drag and drop for file pickers ─── */

function enableDragDrop(pickerEl, fileInput) {
  if (!pickerEl || !fileInput) return;
  ["dragenter", "dragover"].forEach((evt) => {
    pickerEl.addEventListener(evt, (e) => {
      e.preventDefault();
      pickerEl.classList.add("drag-over");
    });
  });
  ["dragleave", "drop"].forEach((evt) => {
    pickerEl.addEventListener(evt, () => {
      pickerEl.classList.remove("drag-over");
    });
  });
  pickerEl.addEventListener("drop", (e) => {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (file) {
      const dt = new DataTransfer();
      dt.items.add(file);
      fileInput.files = dt.files;
      fileInput.dispatchEvent(new Event("change", { bubbles: true }));
    }
  });
}

/* ─── keyboard shortcuts (wizard page only) ─── */

document.addEventListener("keydown", (e) => {
  if (typeof setActiveStep !== "function") return;
  if (e.target.matches("input, textarea, select")) return;
  if (document.querySelector(".modal-backdrop")) return;

  if (e.key === "Enter" && !e.metaKey && !e.ctrlKey) {
    const activePanel = document.querySelector(".wizard-step.is-visible");
    if (activePanel) {
      const btn = activePanel.querySelector("button[type='submit'], .actions button:not(.ghost):not(:disabled)");
      if (btn && !btn.disabled) btn.click();
    }
  }
  if (e.key === "Escape" && typeof state !== "undefined" && state.activeStep > 1) {
    setActiveStep(state.activeStep - 1);
  }
});

/* ─── empty state helper ─── */

function emptyStateHtml(svgContent, text) {
  return `<div class="empty-state"><svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${svgContent}</svg><p>${escapeHtml(text)}</p></div>`;
}

const EMPTY_SVG = {
  search: '<circle cx="26" cy="26" r="16"/><path d="M38 38l10 10"/><path d="M20 24h12M20 30h8"/>',
  download: '<path d="M32 8v32M20 28l12 12 12-12"/><path d="M10 48h44"/>',
  job: '<circle cx="32" cy="32" r="22" stroke-dasharray="6 4"/><path d="M28 26l10 6-10 6z"/>',
  folder: '<path d="M8 18v28a4 4 0 004 4h40a4 4 0 004-4V24a4 4 0 00-4-4H34l-4-6H12a4 4 0 00-4 4z"/><path d="M24 34h16" stroke-dasharray="4 3"/>',
  history: '<circle cx="32" cy="32" r="22"/><path d="M32 18v16l10 6"/>',
};
