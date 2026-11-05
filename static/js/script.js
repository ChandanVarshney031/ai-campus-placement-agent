// Frontend interactions - AI Campus Placement Agent

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  if (typeof str !== "string") str = String(str);
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

document.addEventListener("DOMContentLoaded", () => {
  initThemeToggle();
  initFormUpload();
  initCircularProgress();
  initMockInterview();
  initCompanyExplorer();
  initSkillGapFilter();
  initRoadmapExport();
  initRoadmapTracker();
  initProgressTracker();
  if (typeof initCodingSandbox === "function") {
    initCodingSandbox();
  }
  initOutreachGenerator();
  initRecentAnalysesDrawer();
  initCharacterCounters();
  initCopySuggestions();
});

function initThemeToggle() {
  const currentTheme = localStorage.getItem("theme") || "light";
  document.documentElement.setAttribute("data-bs-theme", currentTheme);

  const toggleBtns = document.querySelectorAll(".theme-toggle-btn");
  toggleBtns.forEach((btn) => {
    updateThemeIcon(btn, currentTheme);
    btn.addEventListener("click", () => {
      const activeTheme = document.documentElement.getAttribute("data-bs-theme");
      const newTheme = activeTheme === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-bs-theme", newTheme);
      localStorage.setItem("theme", newTheme);
      toggleBtns.forEach((b) => updateThemeIcon(b, newTheme));
    });
  });
}

function updateThemeIcon(btn, theme) {
  if (theme === "dark") {
    btn.innerHTML = '<i class="fa-solid fa-sun text-warning"></i>';
    btn.title = "Switch to Light Mode";
  } else {
    btn.innerHTML = '<i class="fa-solid fa-moon text-secondary"></i>';
    btn.title = "Switch to Dark Mode";
  }
}

/**
 * Filter and search controls for the Skill Gap assessment table on the dashboard
 */
function initSkillGapFilter() {
  const searchInput = document.getElementById("skillSearchInput");
  const priorityFilter = document.getElementById("skillPriorityFilter");
  const table = document.getElementById("skillGapTable");
  const countBadge = document.getElementById("skillFilterCount");
  const emptyState = document.getElementById("skillGapEmptyState");

  if (!table || !searchInput || !priorityFilter) return;

  const rows = table.querySelectorAll("tbody tr.skill-gap-row");
  const totalRows = rows.length;

  function applyFilter() {
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

  searchInput.addEventListener("input", applyFilter);
  priorityFilter.addEventListener("change", applyFilter);

  searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && searchInput.value) {
      searchInput.value = "";
      applyFilter();
    }
  });
}

/**
 * Tracks interactive checklist tasks on the 6-Week Learning Roadmap
 * and persists completed task IDs in localStorage per analysis session.
 */
