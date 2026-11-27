/**
 * Common Global Module (static/js/modules/common.js)
 * =================================================
 * Global application UI components loaded across templates:
 * - Back to top smooth scrolling button
 * - Accessibility keyboard traps, skip link, and ARIA sync
 * - Inactive session expiration modal & keep-alive polling (#290)
 * - Recent analyses offcanvas drawer
 * - User logout interview drafts cleanup (#475)
 */

import { escapeHtml, initCsrfFetch } from "./utils.js";
import { initThemeToggle } from "./theme.js";
import { initChartAccessibility, toggleChartTableView } from "./chart_accessibility.js";
import { getMotionPreference, initMotionPreference, isReducedMotion, setMotionPreference } from "./motion.js";
import { initKeyboardShortcuts } from "./keyboard_shortcuts.js";

/**
 * Clear all interview draft answers from browser storage (#475).
 * Used upon user logout, session end, or account reset.
 */
export function clearAllInterviewDrafts() {
  try {
    const keysToRemove = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith("interview_draft_")) {
        keysToRemove.push(k);
      }
    }
    keysToRemove.forEach((k) => localStorage.removeItem(k));
  } catch (err) {
    console.warn("Unable to clear interview drafts from localStorage:", err);
  }
}

if (typeof window !== "undefined") {
  window.clearAllInterviewDrafts = clearAllInterviewDrafts;
}

export function initInterviewDraftLogoutCleanup() {
  if (document.cookie.includes("interview_drafts_cleared=1")) {
    clearAllInterviewDrafts();
    document.cookie = "interview_drafts_cleared=; Max-Age=0; path=/;";
  }
  document.addEventListener("click", (e) => {
    const logoutTarget = e.target.closest('a[href*="/logout"], form[action*="/logout"] button, #logoutLink, .btn-logout, [data-action="logout"]');
    if (logoutTarget) {
      clearAllInterviewDrafts();
    }
  });
  document.addEventListener("submit", (e) => {
    const formAction = e.target.getAttribute("action") || "";
    if (formAction.includes("/logout")) {
      clearAllInterviewDrafts();
    }
  });
  if (window.location.pathname.includes("/logout") || window.location.pathname.includes("/login")) {
    clearAllInterviewDrafts();
  }
}

export function initBackToTop() {
  let btn = document.getElementById("backToTopBtn");
  if (!btn) {
    btn = document.createElement("button");
    btn.type = "button";
    btn.id = "backToTopBtn";
    btn.className = "back-to-top-btn";
    btn.setAttribute("aria-label", "Back to top");
    btn.setAttribute("title", "Back to top");
    btn.innerHTML = '<i class="fa-solid fa-arrow-up"></i>';
    document.body.appendChild(btn);
  }

  const scrollThreshold = 300;
  let ticking = false;

  function updateBackToTopVisibility() {
    const currentScroll = window.scrollY || document.documentElement.scrollTop || document.body.scrollTop || 0;
    if (currentScroll > scrollThreshold) {
      btn.classList.add("show");
    } else {
      btn.classList.remove("show");
    }
    ticking = false;
  }

  window.addEventListener(
    "scroll",
    () => {
      if (!ticking) {
        window.requestAnimationFrame(updateBackToTopVisibility);
        ticking = true;
      }
    },
    { passive: true }
  );

  btn.addEventListener("click", (e) => {
    e.preventDefault();
    window.scrollTo({
      top: 0,
      behavior: isReducedMotion() ? "auto" : "smooth"
    });
    btn.blur();
  });

  updateBackToTopVisibility();
}

