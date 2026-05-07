/**
 * Phase 3b — Add to Cart wiring for the Phase 2 frame configurator.
 *
 * Reads the currently selected SKU from #selected-internal-sku (set by
 * configurator.js as the user clicks size + finish) and posts to
 * /api/cart/add with the render_spec_id baked into the page.
 *
 * Vanilla JS — no module dependencies — so this file can be loaded as a
 * regular <script> after configurator.js (which is type="module").
 */
(function () {
  "use strict";

  function ready(fn) {
    if (document.readyState !== "loading") {
      fn();
    } else {
      document.addEventListener("DOMContentLoaded", fn);
    }
  }

  function showFeedback(el, message, isError) {
    if (!el) return;
    el.textContent = message;
    el.classList.toggle("cart-add-feedback--error", !!isError);
    el.hidden = false;
  }

  function clearFeedback(el) {
    if (!el) return;
    el.hidden = true;
    el.textContent = "";
    el.classList.remove("cart-add-feedback--error");
  }

  function getSelectedSku() {
    var hidden = document.getElementById("selected-internal-sku");
    if (hidden && hidden.value) return hidden.value;
    // Fallback — pull from the active finish swatch + size <select>.
    var sizeSel = document.getElementById("size-select");
    var activeSwatch = document.querySelector(".finish-swatch.is-active");
    if (sizeSel && activeSwatch) {
      var size = sizeSel.value;
      var finish = activeSwatch.getAttribute("data-finish-id");
      if (size && finish) return "FP-CLA-" + size.toUpperCase() + "-" + finish.toUpperCase();
    }
    return "";
  }

  ready(function () {
    var btn = document.getElementById("add-to-cart-btn");
    if (!btn) return;
    var feedback = document.getElementById("add-to-cart-feedback");

    btn.addEventListener("click", function (ev) {
      ev.preventDefault();
      clearFeedback(feedback);

      var sku = getSelectedSku();
      if (!sku) {
        showFeedback(feedback, "Pick a size and finish first.", true);
        return;
      }
      var renderSpecId = btn.getAttribute("data-render-spec-id") || "";
      var addUrl = btn.getAttribute("data-cart-add-url") || "/api/cart/add";
      var cartUrl = btn.getAttribute("data-cart-page-url") || "/cart";

      btn.disabled = true;
      btn.setAttribute("aria-busy", "true");

      fetch(addUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prodigi_sku_internal: sku,
          render_spec_id: renderSpecId || null,
          quantity: 1,
        }),
      })
        .then(function (resp) {
          if (!resp.ok) {
            return resp.json().then(function (data) {
              throw new Error(data && data.error ? data.error : ("HTTP " + resp.status));
            });
          }
          return resp.json();
        })
        .then(function (data) {
          var count = (data && data.cart && data.cart.item_count) || 0;
          showFeedback(feedback, "Added — " + count + " in cart. Redirecting…", false);
          setTimeout(function () {
            window.location.href = cartUrl;
          }, 700);
        })
        .catch(function (err) {
          showFeedback(feedback, "Could not add: " + (err.message || "unknown error"), true);
          btn.disabled = false;
          btn.removeAttribute("aria-busy");
        });
    });
  });
})();