function initRoadmapTracker() {
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

  function updateVisuals() {
    let totalAll = allCheckboxes.length;
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

    const overallPct = totalAll > 0 ? Math.round((checkedAll / totalAll) * 100) : 0;
    if (overallLabel) overallLabel.innerText = `${overallPct}%`;
    if (overallBar) overallBar.style.width = `${overallPct}%`;
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

  updateVisuals();
}

function initCompanyExplorer() {
  const btn = document.getElementById("btnExploreCompany");
  const select = document.getElementById("companySelect");
  const modalEl = document.getElementById("companyModal");
  const modalBody = document.getElementById("companyModalBody");
  if (!btn || !select || !modalEl) return;

  btn.addEventListener("click", async () => {
    const company = select.value;
    if (!company || company === "General") {
      alert("Please select a specific company (e.g. Google, Microsoft, Amazon) to explore its hiring guide.");
      return;
    }
    const bsModal = new bootstrap.Modal(modalEl);
    modalBody.innerHTML = '<div class="text-center py-4"><span class="spinner-border text-primary" role="status"></span></div>';
    bsModal.show();

    try {
      const res = await fetch(`/api/company/${encodeURIComponent(company)}`);
      const data = await res.json();
      if (data.success && data.details) {
        const d = data.details;
        modalBody.innerHTML = `
          <h4 class="fw-bold text-primary mb-3">${data.company}</h4>
          <p class="text-secondary">${d.overview || ""}</p>
          <hr/>
          <h6 class="fw-bold"><i class="fa-solid fa-list-check text-primary me-2"></i>Hiring Process:</h6>
          <p class="small text-muted mb-3">${d.hiring_process || "N/A"}</p>
          <h6 class="fw-bold"><i class="fa-solid fa-laptop-code text-primary me-2"></i>Technical Rounds:</h6>
          <p class="small text-muted mb-3">${d.technical_rounds || "N/A"}</p>
          <h6 class="fw-bold"><i class="fa-solid fa-lightbulb text-warning me-2"></i>Preparation Tips:</h6>
          <div class="alert alert-light border small text-dark">${d.prep_tips || "N/A"}</div>
        `;
      } else {
        modalBody.innerHTML = `<div class="alert alert-warning">No details found for ${company}.</div>`;
      }
    } catch (err) {
      modalBody.innerHTML = '<div class="alert alert-danger">Error loading company details.</div>';
    }
  });
}

/**
 * Handlers for landing page upload forms & progress panel animations
 */
function initFormUpload() {
  const uploadForm = document.getElementById("analysisForm");
  if (!uploadForm) return;

  const fileInput = document.getElementById("resumeInput");
  const uploadArea = document.getElementById("uploadArea");
  const progressOverlay = document.getElementById("progressOverlay");
  const fileFeedbackBadge = document.getElementById("fileFeedbackBadge");
  const fileFeedbackBadgeText = document.getElementById("fileFeedbackBadgeText");
  const uploadTypeErrorAlert = document.getElementById("uploadTypeErrorAlert");
  const uploadTypeErrorMessage = document.getElementById("uploadTypeErrorMessage");
  const btnRemoveFile = document.getElementById("btnRemoveFile");
  const uploadIcon = document.getElementById("uploadIcon");
  const uploadPromptText = document.getElementById("uploadPromptText");
  const uploadSubText = document.getElementById("uploadSubText");

  const previewCard = document.getElementById("resumePreviewCard");
  const previewFileName = document.getElementById("previewFileName");
  const previewFileSize = document.getElementById("previewFileSize");
  const previewFileType = document.getElementById("previewFileType");
  const previewFileIcon = document.getElementById("previewFileIcon");
  const resumeEmptyNotice = document.getElementById("resumeEmptyNotice");
  const btnChangeResumeFile = document.getElementById("btnChangeResumeFile");
  const btnDiscardResumePreview = document.getElementById("btnDiscardResumePreview");

  // Format file size nicely
  const formatFileSize = (bytes) => {
    if (bytes === 0) return "0 Bytes";
    const k = 1024;
    const sizes = ["Bytes", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
  };

  // Helper to validate and display feedback for chosen resume file
  const handleSelectedFile = (file) => {
    if (!file) {
      resetUploadArea();
      return;
    }

    const fileName = file.name || "";
    const lowerName = fileName.toLowerCase();
    const isPdf = lowerName.endsWith(".pdf");
    const isDocx = lowerName.endsWith(".docx");

    // Hide any previous error
    if (uploadTypeErrorAlert) {
      uploadTypeErrorAlert.classList.add("d-none");
    }

    if (!isPdf && !isDocx) {
      // Show explicit unsupported error alert
      if (uploadTypeErrorAlert && uploadTypeErrorMessage) {
        uploadTypeErrorMessage.textContent = `Unsupported file type "${fileName.split('.').pop() || 'unknown'}". Only .pdf and .docx resume files are supported.`;
        uploadTypeErrorAlert.classList.remove("d-none");
      }
      uploadArea.classList.add("border-danger");
      uploadArea.style.backgroundColor = "#fff8f8";
      // Clear input
      if (fileInput) fileInput.value = "";
      if (fileFeedbackBadge) fileFeedbackBadge.classList.add("d-none");
      if (previewCard) previewCard.classList.add("d-none");
      if (resumeEmptyNotice) resumeEmptyNotice.classList.remove("d-none");
      return false;
    }

    // Success styling and visual feedback badge
    uploadArea.classList.remove("border-danger");
    uploadArea.style.borderColor = "var(--primary-blue)";
    uploadArea.style.backgroundColor = "var(--secondary-light)";

    const iconClass = isDocx ? "fa-file-word text-primary" : "fa-file-pdf text-danger";
    if (uploadIcon) {
      uploadIcon.className = `fa-solid ${iconClass} upload-icon`;
    }

    if (uploadPromptText) {
      uploadPromptText.innerHTML = `Ready for analysis: <b>${fileName}</b>`;
    }

    if (uploadSubText) {
      uploadSubText.textContent = `File size: ${formatFileSize(file.size)}`;
    }

    if (fileFeedbackBadge && fileFeedbackBadgeText) {
      fileFeedbackBadgeText.innerHTML = `<i class="fa-solid ${iconClass} me-1"></i> ${fileName} (${formatFileSize(file.size)})`;
      fileFeedbackBadge.classList.remove("d-none");
    }

    // Populate Resume Preview Card (#133)
    if (previewCard) {
      if (previewFileName) previewFileName.textContent = fileName;
      if (previewFileSize) previewFileSize.textContent = formatFileSize(file.size);
      if (previewFileType) previewFileType.textContent = isDocx ? "Word Document (.docx)" : "PDF Document (.pdf)";
      if (previewFileIcon) previewFileIcon.className = `fa-solid ${iconClass}`;
      previewCard.classList.remove("d-none");
    }
    if (resumeEmptyNotice) {
      resumeEmptyNotice.classList.add("d-none");
    }

    return true;
  };

  // Helper to reset upload area to clean default state
  const resetUploadArea = () => {
    if (fileInput) fileInput.value = "";
    if (uploadArea) {
      uploadArea.style.borderColor = "var(--border-color)";
      uploadArea.style.backgroundColor = "#ffffff";
      uploadArea.classList.remove("border-danger", "dragover-active");
    }
    if (uploadIcon) {
      uploadIcon.className = "fa-solid fa-cloud-arrow-up upload-icon";
    }
    if (uploadPromptText) {
      uploadPromptText.textContent = "Drag and drop your resume here, or click to browse";
    }
    if (uploadSubText) {
      uploadSubText.textContent = "Supports .pdf and .docx documents (max 10MB)";
    }
    if (fileFeedbackBadge) {
      fileFeedbackBadge.classList.add("d-none");
    }
    if (uploadTypeErrorAlert) {
      uploadTypeErrorAlert.classList.add("d-none");
    }
    if (previewCard) {
      previewCard.classList.add("d-none");
    }
    if (resumeEmptyNotice) {
      resumeEmptyNotice.classList.remove("d-none");
    }
  };

  // Highlight upload area on drag events and file change
  if (uploadArea && fileInput) {
    uploadArea.addEventListener("click", (e) => {
      // Don't trigger browse if clicking the remove button
      if (e.target.closest("#btnRemoveFile")) return;
      fileInput.click();
    });

    if (btnRemoveFile) {
      btnRemoveFile.addEventListener("click", (e) => {
        e.stopPropagation();
        resetUploadArea();
      });
    }

    fileInput.addEventListener("change", () => {
      if (fileInput.files.length > 0) {
        handleSelectedFile(fileInput.files[0]);
      } else {
        resetUploadArea();
      }
    });

    if (btnChangeResumeFile) {
      btnChangeResumeFile.addEventListener("click", () => {
        fileInput.click();
      });
    }

    if (btnDiscardResumePreview) {
      btnDiscardResumePreview.addEventListener("click", () => {
        resetUploadArea();
      });
    }

    ["dragenter", "dragover"].forEach(eventName => {
      uploadArea.addEventListener(eventName, (e) => {
        e.preventDefault();
        e.stopPropagation();
        uploadArea.classList.add("dragover-active");
        uploadArea.style.borderColor = "var(--primary-blue)";
        uploadArea.style.backgroundColor = "var(--secondary-light)";
      }, false);
    });

    ["dragleave"].forEach(eventName => {
      uploadArea.addEventListener(eventName, (e) => {
        e.preventDefault();
        e.stopPropagation();
        uploadArea.classList.remove("dragover-active");
        if (fileInput.files.length === 0) {
          uploadArea.style.borderColor = "var(--border-color)";
          uploadArea.style.backgroundColor = "#ffffff";
        }
      }, false);
    });

    uploadArea.addEventListener("drop", (e) => {
      e.preventDefault();
      e.stopPropagation();
      uploadArea.classList.remove("dragover-active");
      const dt = e.dataTransfer;
      const files = dt.files;
      if (files.length > 0) {
        const file = files[0];
        const lowerName = file.name.toLowerCase();
        if (lowerName.endsWith(".pdf") || lowerName.endsWith(".docx")) {
          fileInput.files = files;
          const isDocx = lowerName.endsWith(".docx");
          const iconClass = isDocx ? "fa-file-word text-primary" : "fa-file-pdf text-danger";
          uploadArea.querySelector("p").innerHTML = `<i class="fa-solid ${iconClass} me-2"></i>Selected File: <b>${file.name}</b>`;
          uploadArea.style.borderColor = "var(--primary-blue)";
          uploadArea.style.backgroundColor = "var(--secondary-light)";
        }
      }
    });
  }

  const progressTitle = document.getElementById("progressTitle");
  const progressSubtitle = document.getElementById("progressSubtitle");
  const analysisProgressBar = document.getElementById("analysisProgressBar");
  const progressSpinner = document.getElementById("progressSpinner");
  const progressErrorAlert = document.getElementById("progressErrorAlert");
  const progressErrorMessage = document.getElementById("progressErrorMessage");
  const btnCloseProgressError = document.getElementById("btnCloseProgressError");

  if (btnCloseProgressError) {
    btnCloseProgressError.addEventListener("click", () => {
      if (progressOverlay) progressOverlay.classList.add("d-none");
      if (progressSpinner) progressSpinner.classList.remove("d-none");
      if (progressErrorAlert) progressErrorAlert.classList.add("d-none");
    });
  }

  const stepOrder = ["step1", "step2", "step3", "step4", "step5", "step6"];

  function resetProgressPanel() {
    if (progressSpinner) progressSpinner.classList.remove("d-none");
    if (progressErrorAlert) progressErrorAlert.classList.add("d-none");
    if (progressTitle) progressTitle.textContent = "Coordinating AI Agents";
    if (progressSubtitle) progressSubtitle.textContent = "Orchestrating multi-agent analysis in progress...";
    if (analysisProgressBar) {
      analysisProgressBar.style.width = "5%";
      analysisProgressBar.setAttribute("aria-valuenow", "5");
      analysisProgressBar.classList.add("progress-bar-animated");
    }

    stepOrder.forEach((stepId, idx) => {
      const el = document.getElementById(stepId);
      if (el) {
        el.className = "progress-step text-muted";
        const ind = el.querySelector(".step-indicator");
        if (ind) ind.textContent = String(idx + 1);
      }
    });
  }

  function startPolling(statusUrl, onMilestone, onError) {
    if (!statusUrl) return;
    let pollCount = 0;
    const maxPolls = 120; // 2 minutes max
    const pollInterval = setInterval(async () => {
      pollCount++;
      try {
        const res = await fetch(statusUrl);
        if (!res.ok) throw new Error("Status check failed");
        const statusData = await res.json();

        if (statusData.milestones && statusData.milestones.length > 0) {
          const latest = statusData.milestones[statusData.milestones.length - 1];
          onMilestone(latest);
        }

        if (statusData.status === "completed") {
          clearInterval(pollInterval);
          onMilestone({
            status: "completed",
            step: "completed",
            progress: 100,
            redirect_url: statusData.redirect_url,
            message: "Multi-agent placement analysis complete! Redirecting..."
          });
        } else if (statusData.status === "error") {
          clearInterval(pollInterval);
          onError(statusData.error_message || "Analysis pipeline encountered an error.");
        } else if (pollCount >= maxPolls) {
          clearInterval(pollInterval);
          onError("Analysis pipeline timed out. Please try again.");
        }
      } catch (err) {
        if (pollCount >= maxPolls) {
          clearInterval(pollInterval);
          onError("Failed to poll analysis status: " + err.message);
        }
      }
    }, 1000);
  }

  // Multi-Company Matrix Mode Controls
  const batchModeSwitch = document.getElementById("batchModeSwitch");
  const singleCompanyContainer = document.getElementById("singleCompanyContainer");
  const batchCompanyContainer = document.getElementById("batchCompanyContainer");
  const submitBtnSpan = uploadForm.querySelector("button[type='submit'] span");
  const submitBtnIcon = uploadForm.querySelector("button[type='submit'] i");

  if (batchModeSwitch) {
    batchModeSwitch.addEventListener("change", () => {
      const isBatch = batchModeSwitch.checked;
      if (isBatch) {
        if (singleCompanyContainer) singleCompanyContainer.classList.add("d-none");
        if (batchCompanyContainer) batchCompanyContainer.classList.remove("d-none");
        if (submitBtnSpan) submitBtnSpan.textContent = "Compare Companies & Offer Matrix";
        if (submitBtnIcon) submitBtnIcon.className = "fa-solid fa-chart-pie me-2";
      } else {
        if (singleCompanyContainer) singleCompanyContainer.classList.remove("d-none");
        if (batchCompanyContainer) batchCompanyContainer.classList.add("d-none");
        if (submitBtnSpan) submitBtnSpan.textContent = "Analyze Readiness";
        if (submitBtnIcon) submitBtnIcon.className = "fa-solid fa-wand-magic-sparkles me-2";
      }
    });
  }

  // Pill badge toggles
  const companyPills = document.querySelectorAll(".company-pill-badge");
  companyPills.forEach((pill) => {
    const chk = pill.querySelector(".company-pill-check");
    pill.addEventListener("click", (e) => {
      if (e.target !== chk) {
        chk.checked = !chk.checked;
      }
      if (chk.checked) {
        pill.classList.add("active");
      } else {
        pill.classList.remove("active");
      }
    });
    if (chk && chk.checked) {
      pill.classList.add("active");
    }
  });

  const btnSelectAllBatch = document.getElementById("btnSelectAllBatch");
  if (btnSelectAllBatch) {
    btnSelectAllBatch.addEventListener("click", () => {
      companyPills.forEach((pill, idx) => {
        const chk = pill.querySelector(".company-pill-check");
        if (chk) {
          chk.checked = (idx < 4);
          if (chk.checked) pill.classList.add("active");
          else pill.classList.remove("active");
        }
      });
    });
  }

  const btnClearAllBatch = document.getElementById("btnClearAllBatch");
  if (btnClearAllBatch) {
    btnClearAllBatch.addEventListener("click", () => {
      companyPills.forEach((pill) => {
        const chk = pill.querySelector(".company-pill-check");
        if (chk) {
          chk.checked = false;
          pill.classList.remove("active");
        }
      });
    });
  }

  // -------------------------------------------------------------
  // Handle Clear All option for the Analysis Form
  // -------------------------------------------------------------
  const btnClearForm = document.getElementById("btnClearForm");
  const formResetAlert = document.getElementById("formResetAlert");
  const formResetAlertMessage = document.getElementById("formResetAlertMessage");
  const btnCloseResetAlert = document.getElementById("btnCloseResetAlert");

  if (btnCloseResetAlert && formResetAlert) {
    btnCloseResetAlert.addEventListener("click", () => {
      formResetAlert.classList.add("d-none");
    });
  }

  if (btnClearForm) {
    btnClearForm.addEventListener("click", (e) => {
      // Ensure the button does not accidentally submit the form
      e.preventDefault();
      e.stopPropagation();

      // 1. Clear the pasted resume text
      const resumeTextInput = document.getElementById("resumeTextInput");
      if (resumeTextInput) {
        resumeTextInput.value = "";
      }

      // 2. Remove the currently selected PDF/DOCX file and reset upload dropzone
      resetUploadArea();

      // 3. Clear the job description field
      const jdInput = document.getElementById("jdInput");
      if (jdInput) {
        jdInput.value = "";
      }

      // 4. Reset the selected company to General Placement Preparation
      const companySelect = document.getElementById("companySelect");
      if (companySelect) {
        companySelect.value = "General";
      }

      // Reset candidate profile identifier if present
      const studentIdInput = document.getElementById("studentIdInput");
      if (studentIdInput) {
        studentIdInput.value = "";
      }

      // Reset batch matrix mode switch if active
      if (batchModeSwitch && batchModeSwitch.checked) {
        batchModeSwitch.checked = false;
        batchModeSwitch.dispatchEvent(new Event("change"));
      }

      // Switch back to the primary Upload PDF / DOCX tab
      const pdfTab = document.getElementById("pdf-tab");
      if (pdfTab) {
        if (window.bootstrap && window.bootstrap.Tab) {
          const tabInstance = window.bootstrap.Tab.getOrCreateInstance(pdfTab);
          tabInstance.show();
        } else {
          pdfTab.click();
        }
      }

      // 5. Display a confirmation message after clearing the form
      if (formResetAlert) {
        if (formResetAlertMessage) {
          formResetAlertMessage.textContent = "Form cleared successfully. All inputs have been reset.";
        }
        formResetAlert.classList.remove("d-none");

        // Scroll confirmation alert into view smoothly if not visible
        formResetAlert.scrollIntoView({ behavior: "smooth", block: "nearest" });

        // Auto-dismiss after 4 seconds
        clearTimeout(window._formResetAlertTimeout);
        window._formResetAlertTimeout = setTimeout(() => {
          formResetAlert.classList.add("d-none");
        }, 4000);
      }
    });
  }

  // Intercept form submit to launch async pipeline & stream milestones via EventSource
  uploadForm.addEventListener("submit", (e) => {
    e.preventDefault();

    const textInput = document.getElementById("resumeTextInput");
    const hasFile = fileInput && fileInput.files && fileInput.files.length > 0;
    const hasText = textInput && textInput.value.trim().length > 0;

    if (!hasFile && !hasText) {
      alert("Please upload a PDF or DOCX resume, or paste your resume text first.");
      return;
    }

    // Handle Multi-Company Matrix Mode Submission
    if (batchModeSwitch && batchModeSwitch.checked) {
      const selectedChecks = uploadForm.querySelectorAll(".company-pill-check:checked");
      if (selectedChecks.length === 0) {
        alert("Please select at least one company to compare.");
        return;
      }

      resetProgressPanel();
      if (progressOverlay) progressOverlay.classList.remove("d-none");
      if (progressTitle) progressTitle.textContent = "Multi-Company Batch Comparison";
      if (progressSubtitle) progressSubtitle.textContent = "Evaluating candidate profile across selected recruiters in background workers...";
      if (analysisProgressBar) {
        analysisProgressBar.style.width = "40%";
        analysisProgressBar.setAttribute("aria-valuenow", "40");
      }

      const formData = new FormData(uploadForm);
      fetch("/api/analysis/batch-compare", {
        method: "POST",
        body: formData,
        headers: { "X-Requested-With": "XMLHttpRequest" }
      })
        .then(async (response) => {
          const data = await response.json();
          if (!response.ok || !data.success) {
            throw new Error(data.error || "Batch comparison failed.");
          }
          if (analysisProgressBar) analysisProgressBar.style.width = "100%";
          if (progressTitle) progressTitle.textContent = "Comparison Complete!";
          if (progressSubtitle) progressSubtitle.textContent = "Redirecting to your Company Comparison Matrix...";
          setTimeout(() => {
            window.location.href = data.redirect_url;
          }, 500);
        })
        .catch((err) => {
          showPipelineError(err.message || "An unexpected error occurred during batch comparison.");
        });
      return;
    }

    // Display and reset progress overlay
    resetProgressPanel();
    if (progressOverlay) progressOverlay.classList.remove("d-none");

    let eventSource = null;

    function handleMilestone(milestone) {
      const step = milestone.step;
      const progress = milestone.progress || 0;
      const message = milestone.message || "";
      const status = milestone.status;

      if (analysisProgressBar) {
        analysisProgressBar.style.width = `${progress}%`;
        analysisProgressBar.setAttribute("aria-valuenow", String(progress));
      }

      if (progressSubtitle && message) {
        progressSubtitle.textContent = message;
      }

      if (step && step.startsWith("step")) {
        const currentIdx = stepOrder.indexOf(step);
        stepOrder.forEach((sId, idx) => {
          const el = document.getElementById(sId);
          if (!el) return;
          const ind = el.querySelector(".step-indicator");
          if (idx < currentIdx) {
            el.className = "progress-step completed";
            if (ind) ind.innerHTML = "✓";
          } else if (idx === currentIdx) {
            el.className = "progress-step active";
          } else {
            el.className = "progress-step text-muted";
          }
        });
      }

      if (status === "completed" || step === "completed") {
        stepOrder.forEach((sId) => {
          const el = document.getElementById(sId);
          if (el) {
            el.className = "progress-step completed";
            const ind = el.querySelector(".step-indicator");
            if (ind) ind.innerHTML = "✓";
          }
        });

        if (analysisProgressBar) {
          analysisProgressBar.style.width = "100%";
          analysisProgressBar.classList.remove("progress-bar-animated");
        }
        if (progressTitle) progressTitle.textContent = "Analysis Complete!";
        if (progressSubtitle) progressSubtitle.textContent = "Redirecting to your comprehensive placement dashboard...";

        if (eventSource) {
          eventSource.close();
          eventSource = null;
        }

        const redirectUrl = milestone.redirect_url ||
          (milestone.extra && milestone.extra.redirect_url) ||
          "/";
        setTimeout(() => {
          window.location.href = redirectUrl;
        }, 500);
      } else if (status === "error" || step === "error") {
        if (eventSource) {
          eventSource.close();
          eventSource = null;
        }
        showPipelineError(milestone.message || "An unexpected error occurred during multi-agent analysis.");
      }
    }

    function showPipelineError(errMsg) {
      if (progressSpinner) progressSpinner.classList.add("d-none");
      if (progressErrorAlert && progressErrorMessage) {
        progressErrorMessage.textContent = errMsg;
        progressErrorAlert.classList.remove("d-none");
      } else {
        alert(errMsg);
        if (progressOverlay) progressOverlay.classList.add("d-none");
      }
    }

    const formData = new FormData(uploadForm);

    fetch("/api/analysis/start", {
      method: "POST",
      body: formData
    })
      .then(async (response) => {
        const data = await response.json();
        if (!response.ok || !data.success) {
          throw new Error(data.error || "Failed to initiate background analysis.");
        }

        const streamUrl = data.stream_url;
        const statusUrl = data.status_url;

        if (window.EventSource && streamUrl) {
          eventSource = new EventSource(streamUrl);

          eventSource.onmessage = (event) => {
            try {
              const milestone = JSON.parse(event.data);
              handleMilestone(milestone);
            } catch (jsonErr) {
              console.error("Failed to parse SSE JSON payload:", jsonErr);
            }
          };

          eventSource.onerror = (err) => {
            console.warn("EventSource disconnected; switching to polling fallback.", err);
            if (eventSource) {
              eventSource.close();
              eventSource = null;
            }
            startPolling(statusUrl, handleMilestone, showPipelineError);
          };
        } else {
          startPolling(statusUrl, handleMilestone, showPipelineError);
        }
      })
      .catch((err) => {
        console.error("Async analysis dispatch failed:", err);
        showPipelineError(err.message || "Could not start analysis. Please check your network and retry.");
      });
  });
}

/**
 * Renders the circular SVG progress dials
 */
function initCircularProgress() {
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
function initMockInterview() {
  const chatForm = document.getElementById("chatForm");
  if (!chatForm) return;

  const chatContainer = document.getElementById("chatContainer");
  const answerInput = document.getElementById("answerInput");
  const submitBtn = chatForm.querySelector("button[type='submit']");
  const evaluationPanel = document.getElementById("evaluationPanel");
  const btnMic = document.getElementById("btnMic");
  const voiceRecordingPanel = document.getElementById("voiceRecordingPanel");
  const btnStopVoice = document.getElementById("btnStopVoice");
  const btnClearTranscript = document.getElementById("btnClearTranscript");
  const btnAppendTranscript = document.getElementById("btnAppendTranscript");
  const liveInterimText = document.getElementById("liveInterimText");
  const recordingStatusText = document.getElementById("recordingStatusText");
  const voiceWaveform = document.getElementById("voiceWaveform");
  const speechTimerBadge = document.getElementById("speechTimerBadge");
  const transcriptWordCount = document.getElementById("transcriptWordCount");
  const toggleAutoTTS = document.getElementById("toggleAutoTTS");

  // Video Coach & Composure Telemetry Elements
  const btnToggleVideoCoach = document.getElementById("btnToggleVideoCoach");
  const videoCoachPanel = document.getElementById("videoCoachPanel");
  const btnStopCamera = document.getElementById("btnStopCamera");
  const candidateVideoFeed = document.getElementById("candidateVideoFeed");
  const videoAnalysisCanvas = document.getElementById("videoAnalysisCanvas");
  const cameraStatusText = document.getElementById("cameraStatusText");
  const telemetryEyeScore = document.getElementById("telemetryEyeScore");
  const telemetryEyeStatus = document.getElementById("telemetryEyeStatus");
  const telemetryComposureScore = document.getElementById("telemetryComposureScore");
  const telemetryComposureStatus = document.getElementById("telemetryComposureStatus");
  const telemetryCadenceWpm = document.getElementById("telemetryCadenceWpm");
  const telemetryCadenceStatus = document.getElementById("telemetryCadenceStatus");
  const telemetryFillerCount = document.getElementById("telemetryFillerCount");
  const telemetryFillerList = document.getElementById("telemetryFillerList");

  // State trackers
  let recognition = null;
  let isRecording = false;
  let speechTimerInterval = null;
  let speechSeconds = 0;
  let activeUtteranceBtn = null;

  // Video Coach & Composure States
  let videoStream = null;
  let videoAnalysisInterval = null;
  let eyeCenteredFrames = 0;
  let totalVideoFrames = 0;
  let prevFramePixels = null;
  let composureHistory = [];
  let answerTypingStartTime = null;
  let lastMeasuredWpm = 130;
  let lastMeasuredFillerCount = 0;

  // -------------------------------------------------------------
  // Web MediaStream Video Coach & Non-Verbal Composure Analyzer
  // -------------------------------------------------------------
  async function startVideoCoach() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      alert("Camera access via MediaStream API is not supported in this browser environment.");
      return;
    }
    try {
      if (cameraStatusText) cameraStatusText.textContent = "Requesting camera permission...";
      videoStream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 320 }, height: { ideal: 240 }, facingMode: "user" },
        audio: false,
      });

      if (candidateVideoFeed) {
        candidateVideoFeed.srcObject = videoStream;
        candidateVideoFeed.play().catch(() => {});
      }
      if (videoCoachPanel) videoCoachPanel.classList.remove("d-none");
      if (cameraStatusText) cameraStatusText.textContent = "Live camera active (Tracking non-verbal composure)";
      if (btnToggleVideoCoach) {
        btnToggleVideoCoach.classList.remove("btn-outline-secondary");
        btnToggleVideoCoach.classList.add("btn-primary");
      }

      eyeCenteredFrames = 0;
      totalVideoFrames = 0;
      prevFramePixels = null;
      composureHistory = [];

      startVideoAnalysisLoop();
    } catch (err) {
      console.error("Camera access error:", err);
      if (cameraStatusText) cameraStatusText.textContent = "Camera access denied or unavailable";
      alert("Could not access camera for non-verbal posture analysis. Please grant camera permission in your browser.");
    }
  }

  function stopVideoCoach() {
    if (videoAnalysisInterval) {
      clearInterval(videoAnalysisInterval);
      videoAnalysisInterval = null;
    }
    if (videoStream) {
      videoStream.getTracks().forEach((track) => track.stop());
      videoStream = null;
    }
    if (candidateVideoFeed) {
      candidateVideoFeed.srcObject = null;
    }
    if (videoCoachPanel) videoCoachPanel.classList.add("d-none");
    if (btnToggleVideoCoach) {
      btnToggleVideoCoach.classList.remove("btn-primary");
      btnToggleVideoCoach.classList.add("btn-outline-secondary");
    }
    if (cameraStatusText) cameraStatusText.textContent = "Camera standby";
  }

  function startVideoAnalysisLoop() {
    if (videoAnalysisInterval) clearInterval(videoAnalysisInterval);
    const ctx = videoAnalysisCanvas ? videoAnalysisCanvas.getContext("2d", { willReadFrequently: true }) : null;

    videoAnalysisInterval = setInterval(() => {
      if (!candidateVideoFeed || candidateVideoFeed.readyState < 2 || !ctx) return;

      totalVideoFrames++;
      ctx.drawImage(candidateVideoFeed, 0, 0, 160, 120);
      const frame = ctx.getImageData(0, 0, 160, 120);
      const data = frame.data;

      // 1. Center quadrant eye-contact presence estimation
      let centerLuminance = 0;
      let centerCount = 0;
      for (let y = 20; y < 85; y += 4) {
        for (let x = 40; x < 120; x += 4) {
          const idx = (y * 160 + x) * 4;
          centerLuminance += 0.299 * data[idx] + 0.587 * data[idx + 1] + 0.114 * data[idx + 2];
          centerCount++;
        }
      }
      const avgCenter = centerCount > 0 ? centerLuminance / centerCount : 128;
      const isCentered = avgCenter > 25 && avgCenter < 235;
      if (isCentered) eyeCenteredFrames++;

      const eyeScore = Math.min(100, Math.max(35, Math.round((eyeCenteredFrames / totalVideoFrames) * 100)));
      if (telemetryEyeScore) telemetryEyeScore.textContent = `${eyeScore}%`;
      if (telemetryEyeStatus) {
        if (eyeScore >= 75) {
          telemetryEyeStatus.textContent = "Good Center Focus";
          telemetryEyeStatus.className = "text-success fw-semibold";
        } else if (eyeScore >= 50) {
          telemetryEyeStatus.textContent = "Intermittent";
          telemetryEyeStatus.className = "text-warning fw-semibold";
        } else {
          telemetryEyeStatus.textContent = "Looking Away";
          telemetryEyeStatus.className = "text-danger fw-semibold";
        }
      }

      // 2. Motion velocity delta for Composure estimation
      if (prevFramePixels) {
        let diffSum = 0;
        const sampleCount = data.length / 16;
        for (let i = 0; i < data.length; i += 16) {
          diffSum += Math.abs(data[i] - prevFramePixels[i]);
        }
        const avgDelta = diffSum / sampleCount;
        let frameComposure = 90;
        if (avgDelta < 15) {
          frameComposure = 90 - Math.round(avgDelta);
        } else {
          frameComposure = Math.max(40, 80 - Math.round((avgDelta - 15) * 2));
        }
        composureHistory.push(frameComposure);
        if (composureHistory.length > 25) composureHistory.shift();

        const avgComposure = Math.round(composureHistory.reduce((a, b) => a + b, 0) / composureHistory.length);
        if (telemetryComposureScore) telemetryComposureScore.textContent = `${avgComposure}%`;
        if (telemetryComposureStatus) {
          if (avgComposure >= 75) {
            telemetryComposureStatus.textContent = "Calm & Steady";
            telemetryComposureStatus.className = "text-primary fw-semibold";
          } else if (avgComposure >= 55) {
            telemetryComposureStatus.textContent = "Mild Movement";
            telemetryComposureStatus.className = "text-warning fw-semibold";
          } else {
            telemetryComposureStatus.textContent = "Restless Motion";
            telemetryComposureStatus.className = "text-danger fw-semibold";
          }
        }
      }
      prevFramePixels = new Uint8Array(data);
    }, 400);
  }

  if (btnToggleVideoCoach) {
    btnToggleVideoCoach.addEventListener("click", () => {
      if (videoStream) {
        stopVideoCoach();
      } else {
        startVideoCoach();
      }
    });
  }

  if (btnStopCamera) {
    btnStopCamera.addEventListener("click", () => {
      stopVideoCoach();
    });
  }

  // Live Speech Cadence & Verbal Filler Analyzer
  const FILLER_WORDS_REGEX = /\b(um+|uh+|like|you know|actually|basically|literally|sort of|kind of|i mean|so yeah)\b/gi;

  function updateLiveSpeechTelemetry(text) {
    if (!text) {
      if (telemetryCadenceWpm) telemetryCadenceWpm.textContent = "-- WPM";
      if (telemetryFillerCount) telemetryFillerCount.textContent = "0";
      return;
    }
    const words = text.split(/\s+/).filter(Boolean);
    const wordCount = words.length;

    if (!answerTypingStartTime && wordCount > 0) {
      answerTypingStartTime = Date.now();
    }

    if (answerTypingStartTime && wordCount >= 3) {
      const elapsedMinutes = (Date.now() - answerTypingStartTime) / 60000;
      const wpm = elapsedMinutes > 0.04 ? Math.round(wordCount / elapsedMinutes) : 130;
      lastMeasuredWpm = wpm;
      if (telemetryCadenceWpm) telemetryCadenceWpm.textContent = `${wpm} WPM`;
      if (telemetryCadenceStatus) {
        if (wpm >= 110 && wpm <= 165) {
          telemetryCadenceStatus.textContent = "Optimal Pacing";
          telemetryCadenceStatus.className = "text-success fw-semibold";
        } else if (wpm > 165) {
          telemetryCadenceStatus.textContent = "Too Fast";
          telemetryCadenceStatus.className = "text-warning fw-semibold";
        } else {
          telemetryCadenceStatus.textContent = "Slow Delivery";
          telemetryCadenceStatus.className = "text-secondary fw-semibold";
        }
      }
    }

    const fillerMatches = text.match(FILLER_WORDS_REGEX) || [];
    lastMeasuredFillerCount = fillerMatches.length;
    if (telemetryFillerCount) telemetryFillerCount.textContent = String(fillerMatches.length);
    if (telemetryFillerList) {
      if (fillerMatches.length > 0) {
        const unique = Array.from(new Set(fillerMatches.map((f) => f.toLowerCase())));
        telemetryFillerList.textContent = unique.slice(0, 3).join(", ");
        telemetryFillerList.className = "text-warning fw-semibold text-truncate";
      } else {
        telemetryFillerList.textContent = "Clean speech";
        telemetryFillerList.className = "text-success";
      }
    }
  }

  // -------------------------------------------------------------
  // 1. Text-to-Speech (TTS) Speech Synthesis Integration
  // -------------------------------------------------------------
  const isSpeechSynthesisSupported = "speechSynthesis" in window;

  // Restore Auto-Speak toggle preference from localStorage
  if (toggleAutoTTS) {
    const savedAutoTTS = localStorage.getItem("ai_interview_auto_tts");
    if (savedAutoTTS !== null) {
      toggleAutoTTS.checked = savedAutoTTS === "true";
    }
    toggleAutoTTS.addEventListener("change", () => {
      localStorage.setItem("ai_interview_auto_tts", toggleAutoTTS.checked);
      if (!toggleAutoTTS.checked && isSpeechSynthesisSupported) {
        window.speechSynthesis.cancel();
        resetSpeakingButtons();
      }
    });
  }

  function resetSpeakingButtons() {
    document.querySelectorAll(".btn-tts.speaking").forEach((btn) => {
      btn.classList.remove("speaking");
      btn.innerHTML = '<i class="fa-solid fa-volume-high me-1"></i> Listen';
    });
    activeUtteranceBtn = null;
  }

  function speakQuestionText(text, btnElement = null) {
    if (!isSpeechSynthesisSupported || !text) return;

    // Toggle stop if already speaking this button
    if (btnElement && btnElement.classList.contains("speaking")) {
      window.speechSynthesis.cancel();
      resetSpeakingButtons();
      return;
    }

    window.speechSynthesis.cancel();
    resetSpeakingButtons();

    // Clean plain text without markdown or HTML tags
    const cleanText = text.replace(/<[^>]*>?/gm, "").replace(/\[\s*Question[^\]]+\]/gi, "").trim();
    if (!cleanText) return;

    const utterance = new SpeechSynthesisUtterance(cleanText);
    utterance.rate = 0.95;
    utterance.pitch = 1.0;

    // Choose preferred English voice if available
    const voices = window.speechSynthesis.getVoices();
    const englishVoice = voices.find((v) => v.lang.startsWith("en") && (v.name.includes("Google") || v.name.includes("Natural") || v.name.includes("Online")));
    if (englishVoice) {
      utterance.voice = englishVoice;
    }

    if (btnElement) {
      activeUtteranceBtn = btnElement;
      btnElement.classList.add("speaking");
      btnElement.innerHTML = '<i class="fa-solid fa-volume-xmark me-1"></i> Stop';
    }

    utterance.onend = () => {
      resetSpeakingButtons();
    };

    utterance.onerror = () => {
      resetSpeakingButtons();
    };

    window.speechSynthesis.speak(utterance);
  }

  // Delegated click listener for all TTS buttons (initial & dynamically added)
  if (chatContainer) {
    chatContainer.addEventListener("click", (e) => {
      const ttsBtn = e.target.closest(".btn-tts");
      if (ttsBtn) {
        e.preventDefault();
        const textToSpeak = ttsBtn.getAttribute("data-tts") || ttsBtn.closest(".chat-bubble-agent")?.innerText || "";
        speakQuestionText(textToSpeak, ttsBtn);
      }
    });
  }

  // Auto-speak initial active question if toggle is checked
  if (toggleAutoTTS && toggleAutoTTS.checked) {
    setTimeout(() => {
      const currentQBubble = document.getElementById("currentQuestionBubble");
      if (currentQBubble) {
        const initialTtsBtn = currentQBubble.querySelector(".btn-tts");
        const currentQText = document.getElementById("currentQuestionText")?.innerText || "";
        speakQuestionText(currentQText, initialTtsBtn);
      }
    }, 600);
  }

  // -------------------------------------------------------------
  // 2. Web Speech API (Speech-to-Text) & Audio Visualizer
  // -------------------------------------------------------------
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

  if (SpeechRecognition) {
    recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = "en-US";

    let finalTranscript = "";

    function updateSpeechTimer() {
      speechSeconds++;
      const mins = String(Math.floor(speechSeconds / 60)).padStart(2, "0");
      const secs = String(speechSeconds % 60).padStart(2, "0");
      if (speechTimerBadge) {
        speechTimerBadge.innerHTML = `<i class="fa-regular fa-clock me-1"></i>${mins}:${secs}`;
      }
    }

    function updateTranscriptWordCount(text) {
      if (!transcriptWordCount) return;
      const count = text.trim() ? text.trim().split(/\s+/).filter(Boolean).length : 0;
      transcriptWordCount.innerText = `${count} word${count === 1 ? "" : "s"} captured`;
    }

    function startVoiceRecording() {
      try {
        finalTranscript = liveInterimText ? liveInterimText.innerText.trim() : "";
        recognition.start();
        isRecording = true;
        speechSeconds = 0;
        if (speechTimerBadge) speechTimerBadge.innerHTML = '<i class="fa-regular fa-clock me-1"></i>00:00';
        clearInterval(speechTimerInterval);
        speechTimerInterval = setInterval(updateSpeechTimer, 1000);

        if (voiceRecordingPanel) voiceRecordingPanel.classList.remove("d-none");
        if (voiceWaveform) voiceWaveform.classList.add("active");
        if (recordingStatusText) {
          recordingStatusText.innerText = "Listening... Speak clearly into your microphone";
          recordingStatusText.className = "small fw-bold text-danger";
        }
        if (btnMic) {
          btnMic.classList.remove("btn-outline-primary");
          btnMic.classList.add("btn-danger");
          btnMic.innerHTML = '<i class="fa-solid fa-microphone-slash me-1"></i> Stop Voice';
        }
      } catch (err) {
        console.warn("Speech recognition already running or permission issue:", err);
      }
    }

    function stopVoiceRecording() {
      if (!isRecording) return;
      isRecording = false;
      try {
        recognition.stop();
      } catch (e) {
        // Safe ignore
      }
      clearInterval(speechTimerInterval);
      if (voiceWaveform) voiceWaveform.classList.remove("active");
      if (recordingStatusText) {
        recordingStatusText.innerText = "Recording paused. Review or insert your answer below.";
        recordingStatusText.className = "small fw-bold text-success";
      }
      if (btnMic) {
        btnMic.classList.remove("btn-danger");
        btnMic.classList.add("btn-outline-primary");
        btnMic.innerHTML = '<i class="fa-solid fa-microphone"></i> <span class="d-none d-sm-inline small fw-semibold">Voice Answer</span>';
      }
    }

    if (btnMic) {
      btnMic.addEventListener("click", () => {
        if (!isRecording) {
          startVoiceRecording();
        } else {
          stopVoiceRecording();
        }
      });
    }

    if (btnStopVoice) {
      btnStopVoice.addEventListener("click", () => {
        stopVoiceRecording();
      });
    }

    recognition.onresult = (event) => {
      let interimTranscript = "";
      for (let i = event.resultIndex; i < event.results.length; ++i) {
        if (event.results[i].isFinal) {
          finalTranscript += " " + event.results[i][0].transcript;
        } else {
          interimTranscript += event.results[i][0].transcript;
        }
      }

      const fullCapturedText = (finalTranscript + " " + interimTranscript).trim();
      if (liveInterimText) {
        liveInterimText.innerText = fullCapturedText;
        updateTranscriptWordCount(fullCapturedText);
      }
    };

    recognition.onerror = (event) => {
      console.warn("Speech recognition error:", event.error);
      if (recordingStatusText) {
        recordingStatusText.innerText = `Microphone notice: ${event.error}. Click Voice Answer to retry.`;
        recordingStatusText.className = "small fw-semibold text-warning";
      }
      stopVoiceRecording();
    };

    recognition.onend = () => {
      if (isRecording) {
        // Auto-restart if continuous dictation stopped unexpectedly
        try {
          recognition.start();
        } catch (e) {
          stopVoiceRecording();
        }
      } else {
        stopVoiceRecording();
      }
    };

    // Pre-submission review actions
    if (btnClearTranscript && liveInterimText) {
      btnClearTranscript.addEventListener("click", () => {
        liveInterimText.innerText = "";
        finalTranscript = "";
        updateTranscriptWordCount("");
      });
    }

    if (btnAppendTranscript && liveInterimText && answerInput) {
      btnAppendTranscript.addEventListener("click", () => {
        const textToInsert = liveInterimText.innerText.trim();
        if (textToInsert) {
          const currentVal = answerInput.value.trim();
          answerInput.value = currentVal ? `${currentVal} ${textToInsert}` : textToInsert;
          triggerWordCounter(answerInput);
          liveInterimText.innerText = "";
          finalTranscript = "";
          updateTranscriptWordCount("");
          stopVoiceRecording();
          answerInput.focus();
        }
      });
    }

    if (liveInterimText) {
      liveInterimText.addEventListener("input", () => {
        finalTranscript = liveInterimText.innerText.trim();
        updateTranscriptWordCount(finalTranscript);
      });
    }

  } else {
    // Web Speech API not supported in browser
    if (btnMic) {
      btnMic.title = "Speech recognition is not supported in this browser. Please use Chrome, Edge, or Safari.";
      btnMic.style.opacity = "0.7";
      btnMic.addEventListener("click", () => {
        alert("Speech Recognition is not supported by your browser. Please use Google Chrome, Microsoft Edge, or Safari for voice answering.");
      });
    }
  }

  // -------------------------------------------------------------
  // 3. Live Word Counter & Keyboard Shortcuts
  // -------------------------------------------------------------
  function triggerWordCounter(inputEl) {
    const wordCountBadge = document.getElementById("wordCountBadge");
    if (!wordCountBadge || !inputEl) return;
    const text = inputEl.value.trim();
    const words = text ? text.split(/\s+/).filter(Boolean).length : 0;
    wordCountBadge.innerText = `${words} word${words === 1 ? "" : "s"}`;
    if (words >= 30) {
      wordCountBadge.className = "text-success fw-bold";
    } else {
      wordCountBadge.className = "text-secondary fw-semibold";
    }
  }

  if (answerInput) {
    answerInput.addEventListener("input", () => {
      triggerWordCounter(answerInput);
      updateLiveSpeechTelemetry(answerInput.value);
    });

    answerInput.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        chatForm.requestSubmit();
      }
    });
  }

  // -------------------------------------------------------------
  // 4. Form Submission & Agent Evaluation Pipeline
  // -------------------------------------------------------------
  chatForm.addEventListener("submit", async (e) => {
    e.preventDefault();

    const answer = answerInput.value ? answerInput.value.trim() : "";
    if (!answer) {
      alert("Please enter or voice-record a response before submitting.");
      return;
    }

    // Stop active speech recognition or TTS
    if (recognition && isRecording) {
      try { recognition.stop(); } catch (err) {}
      isRecording = false;
    }
    if (isSpeechSynthesisSupported) {
      window.speechSynthesis.cancel();
      resetSpeakingButtons();
    }

    const questionIndex = parseInt(chatForm.getAttribute("data-question-index"));
    const analysisId = chatForm.getAttribute("data-analysis-id");

    // Compute final speech and non-verbal telemetry metrics
    const finalSpeechMetrics = {
      duration_seconds: speechSeconds || (answerTypingStartTime ? Math.max(5, Math.round((Date.now() - answerTypingStartTime) / 1000)) : 30),
      words_per_minute: lastMeasuredWpm || 130,
      filler_word_count: lastMeasuredFillerCount || 0,
      eye_contact_ratio: totalVideoFrames > 0 ? (eyeCenteredFrames / totalVideoFrames) : 0.85,
      composure_score: composureHistory.length > 0 ? Math.round(composureHistory.reduce((a, b) => a + b, 0) / composureHistory.length) : 85,
    };

    // Reset session timer counters for next answer
    answerTypingStartTime = null;

    // Disable inputs during evaluation
    answerInput.disabled = true;
    submitBtn.disabled = true;
    submitBtn.innerHTML = `<span class="spinner-border spinner-border-sm" role="status"></span>`;

    // Append user answer bubble
    appendChatBubble(answer, "user");
    answerInput.value = "";
    triggerWordCounter(answerInput);

    // Append loader bubble from Interview Agent
    const loaderBubble = appendChatBubble("<i>Interview Agent & STAR Story Coach are analyzing your response and verbal delivery...</i>", "agent");

    try {
      const response = await fetch(`/interview/${analysisId}/submit`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify({
          answer: answer,
          question_index: questionIndex,
          speech_metrics: finalSpeechMetrics,
        }),
      });

      const result = await response.json();
      loaderBubble.remove();

      if (result.error) {
        appendChatBubble(`Error: ${result.error}`, "agent");
        resetInputs();
        return;
      }

      // Display dynamic feedback scores on dashboard panels
      renderEvaluationDetails(result.evaluation, questionIndex + 1);

      // Construct rich feedback bubble with verbal metrics and STAR pills
      const evalData = result.evaluation;
      const verbalClarity = evalData.verbal_clarity_score || evalData.communication_score || 50;
      let fillersHtml = "";
      if (evalData.filler_words_detected && evalData.filler_words_detected.length > 0) {
        fillersHtml = `
          <div class="small mb-1 text-muted">
            <i class="fa-solid fa-microphone-lines text-warning me-1"></i> <b>Fillers detected (${evalData.filler_word_count || evalData.filler_words_detected.length}):</b>
            ${evalData.filler_words_detected.map(fw => `<span class="badge bg-warning-subtle text-dark border border-warning-subtle rounded-pill me-1">${fw}</span>`).join("")}
          </div>
        `;
      }

      let starHtml = "";
      const se = evalData.star_evaluation;
      if (se) {
        starHtml = `
          <div class="p-2 bg-white rounded-3 border mb-2">
            <div class="d-flex justify-content-between align-items-center mb-1">
              <small class="fw-bold text-dark"><i class="fa-solid fa-sitemap text-primary me-1"></i> STAR Story Breakdown</small>
              <span class="badge bg-primary-subtle text-primary border border-primary-subtle rounded-pill">STAR Score: ${se.star_score}/100</span>
            </div>
            <div class="d-flex flex-wrap gap-1 mb-1">
              <span class="badge ${se.situation.present ? 'bg-success-subtle text-success border border-success-subtle' : 'bg-danger-subtle text-danger border border-danger-subtle'} rounded-pill">S: ${se.situation.score}%</span>
              <span class="badge ${se.task.present ? 'bg-success-subtle text-success border border-success-subtle' : 'bg-danger-subtle text-danger border border-danger-subtle'} rounded-pill">T: ${se.task.score}%</span>
              <span class="badge ${se.action.present ? 'bg-success-subtle text-success border border-success-subtle' : 'bg-danger-subtle text-danger border border-danger-subtle'} rounded-pill">A: ${se.action.score}%</span>
              <span class="badge ${se.result.present ? 'bg-success-subtle text-success border border-success-subtle' : 'bg-danger-subtle text-danger border border-danger-subtle'} rounded-pill">R: ${se.result.score}%</span>
            </div>
            ${se.coaching && se.coaching.suggested_star_rewrite ? `<div class="small text-muted fst-italic"><b>Coach's Model Rewrite:</b> "${se.coaching.suggested_star_rewrite}"</div>` : ''}
          </div>
        `;
      } else if (evalData.star_method_analysis && evalData.star_method_analysis.feedback) {
        starHtml = `
          <div class="small mb-2 text-secondary">
            <i class="fa-solid fa-sitemap text-primary me-1"></i> <b>STAR Framework:</b> ${evalData.star_method_analysis.feedback}
          </div>
        `;
      }

      const feedbackText = `
        <div class="d-flex justify-content-between align-items-center mb-2 pb-1 border-bottom">
          <span class="badge bg-success-subtle text-success border border-success-subtle rounded-pill px-3 py-1">
            <i class="fa-solid fa-clipboard-check me-1"></i> AI Feedback &amp; Analysis
          </span>
        </div>
        <div class="mb-2 lh-base"><b>Feedback:</b> ${evalData.feedback}</div>
        <div class="row g-2 mt-2 mb-2">
          <div class="col-sm-4"><span class="badge bg-primary-subtle text-primary border border-primary-subtle w-100 py-1">Tech: ${evalData.technical_accuracy}/100</span></div>
          <div class="col-sm-4"><span class="badge bg-success-subtle text-success border border-success-subtle w-100 py-1">Comm: ${evalData.communication_score}/100</span></div>
          <div class="col-sm-4"><span class="badge bg-info-subtle text-info border border-info-subtle w-100 py-1">Verbal Clarity: ${verbalClarity}/100</span></div>
        </div>
        ${fillersHtml}
        ${starHtml}
        <b>Suggestions:</b><br/>
        <ul class="mb-0 mt-1 list-unstyled">
          ${(evalData.suggestions || []).map(s => `
            <li class="mb-1 p-2 bg-white rounded-3 border d-flex justify-content-between align-items-center gap-2">
              <span class="small text-dark">${escapeHtml(s)}</span>
              <button type="button" class="btn btn-sm btn-outline-secondary rounded-pill py-0 px-2 flex-shrink-0 btn-copy-suggestion" data-copy-text="${escapeHtml(s)}" title="Copy suggestion">
                <i class="fa-regular fa-copy"></i>
              </button>
            </li>
          `).join("")}
        </ul>
      `;
      appendChatBubble(feedbackText, "evaluation");

      if (result.completed) {
        appendChatBubble("<b>Congratulations! You have completed all mock interview questions.</b><br/>Please review your performance summary and export your transcript.", "agent");
        resetInputs(true);
        document.getElementById("btnBackToDashboard")?.classList.remove("btn-outline-secondary");
        document.getElementById("btnBackToDashboard")?.classList.add("btn-primary");

        if (result.summary) {
          showCompletionModal(result.summary);
        }
      } else {
        // Move to next question with TTS vocalization
        const nextQ = result.next_question;
        setTimeout(() => {
          const nextBubbleHtml = `
            <div class="d-flex justify-content-between align-items-center mb-2 pb-1 border-bottom">
              <span class="badge bg-primary text-white rounded-pill px-3 py-1 shadow-sm">
                <i class="fa-solid fa-play me-1"></i> Active Question ${nextQ.index + 1} &bull; ${nextQ.type}
              </span>
              <button type="button" class="btn btn-sm btn-outline-primary btn-tts py-0 px-2 rounded-pill" title="Read question aloud" data-tts="${nextQ.question.replace(/"/g, '&quot;')}">
                <i class="fa-solid fa-volume-high me-1"></i> Listen
              </button>
            </div>
            <div class="fw-semibold text-dark fs-6 lh-base">${nextQ.question}</div>
          `;
          const newBubble = appendChatBubble(nextBubbleHtml, "question");
          chatForm.setAttribute("data-question-index", nextQ.index);
          resetInputs();

          // Auto-read question aloud if toggle is checked
          if (toggleAutoTTS && toggleAutoTTS.checked) {
            const newTtsBtn = newBubble.querySelector(".btn-tts");
            speakQuestionText(nextQ.question, newTtsBtn);
          }
        }, 1000);
      }

    } catch (err) {
      loaderBubble.remove();
      appendChatBubble("Could not evaluate response. Check your network connection or API configs.", "agent");
      resetInputs();
    }
  });

  function appendChatBubble(text, sender) {
    const bubble = document.createElement("div");
    bubble.className = `chat-bubble chat-bubble-${sender} fade-in`;
    bubble.innerHTML = text;
    chatContainer.appendChild(bubble);
    chatContainer.scrollTop = chatContainer.scrollHeight;
    return bubble;
  }

  function resetInputs(disableAll = false) {
    answerInput.disabled = disableAll;
    submitBtn.disabled = disableAll;
    submitBtn.innerHTML = "Submit Answer";
  }

  function renderEvaluationDetails(evaluation, totalAnswered) {
    if (evaluationPanel) {
      evaluationPanel.classList.remove("d-none");
    }

    // Gauges
    const conf = evaluation.confidence_score || 0;
    const comm = evaluation.communication_score || 0;
    const tech = evaluation.technical_accuracy || 0;
    const verbal = evaluation.verbal_clarity_score || comm || 0;

    const confScoreEl = document.getElementById("confScore");
    const confBarEl = document.getElementById("confBar");
    if (confScoreEl) confScoreEl.innerText = `${conf}%`;
    if (confBarEl) confBarEl.style.width = `${conf}%`;

    const commScoreEl = document.getElementById("commScore");
    const commBarEl = document.getElementById("commBar");
    if (commScoreEl) commScoreEl.innerText = `${comm}%`;
    if (commBarEl) commBarEl.style.width = `${comm}%`;

    const techScoreEl = document.getElementById("techScore");
    const techBarEl = document.getElementById("techBar");
    if (techScoreEl) techScoreEl.innerText = `${tech}%`;
    if (techBarEl) techBarEl.style.width = `${tech}%`;

    const verbalScoreEl = document.getElementById("verbalScore");
    const verbalBarEl = document.getElementById("verbalBar");
    if (verbalScoreEl) verbalScoreEl.innerText = `${verbal}%`;
    if (verbalBarEl) verbalBarEl.style.width = `${verbal}%`;

    // Verbal indicators card
    const fillerWordsText = document.getElementById("fillerWordsText");
    if (fillerWordsText) {
      if (evaluation.filler_words_detected && evaluation.filler_words_detected.length > 0) {
        fillerWordsText.innerText = `${evaluation.filler_word_count || evaluation.filler_words_detected.length} (${evaluation.filler_words_detected.join(", ")})`;
      } else {
        fillerWordsText.innerText = "None detected (Clean pacing)";
      }
    }

    const starStructureText = document.getElementById("starStructureText");
    if (starStructureText) {
      starStructureText.innerText = evaluation.star_method_analysis?.feedback || "Structured response recorded";
    }

    // Render Behavioral STAR Story Breakdown Card
    const starEval = evaluation.star_evaluation || (evaluation.star_method_analysis ? {
      star_score: evaluation.star_score || 75,
      situation: { present: evaluation.star_method_analysis.situation_present ?? true, score: 75, excerpt: "" },
      task: { present: evaluation.star_method_analysis.task_present ?? true, score: 75, excerpt: "" },
      action: { present: evaluation.star_method_analysis.action_present ?? true, score: 80, excerpt: "" },
      result: { present: evaluation.star_method_analysis.result_present ?? true, score: 70, excerpt: "" },
      non_verbal_composure: { eye_contact_score: 85, eye_contact_status: "Good Focus", composure_score: 85, composure_rating: "Confident" },
      coaching: { suggested_star_rewrite: evaluation.star_method_analysis.feedback || "" },
    } : null);

    if (starEval) {
      const starCard = document.getElementById("starBreakdownCard");
      if (starCard) starCard.classList.remove("d-none");

      const overallBadge = document.getElementById("starOverallScoreBadge");
      if (overallBadge) {
        const sScore = starEval.star_score ?? 75;
        overallBadge.textContent = `STAR: ${sScore}/100`;
        overallBadge.className = `badge rounded-pill px-2 py-1 small ${sScore >= 75 ? "bg-success" : (sScore >= 50 ? "bg-warning text-dark" : "bg-danger")}`;
      }

      function updateStarPill(prefix, dataBlock) {
        const pill = document.getElementById(`pill${prefix}`);
        const scoreEl = document.getElementById(`score${prefix}`);
        const excerptEl = document.getElementById(`excerpt${prefix}`);
        if (!pill || !scoreEl) return;

        const score = dataBlock ? (dataBlock.score ?? 50) : 50;
        const present = dataBlock ? dataBlock.present : false;
        scoreEl.textContent = `${score}%`;

        pill.classList.remove("passed", "needs-work", "missing");
        if (score >= 75 && present) {
          pill.classList.add("passed");
        } else if (score >= 50) {
          pill.classList.add("needs-work");
        } else {
          pill.classList.add("missing");
        }

        if (excerptEl && dataBlock) {
          excerptEl.textContent = dataBlock.excerpt || dataBlock.feedback || "Identified";
          excerptEl.title = dataBlock.feedback || "";
        }
      }

      updateStarPill("Situation", starEval.situation);
      updateStarPill("Task", starEval.task);
      updateStarPill("Action", starEval.action);
      updateStarPill("Result", starEval.result);

      // Non-verbal telemetry summary
      const starEye = document.getElementById("starEyeSummary");
      const starComp = document.getElementById("starComposureSummary");
      const nv = starEval.non_verbal_composure || {};
      if (starEye) {
        const eScore = nv.eye_contact_score ?? 85;
        starEye.textContent = `${eScore}% (${nv.eye_contact_status || (eScore >= 75 ? "Good Focus" : "Intermittent")})`;
      }
      if (starComp) {
        const cScore = nv.composure_score ?? 85;
        starComp.textContent = `${cScore}% (${nv.composure_rating || (cScore >= 75 ? "Steady" : "Moderate")})`;
      }

      // Model STAR Rewrite
      const rewriteText = document.getElementById("starModelRewriteText");
      if (rewriteText && starEval.coaching && starEval.coaching.suggested_star_rewrite) {
        rewriteText.textContent = starEval.coaching.suggested_star_rewrite;
      }
    }

    // Progress updates
    const totalQuestions = parseInt(chatForm.getAttribute("data-total-questions")) || 15;
    const progPercent = totalQuestions > 0 ? Math.round((totalAnswered / totalQuestions) * 100) : 0;
    const progressTextEl = document.getElementById("interviewProgressText");
    const progressBarEl = document.getElementById("interviewProgressBar");
    if (progressTextEl) progressTextEl.innerText = `${totalAnswered}/${totalQuestions} Questions Answered (${progPercent}%)`;
    if (progressBarEl) progressBarEl.style.width = `${progPercent}%`;
  }

  function showCompletionModal(summary) {
    const modalEl = document.getElementById("completionModal");
    if (!modalEl || typeof bootstrap === "undefined") return;

    const overallScoreEl = document.getElementById("modalOverallScore");
    const verdictBadge = document.getElementById("modalVerdictBadge");
    const feedbackEl = document.getElementById("modalSummaryFeedback");
    const hrScoreEl = document.getElementById("modalHrScore");
    const techScoreEl = document.getElementById("modalTechScore");
    const behScoreEl = document.getElementById("modalBehScore");

    if (overallScoreEl) overallScoreEl.innerText = summary.overall_score || "0";
    if (verdictBadge) verdictBadge.innerText = summary.readiness_verdict || "Completed";
    if (feedbackEl && summary.summary_feedback) feedbackEl.innerText = summary.summary_feedback;

    const cats = summary.category_scores || {};
    if (hrScoreEl && cats.HR) hrScoreEl.innerText = `${cats.HR.average_score}%`;
    if (techScoreEl && cats.Technical) techScoreEl.innerText = `${cats.Technical.average_score}%`;
    if (behScoreEl && cats.Behavioral) behScoreEl.innerText = `${cats.Behavioral.average_score}%`;

    // Also populate inline completed scorecard if present
    const scorecardTech = document.getElementById("scorecardTechScore");
    const scorecardComm = document.getElementById("scorecardCommScore");
    const scorecardClarity = document.getElementById("scorecardClarityScore");
    const barTech = document.getElementById("progressBarTech");
    const barComm = document.getElementById("progressBarComm");
    const barClarity = document.getElementById("progressBarClarity");

    const tScore = cats.Technical ? Math.round(cats.Technical.average_score) : Math.round(summary.overall_score || 0);
    const cScore = cats.HR ? Math.round(cats.HR.average_score) : Math.round(summary.overall_score || 0);
    const clarScore = summary.verbal_metrics ? Math.round(summary.verbal_metrics.average_verbal_clarity || 0) : cScore;

    if (scorecardTech) scorecardTech.innerText = `${tScore}%`;
    if (barTech) barTech.style.width = `${tScore}%`;
    if (scorecardComm) scorecardComm.innerText = `${cScore}%`;
    if (barComm) barComm.style.width = `${cScore}%`;
    if (scorecardClarity) scorecardClarity.innerText = `${clarScore}%`;
    if (barClarity) barClarity.style.width = `${clarScore}%`;

    // Display Verbal Performance Metrics in Modal if available
    const modalVerbalStatsCard = document.getElementById("modalVerbalStatsCard");
    const modalVerbalClarity = document.getElementById("modalVerbalClarity");
    const modalTotalFillers = document.getElementById("modalTotalFillers");
    const modalStarRate = document.getElementById("modalStarRate");

    if (summary.verbal_metrics && modalVerbalStatsCard) {
      modalVerbalStatsCard.classList.remove("d-none");
      if (modalVerbalClarity) modalVerbalClarity.innerText = `${summary.verbal_metrics.average_verbal_clarity}%`;
      if (modalTotalFillers) modalTotalFillers.innerText = `${summary.verbal_metrics.total_filler_words}`;
      if (modalStarRate) modalStarRate.innerText = `${summary.verbal_metrics.star_adherence_rate}`;
    }

    const bsModal = new bootstrap.Modal(modalEl);
    bsModal.show();
  }
}

/**
 * Enables copying single-week roadmap tasks and exporting the complete 6-week
 * preparation schedule as a structured Markdown file for study notes or Notion.
 */
function initRoadmapExport() {
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
}

/**
 * Multi-Version Resume Comparison & Longitudinal Progress Tracker
 * - Initializes Chart.js line/area chart showing ATS Score, Job Match %, and Skill Gaps over time
 * - Supports theme-reactive colors for gridlines and text
 * - Manages interactive side-by-side diff modal and dynamic baseline selection
 */
function initProgressTracker() {
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
    const textColor = isDark ? "#c4c7c5" : "#444746";
    const gridColor = isDark ? "rgba(255, 255, 255, 0.08)" : "rgba(0, 0, 0, 0.06)";

    const ctx = canvas.getContext("2d");
    const progressChart = new window.Chart(ctx, {
      type: "line",
      data: {
        labels: trendData.labels,
        datasets: [
          {
            label: "ATS Profile Score",
            data: trendData.ats_scores,
            borderColor: "#1a73e8",
            backgroundColor: "rgba(26, 115, 232, 0.12)",
            fill: true,
            tension: 0.35,
            borderWidth: 2.5,
            pointRadius: 5,
            pointHoverRadius: 7,
            pointBackgroundColor: "#1a73e8",
            yAxisID: "y"
          },
          {
            label: "Job Match %",
            data: trendData.match_scores,
            borderColor: "#34a853",
            backgroundColor: "rgba(52, 168, 83, 0.08)",
            fill: true,
            tension: 0.35,
            borderWidth: 2.5,
            pointRadius: 5,
            pointHoverRadius: 7,
            pointBackgroundColor: "#34a853",
            yAxisID: "y"
          },
          {
            label: "Skill Gaps Count",
            data: trendData.gap_counts,
            borderColor: "#ea4335",
            borderDash: [5, 5],
            backgroundColor: "transparent",
            fill: false,
            tension: 0.3,
            borderWidth: 2,
            pointRadius: 4,
            pointBackgroundColor: "#ea4335",
            yAxisID: "y1"
          }
        ]
      },
      options: {
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
              boxWidth: 14,
              font: { size: 12, family: "'Outfit', sans-serif" },
              color: textColor
            }
          },
          tooltip: {
            backgroundColor: isDark ? "#282a2c" : "#ffffff",
            titleColor: isDark ? "#e3e3e3" : "#1f1f1f",
            bodyColor: isDark ? "#c4c7c5" : "#444746",
            borderColor: isDark ? "#444746" : "#dadce0",
            borderWidth: 1,
            padding: 10,
            callbacks: {
              title: function (items) {
                const idx = items[0].dataIndex;
                const date = trendData.dates && trendData.dates[idx] ? trendData.dates[idx] : "";
                const filename = trendData.filenames && trendData.filenames[idx] ? trendData.filenames[idx] : "";
                return `Iteration ${trendData.labels[idx]} (${date}) - ${filename}`;
              }
            }
          }
        },
        scales: {
          x: {
            grid: { color: gridColor },
            ticks: { color: textColor, font: { family: "'Outfit', sans-serif" } }
          },
          y: {
            type: "linear",
            display: true,
            position: "left",
            min: 0,
            max: 100,
            grid: { color: gridColor },
            ticks: {
              color: textColor,
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
              color: "#ea4335",
              precision: 0,
              font: { family: "'Outfit', sans-serif" }
            }
          }
        }
      }
    });

    // Theme toggle listener to update chart colors
    const observer = new MutationObserver(() => {
      const darkNow = document.documentElement.getAttribute("data-bs-theme") === "dark";
      const newTextColor = darkNow ? "#c4c7c5" : "#444746";
      const newGridColor = darkNow ? "rgba(255, 255, 255, 0.08)" : "rgba(0, 0, 0, 0.06)";

      if (progressChart) {
        progressChart.options.scales.x.ticks.color = newTextColor;
        progressChart.options.scales.x.grid.color = newGridColor;
        progressChart.options.scales.y.ticks.color = newTextColor;
        progressChart.options.scales.y.grid.color = newGridColor;
        progressChart.options.plugins.legend.labels.color = newTextColor;
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
function initOutreachGenerator() {
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
function initCopySuggestions() {
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

  // Event delegation to catch clicks on any .btn-copy-suggestion or .btn-copy-text
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest(".btn-copy-suggestion, .btn-copy-text");
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
function initRecentAnalysesDrawer() {
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

      // Hook up delete buttons
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

/**
 * Real-time character counter and limit monitor for resume text and job description inputs (#132).
 */
function initCharacterCounters() {
  const resumeInput = document.getElementById("resumeTextInput");
  const resumeCurrent = document.getElementById("resumeCurrentChars");
  const resumeLimitWarning = document.getElementById("resumeCharLimitWarning");
  const resumeMax = 10000;

  const jdInput = document.getElementById("jdInput");
  const jdCurrent = document.getElementById("jdCurrentChars");
  const jdLimitWarning = document.getElementById("jdCharLimitWarning");
  const jdMax = 5000;

  function updateResumeCounter() {
    if (!resumeInput || !resumeCurrent) return;
    const len = resumeInput.value.length;
    resumeCurrent.textContent = len.toLocaleString();

    if (len >= resumeMax) {
      resumeCurrent.classList.add("text-danger", "fw-bold");
      resumeCurrent.classList.remove("text-warning");
      if (resumeLimitWarning) resumeLimitWarning.classList.remove("d-none");
    } else if (len >= resumeMax * 0.9) {
      resumeCurrent.classList.add("text-warning", "fw-bold");
      resumeCurrent.classList.remove("text-danger");
      if (resumeLimitWarning) resumeLimitWarning.classList.remove("d-none");
    } else {
      resumeCurrent.classList.remove("text-danger", "text-warning", "fw-bold");
      if (resumeLimitWarning) resumeLimitWarning.classList.add("d-none");
    }
  }

  function updateJdCounter() {
    if (!jdInput || !jdCurrent) return;
    const len = jdInput.value.length;
    jdCurrent.textContent = len.toLocaleString();

    if (len >= jdMax) {
      jdCurrent.classList.add("text-danger", "fw-bold");
      jdCurrent.classList.remove("text-warning");
      if (jdLimitWarning) jdLimitWarning.classList.remove("d-none");
    } else if (len >= jdMax * 0.9) {
      jdCurrent.classList.add("text-warning", "fw-bold");
      jdCurrent.classList.remove("text-danger");
      if (jdLimitWarning) jdLimitWarning.classList.remove("d-none");
    } else {
      jdCurrent.classList.remove("text-danger", "text-warning", "fw-bold");
      if (jdLimitWarning) jdLimitWarning.classList.add("d-none");
    }
  }

  if (resumeInput) {
    resumeInput.addEventListener("input", updateResumeCounter);
    resumeInput.addEventListener("change", updateResumeCounter);
    resumeInput.addEventListener("paste", () => setTimeout(updateResumeCounter, 0));
    updateResumeCounter();
  }

  if (jdInput) {
    jdInput.addEventListener("input", updateJdCounter);
    jdInput.addEventListener("change", updateJdCounter);
    jdInput.addEventListener("paste", () => setTimeout(updateJdCounter, 0));
    updateJdCounter();
  }

  // Handle clear or form reset events
  const form = document.getElementById("analyzeForm");
  if (form) {
    form.addEventListener("reset", () => {
      setTimeout(() => {
        updateResumeCounter();
        updateJdCounter();
      }, 0);
    });
  }
}


