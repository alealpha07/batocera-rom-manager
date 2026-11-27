"use strict";

{
  const modal = document.getElementById("check-modal");
  const form = document.getElementById("check-form");
  const setup = document.getElementById("check-setup");
  const progress = document.getElementById("check-progress");
  const systemSelect = document.getElementById("check-system");
  const gameInput = document.getElementById("check-game");
  const gameList = document.getElementById("check-games");
  const secondsInput = document.getElementById("check-seconds");
  const skipPassed = document.getElementById("check-skip-passed");
  const errorBox = document.getElementById("check-error");
  const current = document.getElementById("check-current");
  const bar = document.getElementById("check-bar");
  const counts = document.getElementById("check-counts");
  const last = document.getElementById("check-last");
  const lastTitle = document.getElementById("check-last-title");
  const lastLines = document.getElementById("check-last-lines");
  const runButton = document.getElementById("check-run");
  const stopButton = document.getElementById("check-stop");
  const newButton = document.getElementById("check-new");
  const POLL_MS = 2000;
  let gameKeys = null;
  let status = {};
  let view = "setup";
  let open = false;
  let changed = false;
  let pollTimer = null;

  function scope() {
    return form.querySelector("[name=scope]:checked").value;
  }

  function setScope(value) {
    form.querySelector(`[name=scope][value=${value}]`).checked = true;
    showScope();
  }

  function showScope() {
    for (const field of form.querySelectorAll("[data-check-scope]")) {
      field.hidden = field.dataset.checkScope !== scope();
    }
  }

  async function loadChoices() {
    if (gameKeys) {
      return;
    }
    const data = await (await fetch("/check/games")).json();
    gameKeys = new Map();
    for (const system of data.systems) {
      systemSelect.add(new Option(system.fullname, system.name));
    }
    for (const game of data.games) {
      gameKeys.set(game.label, game.key);
      gameList.append(new Option(game.label));
    }
  }

  function finishedText() {
    if (status.message === "Done") {
      return `Finished: ${status.done} of ${status.total} games checked`;
    }
    return status.message;
  }

  function showLast() {
    const result = status.last;
    last.hidden = !result;
    if (!result) {
      return;
    }
    lastTitle.textContent = `${result.name}: ${result.passed ? "passed" : "failed"}, ${result.reason}`;
    lastTitle.className = "small fw-semibold text-break " + (result.passed ? "text-success" : "text-danger");
    lastLines.replaceChildren(...(result.lines || []).map(line => {
      const item = document.createElement("li");
      item.textContent = line;
      return item;
    }));
  }

  function show() {
    setup.hidden = view !== "setup";
    progress.hidden = view !== "progress";
    runButton.hidden = view !== "setup";
    stopButton.hidden = !status.running;
    newButton.hidden = view !== "progress" || Boolean(status.running);
    if (!status.total) {
      return;
    }
    const position = Math.min(status.done + 1, status.total);
    current.textContent = status.running && !status.message
      ? `Checking ${position}/${status.total}: ${status.current}` : status.running ? status.message : finishedText();
    bar.style.width = `${100 * status.done / status.total}%`;
    counts.textContent = `${status.passed} passed, ${status.failed} failed ` +
      `(${status.seconds} s per game)`;
    showLast();
  }

  function update(next) {
    if (open && Boolean(next.running) !== Boolean(status.running)) {
      changed = true;
    }
    status = next;
    if (status.running) {
      view = "progress";
    }
    show();
    clearTimeout(pollTimer);
    if (open && status.running) {
      pollTimer = setTimeout(poll, POLL_MS);
    }
  }

  async function poll() {
    try {
      update(await (await fetch("/check/status")).json());
    } catch {
      clearTimeout(pollTimer);
      pollTimer = setTimeout(poll, POLL_MS);
    }
  }

  function post(url, body) {
    return fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  }

  modal.addEventListener("show.bs.modal", async event => {
    const trigger = event.relatedTarget;
    const prefill = trigger ? trigger.dataset : {};
    open = true;
    changed = false;
    errorBox.textContent = "";
    await poll();
    if (status.running) {
      return;
    }
    view = prefill.checkGame || prefill.checkSystem || !status.total ? "setup" : "progress";
    show();
    try {
      await loadChoices();
    } catch {
      errorBox.textContent = "Couldn't load the game list";
      return;
    }
    if (prefill.checkGame) {
      gameKeys.set(prefill.checkLabel, prefill.checkGame);
      gameInput.value = prefill.checkLabel;
      setScope("game");
    } else if (prefill.checkSystem && [...systemSelect.options].some(o => o.value === prefill.checkSystem)) {
      systemSelect.value = prefill.checkSystem;
      setScope("system");
    }
  });

  modal.addEventListener("hidden.bs.modal", () => {
    open = false;
    clearTimeout(pollTimer);
    if (changed) {
      location.reload();
    }
  });

  for (const radio of form.querySelectorAll("[name=scope]")) {
    radio.addEventListener("change", showScope);
  }

  form.addEventListener("submit", async event => {
    event.preventDefault();
    errorBox.textContent = "";
    const body = {scope: scope(), seconds: secondsInput.value, skip_passed: skipPassed.checked};
    if (body.scope === "system") {
      body.system = systemSelect.value;
    }
    if (body.scope === "game") {
      body.game = gameKeys && gameKeys.get(gameInput.value);
      if (!body.game) {
        errorBox.textContent = "Pick a game from the list";
        return;
      }
    }
    runButton.disabled = true;
    try {
      let response = await post("/check", body);
      let data = await response.json();
      if (response.status === 409) {
        if (!confirm(data.confirm)) {
          return;
        }
        response = await post("/check", {...body, running: data.running});
        data = await response.json();
      }
      if (!response.ok) {
        errorBox.textContent = data.confirm || data.error;
        return;
      }
      update(data);
    } catch {
      errorBox.textContent = "The app isn't answering";
    } finally {
      runButton.disabled = false;
    }
  });

  stopButton.addEventListener("click", async () => {
    stopButton.disabled = true;
    try {
      update(await (await fetch("/check/stop", {method: "POST"})).json());
    } catch {
      errorBox.textContent = "The app isn't answering";
    } finally {
      stopButton.disabled = false;
    }
  });

  newButton.addEventListener("click", () => {
    view = "setup";
    show();
  });
}
