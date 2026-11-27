/**
 * Dashboard Module (static/js/modules/dashboard.js)
 * =================================================
 * Dashboard visualizations, skill gap filtering, 6-week roadmap trackers & exports,
 * version diffs, role preparation switchers, resume keyword highlighters,
 * section feedback, and interactive walkthrough tours.
 */

import { escapeHtml, showToast, getCsrfToken, copyToClipboard } from "./utils.js";
import { OKABE_ITO, createAccessiblePattern, getChartThemeTokens } from "./chart_palette.js";
import { isReducedMotion } from "./motion.js";

export function initSkillGapFilter() {
  const searchInput = document.getElementById("skillSearchInput");
  const priorityFilter = document.getElementById("skillPriorityFilter");
  const table = document.getElementById("skillGapTable");
  const countBadge = document.getElementById("skillFilterCount");
  const emptyState = document.getElementById("skillGapEmptyState");

  if (!table || !searchInput || !priorityFilter) return;

  function applyFilter() {
    const rows = table.querySelectorAll("tbody tr.skill-gap-row");
    const totalRows = rows.length;
    const q = searchInput.value.toLowerCase().trim();
    const p = priorityFilter.value;
    let visibleCount = 0;

    rows.forEach((row) => {
      const skillName = (row.getAttribute("data-skill") || "").toLowerCase();
      const priority = (row.getAttribute("data-priority") || "").toLowerCase();
      const fullText = row.innerText.toLowerCase();

      const matchesSearch = !q || skillName.includes(q) || fullText.includes(q);
      const matchesPriority = p === "ALL" || priority === p.toLowerCase();

      if (matchesSearch && matchesPriority) {
        row.style.display = "";
        visibleCount++;
      } else {
        row.style.display = "none";
      }
    });

    if (emptyState) {
      if (visibleCount === 0 && totalRows > 0) {
        emptyState.classList.remove("d-none");
      } else {
        emptyState.classList.add("d-none");
      }
    }

    if (countBadge) {
      if (q || p !== "ALL") {
        countBadge.innerText = `Showing ${visibleCount} of ${totalRows} Skills`;
      } else {
        countBadge.innerText = `${totalRows} Skills`;
      }
    }
  }

  // Bind listener safely
  searchInput.oninput = applyFilter;
  priorityFilter.onchange = applyFilter;

  searchInput.onkeydown = (e) => {
    if (e.key === "Escape" && searchInput.value) {
      searchInput.value = "";
      applyFilter();
    }
  };
}

/**
 * Role-targeted Skill Gap Selector (#495)
 * Dynamically switches benchmark target role without full page reload,
 * updating role match score, readiness verdict, matched competencies,
 * and missing skills with their suggested topics to learn.
 */


export function initRoleSkillGapSelector() {
  const roleSelector = document.getElementById("roleGapSelector");
  if (!roleSelector) return;

  const container = document.getElementById("dashboardContainer");
  const analysisId = (container && container.getAttribute("data-analysis-id")) ||
    (window.location.pathname.split("/")[2] || "");

  roleSelector.addEventListener("change", async function () {
    const selectedRoleId = this.value;
    if (!selectedRoleId) return;

    // Elements to update
    const scoreBadge = document.getElementById("roleMatchScore");
    const readinessBadge = document.getElementById("roleReadinessBadge");
    const matchedRatio = document.getElementById("roleMatchedRatio");
    const descText = document.getElementById("roleBenchmarkDesc");
    const matchedCount = document.getElementById("roleMatchedSkillsCount");
    const matchedList = document.getElementById("roleMatchedSkillsList");
    const tableBody = document.querySelector("#skillGapTable tbody");
    const countBadge = document.getElementById("skillFilterCount");

    if (scoreBadge) {
      scoreBadge.innerHTML = '<span class="spinner-border spinner-border-sm text-primary" role="status"></span>';
    }

    try {
      let data = null;
      if (analysisId) {
        const resp = await fetch(`/api/analysis/${encodeURIComponent(analysisId)}/role-skill-gap?role=${encodeURIComponent(selectedRoleId)}`);
        if (resp.ok) {
          data = await resp.json();
        }
      }
      if (!data) {
        // Fallback to general analyze endpoint
        const resp = await fetch("/api/skill-gap/analyze", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role_id: selectedRoleId, analysis_id: analysisId }),
        });
        if (resp.ok) {
          data = await resp.json();
        }
      }

      if (!data || (!data.success && data.status !== "success")) {
        throw new Error((data && data.error) ? data.error : "Failed to load role skill gap data");
      }

      const roleData = data.data || data;

      // 1. Update Match Score
      if (scoreBadge) {
        scoreBadge.textContent = `${roleData.match_percentage}%`;
      }

      // 2. Update Readiness Badge
      if (readinessBadge) {
        readinessBadge.textContent = roleData.readiness_verdict || "Targeted Prep";
        readinessBadge.className = "badge border " + (
          roleData.match_percentage >= 80 ? "bg-success-subtle text-success" :
          (roleData.match_percentage >= 55 ? "bg-primary-subtle text-primary" : "bg-warning-subtle text-warning")
        );
      }

      // 3. Update Ratio & Description
      if (matchedRatio) {
        matchedRatio.textContent = `${(roleData.matched_skills || []).length} / ${(roleData.total_required || 0) + (roleData.total_nice_to_have || 0)}`;
      }
      if (descText && roleData.role_description) {
        descText.textContent = roleData.role_description;
      }

      // 4. Update Matched Skills List
      if (matchedList && roleData.matched_skills) {
        if (matchedCount) matchedCount.textContent = roleData.matched_skills.length;
        if (roleData.matched_skills.length === 0) {
          matchedList.innerHTML = '<span class="text-muted small fst-italic">No matched competencies detected for this role yet.</span>';
        } else {
          matchedList.innerHTML = roleData.matched_skills.map((m) => {
            return `<span class="badge bg-success-subtle text-success border border-success-subtle rounded-pill px-3 py-1 small">
              <i class="fa-solid fa-check small me-1"></i>${escapeHtml(m.skill)}
              <span class="opacity-75 ms-1 fw-normal" style="font-size: 0.75rem;">(${escapeHtml(m.type || "Required")})</span>
            </span>`;
          }).join(" ");
        }
      }

      // 5. Update Missing Skills Table Rows with Suggested Topics
      if (tableBody && roleData.missing_skills) {
        const missing = roleData.missing_skills;
        if (countBadge) {
          countBadge.textContent = `${missing.length} Skills`;
        }

        if (missing.length === 0) {
          tableBody.innerHTML = `
            <tr id="skillGapNoItemsInitial">
              <td colspan="6" class="text-center py-5">
                <div class="empty-state-container p-4">
                  <i class="fa-solid fa-circle-check fa-3x text-success mb-3 d-block"></i>
                  <h5 class="fw-bold text-dark mb-1">No Skill Gaps Identified</h5>
                  <p class="text-secondary small mb-3">Your resume matches all required competencies for this target role! Explore the interview prep or practice DSA coding problems below.</p>
                </div>
              </td>
            </tr>
          `;
        } else {
          const rowsHtml = missing.map((gap) => {
            const reqBadge = gap.is_required
              ? '<span class="badge bg-danger-subtle text-danger border border-danger-subtle ms-1" style="font-size: 0.7rem;">Required</span>'
              : '<span class="badge bg-secondary-subtle text-secondary border ms-1" style="font-size: 0.7rem;">Nice-to-Have</span>';

            const topicsHtml = Array.isArray(gap.suggested_topics) && gap.suggested_topics.length > 0
              ? `<div class="small fw-semibold text-dark mb-1"><i class="fa-solid fa-graduation-cap text-primary me-1"></i> Suggested Topics to Learn:</div>
                 <ul class="mb-0 ps-3 small text-secondary">
                   ${gap.suggested_topics.map(t => `<li class="mb-1">${escapeHtml(t)}</li>`).join("")}
                 </ul>`
              : `<span class="text-secondary small">${escapeHtml(gap.learning_resource || "Core technical documentation & practice")}</span>`;

            const projectSuggestion = gap.project_suggestion || "";
            const copyBtn = projectSuggestion
              ? `<button type="button" class="btn btn-sm btn-outline-secondary rounded-pill py-0 px-2 flex-shrink-0 btn-copy-suggestion" data-copy-text="${escapeHtml(projectSuggestion)}" title="Copy mini project suggestion" aria-label="Copy mini project suggestion">
                   <i class="fa-regular fa-copy small"></i>
                 </button>`
              : "";

            return `
              <tr class="skill-gap-row" data-skill="${escapeHtml((gap.skill || "").toLowerCase())}" data-priority="${escapeHtml(gap.priority || "Medium")}">
                <td>
                  <b class="text-primary">${escapeHtml(gap.skill)}</b>
                  ${reqBadge}
                </td>
                <td><span class="badge bg-light text-dark border">${escapeHtml(gap.importance || "High")}</span></td>
                <td>${escapeHtml(gap.learning_time || "2-3 weeks")}</td>
                <td>${topicsHtml}</td>
                <td>
                  <div class="d-flex align-items-center justify-content-between gap-1">
                    <span class="small text-muted">${escapeHtml(projectSuggestion)}</span>
                    ${copyBtn}
                  </div>
                </td>
                <td>
                  <span class="badge badge-pill-priority priority-${escapeHtml((gap.priority || "medium").toLowerCase())}">
                    ${escapeHtml(gap.priority || "Medium")}
                  </span>
                </td>
              </tr>
            `;
          }).join("");

          tableBody.innerHTML = rowsHtml + `
            <tr id="skillGapEmptyState" class="d-none">
              <td colspan="6" class="text-center py-5">
                <div class="empty-state-container p-4">
                  <i class="fa-solid fa-filter-circle-xmark fa-3x mb-3 text-secondary d-block"></i>
                  <h5 class="fw-bold text-dark mb-1">No Matching Skills Found</h5>
                  <p class="text-secondary small mb-3">No identified missing skills matched your current keyword search or priority filter.</p>
                </div>
              </td>
            </tr>
          `;
        }

        // Re-attach filter listeners
        initSkillGapFilter();
      }

      // Update URL query param without full page reload
      const newUrl = new URL(window.location.href);
      newUrl.searchParams.set("role", selectedRoleId);
      window.history.replaceState({}, "", newUrl.toString());

    } catch (err) {
      console.error("Error switching target role benchmark:", err);
      if (scoreBadge) scoreBadge.textContent = "Error";
    }
  });
}

/**
 * Tracks interactive checklist tasks on the 6-Week Learning Roadmap
 * and persists completed task IDs in localStorage per analysis session.
 * Displays overall Preparation Progress Overview with total, completed, pending, and percentage.
 */


