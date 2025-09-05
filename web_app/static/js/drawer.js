// static/js/drawer.js — neon-tech aligned drawer (no sticky, emits cm:months for Glass Select)
(function () {
  'use strict';

  if (window.openCategoryManager && window.openCategoryManager.__cl_neon_fit === true) return;

  const urls = (window.CL_URLS || {});
  const PATH_TXN_URL = urls.PATH_TXN_URL || '/api/path/transactions';

  const QS  = s => document.querySelector(s);
  const QSA = s => Array.from(document.querySelectorAll(s));
  const $   = id => document.getElementById(id);
  const ocEl = $('dashCategoryManager');

  let offcanvas = null;
  function ensureOC() {
    const el = $('dashCategoryManager');
    if (!el || !window.bootstrap || !window.bootstrap.Offcanvas) return null;
    if (!offcanvas || !offcanvas._element || offcanvas._element !== el) {
      try { offcanvas = new bootstrap.Offcanvas(el); } catch (_) { offcanvas = null; }
    }
    return offcanvas;
  }

  const state = {
    ctx: { level: 'category', cat: '', sub: '', ssub: '', sss: '', month: '', allowHidden: false },
    months: [],
    tx: [],
    children: [],
    total: 0,
    magnitude_total: 0,
    showAll: false
  };

  // ---------- helpers ----------
  function setText(id, v) { const el = $(id); if (el) el.textContent = v == null ? '' : String(v); }
  function fmtUSD(n) { try { return new Intl.NumberFormat(undefined,{style:'currency',currency:'USD'}).format(+n||0); } catch { return '$'+Number(+n||0).toFixed(2); } }
  function fmtDate(s) { return s || ''; }
  function escapeHTML(s){
    return String(s||'').replace(/[&<>"']/g, c => (
      c === '&' ? '&amp;' :
      c === '<' ? '&lt;'  :
      c === '>' ? '&gt;'  :
      c === '"' ? '&quot;': '&#39;'
    ));
  }
  function pathParts(){ return [state.ctx.cat, state.ctx.sub, state.ctx.ssub, state.ctx.sss].filter(Boolean); }

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

  function nearestMonth(preferred, available) {
    if (!available || !available.length) return '';
    const set = new Set(available);
    if (preferred && set.has(preferred)) return preferred;
    const earlier = available.filter(m => m <= preferred).sort().reverse();
    if (earlier.length) return earlier[0];
    const later = available.filter(m => m > preferred).sort();
    return later[0] || available[available.length - 1];
  }

  // ---------- renderers ----------
  function renderBreadcrumb(){
    const host = $('drawer-breadcrumb'); if (!host) return;
    const parts = pathParts();
    if (!parts.length){
      host.innerHTML = '<a href="#" data-bc-index="-1">All Categories</a>';
      return;
    }
    const segs = ['<a href="#" data-bc-index="-1">All Categories</a>'];
    parts.forEach((p, i) => {
      segs.push('<span class="sep">›</span>');
      segs.push('<a href="#" data-bc-index="'+i+'">'+escapeHTML(p)+'</a>');
    });
    host.innerHTML = segs.join(' ');
    host.querySelectorAll('a[data-bc-index]').forEach(a => {
      a.addEventListener('click', function(e){
        e.preventDefault();
        const idx = Number(a.getAttribute('data-bc-index') || -1);
        if (idx < 0){
          state.ctx.level = 'category';
          state.ctx.cat = state.ctx.sub = state.ctx.ssub = state.ctx.sss = '';
        } else {
          const order = ['cat','sub','ssub','sss'];
          order.slice(idx + 1).forEach((k) => { state.ctx[k] = ''; });
          state.ctx.level = ['category','subcategory','subsubcategory','subsubsubcategory'][idx] || 'category';
        }
        fetchPathTx(state.ctx).catch(()=>{});
      }, { passive:false });
    });
  }

  function renderChildren(){
    const host = $('drawer-children'); if (!host) return;
    const kids = state.children || [];
    if (!kids.length){ host.innerHTML = '<span class="text-muted">No children.</span>'; return; }
    host.innerHTML = kids.map(function(name){
      return '<button type="button" class="child-pill" data-child="'+encodeURIComponent(name)+'" title="Drill into '+escapeHTML(name)+'">' +
               '<span class="dot" aria-hidden="true"></span>' +
               '<span class="label">'+escapeHTML(name)+'</span>' +
               '<span class="chev" aria-hidden="true">›</span>' +
             '</button>';
    }).join('');
    host.querySelectorAll('.child-pill').forEach(pill => {
      pill.addEventListener('click', function(){
        const name = decodeURIComponent(pill.getAttribute('data-child') || '');
        if (!state.ctx.cat) { state.ctx.cat = name; state.ctx.level = 'category'; }
        else if (!state.ctx.sub) { state.ctx.sub = name; state.ctx.level = 'subcategory'; }
        else if (!state.ctx.ssub) { state.ctx.ssub = name; state.ctx.level = 'subsubcategory'; }
        else { state.ctx.sss = name; state.ctx.level = 'subsubsubcategory'; }
        fetchPathTx(state.ctx).catch(()=>{});
      }, { passive:true });
    });
  }

  function renderTx(){
    const body = $('drawer-tx-body'); if (!body) return;
    const rows = state.tx || [];
    if (!rows.length){ body.innerHTML = '<tr><td colspan="4" class="text-muted">No transactions.</td></tr>'; return; }

    // group by YYYY-MM
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
        '<tr class="month-divider" id="'+escapeHTML(monthId(k))+'">' +
        '  <td colspan="4"><span class="fw-bold">'+escapeHTML(g.label)+'</span> — ' +
        '    <span class="'+netCls+'">Net: '+fmtUSD(net)+'</span>' +
        '  </td>' +
        '</tr>'
      );
      for (const t of g.items){
        const cls = (parseFloat(t.amount||0) < 0) ? 'tx-neg' : 'tx-pos';
        // deepest category name:
        const catPath = (t.sssubcategory || t.subsubcategory || t.subcategory || t.category || '') || '';
        parts.push(
          '<tr>' +
          '  <td class="text-nowrap">'+escapeHTML(fmtDate(t.date))+'</td>' +
          '  <td>'+escapeHTML(t.description || '')+'</td>' +
          '  <td class="amount '+cls+'">'+fmtUSD(Math.abs(t.amount||0))+'</td>' +
          '  <td class="cat">'+escapeHTML(catPath)+'</td>' +
          '</tr>'
        );
      }
    }
    body.innerHTML = parts.join('');
  }

  // ---------- data ----------
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

    const monthLower = String(ctx.month || '').toLowerCase();
    if (monthLower === 'all') params.set('month', 'all');
    else if (ctx.month) params.set('month', ctx.month);

    params.set('months', 'all');
    params.set('_', Date.now().toString());

    let j;
    try {
      const res = await fetch(PATH_TXN_URL + '?' + params.toString(), { headers: { 'Accept': 'application/json' } });
      if (!res.ok) throw new Error(String(res.status));
      j = await res.json();
    } catch (err){
      console.error('drawer fetchPathTx failed:', err);
      if (body) body.innerHTML = '<tr><td colspan="4" class="text-danger">Failed to load.</td></tr>';
      return;
    }

    state.months = j.months || [];
    state.tx = j.transactions || [];
    state.children = j.children || [];
    state.total = j.total || 0;
    state.magnitude_total = j.magnitude_total || 0;

    const serverMonth = String(j.month || '').toLowerCase();
    state.showAll = (serverMonth === 'all') || (monthLower === 'all');

    setText('drawer-total', fmtUSD(state.total));
    renderBreadcrumb();
    renderChildren();
    renderTx();

    // Build native month <select> (for form state only)
    const sel = $('drawer-months');
    if (sel){
      const opts = [];
      opts.push('<option value="all"' + (state.showAll ? ' selected' : '') + '>All months</option>');
      const monthsDesc = (state.months || []).slice().sort().reverse();
      const current = (!state.showAll && state.ctx.month) ? state.ctx.month : (monthsDesc[0] || '');
      monthsDesc.forEach(function(m){
        const selAttr = (m === current) ? ' selected' : '';
        opts.push('<option value="' + m + '"' + selAttr + '>' + m + '</option>');
      });
      sel.innerHTML = opts.join('');
      sel.value = state.showAll ? 'all' : (state.ctx.month || sel.value || '');
      // Emit event for Glass Select consumers
      document.dispatchEvent(new CustomEvent('cm:months', {
        detail: { months: monthsDesc, selected: sel.value || '', showAll: state.showAll }
      }));
    }
  }

  // ---------- keywords (optional endpoints) ----------
  function payloadForKeywords(){
    const parts = pathParts(); const last = parts[parts.length-1] || '';
    return { level: state.ctx.level||'category', cat:state.ctx.cat||'', sub:state.ctx.sub||'', ssub:state.ctx.ssub||'', sss:state.ctx.sss||'', last };
  }
  async function fetchKeywords(){
    if (!urls.KW_GET_URL) return { keywords: [] };
    const qp = new URLSearchParams(payloadForKeywords());
    qp.set('_', Date.now().toString());
    try {
      const res = await fetch(urls.KW_GET_URL + '?' + qp.toString(), { headers:{'Accept':'application/json'} });
      return await res.json();
    } catch { return { keywords: [] }; }
  }
  async function addKeyword(kw){
    if (!urls.KW_ADD_URL || !kw) return;
    const payload = Object.assign({}, payloadForKeywords(), { keyword: kw });
    try { await fetch(urls.KW_ADD_URL, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) }); } catch {}
  }
  async function removeKeyword(kw){
    if (!urls.KW_REMOVE_URL || !kw) return;
    const payload = Object.assign({}, payloadForKeywords(), { keyword: kw, remove: true });
    try { await fetch(urls.KW_REMOVE_URL, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) }); } catch {}
  }
  async function refreshKeywords(){
    const host = $('kw-list'); if (!host) return;
    host.innerHTML = '<span class="text-muted">Loading…</span>';
    const j = await fetchKeywords();
    const kws = (j && j.keywords) || [];
    if (!kws.length){ host.innerHTML = '<span class="text-muted">No keywords yet.</span>'; return; }
    host.innerHTML = kws.map(k => (
      '<span class="badge text-bg-secondary me-1">' + escapeHTML(k) + ' <a href="#" data-kw="'+encodeURIComponent(k)+'" class="text-reset text-decoration-none ms-1" title="Remove">×</a></span>'
    )).join('');
    host.querySelectorAll('a[data-kw]').forEach(a => {
      a.addEventListener('click', async function(e){
        e.preventDefault();
        await removeKeyword(decodeURIComponent(a.getAttribute('data-kw') || ''));
        refreshKeywords();
      });
    });
  }

  // ---------- actions ----------
  function payloadForActions(){
    const parts = pathParts();
    return { path: parts.join(' / '), name: parts[parts.length-1] || '', allow_hidden: state.ctx.allowHidden ? 1 : 0 };
  }

  const btnInspect = document.getElementById('drawer-inspect');
  const btnRename  = document.getElementById('drawer-rename');
  const btnUpsert  = document.getElementById('drawer-upsert');

  if (btnInspect && urls.INSPECT_URL){
    btnInspect.addEventListener('click', async function(){
      const p = payloadForActions();
      const qp = new URLSearchParams(p);
      try {
        const res = await fetch(urls.INSPECT_URL + '?' + qp.toString(), { headers:{ 'Accept':'application/json' }});
        const j   = await res.json();
        alert(JSON.stringify(j, null, 2));
      } catch (e) { alert('Inspect failed.'); }
    });
  }
  if (btnRename && urls.RENAME_URL){
    btnRename.addEventListener('click', async function(){
      const p = payloadForActions();
      if (!p.path) { alert('Select a node to rename.'); return; }
      const from = p.name || '(unnamed)';
      const to   = prompt('Rename "' + from + '" to:', from);
      if (!to || to.trim() === from) return;
      try {
        await fetch(urls.RENAME_URL, {
          method: 'POST',
          headers: { 'Content-Type':'application/json' },
          body: JSON.stringify({
            path: p.path,
            new_name: to.trim(),
            allow_hidden: p.allow_hidden
          })
        });
        fetchPathTx(state.ctx).catch(()=>{});
        alert('Rename attempted (check the drawer).');
      } catch (e) { alert('Rename failed.'); }
    });
  }

  if (btnUpsert && urls.UPSERT_URL){
    btnUpsert.addEventListener('click', async function(){
      const p = payloadForActions();
      try {
        await fetch(urls.UPSERT_URL, {
          method: 'POST',
          headers: { 'Content-Type':'application/json' },
          body: JSON.stringify({ path: p.path, allow_hidden: p.allow_hidden })
        });
        alert('Upsert attempted.');
      } catch (e) { alert('Upsert failed.'); }
    });
  }

  // ---------- public open ----------
  function openCategoryManager(ctx){
    ensureOC(); if (offcanvas) offcanvas.show();

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

    fetchPathTx(state.ctx).catch(err => console.error('drawer fetchPathTx failed:', err));
    refreshKeywords();
  }
  openCategoryManager.__cl_neon_fit = true;
  window.openCategoryManager = openCategoryManager;

  // --- Back-compat shims (old API names -> new openCategoryManager) ---
  if (typeof window.openDrawerForPath !== 'function') {
    window.openDrawerForPath = function(state){
      try { openCategoryManager(state || {}); } catch (e) { console.error('openDrawerForPath shim failed', e); }
    };
  }
  if (typeof window.openDrawerForCategory !== 'function') {
    window.openDrawerForCategory = function(cat, opts){
      try {
        openCategoryManager({
          level: 'category',
          cat: cat || '',
          sub: '',
          ssub: '',
          sss: '',
          month: '',
          allowHidden: !!(opts && opts.allowHidden)
        });
      } catch (e) { console.error('openDrawerForCategory shim failed', e); }
    };
  }
  

  // Global click helper
  window.dashManage = function(e, el){
    e.preventDefault();
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
      QSA('.drawer-tab').forEach(t => t.classList.remove('active'));
      QSA('.drawer-pane').forEach(p => p.style.display = 'none');
      tab.classList.add('active');
      const pane = QS('.drawer-pane[data-pane="'+target+'"]');
      if (pane) pane.style.display = 'block';
      if (target === 'keywords') refreshKeywords();
    });
  });

  // Month selector
  const monthSel = $('drawer-months');
  if (monthSel){
    monthSel.addEventListener('change', async function(e){
      const val = (e.target.value || '').toLowerCase();
      state.ctx.month = val || '';
      state.showAll = (val === 'all');
      await fetchPathTx(state.ctx);
    });
  }
})();