export function initAccessibility() {
  // 1. Skip-To-Main-Content Keyboard Link
  const skipLinks = document.querySelectorAll(".skip-link");
  skipLinks.forEach((skipLink) => {
    skipLink.addEventListener("click", (e) => {
      const targetId = skipLink.getAttribute("href");
      if (targetId && targetId.startsWith("#")) {
        const target = document.querySelector(targetId);
        if (target) {
          e.preventDefault();
          target.setAttribute("tabindex", "-1");
          target.focus();
        }
      }
    });
  });

  // 2. Custom Buttons & Interactive Roles (Enter / Space activation)
  document.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      const target = e.target;
      if (!target) return;
      const role = target.getAttribute("role");
      const isCustomButton = role === "button" || target.classList.contains("custom-btn");
      if (isCustomButton && target.tagName !== "BUTTON" && target.tagName !== "A" && target.tagName !== "INPUT") {
        e.preventDefault();
        target.click();
      }
    }
  });

  // 3. Accessible Resume File Upload Area
  const uploadArea = document.getElementById("uploadArea");
  const resumeInput = document.getElementById("resumeInput");
  if (uploadArea && resumeInput) {
    if (!uploadArea.hasAttribute("role")) {
      uploadArea.setAttribute("role", "button");
    }
    if (!uploadArea.hasAttribute("tabindex")) {
      uploadArea.setAttribute("tabindex", "0");
    }
    if (!uploadArea.hasAttribute("aria-label")) {
      uploadArea.setAttribute("aria-label", "Upload resume file in PDF or DOCX format. Drag and drop or press enter to browse files.");
    }
    uploadArea.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        if (e.target.closest("#btnRemoveFile")) return;
        e.preventDefault();
        resumeInput.click();
      }
    });
  }

  // 4. Modal Dialogs: Focus Preservation, Trapping, and Escape Handling
  const focusableSelector = 'input:not([type="hidden"]):not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]):not(.btn-close), .btn-close, a[href], [tabindex]:not([tabindex="-1"])';

  document.addEventListener("show.bs.modal", (e) => {
    const modal = e.target;
    if (modal) {
      modal._triggerElement = document.activeElement;
      modal.setAttribute("aria-modal", "true");
      modal.setAttribute("role", "dialog");
    }
  });

  document.addEventListener("shown.bs.modal", (e) => {
    const modal = e.target;
    if (modal) {
      const firstFocusable = modal.querySelector(focusableSelector);
      if (firstFocusable) {
        firstFocusable.focus();
      } else {
        modal.setAttribute("tabindex", "-1");
        modal.focus();
      }
    }
  });

  document.addEventListener("hidden.bs.modal", (e) => {
    const modal = e.target;
    if (modal && modal._triggerElement && typeof modal._triggerElement.focus === "function") {
      setTimeout(() => {
        try {
          modal._triggerElement.focus();
        } catch (_) {}
      }, 50);
    }
  });

  // 5. Offcanvas Drawers
  document.addEventListener("show.bs.offcanvas", (e) => {
    const offcanvas = e.target;
    if (offcanvas) {
      offcanvas._triggerElement = document.activeElement;
      offcanvas.setAttribute("aria-modal", "true");
      offcanvas.setAttribute("role", "dialog");
    }
  });

  document.addEventListener("shown.bs.offcanvas", (e) => {
    const offcanvas = e.target;
    if (offcanvas) {
      const firstFocusable = offcanvas.querySelector(focusableSelector);
      if (firstFocusable) {
        firstFocusable.focus();
      } else {
        offcanvas.setAttribute("tabindex", "-1");
        offcanvas.focus();
      }
    }
  });

  document.addEventListener("hidden.bs.offcanvas", (e) => {
    const offcanvas = e.target;
    if (offcanvas && offcanvas._triggerElement && typeof offcanvas._triggerElement.focus === "function") {
      setTimeout(() => {
        try {
          offcanvas._triggerElement.focus();
        } catch (_) {}
      }, 50);
    }
  });

  // 6. Global Escape Key & Focus Trap within Modals and Drawers
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" || e.keyCode === 27) {
      const openModal = document.querySelector(".modal.show");
      if (openModal) {
        e.preventDefault();
        const bsModal = (window.bootstrap && window.bootstrap.Modal) ? window.bootstrap.Modal.getInstance(openModal) : null;
        if (bsModal) {
          bsModal.hide();
        } else {
          const closeBtn = openModal.querySelector('[data-bs-dismiss="modal"]');
          if (closeBtn) closeBtn.click();
        }
        return;
      }

      const openOffcanvas = document.querySelector(".offcanvas.show");
      if (openOffcanvas) {
        e.preventDefault();
        const bsOffcanvas = (window.bootstrap && window.bootstrap.Offcanvas) ? window.bootstrap.Offcanvas.getInstance(openOffcanvas) : null;
        if (bsOffcanvas) {
          bsOffcanvas.hide();
        } else {
          const closeBtn = openOffcanvas.querySelector('[data-bs-dismiss="offcanvas"]');
          if (closeBtn) closeBtn.click();
        }
        return;
      }
    }

    if (e.key === "Tab") {
      const openModal = document.querySelector(".modal.show");
      if (openModal) {
        const rawFocusables = Array.from(openModal.querySelectorAll(focusableSelector));
        const focusables = rawFocusables.filter((el) => {
          return el.offsetParent !== null || el === document.activeElement;
        });

        if (focusables.length > 0) {
          const firstFocusable = focusables[0];
          const lastFocusable = focusables[focusables.length - 1];

          if (e.shiftKey) {
            if (document.activeElement === firstFocusable || !openModal.contains(document.activeElement)) {
              e.preventDefault();
              lastFocusable.focus();
            }
          } else {
            if (document.activeElement === lastFocusable || !openModal.contains(document.activeElement)) {
              e.preventDefault();
              firstFocusable.focus();
            }
          }
        }
      }
    }
  });

  // 7. Icon buttons aria labels
  const iconButtons = document.querySelectorAll("button");
  iconButtons.forEach((btn) => {
    if (btn.hasAttribute("title") && !btn.hasAttribute("aria-label")) {
      const title = btn.getAttribute("title").trim();
      if (title) {
        btn.setAttribute("aria-label", title);
      }
    }
    if (btn.classList.contains("btn-close") && !btn.hasAttribute("aria-label")) {
      btn.setAttribute("aria-label", "Close");
    }
  });

  // 8. Tablist sync
  const tabButtons = document.querySelectorAll('[role="tab"], [data-bs-toggle="tab"], [data-bs-toggle="pill"]');
  tabButtons.forEach((tab) => {
    tab.addEventListener("shown.bs.tab", (e) => {
      const activeTab = e.target;
      const prevTab = e.relatedTarget;
      if (activeTab) {
        activeTab.setAttribute("aria-selected", "true");
        activeTab.setAttribute("tabindex", "0");
      }
      if (prevTab) {
        prevTab.setAttribute("aria-selected", "false");
        prevTab.setAttribute("tabindex", "-1");
      }
    });
  });

  // 9. Accordion sync
  document.addEventListener("shown.bs.collapse", (e) => {
    const collapseEl = e.target;
    if (collapseEl && collapseEl.id) {
      const btn = document.querySelector(`[data-bs-target="#${collapseEl.id}"], [href="#${collapseEl.id}"]`);
      if (btn) btn.setAttribute("aria-expanded", "true");
    }
  });

  document.addEventListener("hidden.bs.collapse", (e) => {
    const collapseEl = e.target;
    if (collapseEl && collapseEl.id) {
      const btn = document.querySelector(`[data-bs-target="#${collapseEl.id}"], [href="#${collapseEl.id}"]`);
      if (btn) btn.setAttribute("aria-expanded", "false");
    }
  });

  // 10. Canvas and image accessibility
  const canvases = document.querySelectorAll("canvas");
  canvases.forEach((canvas) => {
    if (!canvas.hasAttribute("role")) {
      canvas.setAttribute("role", "img");
    }
    if (!canvas.hasAttribute("aria-label")) {
      const label = canvas.id ? `${canvas.id.replace(/([A-Z])/g, " $1").toLowerCase()} chart` : "Data visualization chart";
      canvas.setAttribute("aria-label", label);
    }
  });

  const images = document.querySelectorAll("img");
  images.forEach((img) => {
    if (!img.hasAttribute("alt")) {
      img.setAttribute("alt", "");
    }
  });
}

