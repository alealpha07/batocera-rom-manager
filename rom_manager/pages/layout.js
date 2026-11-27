"use strict";

{
  const themeCookie = "rom_manager_theme";
  const sidebarScrollKey = "rom_manager_sidebar_scroll";
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

  const sidebarScroller = (sidebar) => (sidebar.scrollHeight > sidebar.clientHeight ? sidebar : sidebar.querySelector(".offcanvas-body"));

  const restoreSidebarScroll = () => {
    const sidebar = document.getElementById("sidebar");
    if (!sidebar) {
      return;
    }
    try {
      const saved = sessionStorage.getItem(sidebarScrollKey);
      if (saved !== null) {
        sidebarScroller(sidebar).scrollTop = Number(saved);
      }
    } catch (error) {}
    const active = sidebar.querySelector(".list-group-item.active");
    if (active) {
      const box = sidebarScroller(sidebar).getBoundingClientRect();
      const item = active.getBoundingClientRect();
      if (item.top < box.top || item.bottom > box.bottom) {
        sidebarScroller(sidebar).scrollTop += item.top - box.top - box.height / 2 + item.height / 2;
      }
    }
    window.addEventListener("pagehide", () => {
      try {
        sessionStorage.setItem(sidebarScrollKey, sidebarScroller(sidebar).scrollTop);
      } catch (error) {}
    });
  };

  const submitButtons = (form) => [...form.elements].filter((element) => element.type === "submit");

  // every post form asks its data-confirm question, then locks its buttons so a double click doesn't send it twice
  const confirmAndLock = (event) => {
    const form = event.target;
    if (form.dataset.confirm && !confirm(form.dataset.confirm)) {
      event.preventDefault();
      return;
    }
    if (!event.defaultPrevented && form.method === "post") {
      submitButtons(form).forEach((button) => {
        button.disabled = true;
        button.dataset.locked = "";
      });
    }
  };

  const unlockAfterBack = (event) => {
    if (event.persisted) {
      document.querySelectorAll("[data-locked]").forEach((button) => {
        button.disabled = false;
        delete button.dataset.locked;
      });
    }
  };

  const autoSubmit = (event) => {
    if (event.target.matches("[data-autosubmit]")) {
      event.target.form.submit();
    }
  };

  applyTheme();
  deviceDark.addEventListener("change", applyTheme);
  document.addEventListener("submit", confirmAndLock);
  document.addEventListener("change", autoSubmit);
  window.addEventListener("pageshow", unlockAfterBack);
  document.addEventListener("DOMContentLoaded", () => {
    applyTheme();
    document.getElementById("theme-switch").addEventListener("click", switchTheme);
    restoreSidebarScroll();
  });
}
