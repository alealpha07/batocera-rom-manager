"use strict";

{
  const themeCookie = "rom_manager_theme";
  const deviceDark = matchMedia("(prefers-color-scheme: dark)");

  const savedTheme = () => {
    const match = document.cookie.match(/(?:^|; )rom_manager_theme=(light|dark)(?:;|$)/);
    return match && match[1];
  };

  const applyTheme = () => {
    const theme = savedTheme() || (deviceDark.matches ? "dark" : "light");
    document.documentElement.dataset.bsTheme = theme;
    const button = document.getElementById("theme-switch");
    if (button) {
      button.textContent = theme === "dark" ? "☀︎" : "☾︎";
      button.title = theme === "dark" ? "Switch to light theme" : "Switch to dark theme";
      button.setAttribute("aria-label", button.title);
    }
  };

  const switchTheme = () => {
    const theme = document.documentElement.dataset.bsTheme === "dark" ? "light" : "dark";
    document.cookie = themeCookie + "=" + theme + "; path=/; max-age=31536000; SameSite=Strict";
    applyTheme();
  };

  applyTheme();
  deviceDark.addEventListener("change", applyTheme);
  document.addEventListener("DOMContentLoaded", () => {
    applyTheme();
    document.getElementById("theme-switch").addEventListener("click", switchTheme);
  });
}
