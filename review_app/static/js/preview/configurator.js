// configurator.js — Phase 2 frame preview compositor.
//
// Vanilla ES2020 module — no framework. Loaded as <script type="module">.
//
// Architecture (sub-100ms requirement):
//   1. On page load: fetch frame_skus catalog (one round-trip).
//   2. Eagerly preload all 8 finish blank PNGs + the poster preview image
//      (~2-4 MB total — acceptable for a configurator page).
//   3. On every dropdown / swatch change: swap the cached Image object
//      and redraw the canvas. ZERO network. The redraw is a single
//      ctx.drawImage() pair — well under one frame at 60 Hz.

const FRAME_DATA_URL = "/static/data/frame_skus.json";

/**
 * @typedef {{x:number,y:number,w:number,h:number}} Rect
 * @typedef {Object} Sku
 * @property {string} internal_sku
 * @property {string} prodigi_sku
 * @property {{color:string}} prodigi_attributes
 * @property {string} size_inches
 * @property {[number,number]} size_aspect
 * @property {string} finish_id
 * @property {string} finish_display
 * @property {string} blank_asset
 * @property {string} chevron_asset
 * @property {string} swatch_asset
 * @property {Rect} inner_rect_pct
 */

/** @type {Sku[]} */
let CATALOG = [];
/** @type {Map<string, HTMLImageElement>} key = blank_asset URL */
const FRAME_IMAGE_CACHE = new Map();
/** @type {HTMLImageElement | null} */
let POSTER_IMAGE = null;

/** @type {{size:string, finish:string}} */
const STATE = { size: "16x20", finish: "brown" };

/* ------------------------------------------------------------------ */
/* Bootstrap                                                          */
/* ------------------------------------------------------------------ */

/**
 * Boot the configurator. Reads:
 *   - data-poster-preview-url   on #configurator-root
 *   - data-default-size         on #configurator-root
 *   - data-default-finish       on #configurator-root
 *   - data-frame-data-url       on #configurator-root (optional override)
 */
export async function boot() {
  const root = document.getElementById("configurator-root");
  if (!root) {
    console.error("[configurator] #configurator-root not found");
    return;
  }

  const posterUrl = root.dataset.posterPreviewUrl;
  if (!posterUrl) {
    console.error("[configurator] data-poster-preview-url is required");
    return;
  }

  STATE.size = root.dataset.defaultSize || "16x20";
  STATE.finish = root.dataset.defaultFinish || "brown";

  const dataUrl = root.dataset.frameDataUrl || FRAME_DATA_URL;
  CATALOG = await loadCatalog(dataUrl);

  // Kick off all preloads in parallel — don't block the first paint.
  const posterPromise = preloadImage(posterUrl).then((img) => {
    POSTER_IMAGE = img;
  });

  const finishToBlankUrl = new Map();
  for (const sku of CATALOG) {
    if (!finishToBlankUrl.has(sku.finish_id)) {
      finishToBlankUrl.set(sku.finish_id, sku.blank_asset);
    }
  }
  const framePromises = [];
  for (const [, url] of finishToBlankUrl) {
    framePromises.push(
      preloadImage(url).then((img) => {
        FRAME_IMAGE_CACHE.set(url, img);
      })
    );
  }

  // Wire UI before assets land — picker can change selection while loading.
  wireUi(root);
  syncHiddenInput(root);
  updatePriceDisplay(root);

  // Render as soon as both poster + the currently-selected blank are ready.
  await Promise.all([posterPromise, ...framePromises]);
  render();
}

/**
 * @param {string} url
 * @returns {Promise<Sku[]>}
 */
async function loadCatalog(url) {
  const res = await fetch(url, { credentials: "same-origin" });
  if (!res.ok) {
    throw new Error(`[configurator] failed to load ${url}: ${res.status}`);
  }
  const data = await res.json();
  if (!Array.isArray(data)) {
    throw new Error("[configurator] frame_skus.json is not an array");
  }
  return data;
}

/**
 * @param {string} url
 * @returns {Promise<HTMLImageElement>}
 */
function preloadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`failed to load image: ${url}`));
    img.src = url;
  });
}

/* ------------------------------------------------------------------ */
/* Lookup helpers                                                     */
/* ------------------------------------------------------------------ */

/**
 * @param {string} size
 * @param {string} finish
 * @returns {Sku | null}
 */
function findSku(size, finish) {
  return (
    CATALOG.find((s) => s.size_inches === size && s.finish_id === finish) ||
    null
  );
}

