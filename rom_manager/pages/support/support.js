"use strict";

{
  const restartStatus = document.getElementById("restart-status");
  const restartButtons = document.querySelectorAll("[data-restart]");
  const pollMs = 1000;
  const goneTimeoutMs = 15000;
  const backTimeoutMs = 120000;
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  const showStatus = (text, isError) => {
    restartStatus.className = "small mt-2 " + (isError ? "text-danger" : "text-body-secondary");
    restartStatus.textContent = text;
  };

  const isUp = async (kind) => {
    try {
      const response = await fetch("/support/status", {cache: "no-store"});
      if (!response.ok) {
        return false;
      }
      return kind === "app" || (await response.json()).es !== "down";
    } catch (error) {
      return false;
    }
  };

  const waitFor = async (kind, up, timeoutMs) => {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (await isUp(kind) === up) {
        return true;
      }
      await sleep(pollMs);
    }
    return false;
  };

  const restart = async (button) => {
    const kind = button.dataset.restart;
    if (!confirm(button.dataset.confirm)) {
      return;
    }
    restartButtons.forEach((other) => other.disabled = true);
    showStatus("Restarting…");
    try {
      const response = await fetch("/support/restart/" + kind, {method: "POST"});
      const data = await response.json().catch(() => ({error: "Server error (" + response.status + ")"}));
      if (!response.ok) {
        throw new Error(data.error || "Server error (" + response.status + ")");
      }
    } catch (error) {
      showStatus(error.message, true);
      restartButtons.forEach((other) => other.disabled = false);
      return;
    }
    await waitFor(kind, false, goneTimeoutMs);
    showStatus("Waiting for it to come back…");
    if (await waitFor(kind, true, backTimeoutMs)) {
      location.href = "/support?restarted=" + kind;
    } else {
      showStatus("It didn't come back within two minutes. Reload the page to check again.", true);
    }
  };

  restartButtons.forEach((button) => button.addEventListener("click", () => restart(button)));
}
