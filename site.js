const INITIAL_BATCH = 300;
const APPEND_BATCH = 300;

const $grid = document.getElementById("grid");
const $status = document.getElementById("status");
const $search = document.getElementById("search");
const $player = document.getElementById("player");

let allEntries = [];
let visibleEntries = [];
let renderedCount = 0;
let activeTile = null;

function indexJsonUrl() {
  // Use ./index.json in production, ./fixtures/index.json when running locally without an extracted index.
  const params = new URLSearchParams(location.search);
  return params.get("fixtures") === "1" ? "fixtures/index.json" : "index.json";
}

async function load() {
  try {
    const res = await fetch(indexJsonUrl(), { cache: "no-cache" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    allEntries = await res.json();
  } catch (err) {
    $status.textContent = "extractor hasn't run yet — run `python extract.py` and reload, " +
      "or open this page with ?fixtures=1 to see demo data.";
    return;
  }

  visibleEntries = allEntries;
  $status.hidden = true;
  $grid.hidden = false;
  renderInitial();
}

function renderInitial() {
  $grid.replaceChildren();
  renderedCount = 0;
  appendNextBatch(INITIAL_BATCH);
}

function appendNextBatch(n) {
  const slice = visibleEntries.slice(renderedCount, renderedCount + n);
  const frag = document.createDocumentFragment();
  for (const entry of slice) frag.appendChild(buildTile(entry));
  $grid.appendChild(frag);
  renderedCount += slice.length;
}

function buildTile(entry) {
  const tile = document.createElement("article");
  tile.className = "tile";
  tile.dataset.prefab = entry.prefab;
  tile.dataset.file = entry.file;
  tile.innerHTML = `
    <div class="name"></div>
    <div class="category"></div>
    <div class="duration"></div>
    <div class="actions">
      <button class="play" title="Play">▶</button>
      <button class="copy" title="Copy prefab path">📋</button>
    </div>
  `;
  tile.querySelector(".name").textContent = entry.name;
  tile.querySelector(".category").textContent = entry.category || "(root)";
  tile.querySelector(".duration").textContent = `${entry.duration_ms} ms`;
  return tile;
}

load();

const $sentinel = document.getElementById("sentinel");

const observer = new IntersectionObserver((entries) => {
  for (const e of entries) {
    if (e.isIntersecting && renderedCount < visibleEntries.length) {
      appendNextBatch(APPEND_BATCH);
    }
  }
}, { rootMargin: "400px 0px" });

observer.observe($sentinel);

let searchTimer = null;

$search.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(applySearch, 80);
});

function applySearch() {
  const q = $search.value.trim().toLowerCase();
  if (!q) {
    visibleEntries = allEntries;
  } else {
    visibleEntries = allEntries.filter((e) =>
      e.prefab.toLowerCase().includes(q) ||
      e.name.toLowerCase().includes(q) ||
      (e.category && e.category.toLowerCase().includes(q))
    );
  }
  renderInitial();
}

$grid.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLButtonElement)) return;
  const tile = target.closest(".tile");
  if (!tile) return;

  if (target.classList.contains("play")) handlePlay(tile);
  else if (target.classList.contains("copy")) handleCopy(tile);
});

function handlePlay(tile) {
  if (activeTile && activeTile !== tile) {
    activeTile.classList.remove("active");
  }
  activeTile = tile;
  tile.classList.add("active");

  $player.src = tile.dataset.file;
  $player.play().catch(() => markTileError(tile));
}

function markTileError(tile) {
  const playBtn = tile.querySelector(".play");
  playBtn.textContent = "⚠";
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