export function initRecentAnalysesDrawer() {
  const drawerEl = document.getElementById("offcanvasRecentAnalyses");
  const listEl = document.getElementById("recentAnalysesList");
  if (!drawerEl || !listEl) return;

  async function loadRecentAnalyses() {
    listEl.innerHTML = `
      <div class="text-center py-5 text-muted">
        <div class="spinner-border text-primary spinner-border-sm mb-2" role="status"></div>
        <p class="small mb-0">Loading recent evaluations...</p>
      </div>
    `;

    try {
      const response = await fetch("/api/analyses/recent?limit=10");
      if (!response.ok) throw new Error("Network response was not ok");
      const data = await response.json();

      if (!data.analyses || data.analyses.length === 0) {
        listEl.innerHTML = `
          <div class="text-center py-5 px-3">
            <div class="p-4 bg-light rounded-4 border mb-3">
              <i class="fa-solid fa-folder-open fa-3x text-secondary mb-3 d-block"></i>
              <h5 class="fw-bold text-dark mb-1">No Analysis History</h5>
              <p class="small text-secondary mb-3">You haven't analyzed any resumes yet. Upload a PDF or DOCX file on the homepage to generate your first placement readiness evaluation.</p>
              <a href="/" class="btn btn-sm btn-primary rounded-pill px-4">
                <i class="fa-solid fa-cloud-arrow-up me-1"></i> Start First Analysis
              </a>
            </div>
          </div>
        `;
        return;
      }

      listEl.innerHTML = "";
      data.analyses.forEach((a) => {
        const card = document.createElement("div");
        card.className = "card border rounded-3 p-3 shadow-sm bg-body";
        const dateStr = a.created_at ? new Date(a.created_at).toLocaleDateString() : "";

        card.innerHTML = `
          <div class="d-flex justify-content-between align-items-start mb-2">
            <div>
              <span class="badge bg-primary-subtle text-primary fw-bold me-1">${escapeHtml(a.company_name)}</span>
              <span class="badge bg-secondary-subtle text-secondary small">${dateStr}</span>
            </div>
            <button class="btn btn-outline-danger btn-sm border-0 rounded-circle delete-analysis-btn" data-id="${a.id}" title="Delete Analysis">
              <i class="fa-solid fa-trash-can"></i>
            </button>
          </div>
          <div class="small text-truncate fw-semibold mb-2" title="${escapeHtml(a.resume_filename)}">
            <i class="fa-solid fa-file-pdf text-danger me-1"></i> ${escapeHtml(a.resume_filename)}
          </div>
          <div class="d-flex align-items-center gap-2 mb-3 small">
            <span class="badge bg-success-subtle text-success">ATS: ${a.ats_score}%</span>
            <span class="badge bg-info-subtle text-info">Match: ${a.match_percentage}%</span>
          </div>
          <a href="/dashboard/${a.id}" class="btn btn-sm btn-outline-primary rounded-pill w-100">
            <i class="fa-solid fa-chart-pie me-1"></i> View Report
          </a>
        `;
        listEl.appendChild(card);
      });

      listEl.querySelectorAll(".delete-analysis-btn").forEach((btn) => {
        btn.addEventListener("click", async (e) => {
          e.stopPropagation();
          const analysisId = btn.getAttribute("data-id");
          if (!confirm("Are you sure you want to delete this placement analysis?")) return;

          try {
            const delRes = await fetch(`/api/analysis/${analysisId}`, { method: "DELETE" });
            if (delRes.ok) {
              loadRecentAnalyses();
            } else {
              alert("Failed to delete the analysis record.");
            }
          } catch (err) {
            console.error("Error deleting analysis:", err);
            alert("Error deleting analysis.");
          }
        });
      });
    } catch (err) {
      console.warn("Could not load recent analyses:", err);
      listEl.innerHTML = `
        <div class="text-center py-5 px-3">
          <div class="p-4 bg-light rounded-4 border">
            <i class="fa-solid fa-triangle-exclamation fa-2x text-warning mb-2 d-block"></i>
            <h6 class="fw-bold mb-1">Service Unavailable</h6>
            <p class="small text-muted mb-3">Unable to retrieve recent sessions at this time.</p>
            <button type="button" class="btn btn-sm btn-outline-primary rounded-pill px-3" onclick="location.reload()">
              <i class="fa-solid fa-rotate-right me-1"></i> Refresh
            </button>
          </div>
        </div>
      `;
    }
  }

  drawerEl.addEventListener("show.bs.offcanvas", loadRecentAnalyses);
}

