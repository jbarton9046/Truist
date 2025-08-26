// static/js/movers_weekly.js
// Appends weekly (Mon–Sun) % change next to existing MoM Δ% in your
// "Category Movers" table. No server changes required.
//
// Requirements:
//  - window.CL_URLS.INSPECT_URL must point to admin_categories.inspect_path
//  - The table is either given id="category-movers" (recommended)
//    OR has headers including "Category" and some percent column
//
// You can force a rerun via:  window.refreshMoversWeekly()

(function () {
  const DEBUG = !!window.MOVERS_WEEKLY_DEBUG;
  let initialized = false;

  // Boot
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setup);
  } else {
    setup();
  }
  window.addEventListener("load", tryInitOnce);

  const mo = new MutationObserver(() => tryInitOnce());
  mo.observe(document.documentElement, { childList: true, subtree: true });

  function setup() {
    if (!window.CL_URLS || !window.CL_URLS.INSPECT_URL) {
      warn("[MoversWeekly] window.CL_URLS.INSPECT_URL missing; aborting.");
      return;
    }
    window.refreshMoversWeekly = () => {
      initialized = false;
      tryInitOnce(true);
    };
    tryInitOnce();
  }

  function tryInitOnce(force = false) {
    if (initialized && !force) return;

    const table = findCategoryMoversTable();
    if (!table) return;

    // Avoid double-run unless forced
    if (table.dataset.mwEnhanced === "1" && !force) return;
    table.dataset.mwEnhanced = "1";

    const { headerRow, bodyRows } = splitTable(table);
    if (!headerRow || !bodyRows.length) return;

    const idx = findColumnIndexes(headerRow);
    if (idx.category === -1 || idx.deltaPct === -1) {
      warn("[MoversWeekly] Could not locate Category or Δ% column.");
      return;
    }

    const U = window.CL_URLS;
    const INSPECT_URL = U.INSPECT_URL;

    // Derive category name per row (prefer explicit data-cat)
    const categories = bodyRows.map((tr) => {
      const explicit = tr.getAttribute("data-cat");
      return explicit ? explicit : cleanText(getCellText(tr, idx.category));
    });

    const windows = computeWeeks(); // weekA (most recent full), weekB (prev full)

    // Build per-category tasks and run with modest concurrency
    const tasks = categories.map((cat, i) => async () => {
      try {
        const txs = await fetchCategoryTxs(INSPECT_URL, cat);
        const pct = weeklyChangePct(txs, windows.weekA, windows.weekB);
        annotateRow(bodyRows[i], idx.deltaPct, pct);
      } catch (err) {
        warn("[MoversWeekly] Error for", cat, err);
        annotateRow(bodyRows[i], idx.deltaPct, null);
      }
    });

    runBatched(tasks, 4).then(() => {
      initialized = true;
      log("[MoversWeekly] Done.");
    });
  }

  // ---------- Fetch + compute ----------
  async function fetchCategoryTxs(INSPECT_URL, category) {
    const p = new URLSearchParams();
    p.set("level", "category");
    p.set("cat", category);
    p.set("limit", "5000");
    const res = await fetch(`${INSPECT_URL}?${p.toString()}`, { headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const json = await res.json();
    const data = json?.data || json || {};
    return data.transactions || data.items || json.transactions || json.items || [];
  }

  function weeklyChangePct(txs, weekA, weekB) {
    // Spend = sum of ABS(negative amounts)
    const sumA = sumWeekSpend(txs, weekA.start, weekA.end);
    const sumB = sumWeekSpend(txs, weekB.start, weekB.end);
    if (!isFinite(sumB) || sumB === 0) return sumA === 0 ? 0 : null; // no baseline
    const delta = sumA - sumB;
    return (delta / sumB) * 100;
  }

  function sumWeekSpend(txs, start, end) {
    let total = 0;
    for (const t of txs) {
      const d = parseTxDate(t.date || t.txn_date || t.posted || t.created_at);
      if (!d) continue;
      if (d >= start && d <= end) {
        const a = Number(t.amount || t.amt || 0);
        if (a < 0) total += Math.abs(a);
      }
    }
    return total;
  }

  // ---------- Table helpers ----------
  function findCategoryMoversTable() {
    // Prefer explicit id or data-role
    const byId = document.getElementById("category-movers");
    if (byId) return byId;
    const byRole = document.querySelector('table[data-role="category-movers"]');
    if (byRole) return byRole;

    // Heuristic fallback
    const tables = Array.from(document.querySelectorAll("table"));
    for (const t of tables) {
      const thead = t.tHead || t.querySelector("thead");
      if (!thead) continue;
      const ths = Array.from(thead.querySelectorAll("th,td")).map((th) => cleanText(th.textContent).toLowerCase());
      if (!ths.length) continue;
      const hasCat = ths.some((x) => x === "category" || x === "name");
      const hasPct = ths.some((x) => x.includes("%") || x.includes("delta%") || x.includes("Δ%") || x.includes("Δ %") || x.includes("mom"));
      if (hasCat && hasPct) return t;
    }
    return null;
  }

  function splitTable(table) {
    const thead = table.tHead || table.querySelector("thead");
    const headerRow = thead ? (thead.rows[0] || null) : (table.rows[0] || null);
    const tbody = table.tBodies && table.tBodies[0] ? table.tBodies[0] : table.querySelector("tbody") || table;
    const bodyRows = tbody ? Array.from(tbody.rows).filter((r) => r !== headerRow) : [];
    return { headerRow, bodyRows };
  }

  function findColumnIndexes(headerRow) {
    const idx = { category: -1, deltaPct: -1 };
    const cols = Array.from(headerRow.cells).map((c) => cleanText(c.textContent).toLowerCase());

    idx.category = cols.findIndex((n) => n === "category" || n === "name");
    if (idx.category === -1) idx.category = 0;

    idx.deltaPct = cols.findIndex(
      (n) =>
        n === "Δ%" ||
        n === "delta%" ||
        n === "change%" ||
        n.includes("Δ%") ||
        n.includes("delta%") ||
        n.includes("%") ||
        n.includes("mom")
    );
    if (idx.deltaPct === -1) {
      for (let i = cols.length - 1; i >= 0; i--) {
        if (cols[i].includes("%")) { idx.deltaPct = i; break; }
      }
    }
    return idx;
  }

  function annotateRow(tr, deltaPctIndex, weeklyPct) {
    const td = tr.cells[deltaPctIndex];
    if (!td) return;

    // Remember base text to avoid double-append on refresh
    if (!td.dataset.mwBase) {
      td.dataset.mwBase = cleanText(td.textContent || "");
    }
    const base = td.dataset.mwBase;

    const suffix =
      weeklyPct == null
        ? "(—)"
        : (weeklyPct > 0 ? "(+" : "(") + formatPct(weeklyPct) + "%)";

    td.textContent = `${base} ${suffix}`.trim();
  }

  function getCellText(tr, i) {
    const cell = tr.cells[i];
    return cell ? cell.textContent : "";
  }

  // ---------- Date helpers (Mon–Sun windows) ----------
  function computeWeeks() {
    const today = new Date();
    const lastSun = lastCompletedSunday(today); // 00:00 local Sunday
    const weekAEnd = endOfDay(lastSun);
    const weekAStart = startOfDay(addDays(lastSun, -6));
    const weekBEnd = endOfDay(addDays(weekAStart, -1));
    const weekBStart = startOfDay(addDays(weekBEnd, -6));
    return {
      weekA: { start: weekAStart, end: weekAEnd },
      weekB: { start: weekBStart, end: weekBEnd },
    };
  }

  function lastCompletedSunday(d) {
    const x = new Date(d.getTime());
    x.setHours(0, 0, 0, 0);
    const dow = x.getDay(); // 0=Sun
    const back = dow === 0 ? 7 : dow; // if today is Sun, go back 7; else back to last Sun
    x.setDate(x.getDate() - back);
    return x;
  }

  function startOfDay(d) { const x = new Date(d.getTime()); x.setHours(0,0,0,0); return x; }
  function endOfDay(d)   { const x = new Date(d.getTime()); x.setHours(23,59,59,999); return x; }
  function addDays(d,n)  { const x = new Date(d.getTime()); x.setDate(x.getDate()+n); return x; }

  function parseTxDate(s) {
    if (!s) return null;
    if (s.includes("/")) {
      const [mm, dd, yyyy] = s.split("/").map((x) => parseInt(x, 10));
      if (!yyyy || !mm || !dd) return null;
      return new Date(yyyy, mm - 1, dd, 12, 0, 0, 0); // noon local
    }
    const d = new Date(s);
    if (isNaN(d.getTime())) return null;
    return new Date(d.getFullYear(), d.getMonth(), d.getDate(), 12, 0, 0, 0);
  }

  // ---------- Utils ----------
  function runBatched(tasks, concurrency = 4) {
    return new Promise((resolve) => {
      let i = 0, running = 0;
      function next() {
        while (running < concurrency && i < tasks.length) {
          const job = tasks[i++];
          running++;
          Promise.resolve()
            .then(job)
            .catch(() => {})
            .then(() => {
              running--;
              if (i >= tasks.length && running === 0) resolve();
              else next();
            });
        }
      }
      next();
    });
  }

  function cleanText(s) { return String(s || "").replace(/\s+/g, " ").trim(); }
  function formatPct(n) {
    const v = Math.round(n * 10) / 10;
    const txt = (v === 0 ? 0 : v).toFixed(1);
    return txt.replace(/-0\.0/, "0.0");
  }
  function log(...args)  { if (DEBUG) console.log(...args); }
  function warn(...args) { if (DEBUG) console.warn(...args); }
})();
