
// static/js/drawer.js — single source of truth for the drawer behavior.
// Exposes:
//   - window.openCategoryManager(ctx)
//   - window.dashManage(e, el)
// and tags itself with __cl_v2 = true so inline fallbacks disable themselves.

(function () {
  'use strict';

  if (window.openCategoryManager && window.openCategoryManager.__cl_v2 === true) {
    return;
  }

  const urls = (window.CL_URLS || {});
  const PATH_TXN_URL = urls.PATH_TXN_URL || '/api/path/transactions';

  const QS = s => document.querySelector(s);
  const QSA = s => Array.from(document.querySelectorAll(s));
  const ocEl = document.getElementById('dashCategoryManager');

  let offcanvas = null;
  function ensureOC() {
    if (!offcanvas && window.bootstrap && window.bootstrap.Offcanvas) {
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
  function fmtUSD(n) { try { return new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(n || 0); } catch { return '$' + Number(n||0).toFixed(2); } }
  function fmtDate(s) { return s || ''; }
  function escapeHTML(s) { return String(s || '').replace(/[&<>"']/g, function(c){ return ({ "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;" })[c] || c; }); }

  function monthKeyFromDateStr(s) {
    if (!s) return '0000-00';
    const t = String(s).trim();
    if (t.includes('-')) {
      return t.slice(0, 7);
    }
    const parts = t.split('/');
    const mm = parts[0], yyyy = parts[2];
    if (yyyy && mm) return yyyy + '-' + String(mm).padStart(2,'0');
    return '0000-00';
  }
  function monthLabelFromKey(k) {
    const parts = (k || '0000-00').split('-');
    const yy = Number(parts[0] || 0), mm = Math.max(1, Math.min(12, Number(parts[1]||1)));
    const dt = new Date(yy, mm-1, 1);
    return dt.toLocaleString(undefined, { month:'short', year:'numeric' });
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

  document.addEventListener('click', (e) => {
    const link = e.target.closest('a[data-bc-index]');
    if (!link) return;
    e.preventDefault();
    const idx = parseInt(link.getAttribute('data-bc-index'), 10);
    const parts = currentPathParts().slice(0, idx + 1);

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
    setText('drawer-month', state.ctx.month || (state.months[ state.months.length-1 ] || ''));
    const net = Number(state.total || 0);
    const netStr = fmtUSD(net) + ' net';
    setText('drawer-total', netStr);

    const host = $('drawer-children');
    if (host) {
      if (state.children.length) {
        host.innerHTML = state.children.map(n => (
          '<span class="child-pill" data-child="' + escapeHTML(n) + '">' + escapeHTML(n) + '</span>'
        )).join('');
      } else {
        host.innerHTML = '<span class="text-muted">No children.</span>';
      }
    }

    const kwHdr = $('kw-current-level');
    if (kwHdr) kwHdr.textContent = (p.length ? p.join(' / ') : '(All Categories)');

    renderBreadcrumb();
  }

  function renderTx() {
    const body = $('drawer-tx-body');
    if (!body) return;
    const rows = state.tx || [];

    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="4" class="text-muted">No transactions.</td></tr>';
      return;
    }

    const groups = new Map();
    for (const t of rows) {
      const key = monthKeyFromDateStr(t.date);
      if (!groups.has(key)) groups.set(key, { label: monthLabelFromKey(key), items: [], net: 0 });
      const g = groups.get(key);
      const amt = Number(t.amount || 0);
      g.items.push(t);
      g.net += amt;
    }

    const keys = Array.from(groups.keys()).sort().reverse();

    const parts = [];
    for (const k of keys) {
      const g = groups.get(k);
      const net = Number(g.net || 0);
      const netCls = net < 0 ? 'tx-neg' : 'tx-pos';
      parts.push(
        '<tr class="month-divider">\n' +
        '  <td colspan="4">' +
           escapeHTML(g.label) + ' — <span class="' + netCls + '">Net: ' + fmtUSD(net) + '</span>' +
        '  </td>\n' +
        '</tr>'
      );
      for (const t of g.items) {
        const cls = (parseFloat(t.amount||0) < 0) ? 'tx-neg' : 'tx-pos';
        parts.push(
          '<tr>\n' +
          '  <td class="text-nowrap">' + escapeHTML(fmtDate(t.date)) + '</td>\n' +
          '  <td>' + escapeHTML(t.description || '') + '</td>\n' +
          '  <td class="text-end ' + cls + '">' + fmtUSD(Math.abs(t.amount||0)) + '</td>\n' +
          '  <td>' + escapeHTML((t.category||"") + (t.subcategory?(" / "+t.subcategory):"")) + '</td>\n' +
          '</tr>'
        );
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
    params.set('_', Date.now().toString());

    const url = PATH_TXN_URL + '?' + params.toString();
    const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
    if (!res.ok) {
      console.error('drawer fetchPathTx failed:', res.status, await res.text());
      return;
    }
    const j = await res.json();

    state.months = j.months || [];
    state.tx = j.transactions || [];
    state.children = j.children || [];
    state.total = j.total || 0;
    state.magnitude_total = j.magnitude_total || 0;
    state.ctx.month = ctx.month || j.month || '';

    renderPath();
    renderTx();

    const sel = $('drawer-months');
    if (sel) {
      sel.innerHTML = (state.months || []).map(function(m){
        return '<option value="' + escapeHTML(m) + '" ' + (m===state.ctx.month?'selected':'') + '>' + escapeHTML(m) + '</option>';
      }).join('');
    }
  }

  async function fetchKeywords() {
    if (!urls.KW_GET_URL) return { keywords: [] };
    const qp = new URLSearchParams(currentPathPayload());
    qp.set('_', Date.now().toString());
    const res = await fetch(urls.KW_GET_URL + '?' + qp.toString(), { headers:{'Accept':'application/json'} });
    try { return await res.json(); } catch { return { keywords: [] }; }
  }

  async function refreshKeywords() {
    const host = $('kw-list');
    if (!host) return;
    host.innerHTML = '<span class="text-muted">Loading…</span>';
    try {
      const data = await fetchKeywords();
      const arr = (data && (data.keywords || data.kw || data.items)) || [];
      host.innerHTML = arr.length ? arr.map(function(k){
        return '<span class="kw-chip" data-kw="' + escapeHTML(k) + '">' +
               '  <span>' + escapeHTML(k) + '</span>' +
               '  <span class="x" title="Remove" data-action="kw-remove" data-kw="' + escapeHTML(k) + '">&times;</span>' +
               '</span>';
      }).join('') : '<span class="text-muted">No keywords yet.</span>';
    } catch (e) {
      host.innerHTML = '<span class="text-danger">Failed to load keywords: ' + escapeHTML(e.message || String(e)) + '</span>';
    }
  }

  async function addKeyword(kw) {
    if (!urls.KW_ADD_URL || !kw) return;
    const payload = Object.assign({}, currentPathPayload(), { keyword: kw });
    await fetch(urls.KW_ADD_URL, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
  }

  async function removeKeyword(kw) {
    if (!urls.KW_REMOVE_URL || !kw) return;
    const payload = Object.assign({}, currentPathPayload(), { keyword: kw, remove: true });
    await fetch(urls.KW_REMOVE_URL, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
  }

  function openCategoryManager(ctx) {
    ensureOC();
    if (offcanvas) offcanvas.show();

    state.ctx = {
      level: (ctx && ctx.level) || 'category',
      cat:   (ctx && ctx.cat)   || '',
      sub:   (ctx && ctx.sub)   || '',
      ssub:  (ctx && ctx.ssub)  || '',
      sss:   (ctx && ctx.sss)   || '',
      month: (ctx && ctx.month) || ''
    };

    fetchPathTx(state.ctx).catch(function(err){ console.error('drawer fetchPathTx failed:', err); });
    refreshKeywords();
  }
  openCategoryManager.__cl_v2 = true;

  window.openCategoryManager = openCategoryManager;
  window.DRAWER = window.DRAWER || {};
  window.DRAWER.open = openCategoryManager;

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

  QSA('.drawer-tab').forEach(function(tab){
    tab.addEventListener('click', function(e){
      e.preventDefault();
      const target = tab.getAttribute('data-tab');
      QSA('.drawer-tab').forEach(function(t){ t.classList.remove('active'); });
      QSA('.drawer-pane').forEach(function(p){ p.style.display='none'; });
      tab.classList.add('active');
      const pane = QS('.drawer-pane[data-pane="' + target + '"]');
      if (pane) pane.style.display = 'block';
      if (target === 'keywords') refreshKeywords();
    });
  });

  const monthSel = $('drawer-months');
  if (monthSel) {
    monthSel.addEventListener('change', function(e){
      state.ctx.month = e.target.value || '';
      fetchPathTx(state.ctx).catch(function(){});
      const active = QS('.drawer-tab.active');
      if (active && active.getAttribute('data-tab') === 'keywords') refreshKeywords();
    });
  }

  document.addEventListener('click', function(e){
    const pill = e.target.closest('.child-pill');
    if (!pill) return;
    const name = pill.getAttribute('data-child') || '';
    if (state.ctx.sss) {
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
    fetchPathTx(state.ctx).catch(function(){});
    refreshKeywords();
  });

  const kwInput = $('kw-add-input');
  const kwAddBtn = $('kw-add-btn');

  if (kwAddBtn) {
    kwAddBtn.addEventListener('click', async function(){
      const kw = (kwInput && kwInput.value || '').trim();
      if (!kw) return;
      await addKeyword(kw);
      if (kwInput) kwInput.value = '';
      refreshKeywords();
    });
  }
  if (kwInput) {
    kwInput.addEventListener('keydown', async function(e){
      if (e.key === 'Enter') {
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
    const kw = x.getAttribute('data-kw');
    await removeKeyword(kw);
    refreshKeywords();
  });

  const btnInspect = $('drawer-inspect');
  const btnRename  = $('drawer-rename');
  const btnUpsert  = $('drawer-upsert');

  if (btnInspect && urls.INSPECT_URL) {
    btnInspect.addEventListener('click', async function(){
      const res = await fetch(urls.INSPECT_URL + '?' + new URLSearchParams({ path: currentPathPayload().path }), { headers:{'Accept':'application/json'}});
      const j = await res.json();
      alert(JSON.stringify(j, null, 2));
    });
  }

  if (btnRename && urls.RENAME_URL) {
    btnRename.addEventListener('click', async function(){
      const p = currentPathPayload();
      if (!p.path) return;
      const oldName = p.name;
      const newName = prompt('Rename "' + oldName + '" to:', oldName);
      if (!newName || newName.trim() === oldName) return;
      const payload = { path: p.path, new_name: newName.trim() };
      await fetch(urls.RENAME_URL, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
      fetchPathTx(state.ctx).catch(function(){});
      alert('Rename attempted. If it didn’t take, the server may have rejected it.');
    });
  }

  if (btnUpsert && urls.UPSERT_URL) {
    btnUpsert.addEventListener('click', async function(){
      const p = currentPathPayload();
      const payload = { path: p.path };
      await fetch(urls.UPSERT_URL, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
      alert('Upsert attempted.');
    });
  }

})();