export function initSessionTimeout() {
  if (window._sessionTimeoutInitialized) return;
  window._sessionTimeoutInitialized = true;

  let timeoutSeconds = 1800;
  let warningSeconds = 120;
  let remainingSeconds = 1800;
  let countdownTimer = null;
  let isModalVisible = false;
  let lastKeepAlivePing = Date.now();

  function formatTime(secs) {
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    return `${m < 10 ? "0" : ""}${m}:${s < 10 ? "0" : ""}${s}`;
  }

  function ensureModal() {
    let modalEl = document.getElementById("sessionExpiryModal");
    if (!modalEl) {
      modalEl = document.createElement("div");
      modalEl.id = "sessionExpiryModal";
      modalEl.className = "modal fade";
      modalEl.setAttribute("tabindex", "-1");
      modalEl.setAttribute("role", "dialog");
      modalEl.setAttribute("aria-modal", "true");
      modalEl.setAttribute("aria-labelledby", "sessionExpiryModalLabel");
      modalEl.setAttribute("aria-describedby", "sessionExpiryModalDesc");
      modalEl.setAttribute("data-bs-backdrop", "static");
      modalEl.setAttribute("data-bs-keyboard", "false");

      modalEl.innerHTML = `
        <div class="modal-dialog modal-dialog-centered">
          <div class="modal-content shadow-lg border-warning">
            <div class="modal-header bg-warning bg-opacity-10 border-warning">
              <h5 class="modal-title text-warning-emphasis d-flex align-items-center" id="sessionExpiryModalLabel">
                <i class="fa-solid fa-triangle-exclamation me-2 text-warning fs-4" aria-hidden="true"></i>
                Session Expiration Warning
              </h5>
              <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close" id="btnSessionDismiss"></button>
            </div>
            <div class="modal-body py-4">
              <p id="sessionExpiryModalDesc" class="mb-3 text-body">
                You have been inactive for a while. For your security, your session will automatically expire in:
              </p>
              <div class="text-center my-3">
                <div class="display-6 fw-bold text-danger session-countdown-timer" id="sessionCountdownDisplay" aria-live="polite">
                  02:00
                </div>
                <p class="text-muted small mt-2">Would you like to stay logged in or log out now?</p>
              </div>
            </div>
            <div class="modal-footer bg-light bg-opacity-50 justify-content-between">
              <a href="/logout?expired=0" class="btn btn-outline-secondary" id="btnSessionLogout">
                <i class="fa-solid fa-right-from-bracket me-1" aria-hidden="true"></i> Log Out Now
              </a>
              <button type="button" class="btn btn-primary fw-semibold" id="btnExtendSession">
                <i class="fa-solid fa-rotate me-1" aria-hidden="true"></i> Stay Logged In
              </button>
            </div>
          </div>
        </div>
      `;
      document.body.appendChild(modalEl);

      const btnExtend = modalEl.querySelector("#btnExtendSession");
      if (btnExtend) {
        btnExtend.addEventListener("click", () => extendSession());
      }

      const btnDismiss = modalEl.querySelector("#btnSessionDismiss");
      if (btnDismiss) {
        btnDismiss.addEventListener("click", () => extendSession());
      }
    }
    return modalEl;
  }

  function showWarningModal() {
    if (isModalVisible) return;
    isModalVisible = true;
    const modalEl = ensureModal();

    if (window.bootstrap && bootstrap.Modal) {
      const bsModal = bootstrap.Modal.getOrCreateInstance(modalEl);
      bsModal.show();
    } else {
      modalEl.classList.add("show");
      modalEl.style.display = "block";
      document.body.classList.add("modal-open");
    }

    const btnExtend = modalEl.querySelector("#btnExtendSession");
    if (btnExtend) {
      setTimeout(() => btnExtend.focus(), 150);
    }
  }

  function hideWarningModal() {
    if (!isModalVisible) return;
    isModalVisible = false;
    const modalEl = document.getElementById("sessionExpiryModal");
    if (!modalEl) return;

    if (window.bootstrap && bootstrap.Modal) {
      const bsModal = bootstrap.Modal.getInstance(modalEl);
      if (bsModal) bsModal.hide();
    } else {
      modalEl.classList.remove("show");
      modalEl.style.display = "none";
      document.body.classList.remove("modal-open");
    }
  }

  function updateCountdownDisplay() {
    const displayEl = document.getElementById("sessionCountdownDisplay");
    if (displayEl) {
      displayEl.textContent = formatTime(Math.max(0, remainingSeconds));
      if (remainingSeconds <= 30) {
        displayEl.classList.add("text-danger");
      }
    }
  }

  function handleExpired() {
    if (countdownTimer) clearInterval(countdownTimer);
    window.location.href = "/logout?expired=1";
  }

  function extendSession() {
    fetch("/api/session/keep-alive", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
    })
      .then((res) => {
        if (!res.ok) throw new Error("Session expired");
        return res.json();
      })
      .then((data) => {
        if (data.status === "ok") {
          remainingSeconds = data.remaining_seconds || timeoutSeconds;
          lastKeepAlivePing = Date.now();
          hideWarningModal();
        } else {
          handleExpired();
        }
      })
      .catch(() => handleExpired());
  }

  fetch("/api/session/status", {
    headers: { "X-Requested-With": "XMLHttpRequest" }
  })
    .then((res) => res.json())
    .then((data) => {
      if (!data.authenticated) return;

      timeoutSeconds = data.timeout_seconds || 1800;
      warningSeconds = data.warning_seconds || 120;
      remainingSeconds = data.remaining_seconds || timeoutSeconds;

      ensureModal();

      countdownTimer = setInterval(() => {
        remainingSeconds -= 1;
        if (remainingSeconds <= warningSeconds && remainingSeconds > 0) {
          showWarningModal();
          updateCountdownDisplay();
        } else if (remainingSeconds <= 0) {
          updateCountdownDisplay();
          handleExpired();
        }
      }, 1000);

      const activityEvents = ["click", "keydown", "submit"];
      const onUserActivity = () => {
        if (isModalVisible) return;
        const now = Date.now();
        if (now - lastKeepAlivePing >= 60000) {
          lastKeepAlivePing = now;
          fetch("/api/session/keep-alive", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-Requested-With": "XMLHttpRequest",
            },
          })
            .then((r) => r.ok && r.json())
            .then((resData) => {
              if (resData && resData.status === "ok") {
                remainingSeconds = resData.remaining_seconds || timeoutSeconds;
              }
            })
            .catch(() => {});
        }
      };

      activityEvents.forEach((ev) => {
        window.addEventListener(ev, onUserActivity, { passive: true });
      });
    })
    .catch(() => {});
}

export function initCommon() {
  initCsrfFetch();
  initThemeToggle();
  initMotionPreference();
  initBackToTop();
  initAccessibility();
  initChartAccessibility();
  initRecentAnalysesDrawer();
  initSessionTimeout();
  initInterviewDraftLogoutCleanup();
  initKeyboardShortcuts();
}

export { initMotionPreference, isReducedMotion, getMotionPreference, setMotionPreference };

if (typeof document !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initCommon);
  } else {
    initCommon();
  }
}