/* ------------------------------------------------------------------ */
/* UI wiring                                                          */
/* ------------------------------------------------------------------ */

/**
 * @param {HTMLElement} root
 */
function wireUi(root) {
  const sizeSelect = /** @type {HTMLSelectElement | null} */ (
    root.querySelector("#size-select")
  );
  if (sizeSelect) {
    sizeSelect.addEventListener("change", () => {
      STATE.size = sizeSelect.value;
      updateActiveSizePill(root);
      onSelectionChange(root);
    });
  }

  // Size pill-cards (Phase 6 — Modern Outfitter UX upgrade replacing dropdown).
  const sizePills = /** @type {NodeListOf<HTMLButtonElement>} */ (
    root.querySelectorAll(".size-pill")
  );
  sizePills.forEach((btn) => {
    btn.addEventListener("click", () => {
      const sz = btn.dataset.size;
      if (sz) {
        STATE.size = sz;
        // Keep hidden <select> in sync so backend / tests can still read it.
        if (sizeSelect) sizeSelect.value = sz;
        updateActiveSizePill(root);
        onSelectionChange(root);
      }
    });
  });

  // Keyboard nav for size pill grid (arrow keys).
  const sizeGrid = root.querySelector(".size-grid");
  if (sizeGrid) {
    sizeGrid.addEventListener("keydown", (ev) => {
      const e = /** @type {KeyboardEvent} */ (ev);
      const pills = Array.from(root.querySelectorAll(".size-pill"));
      const focused = document.activeElement;
      const idx = pills.indexOf(/** @type {Element} */ (focused));
      if (idx < 0) return;
      let next = idx;
      if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (idx + 1) % pills.length;
      else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (idx - 1 + pills.length) % pills.length;
      else return;
      e.preventDefault();
      /** @type {HTMLElement} */ (pills[next]).focus();
    });
  }

  const swatches = /** @type {NodeListOf<HTMLButtonElement>} */ (
    root.querySelectorAll(".finish-swatch")
  );
  swatches.forEach((btn) => {
    btn.addEventListener("click", () => {
      const finish = btn.dataset.finishId;
      if (finish) {
        STATE.finish = finish;
        onSelectionChange(root);
      }
    });
  });

  // Keyboard navigation: arrow keys cycle through swatches, Enter activates.
  const swatchRow = root.querySelector(".finish-picker");
  if (swatchRow) {
    swatchRow.addEventListener("keydown", (ev) => {
      const e = /** @type {KeyboardEvent} */ (ev);
      const items = Array.from(
        root.querySelectorAll(".finish-swatch")
      ); // live list at event time
      const focused = document.activeElement;
      const currentIdx = items.indexOf(/** @type {Element} */ (focused));
      if (e.key === "ArrowRight" || e.key === "ArrowDown") {
        e.preventDefault();
        const next = items[(currentIdx + 1) % items.length];
        /** @type {HTMLElement} */ (next).focus();
      } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
        e.preventDefault();
        const prev =
          items[(currentIdx - 1 + items.length) % items.length];
        /** @type {HTMLElement} */ (prev).focus();
      } else if (e.key === "Enter" || e.key === " ") {
        // Default click handler fires Enter, but we want spacebar too.
        if (e.key === " " && focused instanceof HTMLButtonElement) {
          e.preventDefault();
          focused.click();
        }
      }
    });
  }
}

/**
 * @param {HTMLElement} root
 */
function onSelectionChange(root) {
  syncHiddenInput(root);
  updateActiveSwatch(root);
  updateActiveSizePill(root);
  updatePriceDisplay(root);
  render();
}

/**
 * @param {HTMLElement} root
 */
function updateActiveSizePill(root) {
  root.querySelectorAll(".size-pill").forEach((el) => {
    const btn = /** @type {HTMLButtonElement} */ (el);
    const isActive = btn.dataset.size === STATE.size;
    btn.setAttribute("aria-pressed", String(isActive));
    btn.setAttribute("tabindex", isActive ? "0" : "-1");
    btn.classList.toggle("is-active", isActive);
  });
}

/**
 * @param {HTMLElement} root
 */
function syncHiddenInput(root) {
  const hidden = /** @type {HTMLInputElement | null} */ (
    root.querySelector("#selected-internal-sku")
  );
  const sku = findSku(STATE.size, STATE.finish);
  if (hidden && sku) {
    hidden.value = sku.internal_sku;
  }
}

/**
 * @param {HTMLElement} root
 */
