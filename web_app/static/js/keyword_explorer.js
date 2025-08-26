(function () {
  // --- Configuration & helpers ---
  const PAGE_SIZE = 50;

  const state = {
    raw: [],             // [{keyword, category, subcategory, count, recent}]
    filtered: [],
    page: 1,
    view: "table",       // "table" | "cards"
    groupBy: "",         // "", "category", "initial"
    selection: new Set(),
    search: "",
    filterCategory: "",
    filterSubcategory: "",
    sort: "alpha-asc",   // matches <select id="kw-sort">
    onlyDupes: false,
    onlyUnassigned: false,
  };

  const els = {
    summary: null,
    search: null,
    filterCategory: null,
    filterSubcategory: null,
    sort: null,
    groupBy: null,
    dupes: null,
    unassigned: null,
    container: null,
    pagination: null,
    pageLabel: null,
    prev: null,
    next: null,
    selectedBar: null,
    selectedChips: null,
    clearSelection: null,
    exportBtn: null,
    removeBtn: null,
    mergeBtn: null,
    viewTabs: null,
  };

  function $(sel) { return document.querySelector(sel); }
  function h(tag, attrs = {}, children = []) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") el.className = v;
      else if (k === "dataset") Object.assign(el.dataset, v);
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v);
    }
    for (const child of [].concat(children)) {
      if (child == null) continue;
      el.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    }
    return el;
  }

  function formatDate(d) {
    try {
      const dt = (d instanceof Date) ? d : new Date(d);
      if (isNaN(dt)) return "";
      return dt.toISOString().slice(0,10);
    } catch { return ""; }
  }

  function normalizeKeyword(s) {
    return String(s || "").trim();
  }

  // Build index from CFG
  function buildIndexFromCFG(CFG) {
    const catMap = (CFG && CFG.CATEGORY_KEYWORDS) || {};
    const subMap = (CFG && (CFG.SUBCATEGORY_MAPS || CFG.subcategory_maps || {})) || {};

    // Optional usage metadata:
    // CFG.KEYWORD_STATS = { "walmart": {count:123, recent:"2025-08-10"}, ... }
    const stats = (CFG && CFG.KEYWORD_STATS) || {};

    const rows = [];

    const categories = Object.keys(catMap).sort();
    categories.forEach(cat => {
      const keywords = Array.isArray(catMap[cat]) ? catMap[cat] : [];
      keywords.forEach(kwRaw => {
        const kw = normalizeKeyword(kwRaw);
        if (!kw) return;
        const info = stats[kw] || {};
        rows.push({
          keyword: kw,
          category: cat,
          subcategory: "",
          count: Number(info.count || 0),
          recent: info.recent || null,
        });
      });
    });

    // Attribute subcategories if SUBCATEGORY_MAPS lists keywords under subs
    const subParents = Object.keys(subMap);
    subParents.forEach(parentCat => {
      const subcats = subMap[parentCat] || {};
      Object.keys(subcats).forEach(sub => {
        const list = subcats[sub] || [];
        list.forEach(kw => {
          const needle = normalizeKeyword(kw);
          rows.forEach(r => {
            if (r.keyword === needle && (!r.subcategory || r.category === parentCat)) {
              r.subcategory = sub;
              if (!r.category) r.category = parentCat;
            }
          });
        });
      });
    });

    // Add "unassigned" from stats
    Object.keys(stats).forEach(kw => {
      const exists = rows.find(r => r.keyword === kw);
      if (!exists) {
        rows.push({
          keyword: kw,
          category: "",
          subcategory: "",
          count: Number(stats[kw].count || 0),
          recent: stats[kw].recent || null,
        });
      }
    });

    // Deduplicate by (kw,cat,sub) and sum counts / max recent
    const key = r => `${r.keyword}|||${r.category}|||${r.subcategory}`;
    const map = new Map();
    rows.forEach(r => {
      const k = key(r);
      if (!map.has(k)) map.set(k, { ...r });
      else {
        const cur = map.get(k);
        cur.count += r.count;
        if (!cur.recent || (r.recent && new Date(r.recent) > new Date(cur.recent))) {
          cur.recent = r.recent;
        }
      }
    });

    const finalRows = Array.from(map.values());
    // Duplicate flag (same keyword appears in multiple rows)
    const seen = new Map();
    finalRows.forEach(r => {
      const arr = seen.get(r.keyword) || [];
      arr.push(r);
      seen.set(r.keyword, arr);
    });
    finalRows.forEach(r => {
      r.hasDupes = (seen.get(r.keyword) || []).length > 1;
    });

    return { rows: finalRows, categories };
  }

  // Apply filters, search, sort
  function applyFilters() {
    const q = state.search.toLowerCase().trim();
    const cat = state.filterCategory;
    const sub = state.filterSubcategory;
    const dupe = state.onlyDupes;
    const unassigned = state.onlyUnassigned;

    let arr = state.raw.slice();

    if (q) {
      arr = arr.filter(r =>
        r.keyword.toLowerCase().includes(q) ||
        r.category.toLowerCase().includes(q) ||
        r.subcategory.toLowerCase().includes(q)
      );
    }
    if (cat) arr = arr.filter(r => r.category === cat);
    if (sub) arr = arr.filter(r => r.subcategory === sub);
    if (dupe) arr = arr.filter(r => r.hasDupes);
    if (unassigned) arr = arr.filter(r => !r.category);

    // Sort
    const cmp = {
      "alpha-asc": (a,b) => a.keyword.localeCompare(b.keyword),
      "alpha-desc": (a,b) => b.keyword.localeCompare(a.keyword),
      "count-desc": (a,b) => (b.count - a.count) || a.keyword.localeCompare(b.keyword),
      "count-asc": (a,b) => (a.count - b.count) || a.keyword.localeCompare(b.keyword),
      "recent-desc": (a,b) => new Date(b.recent||0) - new Date(a.recent||0),
      "recent-asc": (a,b) => new Date(a.recent||0) - new Date(b.recent||0),
    }[state.sort] || ((a,b) => a.keyword.localeCompare(b.keyword));
    arr.sort(cmp);

    state.filtered = arr;
    state.page = 1;
    updateSummary();
    render();
  }

  function updateSummary() {
    const total = state.raw.length;
    const shown = state.filtered.length;
    const selected = state.selection.size;
    els.summary.textContent = `${shown.toLocaleString()} shown of ${total.toLocaleString()} • ${selected} selected`;
  }

  function paginate(arr) {
    const start = (state.page - 1) * PAGE_SIZE;
    return arr.slice(start, start + PAGE_SIZE);
  }

  function render() {
    const container = els.container;
    container.innerHTML = "";

    // Grouping
    let groups = [{ key: "", items: state.filtered }];
    if (state.groupBy === "category") {
      const map = new Map();
      state.filtered.forEach(r => {
        const k = r.category || "(uncategorized)";
        (map.get(k) || map.set(k, []).get(k)).push(r);
      });
      groups = Array.from(map.entries()).map(([k, items]) => ({ key:k, items }));
    } else if (state.groupBy === "initial") {
      const map = new Map();
      state.filtered.forEach(r => {
        const ch = (r.keyword[0] || "#").toUpperCase();
        const k = /[A-Z]/.test(ch) ? ch : "#";
        (map.get(k) || map.set(k, []).get(k)).push(r);
      });
      groups = Array.from(map.entries()).sort((a,b)=>a[0].localeCompare(b[0])).map(([k, items]) => ({ key:k, items }));
    }

    // Pagination applies per-view to keep it simple
    const pageItems = paginate(groups.length === 1 ? groups[0].items : state.filtered);
    const totalPages = Math.max(1, Math.ceil(state.filtered.length / PAGE_SIZE));
    els.pagination.style.display = totalPages > 1 ? "" : "none";
    els.pageLabel.textContent = `Page ${state.page} / ${totalPages}`;
    els.prev.disabled = state.page <= 1;
    els.next.disabled = state.page >= totalPages;

    if (!pageItems.length) {
      container.appendChild(h("div", { class:"empty", text:"No keywords match your filters." }));
      return;
    }

    if (state.view === "cards") {
      renderCards(container, pageItems);
    } else {
      renderTable(container, pageItems);
    }

    // Selected bar
    const hasSel = state.selection.size > 0;
    els.selectedBar.style.display = hasSel ? "" : "none";
    els.removeBtn.disabled = !hasSel;
    els.mergeBtn.disabled = state.selection.size < 2;
    renderSelectedChips();
  }

  function renderTable(container, items) {
    const table = h("table");
    const thead = h("thead", {}, [
      h("tr", {}, [
        h("th", {}, ["Sel"]),
        h("th", {}, ["Keyword"]),
        h("th", {}, ["Category"]),
        h("th", {}, ["Subcategory"]),
        h("th", {}, ["Count"]),
        h("th", {}, ["Recent"]),
        h("th", {}, ["Actions"]),
      ])
    ]);

    const tbody = h("tbody");
    items.forEach(r => {
      const selected = state.selection.has(r.keyword);
      const tr = h("tr", {}, [
        h("td", {}, [
          h("input", {
            type:"checkbox",
            checked: selected,
            onchange: () => toggleSelect(r.keyword)
          })
        ]),
        h("td", {}, [
          h("span", { class:"keyword-chip" }, [r.keyword])
        ]),
        h("td", {}, [r.category || h("span", { class:"muted", text:"—" })]),
        h("td", {}, [r.subcategory || h("span", { class:"muted", text:"—" })]),
        h("td", { class:"mono" }, [String(r.count || 0)]),
        h("td", {}, [r.recent ? formatDate(r.recent) : h("span", { class:"muted", text:"—" })]),
        h("td", {}, [
          h("div", { class:"row-actions" }, [
            h("button", { class:"btn ghost", onclick: () => renameKeywordPrompt(r.keyword) }, ["Rename"]),
            h("button", { class:"btn ghost", onclick: () => removeKeyword(r.keyword) }, ["Remove"]),
          ])
        ])
      ]);
      tbody.appendChild(tr);
    });

    table.appendChild(thead);
    table.appendChild(tbody);
    const wrap = h("div", { class:"table-wrap" }, [table]);
    container.appendChild(wrap);
  }

  function renderCards(container, items) {
    const grid = h("div", { class:"grid" });
    items.forEach(r => {
      const selected = state.selection.has(r.keyword);
      grid.appendChild(
        h("div", { class:"kw-card" }, [
          h("div", { class:"top" }, [
            h("div", {}, [h("span", { class:"kw", text:r.keyword })]),
            h("input", {
              type:"checkbox",
              checked:selected,
              onchange: () => toggleSelect(r.keyword)
            })
          ]),
          h("div", {}, [h("span", { class:"pill" }, ["Category:", " ", r.category || h("span", { class:"muted", text:"—" })])]),
          h("div", {}, [h("span", { class:"pill" }, ["Subcat:", " ", r.subcategory || h("span", { class:"muted", text:"—" })])]),
          h("div", {}, [h("span", { class:"pill mono" }, ["Count:", " ", String(r.count || 0)])]),
          h("div", {}, [h("span", { class:"pill" }, ["Recent:", " ", r.recent ? formatDate(r.recent) : "—"]) ]),
          h("div", { class:"row-actions" }, [
            h("button", { class:"btn ghost", onclick: () => renameKeywordPrompt(r.keyword) }, ["Rename"]),
            h("button", { class:"btn ghost", onclick: () => removeKeyword(r.keyword) }, ["Remove"]),
          ])
        ])
      );
    });
    container.appendChild(grid);
  }

  function renderSelectedChips() {
    els.selectedChips.innerHTML = "";
    Array.from(state.selection).sort().forEach(kw => {
      els.selectedChips.appendChild(
        h("span", { class:"chip" }, [
          kw,
          h("span", { class:"x", onclick: () => toggleSelect(kw) }, ["×"])
        ])
      );
    });
  }

  function toggleSelect(keyword) {
    if (state.selection.has(keyword)) state.selection.delete(keyword);
    else state.selection.add(keyword);
    updateSummary();
    render();
  }

  function setView(v) {
    state.view = v;
    document.querySelectorAll("#all-keywords .view-tab").forEach(btn => {
      btn.classList.toggle("active", btn.dataset.view === v);
    });
    render();
  }

  function collectFiltersFromDOM() {
    state.search = (els.search.value || "").trim();
    state.filterCategory = els.filterCategory.value || "";
    state.filterSubcategory = els.filterSubcategory.value || "";
    state.sort = els.sort.value || "alpha-asc";
    state.groupBy = els.groupBy.value || "";
    state.onlyDupes = !!els.dupes.checked;
    state.onlyUnassigned = !!els.unassigned.checked;
  }

  // --- Actions (non-destructive; emit patch file) ---
  function removeKeyword(keyword) {
    if (!confirm(`Remove keyword "${keyword}" from config? This won’t touch your data; it prepares a patch to apply to your config.`)) return;
    const patch = {
      remove: [keyword],
      merge: {},
      rename: {},
    };
    emitPatch(patch);
  }

  function renameKeywordPrompt(keyword) {
    const to = prompt(`Rename keyword "${keyword}" to:`, keyword);
    if (!to || to === keyword) return;
    const patch = {
      remove: [],
      merge: {},
      rename: { [keyword]: to }
    };
    emitPatch(patch);
  }

  function bulkRemove() {
    if (!state.selection.size) return;
    if (!confirm(`Remove ${state.selection.size} keywords from config?`)) return;
    const patch = {
      remove: Array.from(state.selection),
      merge: {},
      rename: {}
    };
    emitPatch(patch);
  }

  function bulkMerge() {
    if (state.selection.size < 2) return;
    const kws = Array.from(state.selection);
    const canonical = prompt(
      `Merge ${kws.length} keywords into one canonical keyword.\nEnter the canonical keyword:`,
      kws[0]
    );
    if (!canonical) return;
    const patch = {
      remove: [],
      merge: { [canonical]: kws.filter(k => k !== canonical) },
      rename: {}
    };
    emitPatch(patch);
  }

  function exportSelection() {
    const data = {
      selected: Array.from(state.selection),
      filters: {
        search: state.search,
        category: state.filterCategory,
        subcategory: state.filterSubcategory,
        sort: state.sort,
        groupBy: state.groupBy,
        onlyDupes: state.onlyDupes,
        onlyUnassigned: state.onlyUnassigned,
      }
    };
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = h("a", { href:url, download:"keyword_selection.json" });
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      URL.revokeObjectURL(url);
      a.remove();
    }, 0);
  }

  function emitPatch(patch) {
    const blob = new Blob([JSON.stringify(patch, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = h("a", { href:url, download:"keywords_patch.json" });
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      URL.revokeObjectURL(url);
      a.remove();
    }, 0);
    alert("Patch file generated (keywords_patch.json). Apply it to your CATEGORY_KEYWORDS/SUBCATEGORY_MAPS offline or wire an admin endpoint to consume it.");
  }

  // --- Init ---
  function initDOM() {
    els.summary = $("#kw-summary");
    els.search = $("#kw-search");
    els.filterCategory = $("#kw-filter-category");
    els.filterSubcategory = $("#kw-filter-subcategory");
    els.sort = $("#kw-sort");
    els.groupBy = $("#kw-groupby");
    els.dupes = $("#kw-dupes");
    els.unassigned = $("#kw-unassigned");
    els.container = $("#kw-container");
    els.pagination = $("#kw-pagination");
    els.pageLabel = $("#kw-page-label");
    els.prev = $("#kw-prev");
    els.next = $("#kw-next");
    els.selectedBar = $("#kw-selected-bar");
    els.selectedChips = $("#kw-selected-chips");
    els.clearSelection = $("#kw-clear-selection");
    els.exportBtn = $("#kw-export");
    els.removeBtn = $("#kw-remove");
    els.mergeBtn = $("#kw-merge");
    els.viewTabs = document.querySelectorAll("#all-keywords .view-tab");

    els.search.addEventListener("input", onFilterChange);
    els.filterCategory.addEventListener("change", onCategoryChange);
    els.filterSubcategory.addEventListener("change", onFilterChange);
    els.sort.addEventListener("change", onFilterChange);
    els.groupBy.addEventListener("change", onFilterChange);
    els.dupes.addEventListener("change", onFilterChange);
    els.unassigned.addEventListener("change", onFilterChange);

    els.prev.addEventListener("click", () => { if (state.page > 1) { state.page--; render(); }});
    els.next.addEventListener("click", () => {
      const totalPages = Math.max(1, Math.ceil(state.filtered.length / PAGE_SIZE));
      if (state.page < totalPages) { state.page++; render(); }
    });

    els.clearSelection.addEventListener("click", () => { state.selection.clear(); render(); });
    els.exportBtn.addEventListener("click", exportSelection);
    els.removeBtn.addEventListener("click", bulkRemove);
    els.mergeBtn.addEventListener("click", bulkMerge);

    els.viewTabs.forEach(btn => btn.addEventListener("click", () => setView(btn.dataset.view)));
  }

  function onCategoryChange() {
    const cat = els.filterCategory.value;
    const subMap = (window.CFG && (window.CFG.SUBCATEGORY_MAPS || window.CFG.subcategory_maps || {})) || {};
    els.filterSubcategory.innerHTML = "";
    els.filterSubcategory.appendChild(h("option", { value:"" }, ["All subcategories"]));
    if (!cat) {
      els.filterSubcategory.disabled = true;
    } else {
      els.filterSubcategory.disabled = false;
      const subs = Object.keys(subMap[cat] || {}).sort();
      subs.forEach(s => {
        els.filterSubcategory.appendChild(h("option", { value:s }, [s]));
      });
    }
    onFilterChange();
  }

  function onFilterChange() {
    collectFiltersFromDOM();
    applyFilters();
  }

  function populateCategoryFilter(categories) {
    els.filterCategory.innerHTML = "";
    els.filterCategory.appendChild(h("option", { value:"" }, ["All categories"]));
    categories.forEach(c => {
      els.filterCategory.appendChild(h("option", { value:c }, [c]));
    });
  }

  function bootstrap() {
    initDOM();

    const CFG = window.CFG || {};
    const { rows, categories } = buildIndexFromCFG(CFG);
    state.raw = rows;
    populateCategoryFilter(categories);

    collectFiltersFromDOM();
    applyFilters();
  }

  document.addEventListener("DOMContentLoaded", bootstrap);
})();
