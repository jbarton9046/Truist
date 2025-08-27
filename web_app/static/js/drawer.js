// static/js/drawer.js — single source of truth for the drawer behavior.
// Exposes:
//   - window.openCategoryManager(ctx)
//   - window.dashManage(e, el)
//   - window.openDrawerForCategory(catOrCtx, opts)  // alias/convenience
// and tags itself with __cl_v2 = true so inline fallbacks disable themselves.

(function () {
  if (window.openCategoryManager && window.openCategoryManager.__cl_v2 === true) {
    // Already initialized (another page load or duplicate include) — bail.
    return;
  }

  const urls = (window.CL_URLS || {});
  const QS = s => document.querySelector(s);
  const QSA = s => Array.from(document.querySelectorAll(s));

  // Offcanvas bootstrapper (tolerates late insertion of the DOM node)
  let ocEl = document.getElementById('dashCategoryManager');
  let offcanvas = null;
  function ensureOC() {
    if (!ocEl) ocEl = document.getElementById('dashCategoryManager');
    if (!offcanvas && ocEl && window.bootstrap && window.bootstrap.Offcanvas) {
      offcanvas = new bootstrap.Offcanvas(ocEl);
    }
    return offcanvas;
  }

  // lightweight state
  const state = {
    ctx: { level: 'category', cat: '', sub: '', ssub: '', sss: '', month: '' },
    months: [],
    tx: [],
    children: [],
    total: 0,
    magnitude_total: 0
  };

  // --- UI helpers ---
  function $(id) { return document.getElementById(id); }
  function setText(id, v) { const el = $(id); if (el) el.textContent = v == null ? '' : String(v); }
  function fmtUSD(n) { return new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(n || 0); }
  function fmtDate(s) { return s || ''; }

  function monthKeyFromDateStr(s) {
    if (!s) return '0000-00';
    const t = String(s);
    if (t.includes('-')) {
      // YYYY-MM-DD or YYYY-MM
      return t.slice(0, 7);
    }
    // MM/DD/YYYY
    const [mm, , yyyy] = t.split('/');
    if (yyyy && mm) return `${yyyy}-${String(mm).padStart(2,'0')}`;
    return '0000-00';
  }
  function monthLabelFromKey(k) {
    const [y, m] = (k || '0000-00').split('-');
    const dt = new Date(Number(y), Number(m)-1, 1);
    return dt.toLocaleString(undefined, { month:'short', year:'numeric' }); // e.g., "Aug 2025"
  }

  function currentPathParts() {
    return [state.ctx.cat, state.ctx.sub, state.ctx.ssub, state.ctx.sss].filter(Boolean);
  }
  function currentPathPayload() {
    const parts = currentPathParts();
    const last = parts[parts.length-1] || '';
    return {
      level: state.ctx.level || 'category',
      cat: state.ctx.cat || '',
      sub: state.ctx.sub || '',
      ssub: state.ctx.ssub || '',
      sss: state.ctx.sss || '',
      path: parts.join(' / '),
      name: last
    };
  }

  // --- Breadcrumb render + navigate ---
  function renderBreadcrumb() {
    const bc = $('drawer-breadcrumb');
    if (!bc) return;
    const parts = currentPathParts();
    bc.innerHTML = '';
    if (!parts.length) {
      bc.textContent = '(All Categories)';
      return;
    }
    parts.forEach((p, i) => {
      if (i) {
        const sep = document.createElement('span');
        sep.className = 'sep';
        sep.textContent = '/';
        bc.appendChild(sep);
      }
      if (i === parts.length - 1) {
        const cur = document.createElement('span');
        cur.className = 'current';
        cur.textContent = p;
        bc.appendChild(cur);
      } else {
        const a = document.createElement('a');
        a.href = '#';
        a.dataset.bcIndex = String(i);
        a.textContent = p;
        bc.appendChild(a);
      }
    });
  }

  // click handler for breadcrumb links
  document.addEventListener('click', (e) => {
    const link = e.target.closest('a[data-bc-index]');
    if (!link) return;
    e.preventDefault();
    const idx = parseInt(link.getAttribute('data-bc-index'), 10);
    const parts = currentPathParts().slice(0, idx + 1);

    // assign parts back into ctx and set correct level
    state.ctx.cat  = parts[0] || '';
    state.ctx.sub  = parts[1] || '';
    state.ctx.ssub = parts[2] || '';
    state.ctx.sss  = parts[3] || '';
    state.ctx.level = ['category','subcategory','subsubcategory','subsubsubcategory'][idx] || 'category';

    fetchPathTx(state.ctx).catch(()=>{});
    refreshKeywords();
  });

  function renderPath() {
    const p = ['cat','sub','ssub','sss'].map(k=>state.ctx[k]).filter(Boolean);
    setText('drawer-selected-path', p.length ? p.join(' / ') : '(All Categories)');
    setText('drawer-month', state.ctx.month || (state.months[state.months.length-1] || ''));
    const net = Number(state.total || 0);
    const netStr = `${fmtUSD(net)} net`;
    setText('drawer-total', netStr);

    // children
    const host = $('drawer-children');
    if (host) {
      host.innerHTML = state.children.length
        ? state.children.map(n=>`<span class="child-pill" data-child="${n}">${n}</span>`).join('')
        : `<span class="text-muted">No children.</span>`;
    }

    // keywords header
    const kwHdr = $('kw-current-level');
    if (kwHdr) kwHdr.textContent = (p.length ? p.join(' / ') : '(All Categories)');

    // render clickable breadcrumb
    renderBreadcrumb();
  }

  function renderTx() {
    const body = $('drawer-tx-body');
    if (!body) return;
    const rows = state.tx || [];

    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="4" class="text-muted">No transactions.</td></tr>`;
      return;
    }

    // Group by month key and compute NET only (signed sum)
    const groups = new Map(); // key -> {label, items:[], net}
    for (const t of rows) {
      const key = monthKeyFromDateStr(t.date);
      if (!groups.has(key)) groups.set(key, { label: monthLabelFromKey(key), items: [], net: 0 });
      const g = groups.get(key);
      const amt = Number(t.amount || 0);
      g.items.push(t);
      g.net += amt;
    }

    // Sort groups by month desc
    const keys = Array.from(groups.keys()).sort().reverse();

    const parts = [];
    for (const k of keys) {
      const g = groups.get(k);
      const net = Number(g.net || 0);
      const netCls = net < 0 ? 'text-neg' : 'text-pos';
      // Month divider row with NET only
      parts.push(`
        <tr class="month-divider">
          <td colspan="4">
            ${g.label} — <span class="${netCls}">Net: ${fmtUSD(net)}</span>
          </td>
        </tr>
      `);

      // The month's rows (show absolute value with color by sign, like your old table)
      for (const t of g.items) {
        const cls = (parseFloat(t.amount||0) < 0) ? 'text-neg' : 'text-pos';
        parts.push(`
          <tr>
            <td class="text-nowrap">${fmtDate(t.date)}</td>
            <td>${(t.description||'').replace(/</g,'&lt;')}</td>
            <td class="text-end ${cls}">${fmtUSD(Math.abs(t.amount||0))}</td>
            <td>${(t.category||'') + (t.subcategory?(' / '+t.subcategory):'')}</td>
          </tr>
        `);
      }
    }

    body.innerHTML = parts.join('');
  }

  // --- API calls ---
  async function fetchPathTx(ctx) {
    const params = new URLSearchParams();
    params.set('level', ctx.level || 'category');
    if (ctx.cat)  params.set('cat',  ctx.cat);
    if (ctx.sub)  params.set('sub',  ctx.sub);
    if (ctx.ssub) params.set('ssub', ctx.ssub);
    if (ctx.sss)  params.set('sss',  ctx.sss);
    if (ctx.month) params.set('month', ctx.month);
    params.set('months', '12');

    const res = await fetch(`/api/path/transactions?${params.toString()}`, { headers: { 'Accept': 'application/json' } });
    const raw = await res.json();
    const j = raw && typeof raw === 'object' && 'data' in raw ? (raw.data || {}) : raw;

    state.months = j.months || [];
    state.tx = j.transactions || [];
    state.children = j.children || [];
    state.total = j.total || 0;
    state.magnitude_total = j.magnitude_total || 0;

    // If no explicit month selected, default to server focus month
    state.ctx.month = ctx.month || j.month || '';

    renderPath();
    renderTx();
    // Populate months dropdown
    const sel = $('drawer-months');
    if (sel) {
      sel.innerHTML = (state.months || []).map(m => `<option value="${m}" ${m===state.ctx.month?'selected':''}>${m}</option>`).join('');
    }
  }

  async function fetchKeywords() {
    if (!urls.KW_GET_URL) return { keywords: [] };
    const qp = new URLSearchParams(currentPathPayload());
    const res = await fetch(`${urls.KW_GET_URL}?${qp.toString()}`, { headers:{'Accept':'application/json'} });
    try { return await res.json(); } catch { return { keywords: [] }; }
  }

  async function refreshKeywords() {
    const host = $('kw-list');
    if (!host) return;
    host.innerHTML = '<span class="text-muted">Loading…</span>';
    try {
      const data = await fetchKeywords();
      const arr = (data && (data.keywords || data.kw || data.items)) || [];
      host.innerHTML = arr.length ? arr.map(k => `
        <span class="kw-chip" data-kw="${k}">
          <span>${k}</span>
          <span class="x" title="Remove" data-action="kw-remove" data-kw="${k}">&times;</span>
        </span>`).join('') : `<span class="text-muted">No keywords yet.</span>`;
    } catch (e) {
      host.innerHTML = `<span class="text-danger">Failed to load keywords: ${e.message || e}</span>`;
    }
  }

  async function addKeyword(kw) {
    if (!urls.KW_ADD_URL || !kw) return;
    const payload = { ...currentPathPayload(), keyword: kw };
    const res = await fetch(urls.KW_ADD_URL, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    return res.json();
  }

  async function removeKeyword(kw) {
    if (!urls.KW_REMOVE_URL || !kw) return;
    const payload = { ...currentPathPayload(), keyword: kw, remove: true };
    const res = await fetch(urls.KW_REMOVE_URL, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    return res.json();
  }

  // --- Public open function ---
  function openCategoryManager(ctx) {
    ensureOC();
    if (offcanvas) offcanvas.show();

    // Normalize ctx
    state.ctx = {
      level: (ctx && ctx.level) || 'category',
      cat:   (ctx && ctx.cat)   || '',
      sub:   (ctx && ctx.sub)   || '',
      ssub:  (ctx && ctx.ssub)  || '',
      sss:   (ctx && ctx.sss)   || '',
      month: (ctx && ctx.month) || ''
    };

    fetchPathTx(state.ctx).catch(err => {
      console.error('drawer fetchPathTx failed:', err);
    });

    // refresh keywords for the current selection
    refreshKeywords();
  }
  openCategoryManager.__cl_v2 = true; // <— important flag to defeat inline bootstrap

  // Export
  window.openCategoryManager = openCategoryManager;
  // Convenience alias used in some templates
  window.openDrawerForCategory = function (catOrCtx, opts = {}) {
    if (typeof catOrCtx === 'string') {
      openCategoryManager({ level: 'category', cat: catOrCtx });
    } else {
      openCategoryManager(catOrCtx || {});
    }
  };
  // Also expose a tiny namespace for other scripts to call if needed
  window.DRAWER = window.DRAWER || {};
  window.DRAWER.open = openCategoryManager;

  // Unified click handler used by layout.html's global [data-manage] listener
  window.dashManage = function (e, el) {
    if (e && typeof e.preventDefault === 'function') e.preventDefault();
    const ctx = {
      level: el.getAttribute('data-level') || 'category',
      cat:   el.getAttribute('data-cat')   || '',
      sub:   el.getAttribute('data-sub')   || '',
      ssub:  el.getAttribute('data-ssub')  || '',
      sss:   el.getAttribute('data-sss')   || '',
      month: el.getAttribute('data-month') || ''
    };
    openCategoryManager(ctx);
    return false;
  };

  // --- Wire UI events inside the drawer ---
  // Tabs: when switching to "keywords", refresh so you always see the current list
  QSA('.drawer-tab').forEach(tab=>{
    tab.addEventListener('click', (e)=>{
      e.preventDefault();
      const target = tab.getAttribute('data-tab');
      QSA('.drawer-tab').forEach(t=>t.classList.remove('active'));
      QSA('.drawer-pane').forEach(p=>p.style.display='none');
      tab.classList.add('active');
      const pane = QS(`.drawer-pane[data-pane="${target}"]`);
      if (pane) pane.style.display = 'block';
      if (target === 'keywords') refreshKeywords();
    });
  });

  // Month switch
  const monthSel = $('drawer-months');
  if (monthSel) {
    monthSel.addEventListener('change', (e)=>{
      state.ctx.month = e.target.value || '';
      fetchPathTx(state.ctx).catch(()=>{});
      // keep keywords pane fresh if it's visible
      const active = QS('.drawer-tab.active');
      if (active && active.getAttribute('data-tab') === 'keywords') refreshKeywords();
    });
  }

  // Click a child-pill to drill in one level
  document.addEventListener('click', (e)=>{
    const pill = e.target.closest('.child-pill');
    if (!pill) return;
    const name = pill.getAttribute('data-child') || '';
    // Determine next level slot to fill
    if (state.ctx.sss) {
      // already deepest; do nothing
      return;
    } else if (state.ctx.ssub) {
      state.ctx.sss = name;
      state.ctx.level = 'subsubsubcategory';
    } else if (state.ctx.sub) {
      state.ctx.ssub = name;
      state.ctx.level = 'subsubcategory';
    } else if (state.ctx.cat) {
      state.ctx.sub = name;
      state.ctx.level = 'subcategory';
    } else {
      state.ctx.cat = name;
      state.ctx.level = 'category';
    }
    fetchPathTx(state.ctx).catch(()=>{});
    refreshKeywords();
  });

  // Keyword add/remove
  const kwInput = $('kw-add-input');
  const kwAddBtn = $('kw-add-btn');

  if (kwAddBtn) {
    kwAddBtn.addEventListener('click', async ()=>{
      const kw = (kwInput && kwInput.value || '').trim();
      if (!kw) return;
      await addKeyword(kw);
      if (kwInput) kwInput.value = '';
      refreshKeywords();
    });
  }
  if (kwInput) {
    kwInput.addEventListener('keydown', async (e)=>{
      if (e.key === 'Enter') {
        const kw = (kwInput.value || '').trim();
        if (!kw) return;
        await addKeyword(kw);
        kwInput.value = '';
        refreshKeywords();
      }
    });
  }
  document.addEventListener('click', async (e)=>{
    const x = e.target.closest('[data-action="kw-remove"]');
    if (!x) return;
    const kw = x.getAttribute('data-kw');
    await removeKeyword(kw);
    refreshKeywords();
  });

  // Inspect / Rename / Upsert buttons (optional APIs)
  const btnInspect = $('drawer-inspect');
  const btnRename  = $('drawer-rename');
  const btnUpsert  = $('drawer-upsert');

  if (btnInspect && urls.INSPECT_URL) {
    btnInspect.addEventListener('click', async ()=>{
      const res = await fetch(urls.INSPECT_URL + '?' + new URLSearchParams({ path: currentPathPayload().path }), { headers:{'Accept':'application/json'}});
      const j = await res.json();
      alert(JSON.stringify(j, null, 2));
    });
  }

  if (btnRename && urls.RENAME_URL) {
    btnRename.addEventListener('click', async ()=>{
      const p = currentPathPayload();
      if (!p.path) return;
      const oldName = p.name;
      const newName = prompt(`Rename "${oldName}" to:`, oldName);
      if (!newName || newName.trim() === oldName) return;
      const payload = { path: p.path, new_name: newName.trim() };
      await fetch(urls.RENAME_URL, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
      fetchPathTx(state.ctx).catch(()=>{});
      alert('Rename attempted. If it didn’t take, the server may have rejected it.');
    });
  }

  if (btnUpsert && urls.UPSERT_URL) {
    btnUpsert.addEventListener('click', async ()=>{
      const p = currentPathPayload();
      const name = prompt('Enter a child name to create/attach under this path:');
      if (!name || !p.path) return;
      const payload = { parent_path: p.path, name: name.trim() };
      await fetch(urls.UPSERT_URL, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
      fetchPathTx(state.ctx).catch(()=>{});
      alert('Create/attach attempted.');
    });
  }

})();
