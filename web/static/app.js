(() => {
  "use strict";

  const csrf = document.querySelector('meta[name="codepilot-csrf"]').content;
  const $ = (id) => document.getElementById(id);
  const taskForm = $("task-form");
  const taskInput = $("task-input");
  const fileSelect = $("filename-select");
  const runButton = $("run-button");
  const formError = $("form-error");
  const reviewPanel = $("review-panel");
  const approvalDialog = $("approval-dialog");
  let activeTaskId = null;
  let pollHandle = null;
  let promotionBusy = false;

  async function api(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (options.method === "POST") {
      headers["Content-Type"] = "application/json";
      headers["X-CodePilot-CSRF"] = csrf;
    }
    const response = await fetch(path, { ...options, headers, credentials: "same-origin", cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
    return data;
  }

  function setStatus(status, message, taskId) {
    const orb = $("status-orb");
    orb.className = `status-orb ${String(status || "idle").toLowerCase().replaceAll("_", "-")}`;
    const labels = { RUNNING: "Agent is working", SUCCESS: "Acceptance check passed", FAILED: "Task did not complete", ROLLED_BACK: "Stopped safely", TIMED_OUT: "Execution timed out", REJECTED: "Patch rejected" };
    $("status-text").textContent = labels[status] || "Ready for a task";
    $("status-message").textContent = message || "Submit a task to start the existing CodePilot agent.";
    $("task-id-label").textContent = taskId ? `TASK ${taskId.slice(0, 8).toUpperCase()}` : "NO ACTIVE TASK";
  }

  function renderHealth(data) {
    const pill = $("docker-pill");
    const text = pill.querySelector("span");
    if (data.docker_available && data.sandbox_image_available && data.execution_mode === "docker") {
      pill.className = "docker-pill ready";
      text.textContent = "Docker sandbox ready";
      $("docker-warning").hidden = true;
    } else {
      pill.className = "docker-pill missing";
      text.textContent = "Sandbox unavailable";
      $("docker-warning").hidden = false;
    }
  }

  function renderPlan(plan) {
    const list = $("plan-list");
    list.replaceChildren();
    $("plan-count").textContent = `${plan.length} STEPS`;
    if (!plan.length) {
      const empty = document.createElement("li");
      empty.className = "empty-state";
      empty.textContent = "The agent plan will appear here when a task starts.";
      list.append(empty);
      return;
    }
    plan.forEach((step, index) => {
      const item = document.createElement("li");
      const number = document.createElement("span");
      number.className = "step-number";
      number.textContent = String(index + 1).padStart(2, "0");
      const text = document.createElement("span");
      text.textContent = step;
      item.append(number, text);
      list.append(item);
    });
  }

  function renderExecution(task) {
    const execution = task.execution;
    const badge = $("verification-badge");
    if (task.status === "RUNNING") {
      badge.className = "result-badge neutral";
      badge.textContent = "RUNNING";
    } else if (task.verification?.verified) {
      badge.className = "result-badge pass";
      badge.textContent = "PASS";
    } else if (execution) {
      badge.className = "result-badge fail";
      badge.textContent = task.status === "TIMED_OUT" ? "TIMEOUT" : "FAIL";
    } else {
      badge.className = "result-badge neutral";
      badge.textContent = "NOT RUN";
    }
    $("execution-mode").textContent = execution?.execution_mode || (task.status === "RUNNING" ? "Waiting for sandbox" : "Waiting for execution");
    $("return-code").textContent = execution?.return_code === undefined || execution?.return_code === null ? "Exit code —" : `Exit code ${execution.return_code}`;
    $("stdout").textContent = execution?.output || (task.status === "RUNNING" ? "Waiting for program output…" : "No standard output.");
    $("stderr-wrap").hidden = !execution?.error;
    $("stderr").textContent = execution?.error || "";
    $("classification-row").hidden = !task.error_classification;
    $("classification").textContent = task.error_classification || "";
  }

  function eventDescription(event) {
    if (event.phase === "PLAN") return `${(event.subtasks || []).length} steps added to the plan`;
    if (event.phase === "OBSERVE") {
      const result = event.result || {};
      return `${result.execution_mode || "executor"} · exit ${result.return_code ?? "—"}${result.timed_out ? " · timed out" : ""}`;
    }
    if (event.phase === "REFLECT") return `${event.decision || "Review"}${event.reason ? ` · ${event.reason}` : ""}`;
    if (event.phase === "ACT") return event.action || "Agent action";
    if (event.phase === "ERROR") return event.error || "Agent error";
    return event.reason || event.decision || event.phase || "Agent event";
  }

  function renderLogs(logs) {
    const list = $("logs-list");
    list.replaceChildren();
    $("log-count").textContent = `${logs.length} EVENTS`;
    if (!logs.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = "Plan, execution, reflection, and repair events will appear here.";
      list.append(empty);
      return;
    }
    for (const event of logs) {
      const row = document.createElement("div");
      row.className = "log-event";
      const phase = document.createElement("span");
      phase.className = "log-phase";
      phase.textContent = event.phase || "EVENT";
      const detail = document.createElement("span");
      detail.className = "log-text";
      detail.textContent = eventDescription(event);
      row.append(phase, detail);
      list.append(row);
    }
  }

  function renderFiles(files) {
    const list = $("files-list");
    list.replaceChildren();
    $("files-count").textContent = `${files.length} FILE${files.length === 1 ? "" : "S"}`;
    if (!files.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = "No files changed in the isolated workspace.";
      list.append(empty);
      return;
    }
    for (const name of files) {
      const row = document.createElement("div");
      row.className = "file-row";
      const icon = document.createElement("span");
      icon.className = "file-icon";
      icon.textContent = "PY";
      const filename = document.createElement("span");
      filename.className = "file-name";
      filename.textContent = name;
      const change = document.createElement("span");
      change.className = "file-change";
      change.textContent = "CHANGED";
      row.append(icon, filename, change);
      list.append(row);
    }
  }

  async function loadDiff(taskId) {
    const data = await api(`/api/tasks/${taskId}/diff`);
    $("diff-files").replaceChildren();
    for (const file of data.files) {
      const pill = document.createElement("span");
      pill.className = "diff-file-pill";
      pill.textContent = file;
      $("diff-files").append(pill);
    }
    $("diff-content").textContent = data.patch || "No text diff was produced for this task.";
    reviewPanel.hidden = false;
  }

  async function refreshTask() {
    if (!activeTaskId) return;
    const task = await api(`/api/tasks/${activeTaskId}`);
    setStatus(task.status, task.message, task.task_id);
    renderPlan(task.plan || []);
    renderExecution(task);
    renderLogs(task.logs || []);
    renderFiles(task.modified_files || []);
    $("iteration-current").textContent = String(task.iteration || 0);
    $("iteration-progress").style.width = `${Math.min(100, ((task.iteration || 0) / 5) * 100)}%`;
    const canReview = task.status === "SUCCESS" && task.promotion_status !== "PROMOTING";
    if (canReview && reviewPanel.hidden) {
      try { await loadDiff(activeTaskId); } catch (error) { showError(error.message); }
    }
    if (task.promotion_status && task.promotion_status !== "NOT_REQUESTED" && task.promotion_status !== "PROMOTING") {
      const result = $("promotion-result");
      result.hidden = false;
      const details = task.promotion_result || {};
      result.textContent = details.status === "APPLIED" ? "Patch promoted after explicit approval." :
        details.status === "REJECTED" ? "Patch rejected. The primary workspace was not changed." :
        `Promotion status: ${details.status || task.promotion_status}${details.conflicts?.length ? ` · conflicts: ${details.conflicts.join(", ")}` : ""}`;
      $("approve-button").disabled = true;
      $("reject-button").disabled = true;
    }
    if (task.status !== "RUNNING" && pollHandle) {
      clearInterval(pollHandle);
      pollHandle = null;
      runButton.disabled = false;
      runButton.innerHTML = '<span class="play-icon" aria-hidden="true">▶</span> Run in isolated workspace';
    }
  }

  function showError(message) {
    formError.textContent = message;
    formError.hidden = false;
  }

  taskInput.addEventListener("input", () => {
    $("char-count").textContent = `${taskInput.value.length} / 1500`;
  });
  $("char-count").textContent = `${taskInput.value.length} / 1500`;

  taskForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    formError.hidden = true;
    reviewPanel.hidden = true;
    $("promotion-result").hidden = true;
    try {
      const result = await api("/api/tasks", {
        method: "POST",
        body: JSON.stringify({ task: taskInput.value, filename: fileSelect.value }),
      });
      activeTaskId = result.task_id;
      runButton.disabled = true;
      runButton.textContent = "Agent running…";
      setStatus("RUNNING", "Running the existing agent in a temporary workspace.", activeTaskId);
      renderPlan([]); renderLogs([]); renderFiles([]);
      $("iteration-current").textContent = "0";
      $("iteration-progress").style.width = "0%";
      $("stdout").textContent = "Waiting for program output…";
      $("stderr-wrap").hidden = true;
      $("classification-row").hidden = true;
      pollHandle = setInterval(() => refreshTask().catch((error) => showError(error.message)), 800);
      await refreshTask();
    } catch (error) {
      runButton.disabled = false;
      runButton.innerHTML = '<span class="play-icon" aria-hidden="true">▶</span> Run in isolated workspace';
      showError(error.message);
    }
  });

  $("approve-button").addEventListener("click", () => approvalDialog.showModal());
  $("cancel-approval").addEventListener("click", () => approvalDialog.close());
  $("confirm-approval").addEventListener("click", async () => {
    if (!activeTaskId || promotionBusy) return;
    promotionBusy = true;
    $("confirm-approval").disabled = true;
    try {
      const result = await api(`/api/tasks/${activeTaskId}/promote`, {
        method: "POST", body: JSON.stringify({ approved: true }),
      });
      $("promotion-result").hidden = false;
      $("promotion-result").textContent = result.status === "APPLIED" ? "Patch promoted after explicit approval." :
        `Promotion status: ${result.status}${result.conflicts?.length ? ` · conflicts: ${result.conflicts.join(", ")}` : ""}`;
      $("approve-button").disabled = true;
      $("reject-button").disabled = true;
      approvalDialog.close();
    } catch (error) {
      showError(error.message);
      approvalDialog.close();
    } finally {
      promotionBusy = false;
      $("confirm-approval").disabled = false;
    }
  });
  $("reject-button").addEventListener("click", async () => {
    if (!activeTaskId) return;
    try {
      await api(`/api/tasks/${activeTaskId}/reject`, { method: "POST", body: "{}" });
      $("promotion-result").hidden = false;
      $("promotion-result").textContent = "Patch rejected. The primary workspace was not changed.";
      $("approve-button").disabled = true;
      $("reject-button").disabled = true;
    } catch (error) { showError(error.message); }
  });

  async function initialize() {
    try {
      const [health, files] = await Promise.all([api("/api/health"), api("/api/workspace-files")]);
      renderHealth(health);
      fileSelect.replaceChildren();
      for (const filename of files.files) {
        const option = document.createElement("option");
        option.value = filename;
        option.textContent = filename;
        fileSelect.append(option);
      }
      if ([...fileSelect.options].some((option) => option.value === "repair_loop_temp.py")) {
        fileSelect.value = "repair_loop_temp.py";
      }
      runButton.disabled = files.files.length === 0;
      if (!files.files.length) showError("No Python demo files are available in the local workspace.");
    } catch (error) {
      showError(`Dashboard could not connect to its local API: ${error.message}`);
      runButton.disabled = true;
    }
  }
  initialize();
})();
