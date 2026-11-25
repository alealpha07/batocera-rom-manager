"use strict";

const transfer = document.getElementById("transfer");
const targetField = transfer.querySelector("[data-transfer-target]");
const targetHint = document.getElementById("target-hint");
const subfolderInput = document.getElementById("subfolder");
const overwriteCheck = document.getElementById("overwrite");
const uploadForm = document.getElementById("upload-form");
const filesInput = document.getElementById("files");
const uploadProgress = document.getElementById("upload-progress");
const uploadBar = uploadProgress.querySelector(".progress-bar");
const uploadStatus = document.getElementById("upload-status");
const uploadResults = document.getElementById("upload-results");
const linkForm = document.getElementById("link-form");
const urlsInput = document.getElementById("urls");
const nameInput = document.getElementById("name");
const linkError = document.getElementById("link-error");
const jobsBody = document.getElementById("jobs");
const JOB_POLL_MS = 2000;
const STATUS_STYLES = {queued: "secondary", downloading: "primary", done: "success", failed: "danger", cancelled: "secondary"};
let uploading = false;
let pollTimer = null;

function showTargetHint() {
  const option = targetField.selectedOptions && targetField.selectedOptions[0];
  const extensions = option && option.dataset.extensions;
  if (targetHint) {
    targetHint.textContent = extensions ? "Listed by ES: " + extensions : "";
  }
}

function humanSize(size) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit++;
  }
  return unit ? size.toFixed(1) + " " + units[unit] : size + " B";
}

function sharedFields() {
  return {[targetField.name]: targetField.value, subfolder: subfolderInput.value.trim(), overwrite: overwriteCheck.checked};
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({error: "Server error (" + response.status + ")"}));
  if (!response.ok) {
    throw new Error(data.error || "Server error (" + response.status + ")");
  }
  return data;
}

function addResult(name, note, kind) {
  const item = document.createElement("li");
  item.className = "list-group-item list-group-item-" + kind;
  const title = document.createElement("div");
  title.className = "fw-semibold text-break";
  title.textContent = name;
  const detail = document.createElement("div");
  detail.className = "small";
  detail.textContent = note;
  item.append(title, detail);
  uploadResults.append(item);
}

function setStatus(text, isError, link) {
  uploadStatus.className = "small mt-2 " + (isError ? "text-danger" : "text-body-secondary");
  uploadStatus.textContent = text;
  if (link) {
    const anchor = document.createElement("a");
    anchor.href = link.href;
    anchor.textContent = link.text;
    uploadStatus.append(" ", anchor);
  }
}

function setProgress(fraction, text) {
  const percent = Math.round(fraction * 100);
  uploadProgress.classList.remove("d-none");
  uploadProgress.setAttribute("aria-valuenow", percent);
  uploadBar.style.width = percent + "%";
  uploadBar.textContent = percent + "%";
  setStatus(text);
}

function setBusy(busy) {
  uploading = busy;
  for (const element of [targetField, subfolderInput, overwriteCheck, filesInput, uploadForm.querySelector("button")]) {
    element.disabled = busy;
  }
}

function finishUpload(result, target) {
  setBusy(false);
  const saved = result.saved || [];
  for (const file of saved) {
    addResult(file.name, file.warning ? "Saved, but " + file.warning : "Saved", file.warning ? "warning" : "success");
  }
  for (const [name, reason] of Object.entries(result.failed || {})) {
    addResult(name, reason, "danger");
  }
  if (result.error) {
    setStatus(result.error, true);
  } else {
    const count = saved.length === 1 ? "1 file saved." : saved.length + " files saved.";
    const link = {href: transfer.dataset.doneLink + encodeURIComponent(target), text: transfer.dataset.doneText};
    setStatus(count, false, saved.length ? link : null);
  }
  uploadProgress.classList.add("d-none");
  filesInput.value = "";
}

function sendFiles(files, fields) {
  const form = new FormData();
  form.append(targetField.name, fields[targetField.name]);
  form.append("subfolder", fields.subfolder);
  if (fields.overwrite) {
    form.append("overwrite", "1");
  }
  for (const file of files) {
    form.append("files", file, file.name);
  }
  const request = new XMLHttpRequest();
  request.open("POST", transfer.dataset.base + "/upload");
  request.responseType = "json";
  request.upload.onprogress = (event) => {
    if (event.lengthComputable) {
      setProgress(event.loaded / event.total, humanSize(event.loaded) + " of " + humanSize(event.total));
    }
  };
  request.upload.onload = () => setProgress(1, "Saving…");
  request.onload = () => finishUpload(request.response || {error: "Server error (" + request.status + ")"}, fields[targetField.name]);
  request.onerror = () => finishUpload({error: "Upload failed: the connection was lost"}, fields[targetField.name]);
  setBusy(true);
  setProgress(0, "Starting…");
  request.send(form);
}

