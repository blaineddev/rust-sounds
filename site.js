// rust-sounds frontend.
//
// Loads data/sounds.json (the reference-extracted catalog) and renders one
// tile per FX prefab. Multi-clip prefabs cycle through their variants on
// repeated play presses. The copy button reveals a hover tooltip listing
// other prefabs that share any of this prefab's clips, when applicable.

const INITIAL_BATCH = 300;
const APPEND_BATCH = 300;
const TOOLTIP_MAX_ITEMS = 30;

const $grid = document.getElementById("grid");
const $status = document.getElementById("status");
const $search = document.getElementById("search");
const $player = document.getElementById("player");
const $pills = document.getElementById("pills");
const $volume = document.getElementById("volume");
const $tooltip = document.getElementById("tooltip");
const $sentinel = document.getElementById("sentinel");

const state = {
  data: null,                  // { clips: {...}, prefabs: [...] }
  visiblePrefabs: [],
  prefabsByPath: new Map(),    // path -> prefab object
  clipToPrefabs: new Map(),    // clip_id -> [prefab path, ...]
  renderedCount: 0,
  activeTile: null,
  activeCategory: "all",       // "all" | category name
  searchQuery: "",
  playIndex: new Map(),        // prefab.path -> next clip index (cycle)
};

function dataJsonUrl() {
  const params = new URLSearchParams(location.search);
  return params.get("fixtures") === "1" ? "fixtures/sounds.json" : "data/sounds.json";
}

async function load() {
  try {
    const res = await fetch(dataJsonUrl(), { cache: "no-cache" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.data = await res.json();
  } catch (err) {
    $status.textContent = "extractor hasn't run yet — run `python extract.py` and reload, " +
      "or open this page with ?fixtures=1 to see demo data.";
    return;
  }

  for (const pf of state.data.prefabs) {
    state.prefabsByPath.set(pf.path, pf);
    for (const cid of pf.clips) {
      let arr = state.clipToPrefabs.get(cid);
      if (!arr) {
        arr = [];
        state.clipToPrefabs.set(cid, arr);
      }
      arr.push(pf.path);
    }
  }

  initVolume();
  renderCategoryPills();
  applyFilters();

  $status.hidden = true;
  $grid.hidden = false;
}

function initVolume() {
  const stored = parseFloat(localStorage.getItem("rust-sounds.volume"));
  const vol = Number.isFinite(stored) ? Math.max(0, Math.min(1, stored)) : 0.7;
  $volume.value = String(Math.round(vol * 100));
  $player.volume = vol;
  $volume.addEventListener("input", () => {
    const v = parseInt($volume.value, 10) / 100;
    $player.volume = v;
    localStorage.setItem("rust-sounds.volume", String(v));
  });
}

function renderCategoryPills() {
  const counts = new Map();
  for (const pf of state.data.prefabs) {
    const top = pf.categories[0] || "(uncategorized)";
    counts.set(top, (counts.get(top) || 0) + 1);
  }
  const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]);

  $pills.replaceChildren();
  $pills.appendChild(buildPill("all", "all", state.data.prefabs.length));
  for (const [cat, n] of sorted) {
    $pills.appendChild(buildPill(cat, cat, n));
  }
  updatePillActive();
}

function buildPill(value, label, count) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "pill";
  btn.dataset.category = value;
  btn.innerHTML = `<span class="pill-label"></span><span class="pill-count"></span>`;
  btn.querySelector(".pill-label").textContent = label;
  btn.querySelector(".pill-count").textContent = String(count);
  btn.addEventListener("click", () => {
    state.activeCategory = value;
    updatePillActive();
    applyFilters();
  });
  return btn;
}

function updatePillActive() {
  for (const pill of $pills.querySelectorAll(".pill")) {
    pill.classList.toggle("active", pill.dataset.category === state.activeCategory);
  }
}

function applyFilters() {
  let list = state.data.prefabs;
  if (state.activeCategory && state.activeCategory !== "all") {
    list = list.filter((pf) => (pf.categories[0] || "(uncategorized)") === state.activeCategory);
  }
  const q = state.searchQuery.trim().toLowerCase();
  if (q) {
    list = list.filter((pf) =>
      pf.path.toLowerCase().includes(q) ||
      pf.name.toLowerCase().includes(q) ||
      pf.categories.join("/").toLowerCase().includes(q)
    );
  }
  state.visiblePrefabs = list;
  renderInitial();
}

