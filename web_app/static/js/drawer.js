// static/js/drawer.js
(function () {
  'use strict';

  // Avoid double-loading
  if (window.openCategoryManager && window.openCategoryManager.__cl_v2 === true) return;

  const urls = (window.CL_URLS || {});
  const PATH_TXN_URL = urls.PATH_TXN_URL || '/api/path/transactions';

  const QS  = s => document.querySelector(s);
  const QSA = s => Array.from(document.querySelectorAll(s));
  const ocEl = document.getElementById('dashCategoryManager');

  let offcanvas = null;
  function ensureOC() {
    if (!offcanvas && window.bootstrap && window.bootstrap.Offcanvas && ocEl) {
      offcanvas = new bootstrap.Offcanvas(ocEl);
    }
    return offcanvas;
  }

  // Inject drawer-specific styles (child pills etc.)
  function ensureDrawerStyles() {
    if (document.getElementById('drawer-extra-styles')) return;
    const css = `
#dashCategoryManager #drawer-children{
  display:flex; flex-wrap:wrap; gap:.5rem; align-items:center;
}
#dashCategoryManager .child-pill{
  display:inline-flex; align-items:center; gap:.4rem;
  padding:.38rem .6rem; border-radius:999px;
  border:1px solid var(--bs-border-color, #dee2e6);
  background:var(--bs-body-bg, #fff);
  color:var(--bs-body-color, #212529);
  cursor:pointer; line-height:1; font-size:.875rem;
  transition: background .15s ease, color .15s ease,
              border-color .15s ease, box-shadow .15s ease, transform .05s ease;
}
#dashCategoryManager .child-pill:hover{
  background: var(--bs-primary-bg-subtle, #e7f1ff);
  border-color: var(--bs-primary, #0d6efd);
  color: var(--bs-primary, #0d6efd);
}
#dashCategoryManager .child-pill:active{
  transform: translateY(1px);
}
#dashCategoryManager .child-pill:focus{
  outline:0;
  box-shadow: 0 0 0 .2rem rgba(13,110,253,.25);
}
#dashCategoryManager .child-pill .dot{
  width:6px; height:6px; border-radius:50%;
  background: currentColor; opacity:.65; display:inline-block;
}
#dashCategoryManager .child-pill .chev{
  font-weight:700; opacity:.7; line-height:1; transform: translateY(-1px);
}
    `;
    const style = document.createElement('style');
    style.id = 'drawer-extra-styles';
    style.textContent = css;
    document.head.appendChild(style);
  }
  ensureDrawerStyles();

  const state = {
    ctx: { level: 'category', cat: '', sub: '', ssub: '', sss: '', month: '', allowHidden: false },
    months: [],
    tx: [],
    children: [],
    total: 0,
    magnitude_total: 0,
    showAll: false
  };

  // -------- helpers --------
  function $(id) { return document.getElementById(id); }
  function setText(id, v) { const el = $(id); if (el) el.textContent = v == null ? '' : String(v); }
  function fmtUSD(n) { try { return new Intl.NumberFormat(undefined,{style:'currency',currency:'USD'}).format(n||0); } catch { return '$'+Number(n||0).toFixed(2); } }
  function fmtDate(s) { return s || ''; }
  function escapeHTML(s){
    return String(s||'').replace(/[&<>"']/g, c => (
      c === '&' ? '&amp;' :
      c === '<' ? '&lt;'  :
      c === '>' ? '&gt;'  :
      c === '"' ? '&quot;': '&#39;'
    ));
  }

  function monthKeyFromDateStr(s){
    if (!s) return '0000-00';
    const t = String(s).trim();
    if (t.includes('-')) return t.slice(0,7);
    const parts = t.split('/');
    const mm = parts[0], yyyy = parts[2];
    if (yyyy && mm) return yyyy + '-' + String(mm).padStart(2,'0');
    return '0000-00';
  }
  function monthLabelFromKey(k){
    const parts = (k||'0000-00').split('-');
    const yy = Number(parts[0]||0), mm = Math.max(1, Math.min(12, Number(parts[1]||1)));
    const dt = new Date(yy, mm-1, 1);
    return dt.toLocaleString(undefined, {month:'short', year:'numeric'});
  }
  function monthId(k){ return 'm-' + String(k||'').replace(/[^0-9-]/g,''); }

  // Find nearest available month (<= preferred, else next greater)
  function nearestMonth(preferred, available) {
    if (!available || !available.length) return '';
    const set = new Set(available);
    if (preferred && set.has(preferred)) return preferred;

    const earlier = available.filter(m => m <= preferred).sort().reverse();
    if (earlier.length) return earlier[0];

    const later = available.filter(m => m > preferred).sort();
    return later[0] || available[available.length - 1];
  }

  function currentPathParts(){ return [state.ctx.cat, state.ctx.sub, state.ctx.ssub, state.ctx.sss].filter(Boolean); }
  function currentPathPayload(){
    const parts = currentPathParts();
    const last = parts[parts.length-1] || '';
    return {
      level: state.ctx.level || 'category',
      cat: state.ctx.cat || '',
      sub: state.ctx.sub || '',
      ssub: state.ctx.ssub || '',
      sss: state.ctx.sss || '',
      path: parts.join(' / '),
      name: last,
      allow_hidden: state.ctx.allowHidden ? 1 : 0
    };
  }

  function renderBreadcrumb(){
    const bc = $('drawer-breadcrumb');
    if (!bc) return;
    const parts = currentPathParts();
    bc.innerHTML = '';
    if (!parts.length) { bc.textContent = '(All Categories)'; return; }
    parts.forEach((p,i)=>{
      if (i){
        const sep = document.createElement('span');
        sep.className = 'sep';
        sep.textContent = '/';
        bc.appendChild(sep);
      }
      if (i === parts.length-1){
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

  document.addEventListener('click', (e)=>{
    const link = e.target.closest('a[data-bc-index]');
    if (!link) return;
    e.preventDefault();
    const idx = parseInt(link.getAttribute('data-bc-index'), 10);
    const parts = currentPathParts().slice(0, idx+1);

    state.ctx.cat  = parts[0] || '';
    state.ctx.sub  = parts[1] || '';
    state.ctx.ssub = parts[2] || '';
    state.ctx.sss  = parts[3] || '';
    state.ctx.level = ['category','subcategory','subsubcategory','subsubsubcategory'][idx] || 'category';

    fetchPathTx(state.ctx).catch(()=>{});
    refreshKeywords();
  });

  function renderPath(){
    const p = ['cat','sub','ssub','sss'].map(k=>state.ctx[k]).filter(Boolean);
    setText('drawer-selected-path', p.length ? p.join(' / ') : '(All Categories)');

    const latest = state.months[state.months.length-1] || '';
    const monthDisplay = state.showAll ? 'All months' : (state.ctx.month || latest);
    setText('drawer-month', monthDisplay);

    const net = Number(state.total || 0);
    setText('drawer-total', fmtUSD(net) + ' net');

    const host = $('drawer-children');
    if (host){
      if (state.children.length) {
        host.innerHTML = state.children.map(function(n){
          const label = escapeHTML(n);
          // Store raw value safely via URL encoding; decode when reading.
          const rawAttr = encodeURIComponent(String(n||''));
          return '' +
            '<button type="button" class="child-pill" data-child="'+rawAttr+'" title="Drill into '+label+'" aria-label="Drill into '+label+'">' +
            '  <span class="dot" aria-hidden="true"></span>' +
            '  <span class="label">'+label+'</span>' +
            '  <span class="chev" aria-hidden="true">›</span>' +
            '</button>';
        }).join('');
      } else {
        host.innerHTML = '<span class="text-muted">No children.</span>';
      }
    }

    const kwHdr = $('kw-current-level');
    if (kwHdr) kwHdr.textContent = (p.length ? p.join(' / ') : '(All Categories)');

    renderBreadcrumb();
  }

  function renderTx(){
    const body = $('drawer-tx-body');
    if (!body) return;
    const rows = state.tx || [];

    if (!rows.length){
      body.innerHTML = '<tr><td colspan="4" class="text-muted">No transactions.</td></tr>';
      return;
    }

    const groups = new Map();
    for (const t of rows){
      const key = monthKeyFromDateStr(t.date);
      if (!groups.has(key)) groups.set(key, { label: monthLabelFromKey(key), items: [], net: 0 });
      const g = groups.get(key);
      const amt = Number(t.amount || 0);
      g.items.push(t);
      g.net += amt;
    }

    const keys = Array.from(groups.keys()).sort().reverse();

    const parts = [];
    for (const k of keys){
      const g = groups.get(k);
      const net = Number(g.net || 0);
      const netCls = net < 0 ? 'tx-neg' : 'tx-pos';
      parts.push(
        '<tr class="month-divider" id="'+escapeHTML(monthId(k))+'">\n' +
        '  <td colspan="4">' + escapeHTML(g.label) + ' — ' +
        '    <span class="' + netCls + '">Net: ' + fmtUSD(net) + '</span>' +
        '  </td>\n' +
        '</tr>'
      );
      for (const t of g.items){
        const cls = (parseFloat(t.amount||0) < 0) ? 'tx-neg' : 'tx-pos';
        parts.push(
          '<tr>\n' +
          '  <td class="text-nowrap">' + escapeHTML(fmtDate(t.date)) + '</td>\n' +
          '  <td>' + escapeHTML(t.description || '') + '</td>\n' +
          '  <td class="text-end ' + cls + '">' + fmtUSD(Math.abs(t.amount||0)) + '</td>\n' +
          '  <td>' + escapeHTML((t.category||"") + (t.subcategory?(" / "+t.subcategory):"")) + '</td>\n' +
          '  </tr>'
        );
      }
    }

    body.innerHTML = parts.join('');
  }

  function scrollHost(){
    return QS('#dashCategoryManager .table-responsive');
  }
  function scrollToMonth(key, smooth=true){
    if (!key) return;
    if (String(key).toLowerCase() === 'all') return; // nothing to scroll to
    const host = scrollHost();
    const row = document.getElementById(monthId(key));
    if (!host || !row) return;
    const hostTop = host.getBoundingClientRect().top;
    const rowTop  = row.getBoundingClientRect().top;
    const delta   = (rowTop - hostTop) - 8; // small padding
    host.scrollTo({ top: host.scrollTop + delta, behavior: smooth ? 'smooth' : 'auto' });
  }
  function scrollToPreferredMonth(preferredKey, smooth) {
    if (!preferredKey || String(preferredKey).toLowerCase() === 'all') return;
    const key = nearestMonth(preferredKey, state.months);
    if (key && key !== state.ctx.month) {
      state.ctx.month = key;
      const sel = $('drawer-months');
      if (sel) sel.value = key;
      setText('drawer-month', key);
    }
    if (state.ctx.month) scrollToMonth(state.ctx.month, smooth);
  }

  async function fetchPathTx(ctx){
    const body = $('drawer-tx-body');
    if (body) body.innerHTML = '<tr><td colspan="4" class="text-muted">Loading…</td></tr>';

    const params = new URLSearchParams();
    params.set('level', ctx.level || 'category');
    if (ctx.cat)  params.set('cat',  ctx.cat);
    if (ctx.sub)  params.set('sub',  ctx.sub);
    if (ctx.ssub) params.set('ssub', ctx.ssub);
    if (ctx.sss)  params.set('sss',  ctx.sss);
    if (ctx.allowHidden) params.set('allow_hidden', '1');

    // month param: support specific month *or* "all"
    const monthLower = String(ctx.month || '').toLowerCase();
    if (monthLower === 'all') {
      params.set('month', 'all');
    } else if (ctx.month) {
      params.set('month', ctx.month);
    }

    // full history window on drawer
    params.set('months', 'all');
    params.set('_', Date.now().toString());

    const url = PATH_TXN_URL + '?' + params.toString();
    const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
    if (!res.ok){
      console.error('drawer fetchPathTx failed:', res.status, await res.text());
      if (body) body.innerHTML = '<tr><td colspan="4" class="text-danger">Failed to load.</td></tr>';
      return;
    }
    const j = await res.json();

    state.months = j.months || [];
    state.tx = j.transactions || [];
    state.children = j.children || [];
    state.total = j.total || 0;
    state.magnitude_total = j.magnitude_total || 0;

    // Detect "all months" mode from server or caller
    const serverMonth = String(j.month || '').toLowerCase();
    state.showAll = (serverMonth === 'all') || (monthLower === 'all');

    // Determine selected month for state
    if (!state.showAll) {
      const preferred = ctx.month || j.month || (state.months[state.months.length-1] || '');
      state.ctx.month = nearestMonth(preferred, state.months);
    } else {
      state.ctx.month = 'all';
    }

    renderPath();
    renderTx();

    // Build month <select> with "All months" at top, then months in DESC order (e.g., 2025-08, 2025-07, ...)
    const sel = $('drawer-months');
    if (sel){
      const opts = [];
      opts.push('<option value="all"' + (state.showAll ? ' selected' : '') + '>All months</option>');
      const monthsDesc = (state.months || []).slice().sort().reverse();
      monthsDesc.forEach(function(m){
        const selAttr = (!state.showAll && m === state.ctx.month) ? ' selected' : '';
        const label = m; // or: monthLabelFromKey(m)
        opts.push('<option value="' + escapeHTML(m) + '"' + selAttr + '>' + escapeHTML(label) + '</option>');
      });
      sel.innerHTML = opts.join('');
    }

    // Jump to the preferred/nearest month (no re-fetch, just scroll) when a specific month is selected
    setTimeout(function(){
      if (!state.showAll && state.ctx.month) {
        scrollToPreferredMonth(state.ctx.month, false);
      }
    }, 0);
  }

  async function fetchKeywords(){
    if (!urls.KW_GET_URL) return { keywords: [] };
    const qp = new URLSearchParams(currentPathPayload());
    qp.set('_', Date.now().toString());
    const res = await fetch(urls.KW_GET_URL + '?' + qp.toString(), { headers:{'Accept':'application/json'} });
    try { return await res.json(); } catch { return { keywords: [] }; }
  }

  async function refreshKeywords(){
    const host = $('kw-list');
    if (!host) return;
    host.innerHTML = '<span class="text-muted">Loading…</span>';
    try {
      const data = await fetchKeywords();
      const arr = (data && (data.keywords || data.kw || data.items)) || [];
      host.innerHTML = arr.length ? arr.map(function(k){
        const raw = encodeURIComponent(String(k||''));
        return '<span class="kw-chip" data-kw="'+raw+'">' +
               '  <span>'+escapeHTML(k)+'</span>' +
               '  <span class="x" title="Remove" data-action="kw-remove" data-kw="'+raw+'">&times;</span>' +
               '</span>';
      }).join('') : '<span class="text-muted">No keywords yet.</span>';
    } catch (e) {
      host.innerHTML = '<span class="text-danger">Failed to load keywords: '+escapeHTML(e.message || String(e))+'</span>';
    }
  }

  async function addKeyword(kw){
    if (!urls.KW_ADD_URL || !kw) return;
    const payload = Object.assign({}, currentPathPayload(), { keyword: kw });
    await fetch(urls.KW_ADD_URL, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
  }
  async function removeKeyword(kw){
    if (!urls.KW_REMOVE_URL || !kw) return;
    const payload = Object.assign({}, currentPathPayload(), { keyword: kw, remove: true });
    await fetch(urls.KW_REMOVE_URL, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
  }

  function openCategoryManager(ctx){
    ensureOC();
    if (offcanvas) offcanvas.show();

    state.ctx = {
      level: (ctx && ctx.level) || 'category',
      cat:   (ctx && ctx.cat)   || '',
      sub:   (ctx && ctx.sub)   || '',
      ssub:  (ctx && ctx.ssub)  || '',
      sss:   (ctx && ctx.sss)   || '',
      month: (ctx && ctx.month) || '',
      allowHidden: !!(ctx && ctx.allowHidden)
    };
    state.showAll = (String(state.ctx.month || '').toLowerCase() === 'all');

    fetchPathTx(state.ctx).catch(function(err){ console.error('drawer fetchPathTx failed:', err); });
    refreshKeywords();
  }
  openCategoryManager.__cl_v2 = true;

  // Public exports (back-compat + new aliases used by pages)
  window.openCategoryManager = openCategoryManager;
  window.DRAWER = window.DRAWER || {};
  window.DRAWER.open = openCategoryManager;

  // New lightweight aliases so pages can just call openDrawerForPath / openDrawerForCategory
  if (!window.openDrawerForPath) {
    window.openDrawerForPath = function (state) { openCategoryManager(state || {}); };
  }
  if (!window.openDrawerForCategory) {
    window.openDrawerForCategory = function (cat, opts) {
      openCategoryManager({ level: 'category', cat: cat || '', allowHidden: !!(opts && opts.allowHidden) });
    };
  }

  // Handle links created in other templates
  window.dashManage = function (e, el) {
    if (e && typeof e.preventDefault === 'function') e.preventDefault();
    const ctx = {
      level: el.getAttribute('data-level') || 'category',
      cat:   el.getAttribute('data-cat')   || '',
      sub:   el.getAttribute('data-sub')   || '',
      ssub:  el.getAttribute('data-ssub')  || '',
      sss:   el.getAttribute('data-sss')   || '',
      month: el.getAttribute('data-month') || '',
      allowHidden: !!(el.getAttribute('data-allow-hidden') || '')
    };
    openCategoryManager(ctx);
    return false;
  };

  // Tabs
  QSA('.drawer-tab').forEach(function(tab){
    tab.addEventListener('click', function(e){
      e.preventDefault();
      const target = tab.getAttribute('data-tab');
      QSA('.drawer-tab').forEach(function(t){ t.classList.remove('active'); });
      QSA('.drawer-pane').forEach(function(p){ p.style.display='none'; });
      tab.classList.add('active');
      const pane = QS('.drawer-pane[data-pane="'+target+'"]');
      if (pane) pane.style.display = 'block';
      if (target === 'keywords') refreshKeywords();
    });
  });

  // Month selector: re-fetch the drawer for the selected month (supports "All months")
  const monthSel = $('drawer-months');
  if (monthSel){
    monthSel.addEventListener('change', async function(e){
      const val = (e.target.value || '').toLowerCase();
      state.ctx.month = val || '';
      state.showAll = (val === 'all');

      await fetchPathTx(state.ctx);

      // Only scroll when a specific month is chosen
      if (!state.showAll && state.ctx.month) {
        scrollToPreferredMonth(state.ctx.month, true);
      }

      // If Keywords tab is active, refresh it
      const active = QS('.drawer-tab.active');
      if (active && active.getAttribute('data-tab') === 'keywords') refreshKeywords();
    });
  }

  // Drill deeper by clicking a child pill
  document.addEventListener('click', function(e){
    const pill = e.target.closest('.child-pill');
    if (!pill) return;
    const name = decodeURIComponent(pill.getAttribute('data-child') || '');
    if (state.ctx.sss) {
      return;
    } else if (state.ctx.ssub) {
      state.ctx.sss = name; state.ctx.level = 'subsubsubcategory';
    } else if (state.ctx.sub) {
      state.ctx.ssub = name; state.ctx.level = 'subsubcategory';
    } else if (state.ctx.cat) {
      state.ctx.sub = name; state.ctx.level = 'subcategory';
    } else {
      state.ctx.cat = name; state.ctx.level = 'category';
    }
    fetchPathTx(state.ctx).catch(function(){});
    refreshKeywords();
  });

  // Keyword inputs
  const kwInput = $('kw-add-input');
  const kwAddBtn = $('kw-add-btn');

  if (kwAddBtn){
    kwAddBtn.addEventListener('click', async function(){
      const kw = (kwInput && kwInput.value || '').trim();
      if (!kw) return;
      await addKeyword(kw);
      if (kwInput) kwInput.value = '';
      refreshKeywords();
    });
  }
  if (kwInput){
    kwInput.addEventListener('keydown', async function(e){
      if (e.key === 'Enter'){
        const kw = (kwInput.value || '').trim();
        if (!kw) return;
        await addKeyword(kw);
        kwInput.value = '';
        refreshKeywords();
      }
    });
  }
  document.addEventListener('click', async function(e){
    const x = e.target.closest('[data-action="kw-remove"]');
    if (!x) return;
    const kw = decodeURIComponent(x.getAttribute('data-kw') || '');
    await removeKeyword(kw);
    refreshKeywords();
  });

  // Optional admin buttons
  const btnInspect = $('drawer-inspect');
  const btnRename  = $('drawer-rename');
  const btnUpsert  = $('drawer-upsert');

  if (btnInspect && urls.INSPECT_URL){
    btnInspect.addEventListener('click', async function(){
      const res = await fetch(urls.INSPECT_URL + '?' + new URLSearchParams({ path: currentPathPayload().path, allow_hidden: state.ctx.allowHidden ? 1 : 0 }), { headers:{'Accept':'application/json'} });
      const j = await res.json();
      alert(JSON.stringify(j, null, 2));
    });
  }
  if (btnRename && urls.RENAME_URL){
    btnRename.addEventListener('click', async function(){
      const p = currentPathPayload();
      if (!p.path) return;
      const oldName = p.name;
      const newName = prompt('Rename "'+oldName+'" to:', oldName);
      if (!newName || newName.trim() === oldName) return;
      const payload = { path: p.path, new_name: newName.trim(), allow_hidden: state.ctx.allowHidden ? 1 : 0 };
      await fetch(urls.RENAME_URL, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
      fetchPathTx(state.ctx).catch(function(){});
      alert('Rename attempted. If it didn’t take, the server may have rejected it.');
    });
  }
  if (btnUpsert && urls.UPSERT_URL){
    btnUpsert.addEventListener('click', async function(){
      const p = currentPathPayload();
      const payload = { path: p.path, allow_hidden: state.ctx.allowHidden ? 1 : 0 };
      await fetch(urls.UPSERT_URL, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
      alert('Upsert attempted.');
    });
  }

})();
