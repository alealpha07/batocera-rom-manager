"use strict";

(() => {
  const SCROLL_KEY = "rom_manager_sidebar_scroll";
  const sidebar = document.getElementById("sidebar");
  const scroller = () => (sidebar.scrollHeight > sidebar.clientHeight ? sidebar : sidebar.querySelector(".offcanvas-body"));

  try {
    const saved = sessionStorage.getItem(SCROLL_KEY);
    if (saved !== null) {
      scroller().scrollTop = Number(saved);
    }
  } catch (error) {}

  const active = sidebar.querySelector(".list-group-item.active");
  if (active) {
    const box = scroller().getBoundingClientRect();
    const item = active.getBoundingClientRect();
    if (item.top < box.top || item.bottom > box.bottom) {
      scroller().scrollTop += item.top - box.top - box.height / 2 + item.height / 2;
    }
  }

  window.addEventListener("pagehide", () => {
    try {
      sessionStorage.setItem(SCROLL_KEY, scroller().scrollTop);
    } catch (error) {}
  });
})();