function updateActiveSwatch(root) {
  root.querySelectorAll(".finish-swatch").forEach((el) => {
    const btn = /** @type {HTMLButtonElement} */ (el);
    const isActive = btn.dataset.finishId === STATE.finish;
    btn.setAttribute("aria-pressed", String(isActive));
    btn.classList.toggle("is-active", isActive);
  });
}

/**
 * Update the per-swatch price labels (varies by size) AND the headline price.
 * @param {HTMLElement} root
 */
function updatePriceDisplay(root) {
  // Per-swatch price labels: read data-price-cents-{size} from the button.
  root.querySelectorAll(".finish-swatch").forEach((el) => {
    const btn = /** @type {HTMLButtonElement} */ (el);
    const cents = btn.dataset[`priceCents${STATE.size.replace("x", "X")}`];
    const label = btn.querySelector(".swatch-price");
    if (label) {
      label.textContent = formatPrice(cents);
    }
  });

  // Headline price: the active selection's per-size price.
  const headline = /** @type {HTMLElement | null} */ (
    root.querySelector("#headline-price")
  );
  if (headline) {
    const activeBtn = /** @type {HTMLButtonElement | null} */ (
      root.querySelector(`.finish-swatch[data-finish-id="${STATE.finish}"]`)
    );
    const cents = activeBtn?.dataset[`priceCents${STATE.size.replace("x", "X")}`];
    headline.textContent = formatPrice(cents);
  }
}

/**
 * @param {string | undefined} cents
 * @returns {string}
 */
function formatPrice(cents) {
  if (!cents) return "—";
  const n = Number(cents);
  if (!Number.isFinite(n)) return "—";
  return `$${(n / 100).toFixed(2)}`;
}

/* ------------------------------------------------------------------ */
/* Canvas render                                                      */
/* ------------------------------------------------------------------ */

function render() {
  const canvas = /** @type {HTMLCanvasElement | null} */ (
    document.getElementById("preview-canvas")
  );
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  const sku = findSku(STATE.size, STATE.finish);
  if (!sku) return;

  const blankImg = FRAME_IMAGE_CACHE.get(sku.blank_asset);
  if (!blankImg || !POSTER_IMAGE) {
    // Assets still loading — clear and draw a placeholder.
    ctx.fillStyle = "#eee";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    return;
  }

  // Match canvas backing store to blank asset native size for crisp output,
  // but capped at 1600 px to keep memory reasonable.
  const MAX_DIM = 1600;
  let cw = blankImg.naturalWidth;
  let ch = blankImg.naturalHeight;
  if (Math.max(cw, ch) > MAX_DIM) {
    const scale = MAX_DIM / Math.max(cw, ch);
    cw = Math.round(cw * scale);
    ch = Math.round(ch * scale);
  }
  if (canvas.width !== cw || canvas.height !== ch) {
    canvas.width = cw;
    canvas.height = ch;
  }

  // 1. Frame as background.
  ctx.drawImage(blankImg, 0, 0, cw, ch);

  // 2. Poster scaled into inner_rect_pct, "contain" fit (preserve aspect).
  const ir = sku.inner_rect_pct;
  const innerX = (ir.x / 100) * cw;
  const innerY = (ir.y / 100) * ch;
  const innerW = (ir.w / 100) * cw;
  const innerH = (ir.h / 100) * ch;

  const posterAspect = POSTER_IMAGE.naturalWidth / POSTER_IMAGE.naturalHeight;
  const innerAspect = innerW / innerH;

  let drawW;
  let drawH;
  if (posterAspect > innerAspect) {
    // poster is wider than inner — fit width
    drawW = innerW;
    drawH = innerW / posterAspect;
  } else {
    drawH = innerH;
    drawW = innerH * posterAspect;
  }
  const drawX = innerX + (innerW - drawW) / 2;
  const drawY = innerY + (innerH - drawH) / 2;

  // Clip to inner rect so we never paint over the frame bezel.
  ctx.save();
  ctx.beginPath();
  ctx.rect(innerX, innerY, innerW, innerH);
  ctx.clip();
  ctx.drawImage(POSTER_IMAGE, drawX, drawY, drawW, drawH);
  ctx.restore();
}

/* ------------------------------------------------------------------ */
/* Auto-boot when DOM is ready                                        */
/* ------------------------------------------------------------------ */

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => {
    boot().catch((err) => console.error("[configurator] boot failed", err));
  });
} else {
  boot().catch((err) => console.error("[configurator] boot failed", err));
}