export function initRoadmapTracker() {
  const container = document.getElementById("dashboardContainer");
  if (!container) return;

  const analysisId = container.getAttribute("data-analysis-id");
  if (!analysisId) return;

  const storageKey = `roadmap_tasks_${analysisId}`;
  let completedTasks = new Set();
  try {
    const saved = localStorage.getItem(storageKey);
    if (saved) {
      completedTasks = new Set(JSON.parse(saved));
    }
  } catch (e) {
    console.warn("Could not load roadmap progress from localStorage:", e);
  }

  const allCheckboxes = document.querySelectorAll(".roadmap-task-check");
  const weekCards = document.querySelectorAll(".week-card");
  const overallLabel = document.getElementById("overallRoadmapProgressLabel");
  const overallBar = document.getElementById("overallRoadmapProgressBar");
  const totalTasksEl = document.getElementById("totalRoadmapTasks");
  const completedTasksEl = document.getElementById("completedRoadmapTasks");
  const pendingTasksEl = document.getElementById("pendingRoadmapTasks");
  const completionPercentageEl = document.getElementById("roadmapCompletionPercentage");
  const completedInline = document.getElementById("completedTasksInline");
  const totalInline = document.getElementById("totalTasksInline");
  const percentInline = document.getElementById("progressPercentInline");
  const statusBadge = document.getElementById("roadmapProgressStatusBadge");
  const btnReset = document.getElementById("btnResetRoadmapProgress");

  function updateVisuals() {
    const totalAll = allCheckboxes.length;
    let checkedAll = 0;

    weekCards.forEach((card) => {
      const checksInWeek = card.querySelectorAll(".roadmap-task-check");
      let checkedInWeek = 0;
      checksInWeek.forEach((cb) => {
        if (cb.checked) checkedInWeek++;
      });

      const pct = checksInWeek.length > 0 ? Math.round((checkedInWeek / checksInWeek.length) * 100) : 0;
      const bar = card.querySelector(".week-progress-bar");
      const label = card.querySelector(".week-progress-label");
      if (bar) bar.style.width = `${pct}%`;
      if (label) label.innerText = `${pct}%`;

      checkedAll += checkedInWeek;
    });

    const pendingAll = Math.max(0, totalAll - checkedAll);
    const overallPct = totalAll > 0 ? Math.round((checkedAll / totalAll) * 100) : 0;

    if (totalTasksEl) totalTasksEl.innerText = totalAll;
    if (completedTasksEl) completedTasksEl.innerText = checkedAll;
    if (pendingTasksEl) pendingTasksEl.innerText = pendingAll;

    if (overallLabel) overallLabel.innerText = `${overallPct}%`;
    if (completionPercentageEl) completionPercentageEl.innerText = `${overallPct}% Completed`;

    if (overallBar) {
      overallBar.style.width = `${overallPct}%`;
      overallBar.setAttribute("aria-valuenow", overallPct);
    }

    if (completedInline) completedInline.innerText = checkedAll;
    if (totalInline) totalInline.innerText = totalAll;
    if (percentInline) percentInline.innerText = `${overallPct}%`;

    if (statusBadge) {
      if (totalAll === 0) {
        statusBadge.className = "badge bg-secondary-subtle text-secondary border border-secondary-subtle px-3 py-2 rounded-pill fw-bold";
        statusBadge.innerHTML = '<i class="fa-solid fa-circle-info me-1"></i> No Tasks';
      } else if (overallPct === 100) {
        statusBadge.className = "badge bg-success-subtle text-success border border-success-subtle px-3 py-2 rounded-pill fw-bold";
        statusBadge.innerHTML = '<i class="fa-solid fa-circle-check me-1"></i> Completed (100%)';
      } else if (overallPct >= 50) {
        statusBadge.className = "badge bg-primary-subtle text-primary border border-primary-subtle px-3 py-2 rounded-pill fw-bold";
        statusBadge.innerHTML = `<i class="fa-solid fa-chart-line me-1"></i> Over Halfway (${overallPct}%)`;
      } else if (overallPct > 0) {
        statusBadge.className = "badge bg-info-subtle text-info border border-info-subtle px-3 py-2 rounded-pill fw-bold";
        statusBadge.innerHTML = `<i class="fa-solid fa-play me-1"></i> In Progress (${overallPct}%)`;
      } else {
        statusBadge.className = "badge bg-secondary-subtle text-secondary border border-secondary-subtle px-3 py-2 rounded-pill fw-bold";
        statusBadge.innerHTML = '<i class="fa-solid fa-clock me-1"></i> Not Started (0%)';
      }
    }
  }

  allCheckboxes.forEach((cb) => {
    const taskId = cb.getAttribute("data-task-id");
    if (taskId && completedTasks.has(taskId)) {
      cb.checked = true;
      const span = cb.parentElement?.querySelector("span");
      if (span) span.style.textDecoration = "line-through";
    }

    cb.addEventListener("change", () => {
      const span = cb.parentElement?.querySelector("span");
      if (cb.checked) {
        if (taskId) completedTasks.add(taskId);
        if (span) span.style.textDecoration = "line-through";
      } else {
        if (taskId) completedTasks.delete(taskId);
        if (span) span.style.textDecoration = "none";
      }

      try {
        localStorage.setItem(storageKey, JSON.stringify(Array.from(completedTasks)));
      } catch (e) {
        console.warn("Failed saving roadmap task progress to localStorage:", e);
      }

      updateVisuals();
    });
  });

  if (btnReset) {
    btnReset.addEventListener("click", () => {
      completedTasks.clear();
      try {
        localStorage.removeItem(storageKey);
      } catch (e) {
        console.warn("Failed saving roadmap task progress to localStorage:", e);
      }
      allCheckboxes.forEach((cb) => {
        cb.checked = false;
        const span = cb.parentElement?.querySelector("span");
        if (span) span.style.textDecoration = "none";
      });
      updateVisuals();
    });
  }

  updateVisuals();
}



export function initCircularProgress() {
  const circles = document.querySelectorAll(".circular-fill-ring");
  circles.forEach(circle => {
    const score = circle.getAttribute("data-score");
    if (score) {
      const radius = circle.r.baseVal.value;
      const circumference = 2 * Math.PI * radius;
      const offset = circumference - (score / 100) * circumference;
      
      // Animate circular transition
      setTimeout(() => {
        circle.style.strokeDasharray = `${circumference} ${circumference}`;
        circle.style.strokeDashoffset = offset;
      }, 300);
    }
  });
}

/**
 * Manages the live interactive mock interview simulation
 */
/**
 * Manages the live interactive mock interview simulation with Voice-Enabled AI Interviewer:
 * - Real-time Web Speech API recognition (Speech-to-Text) with continuous interim transcription
 * - Audio visualizer wave animation and recording timer
 * - Pre-submission editable transcript review
 * - Speech Synthesis (Text-to-Speech) for interviewer questions with auto-speak toggle
 * - Dynamic verbal indicators evaluation (Verbal clarity, filler words, STAR method)
 */


export function initRoadmapExport() {
  const container = document.getElementById("dashboardContainer");
  if (!container) return;

  const companyName = container.getAttribute("data-company") || "Placement Target";

  // 1. Copy individual week tasks
  document.querySelectorAll(".copy-week-btn").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.preventDefault();
      const card = btn.closest(".week-card");
      if (!card) return;

      const weekTitle = btn.getAttribute("data-week-name") || "Week";
      const hours = card.querySelector(".badge")?.innerText || "";
      let md = `### ${weekTitle} (${hours})\n\n`;

      const appendSection = (heading, selector) => {
        const items = card.querySelectorAll(selector);
        if (items.length > 0) {
          md += `#### ${heading}:\n`;
          items.forEach((item) => {
            const text = item.innerText.trim();
            if (text) md += `- [ ] ${text}\n`;
          });
          md += "\n";
        }
      };

      appendSection("Topics to Learn", ".roadmap-task-check[data-task-id*='_top_'] + span");
      appendSection("Coding Practice", ".roadmap-task-check[data-task-id*='_code_'] + span");
      appendSection("Micro Projects", ".roadmap-task-check[data-task-id*='_proj_'] + span");
      appendSection("Interview Prep Questions", ".roadmap-task-check[data-task-id*='_q_'] + span");

      const rev = card.querySelector(".border-top p")?.innerText.trim();
      if (rev) {
        md += `#### Revision Tasks:\n${rev}\n\n`;
      }

      try {
        await navigator.clipboard.writeText(md);
        const originalHtml = btn.innerHTML;
        btn.innerHTML = '<i class="fa-solid fa-check text-success me-1"></i> Copied!';
        btn.classList.add("btn-success-subtle");
        setTimeout(() => {
          btn.innerHTML = originalHtml;
          btn.classList.remove("btn-success-subtle");
        }, 2000);
      } catch (err) {
        console.warn("Clipboard copy failed, fallback prompted:", err);
      }
    });
  });

  // 2. Export full 6-week study plan as a Markdown file
  const exportBtn = document.getElementById("btnExportRoadmapMarkdown");
  if (exportBtn) {
    exportBtn.addEventListener("click", () => {
      let fullMd = `# 6-Week Placement Preparation Roadmap\n`;
      fullMd += `**Target Company:** ${companyName}\n`;
      fullMd += `**Generated by:** AI Campus Placement Agent\n\n`;
      fullMd += `---\n\n`;

      document.querySelectorAll(".week-card").forEach((card) => {
        const title = card.querySelector("h5")?.innerText.trim() || "Week";
        const hours = card.querySelector(".badge")?.innerText.trim() || "";
        fullMd += `## ${title} (${hours})\n\n`;

        const appendSection = (heading, selector) => {
          const items = card.querySelectorAll(selector);
          if (items.length > 0) {
            fullMd += `### ${heading}:\n`;
            items.forEach((item) => {
              const text = item.innerText.trim();
              if (text) fullMd += `- [ ] ${text}\n`;
            });
            fullMd += "\n";
          }
        };

        appendSection("Topics to Learn", ".roadmap-task-check[data-task-id*='_top_'] + span");
        appendSection("Coding Practice Tasks", ".roadmap-task-check[data-task-id*='_code_'] + span");
        appendSection("Micro Projects", ".roadmap-task-check[data-task-id*='_proj_'] + span");
        appendSection("Interview Prep Questions", ".roadmap-task-check[data-task-id*='_q_'] + span");

        const rev = card.querySelector(".border-top p")?.innerText.trim();
        if (rev) {
          fullMd += `### Revision Tasks:\n${rev}\n\n`;
        }
        fullMd += `---\n\n`;
      });

      const blob = new Blob([fullMd], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `Study_Plan_${companyName.replace(/[^a-zA-Z0-9_-]/g, "_")}.md`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    });
  }

  // 3. Export study plan to iCalendar (.ics) file (#647)
  const calendarModal = document.getElementById("roadmapCalendarModal");
  const startDateInput = document.getElementById("calendarStartDate");
  const radioTimed = document.getElementById("calendarTypeTimed");
  const radioAllDay = document.getElementById("calendarTypeAllDay");
  const timedContainer = document.getElementById("calendarTimedOptionsContainer");
  const downloadCalendarBtn = document.getElementById("btnDownloadRoadmapCalendar");

  const setDefaultStartDate = () => {
    if (startDateInput && !startDateInput.value) {
      const tomorrow = new Date();
      tomorrow.setDate(tomorrow.getDate() + 1);
      const yyyy = tomorrow.getFullYear();
      const mm = String(tomorrow.getMonth() + 1).padStart(2, "0");
      const dd = String(tomorrow.getDate()).padStart(2, "0");
      startDateInput.value = `${yyyy}-${mm}-${dd}`;
      startDateInput.min = new Date().toISOString().split("T")[0];
    }
  };

  if (calendarModal) {
    calendarModal.addEventListener("show.bs.modal", setDefaultStartDate);
  }
  setDefaultStartDate();

  if (radioTimed && radioAllDay && timedContainer) {
    const updateTimedVisibility = () => {
      timedContainer.style.display = radioAllDay.checked ? "none" : "";
    };
    radioTimed.addEventListener("change", updateTimedVisibility);
    radioAllDay.addEventListener("change", updateTimedVisibility);
  }

  if (downloadCalendarBtn) {
    downloadCalendarBtn.addEventListener("click", () => {
      const analysisId = container.getAttribute("data-analysis-id") || "";
      const startDate = startDateInput ? startDateInput.value : "";
      const isAllDay = radioAllDay && radioAllDay.checked ? "1" : "0";
      const studyTimeInput = document.getElementById("calendarStudyTime");
      const durationSelect = document.getElementById("calendarDurationSelect");
      const studyTime = studyTimeInput ? studyTimeInput.value : "19:00";
      const duration = durationSelect ? durationSelect.value : "60";

      const queryParams = new URLSearchParams({
        start_date: startDate,
        all_day: isAllDay,
        study_time: studyTime,
        duration: duration,
      });

      const exportUrl = analysisId
        ? `/api/analysis/${encodeURIComponent(analysisId)}/roadmap/calendar.ics?${queryParams.toString()}`
        : `/api/roadmap/calendar.ics?${queryParams.toString()}`;

      // Trigger file download
      const tempLink = document.createElement("a");
      tempLink.href = exportUrl;
      tempLink.setAttribute("download", `placement-roadmap-${companyName.replace(/[^a-zA-Z0-9_-]/g, "_")}.ics`);
      document.body.appendChild(tempLink);
      tempLink.click();
      document.body.removeChild(tempLink);

      // Feedback state
      const originalHtml = downloadCalendarBtn.innerHTML;
      downloadCalendarBtn.innerHTML = '<i class="fa-solid fa-check me-1"></i> Downloaded!';
      downloadCalendarBtn.classList.replace("btn-success", "btn-outline-success");
      setTimeout(() => {
        downloadCalendarBtn.innerHTML = originalHtml;
        downloadCalendarBtn.classList.replace("btn-outline-success", "btn-success");
      }, 2500);
    });
  }
}