async function startUpload(event) {
  event.preventDefault();
  if (!targetField.reportValidity() || !filesInput.reportValidity()) {
    return;
  }
  const files = Array.from(filesInput.files);
  const fields = sharedFields();
  uploadResults.replaceChildren();
  setStatus("Checking…");
  let checked;
  try {
    checked = await postJson(transfer.dataset.base + "/check", {
      ...fields,
      names: files.map((file) => file.name),
      size: files.reduce((total, file) => total + file.size, 0),
    });
  } catch (error) {
    setStatus(error.message, true);
    return;
  }
  const problems = Object.entries(checked.problems);
  if (problems.length) {
    for (const [name, reason] of problems) {
      addResult(name, reason, "danger");
    }
    setStatus("Nothing was uploaded. Remove these files" + (fields.overwrite ? "." : " or tick Overwrite existing files."), true);
    return;
  }
  sendFiles(files, fields);
}

function cell(...children) {
  const td = document.createElement("td");
  td.append(...children);
  return td;
}

function textBlock(text, className) {
  const element = document.createElement("div");
  element.className = className;
  element.textContent = text;
  return element;
}

function progressCell(job) {
  if (!job.total) {
    return cell(job.received ? humanSize(job.received) : "");
  }
  const percent = Math.min(100, Math.round(job.received / job.total * 100));
  const progress = document.createElement("div");
  progress.className = "progress";
  progress.setAttribute("role", "progressbar");
  progress.setAttribute("aria-label", "Download progress");
  progress.setAttribute("aria-valuenow", percent);
  const bar = document.createElement("div");
  bar.className = "progress-bar";
  bar.style.width = percent + "%";
  progress.append(bar);
  return cell(progress, textBlock(humanSize(job.received) + " of " + humanSize(job.total), "small text-body-secondary"));
}

function statusCell(job) {
  const badge = document.createElement("span");
  badge.className = "badge text-bg-" + STATUS_STYLES[job.status];
  badge.textContent = job.status;
  const note = job.error || job.warning;
  return note ? cell(badge, textBlock(note, "small " + (job.error ? "text-danger" : "text-warning-emphasis"))) : cell(badge);
}

function actionCell(job) {
  if (job.status !== "queued" && job.status !== "downloading") {
    return cell();
  }
  const button = document.createElement("button");
  button.type = "button";
  button.className = "btn btn-outline-danger btn-sm";
  button.textContent = "Cancel";
  button.addEventListener("click", async () => {
    button.disabled = true;
    await fetch("/jobs/" + job.id + "/cancel", {method: "POST"});
    refreshJobs();
  });
  return cell(button);
}

function jobRow(job) {
  const row = document.createElement("tr");
  const file = (job.subfolder ? job.subfolder + "/" : "") + (job.name || "…");
  const fileCell = cell(textBlock(file, "text-break"), textBlock(job.url, "small text-body-secondary text-break"));
  row.append(fileCell, cell(job.place), progressCell(job), statusCell(job), actionCell(job));
  return row;
}

function renderJobs(jobs) {
  if (!jobs.length) {
    const td = cell("No downloads");
    td.colSpan = 5;
    td.className = "text-center text-body-secondary py-3";
    const row = document.createElement("tr");
    row.append(td);
    jobsBody.replaceChildren(row);
    return;
  }
  jobsBody.replaceChildren(...jobs.map(jobRow));
}

async function refreshJobs() {
  clearTimeout(pollTimer);
  let jobs;
  try {
    const response = await fetch(transfer.dataset.base + "/jobs");
    jobs = await response.json();
  } catch (error) {
    return;
  }
  renderJobs(jobs);
  if (jobs.some((job) => job.status === "queued" || job.status === "downloading")) {
    pollTimer = setTimeout(refreshJobs, JOB_POLL_MS);
  }
}

async function startDownloads(event) {
  event.preventDefault();
  if (!targetField.reportValidity() || !urlsInput.reportValidity()) {
    return;
  }
  linkError.classList.add("d-none");
  const button = linkForm.querySelector("button");
  button.disabled = true;
  try {
    await postJson(transfer.dataset.base + "/links", {...sharedFields(), urls: urlsInput.value, name: nameInput.value.trim()});
    urlsInput.value = "";
    nameInput.value = "";
  } catch (error) {
    linkError.textContent = error.message;
    linkError.classList.remove("d-none");
  } finally {
    button.disabled = false;
  }
  refreshJobs();
}

function rememberTab() {
  for (const tab of document.querySelectorAll('[data-bs-toggle="tab"]')) {
    tab.addEventListener("shown.bs.tab", () => {
      history.replaceState(null, "", tab.id === "link-tab" ? "#link" : location.pathname + location.search);
    });
  }
  if (location.hash === "#link") {
    bootstrap.Tab.getOrCreateInstance(document.getElementById("link-tab")).show();
  }
}

targetField.addEventListener("change", showTargetHint);
uploadForm.addEventListener("submit", startUpload);
linkForm.addEventListener("submit", startDownloads);
window.addEventListener("beforeunload", (event) => {
  if (uploading) {
    event.preventDefault();
  }
});
document.addEventListener("DOMContentLoaded", rememberTab);
showTargetHint();
refreshJobs();