function renderInitial() {
  $grid.replaceChildren();
  state.renderedCount = 0;
  state.activeTile = null;
  appendNextBatch(INITIAL_BATCH);
}

function appendNextBatch(n) {
  const slice = state.visiblePrefabs.slice(state.renderedCount, state.renderedCount + n);
  const frag = document.createDocumentFragment();
  for (const pf of slice) frag.appendChild(buildTile(pf));
  $grid.appendChild(frag);
  state.renderedCount += slice.length;
}

function buildTile(pf) {
  const tile = document.createElement("article");
  tile.className = "tile";
  tile.dataset.prefab = pf.path;

  const breadcrumb = pf.categories.join(" / ") || "(root)";
  const variants = pf.clips.length;

  // Show duration of the first variant; for cycle-on-replay UX we don't need
  // to surface every variant's duration, the play count badge already says
  // "this prefab has multiple sounds".
  const firstClip = state.data.clips[pf.clips[0]];
  const duration = firstClip ? `${firstClip.duration.toFixed(2)}s` : "?";

  tile.innerHTML = `
    <div class="name"></div>
    <div class="category"></div>
    <div class="duration"></div>
    <div class="actions">
      <button class="play" type="button"><span class="play-icon">▶</span><span class="variant-badge"></span></button>
      <button class="copy" type="button" title="Copy prefab path">📋</button>
    </div>
  `;
  tile.querySelector(".name").textContent = pf.name;
  tile.querySelector(".category").textContent = breadcrumb;
  tile.querySelector(".duration").textContent = duration;

  const badge = tile.querySelector(".variant-badge");
  if (variants > 1) {
    badge.textContent = `×${variants}`;
    tile.querySelector(".play").title = `Play (${variants} variants — cycles on replay)`;
  } else {
    badge.remove();
    tile.querySelector(".play").title = "Play";
  }

  return tile;
}

// --- search / scroll ---------------------------------------------------------

const observer = new IntersectionObserver((entries) => {
  for (const e of entries) {
    if (e.isIntersecting && state.renderedCount < state.visiblePrefabs.length) {
      appendNextBatch(APPEND_BATCH);
    }
  }
}, { rootMargin: "400px 0px" });
observer.observe($sentinel);

let searchTimer = null;
$search.addEventListener("input", () => {
  state.searchQuery = $search.value;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(applyFilters, 80);
});

// --- play / copy -------------------------------------------------------------

$grid.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  const button = target.closest("button");
  if (!button) return;
  const tile = button.closest(".tile");
  if (!tile) return;

  if (button.classList.contains("play")) handlePlay(tile);
  else if (button.classList.contains("copy")) handleCopy(tile);
});

function handlePlay(tile) {
  const pf = state.prefabsByPath.get(tile.dataset.prefab);
  if (!pf || pf.clips.length === 0) return;

  const idx = (state.playIndex.get(pf.path) || 0) % pf.clips.length;
  const cid = pf.clips[idx];
  const clip = state.data.clips[cid];
  if (!clip) return;
  state.playIndex.set(pf.path, idx + 1);

  if (state.activeTile && state.activeTile !== tile) {
    state.activeTile.classList.remove("active");
  }
  state.activeTile = tile;
  tile.classList.add("active");

  $player.src = clip.path;
  $player.play().catch((err) => {
    // AbortError fires when the user hits play again before the previous
    // play() resolves; that's normal and shouldn't disable the button.
    if (err && err.name === "AbortError") return;
    markTileError(tile);
  });
}

function markTileError(tile) {
  const playBtn = tile.querySelector(".play");
  playBtn.querySelector(".play-icon").textContent = "⚠";
  playBtn.disabled = true;
  playBtn.title = "audio failed to load";
}

async function handleCopy(tile) {
  const prefab = tile.dataset.prefab;
  let copied = false;
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(prefab);
      copied = true;
    } catch {}
  }
  if (!copied) copied = legacyCopy(prefab);
  if (copied) flashTile(tile);
}

