/**
 * Admin notifications poller (Phase 6 polish).
 *
 * Polls /admin/notifications every 60s and updates the topbar bell:
 *   - data-count attribute on the bell button
 *   - the .admin-topbar__bell-badge element (created if missing)
 *   - a dropdown drawer rendered into #admin-notifications-drawer
 *
 * Designed to fail soft: any fetch error / non-2xx leaves the existing
 * UI in place and retries on the next interval.
 */
(function () {
  "use strict";

  var ENDPOINT = "/admin/notifications";
  var POLL_MS = 60 * 1000;

  function $(sel) { return document.querySelector(sel); }

  function ensureBadge(bell) {
    var badge = bell.querySelector(".admin-topbar__bell-badge");
    if (!badge) {
      badge = document.createElement("span");
      badge.className = "admin-topbar__bell-badge";
      badge.setAttribute("aria-hidden", "true");
      bell.appendChild(badge);
    }
    return badge;
  }

  function ensureDrawer(bell) {
    var drawer = document.getElementById("admin-notifications-drawer");
    if (!drawer) {
      drawer = document.createElement("div");
      drawer.id = "admin-notifications-drawer";
      drawer.className = "admin-topbar__bell-drawer";
      drawer.setAttribute("role", "menu");
      drawer.setAttribute("hidden", "hidden");
      bell.parentNode.insertBefore(drawer, bell.nextSibling);
    }
    return drawer;
  }

  function renderDrawer(drawer, payload) {
    var items = (payload && payload.items) || [];
    if (!items.length) {
      drawer.innerHTML = '<p class="admin-topbar__bell-empty">No new notifications.</p>';
      return;
    }
    var html = '<ul class="admin-topbar__bell-list">';
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      var msg = (it.message || "").replace(/[<>&]/g, function (c) {
        return { "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c] || c;
      });
      var link = (it.link || "#").replace(/"/g, "&quot;");
      html += '<li class="admin-topbar__bell-item">'
            + '<a href="' + link + '" class="admin-topbar__bell-link">'
            + '<span class="admin-topbar__bell-type">' + (it.type || "") + '</span>'
            + '<span class="admin-topbar__bell-msg">' + msg + '</span>'
            + '</a></li>';
    }
    html += "</ul>";
    drawer.innerHTML = html;
  }

  function updateBell(bell, payload) {
    var count = (payload && payload.count) || 0;
    bell.setAttribute("data-count", String(count));
    var badge = ensureBadge(bell);
    if (count > 0) {
      badge.textContent = String(count);
      badge.removeAttribute("hidden");
    } else {
      badge.textContent = "";
      badge.setAttribute("hidden", "hidden");
    }
    var drawer = ensureDrawer(bell);
    renderDrawer(drawer, payload);
  }

  function poll() {
    var bell = $(".admin-topbar__bell");
    if (!bell) return;
    fetch(ENDPOINT, { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (resp) { return resp.ok ? resp.json() : null; })
      .then(function (payload) { if (payload) updateBell(bell, payload); })
      .catch(function () { /* fail soft */ });
  }

  function bindBellToggle() {
    var bell = $(".admin-topbar__bell");
    if (!bell) return;
    bell.addEventListener("click", function (e) {
      e.preventDefault();
      var drawer = ensureDrawer(bell);
      if (drawer.hasAttribute("hidden")) {
        drawer.removeAttribute("hidden");
      } else {
        drawer.setAttribute("hidden", "hidden");
      }
    });
  }

  function start() {
    bindBellToggle();
    poll();
    setInterval(poll, POLL_MS);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
