/* admin.js — Phase 4a admin shell client behaviors.
 *
 * - Sidebar toggle for mobile (hamburger).
 * - Cmd+K / Ctrl+K focuses the global search input.
 * - Notifications polling stub (Phase 5 wires the real endpoint).
 *
 * Kept dependency-free; targets evergreen browsers only (no IE).
 */
(function () {
  "use strict";

  // ---------- Sidebar toggle ----------
  function bindSidebarToggle() {
    var btn = document.querySelector('[data-action="toggle-sidebar"]');
    if (!btn) return;
    btn.addEventListener("click", function () {
      document.body.classList.toggle("sidebar-open");
    });
    // Close on backdrop click (anywhere outside sidebar at <=768px).
    document.addEventListener("click", function (ev) {
      if (window.innerWidth > 768) return;
      if (!document.body.classList.contains("sidebar-open")) return;
      var sidebar = document.querySelector("[data-sidebar]");
      if (!sidebar) return;
      if (sidebar.contains(ev.target) || btn.contains(ev.target)) return;
      document.body.classList.remove("sidebar-open");
    });
  }

  // ---------- Cmd+K focuses global search ----------
  function bindGlobalSearchHotkey() {
    var input = document.getElementById("admin-global-search");
    if (!input) return;
    document.addEventListener("keydown", function (ev) {
      var isMod = ev.metaKey || ev.ctrlKey;
      if (isMod && (ev.key === "k" || ev.key === "K")) {
        ev.preventDefault();
        input.focus();
        input.select();
      }
      if (ev.key === "Escape" && document.activeElement === input) {
        input.blur();
      }
    });
  }

  // ---------- Notifications polling stub ----------
  // Phase 5 wires a real /admin/api/notifications endpoint that returns
  // {count: int, items: [{...}]}. For now this is a no-op placeholder so
  // the bell rendering stays in one place.
  function bindNotifications() {
    var bell = document.querySelector(".admin-topbar__bell");
    if (!bell) return;
    // Hook for Phase 5: setInterval(fetchAndUpdate, 30000);
  }

  function init() {
    bindSidebarToggle();
    bindGlobalSearchHotkey();
    bindNotifications();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