function legacyCopy(text) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch {}
  document.body.removeChild(ta);
  return ok;
}

function flashTile(tile) {
  tile.classList.add("flash");
  setTimeout(() => tile.classList.remove("flash"), 600);
}

// --- copy button hover tooltip ----------------------------------------------
//
// When the user hovers a tile's copy button, we look up every other prefab
// whose clip set overlaps with this tile's, and render a list. Empty list
// (clip is unique to this prefab) -> no tooltip; the native `title` attribute
// covers that case.

let tooltipHideTimer = null;

$grid.addEventListener("mouseover", (event) => {
  const button = event.target instanceof HTMLElement ? event.target.closest(".copy") : null;
  if (button) showTooltipFor(button);
});
$grid.addEventListener("mouseout", (event) => {
  const button = event.target instanceof HTMLElement ? event.target.closest(".copy") : null;
  if (button) scheduleTooltipHide();
});
$grid.addEventListener("focusin", (event) => {
  const button = event.target instanceof HTMLElement ? event.target.closest(".copy") : null;
  if (button) showTooltipFor(button);
});
$grid.addEventListener("focusout", (event) => {
  const button = event.target instanceof HTMLElement ? event.target.closest(".copy") : null;
  if (button) scheduleTooltipHide();
});
$tooltip.addEventListener("mouseenter", () => {
  // Keep open while user mouses over the tooltip itself (e.g. to read).
  if (tooltipHideTimer) {
    clearTimeout(tooltipHideTimer);
    tooltipHideTimer = null;
  }
});
$tooltip.addEventListener("mouseleave", () => scheduleTooltipHide(0));

function scheduleTooltipHide(delay = 120) {
  if (tooltipHideTimer) clearTimeout(tooltipHideTimer);
  tooltipHideTimer = setTimeout(() => { $tooltip.hidden = true; }, delay);
}

function showTooltipFor(button) {
  if (tooltipHideTimer) {
    clearTimeout(tooltipHideTimer);
    tooltipHideTimer = null;
  }
  const tile = button.closest(".tile");
  if (!tile) return;
  const pf = state.prefabsByPath.get(tile.dataset.prefab);
  if (!pf) return;

  const others = new Set();
  for (const cid of pf.clips) {
    for (const otherPath of state.clipToPrefabs.get(cid) || []) {
      if (otherPath !== pf.path) others.add(otherPath);
    }
  }
  if (others.size === 0) {
    $tooltip.hidden = true;
    return;
  }

  const list = [...others].sort();
  const shown = list.slice(0, TOOLTIP_MAX_ITEMS);
  const overflow = list.length - shown.length;

  $tooltip.replaceChildren();
  const heading = document.createElement("div");
  heading.className = "tooltip-heading";
  heading.textContent = `Same audio used by ${list.length} other prefab${list.length === 1 ? "" : "s"}:`;
  $tooltip.appendChild(heading);
  const ul = document.createElement("ul");
  for (const p of shown) {
    const li = document.createElement("li");
    li.textContent = p;
    ul.appendChild(li);
  }
  if (overflow > 0) {
    const li = document.createElement("li");
    li.className = "tooltip-overflow";
    li.textContent = `…and ${overflow} more`;
    ul.appendChild(li);
  }
  $tooltip.appendChild(ul);

  positionTooltip(button);
  $tooltip.hidden = false;
}

function positionTooltip(button) {
  // Show first to measure; we'll reposition. Reserve some viewport padding.
  $tooltip.style.left = "0px";
  $tooltip.style.top = "0px";
  $tooltip.hidden = false;
  const rect = button.getBoundingClientRect();
  const ttRect = $tooltip.getBoundingClientRect();
  const margin = 8;

  // Prefer below-and-aligned-right; fall back to above if not enough room.
  let left = rect.right + window.scrollX - ttRect.width;
  let top = rect.bottom + window.scrollY + margin;
  if (left < window.scrollX + margin) left = window.scrollX + margin;
  if (top + ttRect.height > window.scrollY + window.innerHeight - margin) {
    top = rect.top + window.scrollY - ttRect.height - margin;
  }
  $tooltip.style.left = `${left}px`;
  $tooltip.style.top = `${top}px`;
}

load();