/**
 * Multi-Version Resume Comparison & Longitudinal Progress Tracker
 * - Initializes Chart.js line/area chart showing ATS Score, Job Match %, and Skill Gaps over time
 * - Supports theme-reactive colors for gridlines and text
 * - Manages interactive side-by-side diff modal and dynamic baseline selection
 */


export function initProgressTracker() {
  const trackerCard = document.getElementById("progressTrackerCard");
  if (!trackerCard) return;

  const trendRaw = trackerCard.getAttribute("data-trend");
  let trendData = null;
  try {
    trendData = trendRaw ? JSON.parse(trendRaw) : null;
  } catch (err) {
    console.warn("Could not parse trend data for Chart.js:", err);
  }

  // 1. Initialize Chart.js longitudinal trend chart
  const canvas = document.getElementById("progressTrendChart");
  if (canvas && window.Chart && trendData && Array.isArray(trendData.labels) && trendData.labels.length > 0) {
    const isDark = document.documentElement.getAttribute("data-bs-theme") === "dark";
    const tokens = getChartThemeTokens ? getChartThemeTokens(isDark) : {
      textColor: isDark ? "#f0f2f5" : "#1f1f1f",
      gridColor: isDark ? "rgba(255, 255, 255, 0.12)" : "rgba(0, 0, 0, 0.08)",
      surfaceBg: isDark ? "#1e2022" : "#ffffff",
      borderColor: isDark ? "#444746" : "#dadce0",
    };

    const ctx = canvas.getContext("2d");
    const progressChart = new window.Chart(ctx, {
      type: "line",
      data: {
        labels: trendData.labels,
        datasets: [
          {
            label: "ATS Profile Score",
            data: trendData.ats_scores,
            borderColor: OKABE_ITO.blue,
            backgroundColor: "rgba(0, 114, 178, 0.12)",
            fill: true,
            tension: 0.35,
            borderWidth: 2.5,
            pointRadius: 6,
            pointHoverRadius: 8,
            pointBackgroundColor: OKABE_ITO.blue,
            pointBorderColor: "#ffffff",
            pointStyle: "circle",
            borderDash: [],
            yAxisID: "y"
          },
          {
            label: "Job Match %",
            data: trendData.match_scores,
            borderColor: OKABE_ITO.bluishGreen,
            backgroundColor: "rgba(0, 158, 115, 0.12)",
            fill: true,
            tension: 0.35,
            borderWidth: 2.5,
            pointRadius: 6,
            pointHoverRadius: 8,
            pointBackgroundColor: OKABE_ITO.bluishGreen,
            pointBorderColor: "#ffffff",
            pointStyle: "rectRot",
            borderDash: [6, 4],
            yAxisID: "y"
          },
          {
            label: "Skill Gaps Count",
            data: trendData.gap_counts,
            borderColor: OKABE_ITO.vermilion,
            backgroundColor: "transparent",
            fill: false,
            tension: 0.3,
            borderWidth: 2.5,
            pointRadius: 6,
            pointHoverRadius: 8,
            pointBackgroundColor: OKABE_ITO.vermilion,
            pointBorderColor: "#ffffff",
            pointStyle: "triangle",
            borderDash: [3, 3],
            yAxisID: "y1"
          }
        ]
      },
      options: {
        animation: isReducedMotion() ? false : true,
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
          mode: "index",
          intersect: false
        },
        plugins: {
          legend: {
            position: "top",
            labels: {
              usePointStyle: true,
              boxWidth: 12,
              font: { size: 12, family: "'Outfit', sans-serif", weight: "500" },
              color: tokens.textColor,
              generateLabels: function(chart) {
                const markers = ["●", "◆", "▲"];
                return chart.data.datasets.map((ds, i) => ({
                  text: `${markers[i] || "■"} ${ds.label}`,
                  fillStyle: ds.borderColor,
                  strokeStyle: ds.borderColor,
                  pointStyle: ds.pointStyle,
                  datasetIndex: i,
                  fontColor: tokens.textColor,
                }));
              }
            }
          },
          tooltip: {
            backgroundColor: tokens.surfaceBg,
            titleColor: tokens.textColor,
            bodyColor: tokens.textColor,
            borderColor: tokens.borderColor,
            borderWidth: 1,
            padding: 10,
            callbacks: {
              title: function (items) {
                const idx = items[0].dataIndex;
                const date = trendData.dates && trendData.dates[idx] ? trendData.dates[idx] : "";
                const filename = trendData.filenames && trendData.filenames[idx] ? trendData.filenames[idx] : "";
                return `Iteration ${trendData.labels[idx]} (${date}) - ${filename}`;
              },
              label: function (context) {
                const markers = ["●", "◆", "▲"];
                const sym = markers[context.datasetIndex] || "■";
                const suffix = context.datasetIndex === 2 ? "" : "%";
                return ` ${sym} ${context.dataset.label}: ${context.raw}${suffix}`;
              }
            }
          }
        },
        scales: {
          x: {
            grid: { color: tokens.gridColor },
            ticks: { color: tokens.textColor, font: { family: "'Outfit', sans-serif" } }
          },
          y: {
            type: "linear",
            display: true,
            position: "left",
            min: 0,
            max: 100,
            grid: { color: tokens.gridColor },
            ticks: {
              color: tokens.textColor,
              callback: val => `${val}%`,
              font: { family: "'Outfit', sans-serif" }
            }
          },
          y1: {
            type: "linear",
            display: true,
            position: "right",
            min: 0,
            suggestedMax: 8,
            grid: { drawOnChartArea: false },
            ticks: {
              color: OKABE_ITO.vermilion,
              precision: 0,
              font: { family: "'Outfit', sans-serif", weight: "600" }
            }
          }
        }
      }
    });

    // Theme toggle listener to update chart colors
    const observer = new MutationObserver(() => {
      const darkNow = document.documentElement.getAttribute("data-bs-theme") === "dark";
      const newTokens = getChartThemeTokens ? getChartThemeTokens(darkNow) : {
        textColor: darkNow ? "#f0f2f5" : "#1f1f1f",
        gridColor: darkNow ? "rgba(255, 255, 255, 0.12)" : "rgba(0, 0, 0, 0.08)",
      };

      if (progressChart) {
        progressChart.options.scales.x.ticks.color = newTokens.textColor;
        progressChart.options.scales.x.grid.color = newTokens.gridColor;
        progressChart.options.scales.y.ticks.color = newTokens.textColor;
        progressChart.options.scales.y.grid.color = newTokens.gridColor;
        progressChart.options.plugins.legend.labels.color = newTokens.textColor;
        progressChart.update();
      }
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-bs-theme"] });
  }

  // 2. Interactive side-by-side diff modal dropdown switcher
  const diffModal = document.getElementById("versionDiffModal");
  const selectBaseline = document.getElementById("selectDiffBaseline");
  const diffLoadingSpinner = document.getElementById("diffLoadingSpinner");
  const diffContentArea = document.getElementById("diffContentArea");

  if (diffModal && selectBaseline) {
    const currentAnalysisId = diffModal.getAttribute("data-current-id");

    selectBaseline.addEventListener("change", async () => {
      const baselineId = selectBaseline.value;
      if (!baselineId || !currentAnalysisId) return;

      if (diffLoadingSpinner) diffLoadingSpinner.classList.remove("d-none");
      if (diffContentArea) diffContentArea.classList.add("opacity-25");

      try {
        const response = await fetch(`/api/progress/diff/${baselineId}/${currentAnalysisId}`);
        const data = await response.json();

        if (data.status === "success" && data.diff) {
          renderDiffModalContent(data.diff);
        }
      } catch (err) {
        console.error("Failed to fetch version diff comparison:", err);
      } finally {
        if (diffLoadingSpinner) diffLoadingSpinner.classList.add("d-none");
        if (diffContentArea) diffContentArea.classList.remove("opacity-25");
      }
    });
  }

  function renderDiffModalContent(diff) {
    const a = diff.version_a || {};
    const b = diff.version_b || {};
    const d = diff.delta || {};

    const aTitle = document.getElementById("diffVersionATitle");
    const aScore = document.getElementById("diffVersionAScore");
    const aKeywords = document.getElementById("diffVersionAKeywords");
    const aMissing = document.getElementById("diffVersionAMissing");
    const aGaps = document.getElementById("diffVersionAGaps");

    const bTitle = document.getElementById("diffVersionBTitle");
    const bScore = document.getElementById("diffVersionBScore");
    const bNewKeywords = document.getElementById("diffVersionBNewKeywords");
    const bResolvedGaps = document.getElementById("diffVersionBResolvedGaps");
    const bKeywords = document.getElementById("diffVersionBKeywords");

    if (aTitle) aTitle.innerHTML = `<i class="fa-solid fa-clock-rotate-left me-1"></i> Version ${a.version_number} (Baseline)`;
    if (aScore) aScore.innerText = `ATS: ${a.ats_score}/100`;

    if (aKeywords) {
      aKeywords.innerHTML = "";
      const kwList = Array.isArray(a.keywords_found) ? a.keywords_found : [];
      if (kwList.length > 0) {
        kwList.forEach(k => {
          const span = document.createElement("span");
          span.className = "badge bg-white text-secondary border rounded-pill";
          span.innerText = k;
          aKeywords.appendChild(span);
        });
      } else {
        aKeywords.innerHTML = '<span class="text-muted small">None recorded</span>';
      }
    }

    if (aMissing) {
      aMissing.innerHTML = "";
      const mList = Array.isArray(a.keywords_missing) ? a.keywords_missing : [];
      if (mList.length > 0) {
        mList.forEach(k => {
          const span = document.createElement("span");
          span.className = "badge bg-danger-subtle text-danger border border-danger-subtle rounded-pill";
          span.innerText = k;
          aMissing.appendChild(span);
        });
      } else {
        aMissing.innerHTML = '<span class="text-muted small">None</span>';
      }
    }

    if (aGaps) {
      aGaps.innerHTML = "";
      const gList = Array.isArray(a.skill_gaps) ? a.skill_gaps : [];
      gList.forEach(g => {
        const div = document.createElement("div");
        div.className = "text-secondary mb-1";
        div.innerHTML = `<i class="fa-solid fa-circle-minus me-1 text-danger"></i> ${escapeHtml(g)}`;
        aGaps.appendChild(div);
      });
    }

    if (bTitle) bTitle.innerHTML = `<i class="fa-solid fa-star me-1"></i> Version ${b.version_number} (Active)`;
    if (bScore) bScore.innerText = `ATS: ${b.ats_score}/100`;

    if (bNewKeywords) {
      bNewKeywords.innerHTML = "";
      const newly = Array.isArray(d.newly_matched_keywords) ? d.newly_matched_keywords : [];
      if (newly.length > 0) {
        newly.forEach(k => {
          const span = document.createElement("span");
          span.className = "badge bg-success text-white rounded-pill";
          span.innerHTML = `<i class="fa-solid fa-plus me-1"></i>${escapeHtml(k)}`;
          bNewKeywords.appendChild(span);
        });
      } else {
        bNewKeywords.innerHTML = '<span class="text-muted small">No new keywords identified in this iteration</span>';
      }
    }

    if (bResolvedGaps) {
      bResolvedGaps.innerHTML = "";
      const resolved = Array.isArray(d.resolved_skill_gaps) ? d.resolved_skill_gaps : [];
      if (resolved.length > 0) {
        resolved.forEach(g => {
          const span = document.createElement("span");
          span.className = "badge bg-primary text-white rounded-pill";
          span.innerHTML = `<i class="fa-solid fa-check me-1"></i>${escapeHtml(g)}`;
          bResolvedGaps.appendChild(span);
        });
      } else {
        bResolvedGaps.innerHTML = '<span class="text-muted small">None resolved in this iteration</span>';
      }
    }

    if (bKeywords) {
      bKeywords.innerHTML = "";
      const bKw = Array.isArray(b.keywords_found) ? b.keywords_found : [];
      bKw.forEach(k => {
        const span = document.createElement("span");
        span.className = "badge bg-white text-dark border rounded-pill";
        span.innerText = k;
        bKeywords.appendChild(span);
      });
    }
  }

  function escapeHtml(str) {
    if (typeof str !== "string") str = String(str);
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // -------------------------------------------------------------
  // Share Profile URL Copy Handler
  // -------------------------------------------------------------
  const btnCopyShareUrl = document.getElementById("btnCopyShareUrl");
  const shareProfileUrl = document.getElementById("shareProfileUrl");
  if (btnCopyShareUrl && shareProfileUrl) {
    btnCopyShareUrl.addEventListener("click", () => {
      shareProfileUrl.select();
      shareProfileUrl.setSelectionRange(0, 99999);
      navigator.clipboard.writeText(shareProfileUrl.value).then(() => {
        const originalHtml = btnCopyShareUrl.innerHTML;
        btnCopyShareUrl.innerHTML = '<i class="fa-solid fa-check me-1"></i> Copied!';
        btnCopyShareUrl.classList.replace("btn-outline-primary", "btn-success");
        setTimeout(() => {
          btnCopyShareUrl.innerHTML = originalHtml;
          btnCopyShareUrl.classList.replace("btn-success", "btn-outline-primary");
        }, 2000);
      });
    });
  }
}

/**
 * Outreach & Cover Letter Pitch Generator
 * Handles tone switching, LLM / heuristic regeneration, 1-click clipboard copy,
 * interactive studio modal editing, and formatted download (.docx and .txt).
 */


export function initOutreachGenerator() {
  const sectionCard = document.getElementById("outreachSectionCard");
  if (!sectionCard) return;

  const dashboardContainer = document.getElementById("dashboardContainer");
  const analysisId = dashboardContainer ? dashboardContainer.dataset.analysisId : "";
  const companyName = dashboardContainer ? dashboardContainer.dataset.company : "Company";

  // Elements
  const roleInput = document.getElementById("outreachRoleInput");
  const btnGenerate = document.getElementById("btnGenerateOutreach");
  const spinner = document.getElementById("outreachSpinner");
  const icon = document.getElementById("outreachIcon");

  const coverLetterContent = document.getElementById("coverLetterContent");
  const linkedinContent = document.getElementById("linkedinInmailContent");
  const coldEmailContent = document.getElementById("coldEmailContent");
  const subjectLinesContainer = document.getElementById("subjectLinesContainer");
  const keyHighlightsContainer = document.getElementById("keyHighlightsContainer");

  const btnDownloadDocx = document.getElementById("btnQuickDownloadDocx");
  const btnDownloadTxt = document.getElementById("btnQuickDownloadTxt");

  // Modal elements
  const modalEditor = document.getElementById("modalOutreachEditor");
  const modalWordCount = document.getElementById("modalWordCount");
  const btnModalCopy = document.getElementById("btnModalCopyText");
  const btnModalDocx = document.getElementById("btnModalDownloadDocx");
  const btnModalTxt = document.getElementById("btnModalDownloadTxt");

  // State
  let currentTone = "formal";
  let modalActiveSection = "cover_letter";
  const toneCache = {};

  // Parse initial outreach if rendered from server
  try {
    if (sectionCard.dataset.outreach) {
      const initial = JSON.parse(sectionCard.dataset.outreach);
      if (initial && typeof initial === "object") {
        currentTone = (initial.tone || "formal").toLowerCase();
        toneCache[currentTone] = initial;
      }
    }
  } catch (err) {
    console.warn("Could not parse initial outreach data:", err);
  }

  // Update Download Links
  function updateDownloadUrls() {
    const role = roleInput ? roleInput.value.trim() : "";
    if (btnDownloadDocx && analysisId) {
      btnDownloadDocx.href = `/api/outreach/${analysisId}/download?format=docx&tone=${currentTone}&role_title=${encodeURIComponent(role)}`;
    }
    if (btnDownloadTxt && analysisId) {
      btnDownloadTxt.href = `/api/outreach/${analysisId}/download?format=txt&tone=${currentTone}&role_title=${encodeURIComponent(role)}`;
    }
  }
  updateDownloadUrls();

  // Tone Buttons (Card)
  const toneButtons = document.querySelectorAll(".outreach-tone-btn");
  toneButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const selectedTone = btn.dataset.tone;
      if (selectedTone === currentTone && toneCache[selectedTone]) return;
      setTone(selectedTone, false);
    });
  });

  // Tone Buttons (Modal)
  const modalToneButtons = document.querySelectorAll(".modal-tone-btn");
  modalToneButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const selectedTone = btn.dataset.tone;
      setTone(selectedTone, false);
    });
  });

  function setTone(newTone, forceRegenerate) {
    currentTone = (newTone || "formal").toLowerCase();

    // Sync button styling
    toneButtons.forEach((b) => {
      if (b.dataset.tone === currentTone) {
        b.classList.add("btn-primary", "active");
        b.classList.remove("btn-outline-secondary");
      } else {
        b.classList.remove("btn-primary", "active");
        b.classList.add("btn-outline-secondary");
      }
    });

    modalToneButtons.forEach((b) => {
      if (b.dataset.tone === currentTone) {
        b.classList.add("btn-primary", "active");
        b.classList.remove("btn-outline-primary");
      } else {
        b.classList.remove("btn-primary", "active");
        b.classList.add("btn-outline-primary");
      }
    });

    // Update tone badges
    document.querySelectorAll(".outreach-active-tone-badge").forEach((badge) => {
      badge.textContent = `Tone: ${currentTone.charAt(0).toUpperCase() + currentTone.slice(1)}`;
    });

    updateDownloadUrls();

    if (!forceRegenerate && toneCache[currentTone]) {
      renderOutreach(toneCache[currentTone]);
    } else {
      generateOutreach(currentTone);
    }
  }

  // Regenerate button
  if (btnGenerate) {
    btnGenerate.addEventListener("click", () => {
      generateOutreach(currentTone);
    });
  }

  if (roleInput) {
    roleInput.addEventListener("change", () => {
      updateDownloadUrls();
    });
  }

  // Fetch Outreach from API
  async function generateOutreach(tone) {
    if (!analysisId) return;

    if (btnGenerate) {
      btnGenerate.disabled = true;
      if (spinner) spinner.classList.remove("d-none");
      if (icon) icon.classList.add("d-none");
    }

    try {
      const role = roleInput ? roleInput.value.trim() : "";
      const res = await fetch(`/api/generate-outreach/${analysisId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tone: tone, role_title: role })
      });

      if (!res.ok) {
        throw new Error(`Generation failed with HTTP status ${res.status}`);
      }

      const data = await res.json();
      if (data.success && data.outreach) {
        toneCache[tone] = data.outreach;
        renderOutreach(data.outreach);
      }
    } catch (err) {
      console.error("Error generating outreach:", err);
    } finally {
      if (btnGenerate) {
        btnGenerate.disabled = false;
        if (spinner) spinner.classList.add("d-none");
        if (icon) icon.classList.remove("d-none");
      }
    }
  }

  function renderOutreach(outreach) {
    if (!outreach) return;

    if (coverLetterContent && outreach.cover_letter) {
      coverLetterContent.textContent = outreach.cover_letter;
    }
    if (linkedinContent && outreach.linkedin_inmail) {
      linkedinContent.textContent = outreach.linkedin_inmail;
    }
    if (coldEmailContent && outreach.cold_email) {
      coldEmailContent.textContent = outreach.cold_email;
    }

    // Render Subject Lines
    if (subjectLinesContainer && Array.isArray(outreach.subject_lines)) {
      subjectLinesContainer.innerHTML = outreach.subject_lines
        .map(
          (sl) => `
          <div class="list-group-item d-flex justify-content-between align-items-center border bg-white rounded-3 mb-2">
            <span class="small fw-medium text-dark"><i class="fa-regular fa-envelope me-2 text-primary"></i>${escapeHtml(sl)}</span>
            <button type="button" class="btn btn-sm btn-outline-secondary rounded-pill py-0 px-2 btn-copy-text" data-copy-value="${escapeHtml(sl)}" title="Copy Subject Line">
              <i class="fa-regular fa-copy"></i>
            </button>
          </div>`
        )
        .join("");
      attachCopyTextListeners();
    }

    // Render Key Highlights
    if (keyHighlightsContainer && Array.isArray(outreach.key_highlights)) {
      keyHighlightsContainer.innerHTML = outreach.key_highlights
        .map(
          (hl) => `
          <li class="p-2 bg-white rounded-3 border mb-2 small text-secondary d-flex align-items-start">
            <i class="fa-solid fa-check-circle text-success me-2 mt-1"></i>
            <span>${escapeHtml(hl)}</span>
          </li>`
        )
        .join("");
    }

    // Sync modal editor if open
    syncModalEditor();
  }

  // 1-Click Clipboard Copying
  function copyToClipboard(text, btn) {
    if (!text) return;
    navigator.clipboard.writeText(text).then(
      () => showCopyFeedback(btn),
      () => {
        // Fallback
        const ta = document.createElement("textarea");
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        showCopyFeedback(btn);
      }
    );
  }

  function showCopyFeedback(btn) {
    if (!btn) return;
    const origHtml = btn.innerHTML;
    const isPrimary = btn.classList.contains("btn-outline-primary") || btn.classList.contains("btn-primary");
    btn.innerHTML = '<i class="fa-solid fa-check me-1"></i> Copied!';
    if (isPrimary) {
      btn.classList.add("btn-success");
      btn.classList.remove("btn-outline-primary", "btn-primary");
    }
    setTimeout(() => {
      btn.innerHTML = origHtml;
      if (isPrimary) {
        btn.classList.remove("btn-success");
        btn.classList.add("btn-outline-primary");
      }
    }, 2000);
  }

  // Card Copy Buttons
  document.querySelectorAll(".btn-copy-outreach").forEach((btn) => {
    btn.addEventListener("click", () => {
      const targetId = btn.dataset.copyTarget;
      const targetEl = document.getElementById(targetId);
      if (targetEl) {
        copyToClipboard(targetEl.textContent.trim(), btn);
      }
    });
  });

  function attachCopyTextListeners() {
    document.querySelectorAll(".btn-copy-text").forEach((btn) => {
      btn.removeEventListener("click", onCopyTextClick);
      btn.addEventListener("click", onCopyTextClick);
    });
  }
  function onCopyTextClick(e) {
    const btn = e.currentTarget;
    const val = btn.dataset.copyValue;
    if (val) copyToClipboard(val, btn);
  }
  attachCopyTextListeners();

  // -------------------------------------------------------------
  // Modal Studio Controller
  // -------------------------------------------------------------
  const modalEl = document.getElementById("outreachPreviewModal");
  if (modalEl) {
    modalEl.addEventListener("show.bs.modal", () => {
      syncModalEditor();
    });
  }

  const modalSectionTabs = document.querySelectorAll("#modalSectionTabs [data-modal-target]");
  modalSectionTabs.forEach((tabBtn) => {
    tabBtn.addEventListener("click", () => {
      modalSectionTabs.forEach((b) => b.classList.remove("active"));
      tabBtn.classList.add("active");
      modalActiveSection = tabBtn.dataset.modalTarget;
      syncModalEditor();
    });
  });

  function syncModalEditor() {
    if (!modalEditor) return;
    const cur = toneCache[currentTone] || {};
    let text = "";
    if (modalActiveSection === "cover_letter") {
      text = cur.cover_letter || "";
    } else if (modalActiveSection === "linkedin_inmail") {
      text = cur.linkedin_inmail || "";
    } else if (modalActiveSection === "cold_email") {
      text = cur.cold_email || "";
    }
    modalEditor.value = text;
    updateModalWordCount();
  }

  if (modalEditor) {
    modalEditor.addEventListener("input", updateModalWordCount);
  }

  function updateModalWordCount() {
    if (!modalEditor || !modalWordCount) return;
    const val = modalEditor.value.trim();
    const words = val.length > 0 ? val.split(/\s+/).length : 0;
    const chars = val.length;
    modalWordCount.textContent = `${words} words | ${chars} characters`;
  }

  if (btnModalCopy) {
    btnModalCopy.addEventListener("click", () => {
      if (modalEditor) {
        copyToClipboard(modalEditor.value, btnModalCopy);
      }
    });
  }

  // Modal Download Handlers
  async function downloadFromModal(format) {
    if (!modalEditor) return;
    const content = modalEditor.value;
    const role = roleInput ? roleInput.value.trim() : "Software Engineer";

    try {
      const res = await fetch("/api/outreach/download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          analysis_id: analysisId,
          format: format,
          content_type: modalActiveSection,
          content: content,
          tone: currentTone,
          company_name: companyName,
          role_title: role,
          outreach: toneCache[currentTone]
        })
      });

      if (!res.ok) throw new Error("Download request failed");

      const blob = await res.blob();
      const disposition = res.headers.get("Content-Disposition");
      let filename = `outreach_${modalActiveSection}.${format}`;
      if (disposition && disposition.indexOf("filename=") !== -1) {
        const matches = /filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/.exec(disposition);
        if (matches != null && matches[1]) {
          filename = matches[1].replace(/['"]/g, "");
        }
      }

      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(url);
    } catch (err) {
      console.error("Modal download failed:", err);
    }
  }

  if (btnModalDocx) {
    btnModalDocx.addEventListener("click", () => downloadFromModal("docx"));
  }
  if (btnModalTxt) {
    btnModalTxt.addEventListener("click", () => downloadFromModal("txt"));
  }

  function escapeHtml(str) {
    if (typeof str !== "string") str = String(str);
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }
}

/**
 * Universal Clipboard Copy Helper for AI-Generated Suggestions & Recommendations
 * Supports modern Clipboard API with fallback to document.execCommand('copy').
 * Fully responsive and cross-browser compatible (Chrome, Safari, iOS WebKit, Firefox, Edge).
 * Displays instant inline button state feedback and non-intrusive floating toast notifications.
 */


export function initCopySuggestions() {
  function getOrCreateToastContainer() {
    let container = document.getElementById("copyToastContainer");
    if (!container) {
      container = document.createElement("div");
      container.id = "copyToastContainer";
      container.className = "copy-toast-container";
      container.setAttribute("aria-live", "polite");
      container.setAttribute("aria-atomic", "true");
      document.body.appendChild(container);
    }
    return container;
  }

  function showCopyToast(message, isSuccess = true) {
    const container = getOrCreateToastContainer();
    const toast = document.createElement("div");
    toast.className = "copy-toast";
    toast.setAttribute("role", "status");

    const iconClass = isSuccess
      ? "fa-solid fa-circle-check text-success"
      : "fa-solid fa-triangle-exclamation text-danger";

    toast.innerHTML = `
      <i class="${iconClass} flex-shrink-0"></i>
      <span class="flex-grow-1">${escapeHtml(message)}</span>
    `;

    container.appendChild(toast);

    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        toast.classList.add("show");
      });
    });

    setTimeout(() => {
      toast.classList.remove("show");
      toast.classList.add("fade-out");
      setTimeout(() => {
        if (toast.parentNode) toast.parentNode.removeChild(toast);
      }, 300);
    }, 2400);
  }

  async function copyTextToClipboard(text) {
    if (!text || typeof text !== "string") return false;
    const cleanText = text.trim();
    if (!cleanText) return false;

    // 1. Try modern navigator.clipboard API if available and permitted
    if (navigator.clipboard && window.isSecureContext && typeof navigator.clipboard.writeText === "function") {
      try {
        await navigator.clipboard.writeText(cleanText);
        return true;
      } catch (err) {
        console.warn("navigator.clipboard.writeText failed, trying execCommand fallback:", err);
      }
    }

    // 2. Fallback using document.execCommand('copy') for older browsers, iOS Safari, HTTP, and iframes
    try {
      const textarea = document.createElement("textarea");
      textarea.value = cleanText;
      // Prevent mobile keyboard popping up and viewport scrolling
      textarea.style.position = "fixed";
      textarea.style.top = "0";
      textarea.style.left = "-9999px";
      textarea.style.width = "2em";
      textarea.style.height = "2em";
      textarea.style.padding = "0";
      textarea.style.border = "none";
      textarea.style.outline = "none";
      textarea.style.boxShadow = "none";
      textarea.style.background = "transparent";
      textarea.style.fontSize = "16px";
      textarea.setAttribute("readonly", "");
      textarea.setAttribute("aria-hidden", "true");

      document.body.appendChild(textarea);

      // Handle iOS Safari selection range
      textarea.focus();
      textarea.select();
      textarea.setSelectionRange(0, textarea.value.length);

      const successful = document.execCommand("copy");
      document.body.removeChild(textarea);

      if (!successful) {
        throw new Error("execCommand('copy') returned false");
      }
      return true;
    } catch (fallbackErr) {
      console.error("Clipboard copy failed across all methods:", fallbackErr);
      return false;
    }
  }

  function triggerButtonFeedback(btn, isSuccess) {
    if (!btn) return;
    if (btn.dataset.copyingInProgress === "true") return;
    btn.dataset.copyingInProgress = "true";

    const originalHtml = btn.innerHTML;
    const originalTitle = btn.getAttribute("title") || "";
    const hasText = btn.textContent.trim().length > 0;

    if (isSuccess) {
      btn.classList.add("copied");
      btn.innerHTML = hasText
        ? '<i class="fa-solid fa-check text-success me-1"></i> Copied!'
        : '<i class="fa-solid fa-check text-success"></i>';
      btn.setAttribute("title", "Copied to clipboard!");
    } else {
      btn.classList.add("copy-error");
      btn.innerHTML = hasText
        ? '<i class="fa-solid fa-triangle-exclamation text-danger me-1"></i> Failed'
        : '<i class="fa-solid fa-triangle-exclamation text-danger"></i>';
      btn.setAttribute("title", "Failed to copy");
    }

    setTimeout(() => {
      btn.innerHTML = originalHtml;
      btn.classList.remove("copied", "copy-error");
      if (originalTitle) {
        btn.setAttribute("title", originalTitle);
      } else {
        btn.removeAttribute("title");
      }
      delete btn.dataset.copyingInProgress;
    }, 2000);
  }

  // Event delegation to catch clicks on any .btn-copy-suggestion, .btn-copy-text, or .btn-copy-bullet-rewrite
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest(".btn-copy-suggestion, .btn-copy-text, .btn-copy-bullet-rewrite");
    if (!btn) return;

    e.preventDefault();
    e.stopPropagation();

    // 1. Check data-copy-text or data-copy-value
    let textToCopy = btn.dataset.copyText || btn.dataset.copyValue;

    // 2. Check data-copy-target (id of element containing text)
    if (!textToCopy && btn.dataset.copyTarget) {
      const targetEl = document.getElementById(btn.dataset.copyTarget);
      if (targetEl) {
        textToCopy = targetEl.value !== undefined ? targetEl.value : targetEl.innerText || targetEl.textContent;
      }
    }

    // 3. Fallback: Search parent or sibling container
    if (!textToCopy) {
      const parentContainer = btn.closest("li, .list-group-item, .alert, .p-3, .chat-bubble");
      if (parentContainer) {
        const textEl = parentContainer.querySelector(".resume-bullet-text, .suggestion-text, span, p");
        if (textEl) {
          textToCopy = textEl.innerText || textEl.textContent;
        }
      }
    }

    if (!textToCopy || !textToCopy.trim()) {
      showCopyToast("No recommendation text found to copy.", false);
      triggerButtonFeedback(btn, false);
      return;
    }

    const success = await copyTextToClipboard(textToCopy);

    if (success) {
      triggerButtonFeedback(btn, true);
      const customMsg = btn.dataset.copySuccessMsg || "Recommendation copied to clipboard!";
      showCopyToast(customMsg, true);
    } else {
      triggerButtonFeedback(btn, false);
      showCopyToast("Could not access clipboard. Please copy manually.", false);
    }
  });
}

/**
 * Loads recent analyses from /api/analyses/recent and renders them inside the offcanvas drawer.
 * Displays helpful, styled empty states when no previous analysis history exists (#130).
 */


export function initRoleBasedPrep() {
  const container = document.getElementById("roleBasedPrepSection");
  if (!container) return;

  const dashboardContainer = document.getElementById("dashboardContainer");
  const analysisId = dashboardContainer ? dashboardContainer.getAttribute("data-analysis-id") : "";
  if (!analysisId) return;

  const roleBtns = container.querySelectorAll(".dashboard-role-btn");
  const spinner = document.getElementById("rolePrepLoadingSpinner");
  const mainContent = document.getElementById("rolePrepMainContentArea");

  roleBtns.forEach((btn) => {
    btn.addEventListener("click", async () => {
      const selectedRole = btn.getAttribute("data-role");
      if (!selectedRole) return;

      // Update button visual states
      roleBtns.forEach((b) => {
        b.classList.remove("btn-primary", "shadow-sm", "active");
        b.classList.add("btn-outline-secondary", "border-0");
      });
      btn.classList.remove("btn-outline-secondary", "border-0");
      btn.classList.add("btn-primary", "shadow-sm", "active");

      // Show loader
      if (spinner) spinner.classList.remove("d-none");
      if (mainContent) mainContent.style.opacity = "0.4";

      try {
        const response = await fetch(`/api/role-prep/${encodeURIComponent(analysisId)}?role=${encodeURIComponent(selectedRole)}`);
        const data = await response.json();

        if (data && data.success) {
          updateRolePreparationUI(data);
        }
      } catch (err) {
        console.error("Failed to load role preparation mode data:", err);
      } finally {
        if (spinner) spinner.classList.add("d-none");
        if (mainContent) mainContent.style.opacity = "1";
      }
    });
  });

  function updateRolePreparationUI(data) {
    // 1. Badge & Header
    const badge = document.getElementById("dashRoleBadge");
    if (badge) {
      badge.className = `badge bg-${data.color_class}-subtle text-${data.color_class} border border-${data.color_class}-subtle px-3 py-1 rounded-pill fw-bold`;
      badge.innerHTML = `<i class="${data.icon} me-1"></i> ${escapeHtml(data.role_title)}`;
    }

    const tagline = document.getElementById("dashRoleTagline");
    if (tagline) tagline.innerText = data.tagline;

    const titleDisplay = document.getElementById("dashRoleTitleDisplay");
    if (titleDisplay) titleDisplay.innerText = `${data.role_title} Preparation Track`;

    const guidance = document.getElementById("dashRoleGuidanceText");
    if (guidance) guidance.innerText = data.personalized_guidance;

    // 2. Alignment Gauge
    const scoreText = document.getElementById("dashAlignmentScoreText");
    if (scoreText) scoreText.innerText = `${data.alignment_score}%`;

    const bar = document.getElementById("dashAlignmentProgressBar");
    if (bar) bar.style.width = `${data.alignment_score}%`;

    // 3. Leveraged Skills & Bridged Gaps
    const levCont = document.getElementById("dashLeveragedSkillsContainer");
    if (levCont) {
      levCont.innerHTML = data.leveraged_skills && data.leveraged_skills.length
        ? data.leveraged_skills.map((s) => `<span class="badge bg-success-subtle text-success border border-success-subtle rounded-pill small py-1 px-2">${escapeHtml(s)}</span>`).join(" ")
        : `<span class="badge bg-success-subtle text-success border border-success-subtle rounded-pill small py-1 px-2">Core CS</span>`;
    }

    const gapCont = document.getElementById("dashBridgedGapsContainer");
    if (gapCont) {
      gapCont.innerHTML = data.bridged_skill_gaps && data.bridged_skill_gaps.length
        ? data.bridged_skill_gaps.map((g) => `<span class="badge bg-warning-subtle text-warning border border-warning-subtle rounded-pill small py-1 px-2">${escapeHtml(g)}</span>`).join(" ")
        : `<span class="badge bg-warning-subtle text-warning border border-warning-subtle rounded-pill small py-1 px-2">Role Skills</span>`;
    }

    // 3b. AI Recommendation Explanation Updates (#295)
    const aiRec = data.ai_recommendation_explanation;
    if (aiRec) {
      const tierBadge = document.getElementById("dashAiSuitabilityTierBadge");
      if (tierBadge) {
        tierBadge.innerText = aiRec.suitability_tier || "Recommended";
        tierBadge.className = `badge ${aiRec.suitability_tier === 'Strong Match' ? 'bg-success text-white' : (aiRec.suitability_tier === 'Good Match' ? 'bg-primary text-white' : 'bg-warning text-dark')} rounded-pill px-3 py-1 fw-semibold small`;
      }

      const recReason = document.getElementById("dashAiRecReasonText");
      if (recReason) recReason.innerText = aiRec.recommendation_reason || "";

      const matchCount = document.getElementById("dashAiMatchingSkillsCount");
      if (matchCount) matchCount.innerText = aiRec.matching_skills_count || 0;

      const matchCont = document.getElementById("dashAiMatchingSkillsContainer");
      if (matchCont) {
        matchCont.innerHTML = (aiRec.matching_skills && aiRec.matching_skills.length)
          ? aiRec.matching_skills.map((s) => `<span class="badge bg-success-subtle text-success border border-success-subtle rounded-pill small py-1 px-2">${escapeHtml(s)}</span>`).join(" ")
          : `<span class="text-muted small fst-italic">Core programming knowledge applied</span>`;
      }

      const missCount = document.getElementById("dashAiMissingSkillsCount");
      if (missCount) missCount.innerText = aiRec.missing_skills_count || 0;

      const missImpact = document.getElementById("dashAiMissingSkillsImpact");
      if (missImpact) missImpact.innerText = aiRec.missing_skills_impact || "";

      const missCont = document.getElementById("dashAiMissingSkillsContainer");
      if (missCont) {
        missCont.innerHTML = (aiRec.missing_skills && aiRec.missing_skills.length)
          ? aiRec.missing_skills.map((g) => `<span class="badge bg-warning-subtle text-warning border border-warning-subtle rounded-pill small py-1 px-2">${escapeHtml(g)}</span>`).join(" ")
          : `<span class="text-success small fw-semibold"><i class="fa-solid fa-circle-check me-1"></i>All baseline skills met!</span>`;
      }

      const projRel = document.getElementById("dashAiProjectRelevanceText");
      if (projRel) projRel.innerText = (aiRec.project_experience_relevance && aiRec.project_experience_relevance.summary) || "";

      const projList = document.getElementById("dashAiProjectHighlightsList");
      if (projList) {
        const hls = (aiRec.project_experience_relevance && aiRec.project_experience_relevance.highlights) || [];
        projList.innerHTML = hls.map((h) => `<li class="mb-1">${escapeHtml(h)}</li>`).join("");
      }

      const factCont = document.getElementById("dashAiInfluencingFactorsContainer");
      if (factCont && aiRec.influencing_factors) {
        factCont.innerHTML = aiRec.influencing_factors.map((f) => `
          <div>
            <div class="d-flex justify-content-between align-items-center mb-1">
              <span class="small fw-semibold text-dark">${escapeHtml(f.factor)}</span>
              <span class="badge bg-${f.badge_class}-subtle text-${f.badge_class} rounded-pill small">${f.score_pct}% Match</span>
            </div>
            <div class="progress rounded-pill mb-1" style="height: 5px;">
              <div class="progress-bar bg-${f.badge_class}" role="progressbar" style="width: ${f.score_pct}%"></div>
            </div>
            <small class="text-muted d-block" style="font-size: 0.725rem;">${escapeHtml(f.description)}</small>
          </div>
        `).join("");
      }

      const studSum = document.getElementById("dashAiStudentSummaryText");
      if (studSum) studSum.innerText = aiRec.student_friendly_summary || "";
    }

    // 4. Technical Topics Grid
    const topicsGrid = document.getElementById("dashTechnicalTopicsGrid");
    if (topicsGrid && data.technical_topics) {
      topicsGrid.innerHTML = data.technical_topics.map((top) => `
        <div class="col-md-6 col-12">
          <div class="p-3 bg-light rounded-4 border h-100 d-flex flex-column justify-content-between">
            <div>
              <div class="d-flex justify-content-between align-items-start mb-2 gap-2">
                <h6 class="fw-bold mb-0 text-dark">${escapeHtml(top.topic)}</h6>
                <span class="badge ${top.importance === 'High' ? 'bg-danger-subtle text-danger border-danger-subtle' : 'bg-primary-subtle text-primary border-primary-subtle'} rounded-pill small">
                  ${escapeHtml(top.importance)}
                </span>
              </div>
              <p class="text-secondary small mb-3">${escapeHtml(top.summary)}</p>
            </div>
            <div class="d-flex flex-wrap gap-1 align-items-center justify-content-between pt-2 border-top">
              <div class="d-flex flex-wrap gap-1">
                ${top.skills.map((sk) => `<span class="badge bg-white text-secondary border rounded-pill small">${escapeHtml(sk)}</span>`).join(" ")}
              </div>
              <span class="small text-muted"><i class="fa-solid fa-clock me-1"></i>~${top.estimated_hours}h</span>
            </div>
          </div>
        </div>
      `).join("");
    }

    // 5. Coding Questions Grid
    const codingGrid = document.getElementById("dashCodingQuestionsGrid");
    if (codingGrid && data.coding_questions) {
      codingGrid.innerHTML = data.coding_questions.map((q) => `
        <div class="col-md-6 col-12">
          <div class="p-3 bg-light rounded-4 border h-100 d-flex flex-column justify-content-between">
            <div>
              <div class="d-flex justify-content-between align-items-start mb-2 gap-2">
                <div>
                  <span class="badge bg-white text-secondary border rounded-pill small mb-1">${escapeHtml(q.platform)}</span>
                  <h6 class="fw-bold mb-0 text-dark">${escapeHtml(q.title)}</h6>
                </div>
                <span class="badge ${q.difficulty === 'Easy' ? 'bg-success-subtle text-success border-success-subtle' : (q.difficulty === 'Medium' ? 'bg-warning-subtle text-warning border-warning-subtle' : 'bg-danger-subtle text-danger border-danger-subtle')} rounded-pill small">
                  ${escapeHtml(q.difficulty)}
                </span>
              </div>
              <p class="text-secondary small mb-3">${escapeHtml(q.description)}</p>
              ${q.hints && q.hints.length ? `
                <div class="p-2 px-3 bg-white rounded-3 border small mb-3 text-muted">
                  <i class="fa-solid fa-lightbulb text-warning me-1"></i>
                  <strong>Hint:</strong> ${escapeHtml(q.hints[0])}
                </div>
              ` : ''}
            </div>
            <div class="d-flex flex-wrap justify-content-between align-items-center pt-2 border-top gap-2">
              <div class="d-flex flex-wrap gap-1">
                ${q.skills_targeted.map((sk) => `<span class="badge bg-white text-primary border rounded-pill small">${escapeHtml(sk)}</span>`).join(" ")}
              </div>
              <a href="${escapeHtml(q.practice_url)}" target="_blank" rel="noopener noreferrer" class="btn btn-sm btn-outline-primary rounded-pill px-3">
                Solve <i class="fa-solid fa-arrow-up-right-from-square ms-1 small"></i>
              </a>
            </div>
          </div>
        </div>
      `).join("");
    }

    // 6. Core Tech Grid
    const techGrid = document.getElementById("dashCoreTechGrid");
    if (techGrid && data.core_technologies) {
      techGrid.innerHTML = data.core_technologies.map((tech) => `
        <div class="col-md-4 col-sm-6 col-12">
          <div class="p-3 bg-white rounded-3 border h-100">
            <div class="d-flex justify-content-between align-items-center mb-1">
              <strong class="text-dark small">${escapeHtml(tech.name)}</strong>
              <span class="badge bg-light text-primary border rounded-pill small">${escapeHtml(tech.badge)}</span>
            </div>
            <small class="text-secondary d-block">${escapeHtml(tech.description)}</small>
          </div>
        </div>
      `).join("");
    }

    // 7. Concepts Grid
    const concGrid = document.getElementById("dashConceptsGrid");
    if (concGrid && data.important_concepts) {
      concGrid.innerHTML = data.important_concepts.map((c) => `
        <div class="col-md-6 col-12">
          <div class="p-3 bg-white rounded-3 border h-100">
            <div class="d-flex align-items-center gap-2 mb-1">
              <i class="fa-solid fa-check-circle text-success"></i>
              <strong class="text-dark small">${escapeHtml(c.concept)}</strong>
            </div>
            <small class="text-secondary d-block">${escapeHtml(c.details)}</small>
          </div>
        </div>
      `).join("");
    }

    // 8. Role Roadmap Timeline
    const roadTimeline = document.getElementById("dashRoleRoadmapTimeline");
    if (roadTimeline && data.roadmap) {
      roadTimeline.innerHTML = data.roadmap.map((w) => `
        <div class="timeline-node mb-3">
          <div class="card border-0 bg-light p-4 rounded-4 shadow-sm">
            <div class="d-flex justify-content-between align-items-center flex-wrap mb-2">
              <div>
                <span class="badge bg-primary-subtle text-primary border border-primary-subtle rounded-pill small mb-1">${escapeHtml(w.week)}</span>
                <h6 class="fw-bold mb-0 text-dark">${escapeHtml(w.focus)}</h6>
              </div>
              <span class="badge bg-primary text-white rounded-pill py-2 px-3">${escapeHtml(w.estimated_hours)} Hours</span>
            </div>
            <div class="row g-3 mt-1">
              <div class="col-md-6">
                <small class="fw-semibold text-secondary d-block mb-1"><i class="fa-solid fa-book-open me-1 text-primary"></i>Topics:</small>
                <ul class="mb-2 small list-unstyled">
                  ${w.topics.map((t) => `<li class="mb-1"><i class="fa-solid fa-angle-right text-primary me-1"></i>${escapeHtml(t)}</li>`).join("")}
                </ul>
              </div>
              <div class="col-md-6">
                <small class="fw-semibold text-secondary d-block mb-1"><i class="fa-solid fa-code me-1 text-primary"></i>Coding Practice:</small>
                <ul class="mb-2 small list-unstyled">
                  ${w.coding_practice.map((c) => `<li class="mb-1"><i class="fa-solid fa-check text-success me-1"></i>${escapeHtml(c)}</li>`).join("")}
                </ul>
              </div>
            </div>
          </div>
        </div>
      `).join("");
    }
  }
}

/**
 * Visual Skills Comparison Chart (Chart.js)
 * Visualizes Matching Skills vs. Missing Skills from the Job Matching Agent.
 * Responsive on desktop and mobile, supports theme switching (light/dark mode).
 */


export function initSkillsComparisonChart() {
  const card = document.getElementById("skillsComparisonCard");
  const canvas = document.getElementById("skillsComparisonChart");
  if (!card || !canvas || !window.Chart) return;

  let skillsData = null;
  const rawData = card.getAttribute("data-skills");
  if (rawData) {
    try {
      skillsData = JSON.parse(rawData);
    } catch (err) {
      console.warn("Could not parse data-skills JSON for Chart.js:", err);
    }
  }

  const matchedCount = skillsData && typeof skillsData.matched_count === "number" ? skillsData.matched_count : 0;
  const missingCount = skillsData && typeof skillsData.missing_count === "number" ? skillsData.missing_count : 0;
  const totalCount = matchedCount + missingCount;

  // If no skills evaluated at all, display a clean fallback state
  if (totalCount === 0) {
    const wrapper = canvas.parentElement;
    if (wrapper) {
      wrapper.innerHTML = `
        <div class="d-flex flex-column align-items-center justify-content-center h-100 text-muted p-3">
          <i class="fa-solid fa-chart-pie fa-2x mb-2 text-secondary opacity-50"></i>
          <span class="small">No skills comparison data available for this report.</span>
        </div>
      `;
    }
    return;
  }

  const isDark = document.documentElement.getAttribute("data-bs-theme") === "dark";

  const matchPct = totalCount > 0 ? Math.round((matchedCount / totalCount) * 100) : (skillsData.match_percentage || 0);
  const missingPct = 100 - matchPct;

  const ctx = canvas.getContext("2d");

  // Create accessible patterns for non-color differentiation (diagonal-stripes vs cross-hatch)
  const matchedPattern = createAccessiblePattern
    ? createAccessiblePattern(ctx, "diagonal-stripes", OKABE_ITO.bluishGreen, isDark ? "rgba(0, 158, 115, 0.35)" : "rgba(0, 158, 115, 0.20)")
    : OKABE_ITO.bluishGreen;
  const missingPattern = createAccessiblePattern
    ? createAccessiblePattern(ctx, "cross-hatch", OKABE_ITO.vermilion, isDark ? "rgba(213, 94, 0, 0.35)" : "rgba(213, 94, 0, 0.20)")
    : OKABE_ITO.vermilion;

  const chartInstance = new window.Chart(ctx, {
    type: "doughnut",
    data: {
      labels: ["Matching Skills", "Missing Skills"],
      datasets: [
        {
          data: [matchedCount, missingCount],
          backgroundColor: [matchedPattern, missingPattern],
          borderColor: isDark ? "#161b22" : "#ffffff",
          borderWidth: 3,
          hoverOffset: 6
        }
      ]
    },
    options: {
      animation: isReducedMotion() ? false : true,
      responsive: true,
      maintainAspectRatio: false,
      cutout: "70%",
      plugins: {
        legend: {
          display: false // Custom accessible legend rendered in HTML with check/cross icons
        },
        tooltip: {
          backgroundColor: isDark ? "rgba(22, 27, 34, 0.95)" : "rgba(33, 37, 41, 0.95)",
          titleColor: "#ffffff",
          bodyColor: "#ffffff",
          padding: 10,
          cornerRadius: 8,
          callbacks: {
            label: function (context) {
              const count = context.raw || 0;
              const pct = context.dataIndex === 0 ? matchPct : missingPct;
              const icon = context.dataIndex === 0 ? "✔" : "✖";
              return ` ${icon} ${context.label}: ${count} (${pct}%)`;
            }
          }
        }
      }
    }
  });

  // Ensure center value text is updated
  const centerVal = document.getElementById("skillsChartCenterVal");
  if (centerVal) {
    centerVal.innerText = `${matchPct}%`;
  }
}

/**
 * Resume Keyword Highlighter & In-Context Preview (#430)
 * Binds toggle switch to show/hide keyword highlights on extracted resume text.
 */


export function initResumeKeywordHighlighter() {
  const toggleBtn = document.getElementById("toggleKeywordHighlights");
  const resumeContainer = document.getElementById("resumeHighlightedContent");
  if (!toggleBtn || !resumeContainer) return;

  toggleBtn.addEventListener("change", () => {
    if (toggleBtn.checked) {
      resumeContainer.classList.remove("highlights-hidden");
    } else {
      resumeContainer.classList.add("highlights-hidden");
    }
  });
}

/**
 * Back-to-Top Floating Action Button
 * Shows when scrolled down (> 300px), smoothly scrolls to top on click.
 * Ensures cross-browser compatibility and avoids interfering with touch devices.
 */


export function initSectionFeedback() {
  const widgets = document.querySelectorAll(".section-feedback-widget");
  if (!widgets.length) return;

  const dashboardContainer = document.getElementById("dashboardContainer");
  const defaultAnalysisId = dashboardContainer ? dashboardContainer.dataset.analysisId : "";

  widgets.forEach((widget) => {
    const analysisId = widget.dataset.analysisId || defaultAnalysisId;
    const section = widget.dataset.section;
    if (!analysisId || !section) return;

    const rateBtns = widget.querySelectorAll(".btn-feedback-rate");
    const commentBox = widget.querySelector(".feedback-comment-box");
    const commentInput = widget.querySelector(".feedback-comment-input");
    const submitBtn = widget.querySelector(".btn-feedback-submit");
    const charCounter = widget.querySelector(".feedback-char-counter");
    const statusMsg = widget.querySelector(".feedback-status-msg");

    // Fetch existing user rating & comment if previously submitted
    fetch(`/api/analysis/${encodeURIComponent(analysisId)}/feedback?section=${encodeURIComponent(section)}`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data && data.success && data.feedback) {
          const fb = data.feedback;
          const currentRating = fb.rating; // 'up' or 'down'
          rateBtns.forEach((btn) => {
            const btnRating = btn.dataset.rating;
            if (btnRating === currentRating) {
              btn.classList.add("active");
              const icon = btn.querySelector("i");
              if (icon) {
                icon.className = btnRating === "up" ? "fa-solid fa-thumbs-up me-1" : "fa-solid fa-thumbs-down me-1";
              }
            } else {
              btn.classList.remove("active");
            }
          });

          if (fb.comment && commentInput) {
            commentInput.value = fb.comment;
            if (charCounter) charCounter.textContent = `${fb.comment.length}/500`;
            if (commentBox) commentBox.classList.remove("d-none");
          }
        }
      })
      .catch(() => {});

    // Live character counter on comment input
    if (commentInput && charCounter) {
      commentInput.addEventListener("input", () => {
        charCounter.textContent = `${commentInput.value.length}/500`;
      });
    }

    // Rate button click handler
    rateBtns.forEach((btn) => {
      btn.addEventListener("click", () => {
        const rating = btn.dataset.rating;
        const comment = commentInput ? commentInput.value.trim() : "";

        fetch(`/api/analysis/${encodeURIComponent(analysisId)}/feedback`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            section: section,
            rating: rating,
            comment: comment || null,
          }),
        })
          .then((res) => res.json())
          .then((data) => {
            if (data.success) {
              rateBtns.forEach((b) => {
                b.classList.remove("active");
                const icon = b.querySelector("i");
                if (icon) {
                  icon.className = b.dataset.rating === "up" ? "fa-regular fa-thumbs-up me-1" : "fa-regular fa-thumbs-down me-1";
                }
              });

              btn.classList.add("active");
              const activeIcon = btn.querySelector("i");
              if (activeIcon) {
                activeIcon.className = rating === "up" ? "fa-solid fa-thumbs-up me-1" : "fa-solid fa-thumbs-down me-1";
              }

              if (commentBox) {
                if (rating === "down" || (commentInput && commentInput.value)) {
                  commentBox.classList.remove("d-none");
                }
              }

              if (statusMsg) {
                statusMsg.textContent = "Rating saved!";
                statusMsg.className = "feedback-status-msg text-success";
                setTimeout(() => {
                  if (statusMsg.textContent === "Rating saved!") statusMsg.textContent = "";
                }, 3000);
              }
            } else {
              if (statusMsg) {
                statusMsg.textContent = data.error || "Failed to save.";
                statusMsg.className = "feedback-status-msg text-danger";
              }
            }
          })
          .catch(() => {
            if (statusMsg) {
              statusMsg.textContent = "Error saving feedback.";
              statusMsg.className = "feedback-status-msg text-danger";
            }
          });
      });
    });

    // Save comment button click handler
    if (submitBtn) {
      submitBtn.addEventListener("click", () => {
        let activeRating = "up";
        rateBtns.forEach((b) => {
          if (b.classList.contains("active")) {
            activeRating = b.dataset.rating;
          }
        });

        const comment = commentInput ? commentInput.value.trim() : "";

        fetch(`/api/analysis/${encodeURIComponent(analysisId)}/feedback`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            section: section,
            rating: activeRating,
            comment: comment || null,
          }),
        })
          .then((res) => res.json())
          .then((data) => {
            if (data.success) {
              if (statusMsg) {
                statusMsg.textContent = "Comment saved!";
                statusMsg.className = "feedback-status-msg text-success";
                setTimeout(() => {
                  if (statusMsg.textContent === "Comment saved!") statusMsg.textContent = "";
                }, 3000);
              }
            } else {
              if (statusMsg) {
                statusMsg.textContent = data.error || "Failed to save comment.";
                statusMsg.className = "feedback-status-msg text-danger";
              }
            }
          })
          .catch(() => {
            if (statusMsg) {
              statusMsg.textContent = "Error saving comment.";
              statusMsg.className = "feedback-status-msg text-danger";
            }
          });
      });
    }
  });
}

/**
 * First-Time User Walkthrough & Feature Tour (#350).
 * Handles persistent dismissal via localStorage and session,
 * interactive multi-step navigation, direct jump to feature anchors, and reopening anytime.
 */


export function initUserWalkthrough() {
  const walkthroughBanner = document.getElementById("userWalkthroughBanner");
  const dismissBtn = document.getElementById("btnDismissWalkthroughBanner");
  const openTourBtns = document.querySelectorAll(".btn-open-walkthrough");
  const walkthroughModalEl = document.getElementById("userWalkthroughModal");
  const modalDismissBtn = document.getElementById("btnDismissWalkthroughModal");
  const modalResetBtn = document.getElementById("btnResetWalkthroughState");
  const stepPanes = document.querySelectorAll(".walkthrough-step-pane");
  const stepIndicators = document.querySelectorAll(".walkthrough-step-indicator");
  const prevBtn = document.getElementById("btnWalkthroughPrev");
  const nextBtn = document.getElementById("btnWalkthroughNext");
  const actionNavBtns = document.querySelectorAll(".btn-walkthrough-nav");

  const STORAGE_KEY = "campus_placement_walkthrough_dismissed";

  // Check initial dismissal state from localStorage
  const isDismissed = localStorage.getItem(STORAGE_KEY) === "true";
  if (walkthroughBanner) {
    if (isDismissed) {
      walkthroughBanner.classList.add("d-none");
    } else {
      walkthroughBanner.classList.remove("d-none");
    }
  }

  function dismissWalkthrough() {
    localStorage.setItem(STORAGE_KEY, "true");
    if (walkthroughBanner) {
      walkthroughBanner.classList.add("d-none");
    }
    try {
      fetch("/api/walkthrough/dismiss", {
        method: "POST",
        headers: { "Content-Type": "application/json" }
      }).catch(() => {});
    } catch (e) {}
  }

  function resetWalkthrough() {
    localStorage.removeItem(STORAGE_KEY);
    if (walkthroughBanner) {
      walkthroughBanner.classList.remove("d-none");
    }
    try {
      fetch("/api/walkthrough/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" }
      }).catch(() => {});
    } catch (e) {}
  }

  if (dismissBtn) {
    dismissBtn.addEventListener("click", dismissWalkthrough);
  }

  if (modalDismissBtn) {
    modalDismissBtn.addEventListener("click", dismissWalkthrough);
  }

  if (modalResetBtn) {
    modalResetBtn.addEventListener("click", () => {
      resetWalkthrough();
      if (typeof bootstrap !== "undefined" && walkthroughModalEl) {
        const modal = bootstrap.Modal.getInstance(walkthroughModalEl);
        if (modal) modal.hide();
      }
    });
  }

  openTourBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      if (typeof bootstrap !== "undefined" && walkthroughModalEl) {
        const modal = bootstrap.Modal.getOrCreateInstance(walkthroughModalEl);
        modal.show();
      }
    });
  });

  // Multi-step modal navigation
  let currentStep = 1;
  const totalSteps = stepPanes.length || 4;

  function showStep(stepNum) {
    currentStep = Math.max(1, Math.min(stepNum, totalSteps));
    stepPanes.forEach((pane) => {
      const paneStep = parseInt(pane.dataset.step, 10);
      if (paneStep === currentStep) {
        pane.classList.remove("d-none");
        pane.classList.add("active");
      } else {
        pane.classList.add("d-none");
        pane.classList.remove("active");
      }
    });

    stepIndicators.forEach((ind) => {
      const indStep = parseInt(ind.dataset.step, 10);
      if (indStep === currentStep) {
        ind.classList.add("btn-primary", "text-white");
        ind.classList.remove("btn-outline-secondary", "bg-light");
      } else if (indStep < currentStep) {
        ind.classList.add("btn-outline-primary");
        ind.classList.remove("btn-primary", "text-white", "btn-outline-secondary");
      } else {
        ind.classList.add("btn-outline-secondary");
        ind.classList.remove("btn-primary", "text-white", "btn-outline-primary");
      }
    });

    if (prevBtn) {
      prevBtn.disabled = currentStep === 1;
    }
    if (nextBtn) {
      if (currentStep === totalSteps) {
        nextBtn.innerHTML = 'Finish Tour <i class="fa-solid fa-check ms-1"></i>';
        nextBtn.classList.remove("btn-primary");
        nextBtn.classList.add("btn-success");
      } else {
        nextBtn.innerHTML = 'Next Step <i class="fa-solid fa-arrow-right ms-1"></i>';
        nextBtn.classList.remove("btn-success");
        nextBtn.classList.add("btn-primary");
      }
    }
  }

  if (prevBtn) {
    prevBtn.addEventListener("click", () => {
      if (currentStep > 1) {
        showStep(currentStep - 1);
      }
    });
  }

  if (nextBtn) {
    nextBtn.addEventListener("click", () => {
      if (currentStep < totalSteps) {
        showStep(currentStep + 1);
      } else {
        dismissWalkthrough();
        if (typeof bootstrap !== "undefined" && walkthroughModalEl) {
          const modal = bootstrap.Modal.getInstance(walkthroughModalEl);
          if (modal) modal.hide();
        }
      }
    });
  }

  stepIndicators.forEach((btn) => {
    btn.addEventListener("click", () => {
      const stepNum = parseInt(btn.dataset.step, 10);
      if (!isNaN(stepNum)) {
        showStep(stepNum);
      }
    });
  });

  // Direct feature navigation buttons
  actionNavBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.target;
      if (target === "analysisForm" || target === "uploadArea") {
        if (typeof bootstrap !== "undefined" && walkthroughModalEl) {
          const modal = bootstrap.Modal.getInstance(walkthroughModalEl);
          if (modal) modal.hide();
        }
        const targetEl = document.getElementById("analysisForm") || document.getElementById("uploadArea");
        if (targetEl) {
          targetEl.scrollIntoView({ behavior: isReducedMotion() ? "auto" : "smooth", block: "center" });
          const fileInput = document.getElementById("resumeInput");
          if (fileInput) fileInput.focus();
        }
      }
    });
  });

  if (stepPanes.length > 0) {
    showStep(1);
  }
}

/**
 * Password Visibility Toggle
 * Toggles input type between password and text with synchronized aria attributes and icons.
 */


/**
 * Interactive Filterable Campus Job Recommendations (#533)
 * Supports dynamic filtering by location, role type (internship/full-time), experience level,
 * and text search. Updates recommendation cards with matched skills and fit reasons.
 * Shows actionable broadening suggestions when 0 jobs match.
 */
export function initJobRecommendations() {
  const card = document.getElementById("jobRecommendationsCard");
  if (!card) return;

  const analysisId = card.getAttribute("data-analysis-id");
  if (!analysisId) return;

  const locationFilter = document.getElementById("jobLocationFilter");
  const roleTypeFilter = document.getElementById("jobRoleTypeFilter");
  const experienceFilter = document.getElementById("jobExperienceFilter");
  const searchInput = document.getElementById("jobSearchInput");
  const clearSearchBtn = document.getElementById("btnClearJobSearch");
  const resetBtn = document.getElementById("btnResetJobFilters");
  const emptyStateResetBtn = document.getElementById("btnEmptyStateReset");
  const listContainer = document.getElementById("jobRecommendationsList");
  const emptyState = document.getElementById("jobRecommendationsEmptyState");
  const broadenList = document.getElementById("jobBroadenSuggestionsList");
  const countBadge = document.getElementById("jobMatchedCount");

  let searchTimeout = null;

  async function fetchJobRecommendations() {
    const loc = locationFilter ? locationFilter.value : "all";
    const rt = roleTypeFilter ? roleTypeFilter.value : "all";
    const exp = experienceFilter ? experienceFilter.value : "all";
    const q = searchInput ? searchInput.value.trim() : "";

    const params = new URLSearchParams({
      location: loc,
      role_type: rt,
      experience_level: exp,
      search: q
    });

    try {
      if (listContainer) {
        listContainer.style.opacity = "0.6";
      }
      const response = await fetch(`/api/jobs/recommendations/${encodeURIComponent(analysisId)}?${params.toString()}`);
      if (!response.ok) {
        throw new Error(`Server returned ${response.status}`);
      }
      const data = await response.json();
      if (!data.success) {
        throw new Error(data.error || "Failed to load job recommendations");
      }

      renderJobRecommendations(data.jobs || [], data.suggestions_to_broaden || []);
    } catch (err) {
      console.warn("Error fetching filtered job recommendations:", err);
    } finally {
      if (listContainer) {
        listContainer.style.opacity = "1";
      }
    }
  }

  function renderJobRecommendations(jobs, suggestions) {
    if (countBadge) {
      countBadge.textContent = jobs.length;
    }

    if (jobs.length === 0) {
      if (listContainer) listContainer.innerHTML = "";
      if (emptyState) emptyState.classList.remove("d-none");
      if (broadenList) {
        if (suggestions && suggestions.length > 0) {
          broadenList.innerHTML = suggestions.map(s => `<li>${escapeHtml(s)}</li>`).join("");
        } else {
          broadenList.innerHTML = `
            <li>Switch location to 'All Locations' to see positions across all tech hubs and remote drives.</li>
            <li>Broaden role type to 'All Roles' to include both Full-time and Internship opportunities.</li>
            <li>Clear specific experience criteria to explore all student and early-career drives.</li>
          `;
        }
      }
      return;
    }

    if (emptyState) emptyState.classList.add("d-none");

    if (listContainer) {
      listContainer.innerHTML = jobs.map(job => {
        const matchPct = job.match_percentage || 0;
        let badgeClass = "bg-warning text-dark";
        if (matchPct >= 80) badgeClass = "bg-success text-white";
        else if (matchPct >= 65) badgeClass = "bg-primary text-white";

        const matchedSkills = job.matched_skills || [];
        const missingSkills = job.missing_skills || [];
        const fitReason = job.recommendation_reason || job.fit_reason || job.fit_summary || "Aligned with candidate technical capabilities.";
        const companyShort = (job.company || "CO").substring(0, 2).toUpperCase();
        const logoColor = job.logo_color || "#4f46e5";

        const matchedSkillsHtml = matchedSkills.length > 0
          ? matchedSkills.map(s => `<span class="badge bg-success-subtle text-success border border-success-subtle rounded-pill small">${escapeHtml(s)}</span>`).join(" ")
          : `<span class="text-muted small fst-italic">Standard campus baseline</span>`;

        const missingSkillsHtml = missingSkills.length > 0
          ? `
            <div class="mb-3">
              <span class="small fw-bold text-secondary d-block mb-1">
                <i class="fa-solid fa-circle-info me-1"></i> Skills to Brush Up:
              </span>
              <div class="d-flex flex-wrap gap-1">
                ${missingSkills.slice(0, 5).map(s => `<span class="badge bg-light text-secondary border rounded-pill small">${escapeHtml(s)}</span>`).join(" ")}
              </div>
            </div>
          `
          : "";

        const salaryBadge = job.salary_range
          ? `<span class="badge bg-success-subtle text-success border border-success-subtle rounded-pill small py-1 px-2"><i class="fa-solid fa-indian-rupee-sign me-1"></i> ${escapeHtml(job.salary_range)}</span>`
          : "";

        const targetBadge = job.is_target_company
          ? `<span class="badge bg-danger-subtle text-danger border border-danger-subtle rounded-pill d-block mt-1 small"><i class="fa-solid fa-bullseye me-1"></i> Target</span>`
          : "";

        return `
          <div class="col-lg-6 col-12 job-recommendation-item" data-job-id="${escapeHtml(job.id || '')}">
            <div class="card h-100 border rounded-4 p-3 bg-white shadow-sm d-flex flex-column">
              <div class="d-flex justify-content-between align-items-start mb-2 gap-2">
                <div class="d-flex align-items-center gap-2">
                  <div class="rounded-3 px-2 py-1 text-white fw-bold small text-center" style="background-color: ${escapeHtml(logoColor)}; min-width: 36px;">
                    ${escapeHtml(companyShort)}
                  </div>
                  <div>
                    <h6 class="fw-bold mb-0 text-dark">${escapeHtml(job.title || '')}</h6>
                    <small class="text-muted fw-semibold">${escapeHtml(job.company || '')}</small>
                  </div>
                </div>
                <div class="text-end flex-shrink-0">
                  <span class="badge ${badgeClass} rounded-pill px-3 py-1 fw-bold">
                    ${matchPct}% Match
                  </span>
                  ${targetBadge}
                </div>
              </div>

              <div class="d-flex flex-wrap gap-2 mb-3">
                <span class="badge bg-light text-secondary border rounded-pill small py-1 px-2">
                  <i class="fa-solid fa-location-dot text-danger me-1"></i> ${escapeHtml(job.location || 'Pan India')}
                </span>
                <span class="badge bg-light text-secondary border rounded-pill small py-1 px-2">
                  <i class="fa-solid fa-briefcase text-primary me-1"></i> ${escapeHtml(job.role_type || 'Full-time')}
                </span>
                <span class="badge bg-light text-secondary border rounded-pill small py-1 px-2">
                  <i class="fa-solid fa-graduation-cap text-success me-1"></i> ${escapeHtml(job.experience_level || 'Fresher')}
                </span>
                ${salaryBadge}
              </div>

              <div class="p-2 px-3 bg-light rounded-3 small border mb-3">
                <i class="fa-solid fa-wand-magic-sparkles text-primary me-1"></i>
                <strong>Why this fits:</strong>
                <span class="text-secondary">${escapeHtml(fitReason)}</span>
              </div>

              <div class="mb-2">
                <span class="small fw-bold text-success d-block mb-1">
                  <i class="fa-solid fa-circle-check me-1"></i> Matched Skills (${matchedSkills.length}):
                </span>
                <div class="d-flex flex-wrap gap-1">
                  ${matchedSkillsHtml}
                </div>
              </div>

              ${missingSkillsHtml}

              <div class="mt-auto pt-2 border-top d-flex justify-content-between align-items-center">
                <small class="text-muted">
                  <i class="fa-regular fa-clock me-1"></i> ${escapeHtml(job.deadline || 'Active Hiring Drive')}
                </small>
                <a href="${escapeHtml(job.apply_url || '#')}" target="_blank" rel="noopener noreferrer" class="btn btn-sm btn-custom-primary rounded-pill px-3">
                  <i class="fa-solid fa-arrow-up-right-from-square me-1"></i> Apply / View Drive
                </a>
              </div>
            </div>
          </div>
        `;
      }).join("");
    }
  }

  function resetAllFilters() {
    if (locationFilter) locationFilter.value = "all";
    if (roleTypeFilter) roleTypeFilter.value = "all";
    if (experienceFilter) experienceFilter.value = "all";
    if (searchInput) searchInput.value = "";
    fetchJobRecommendations();
  }

  if (locationFilter) {
    locationFilter.addEventListener("change", fetchJobRecommendations);
  }
  if (roleTypeFilter) {
    roleTypeFilter.addEventListener("change", fetchJobRecommendations);
  }
  if (experienceFilter) {
    experienceFilter.addEventListener("change", fetchJobRecommendations);
  }
  if (searchInput) {
    searchInput.addEventListener("input", () => {
      clearTimeout(searchTimeout);
      searchTimeout = setTimeout(fetchJobRecommendations, 250);
    });
  }
  if (clearSearchBtn) {
    clearSearchBtn.addEventListener("click", () => {
      if (searchInput) {
        searchInput.value = "";
        fetchJobRecommendations();
      }
    });
  }
  if (resetBtn) {
    resetBtn.addEventListener("click", resetAllFilters);
  }
  if (emptyStateResetBtn) {
    emptyStateResetBtn.addEventListener("click", resetAllFilters);
  }
}

export function initDashboard() {
  initCircularProgress();
  initSkillGapFilter();
  initRoleSkillGapSelector();
  initRoadmapExport();
  initRoadmapTracker();
  initProgressTracker();
  initOutreachGenerator();
  initCopySuggestions();
  initRoleBasedPrep();
  initSkillsComparisonChart();
  initResumeKeywordHighlighter();
  initSectionFeedback();
  initUserWalkthrough();
  initJobRecommendations();
}

if (typeof document !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initDashboard);
  } else {
    initDashboard();
  }
}
