// static/js/drawer.js — neon-tech aligned drawer (no sticky, emits cm:months for Glass Select)
(function () {
  'use strict';

  if (window.openCategoryManager && window.openCategoryManager.__cl_neon_fit === true) {
  if (typeof window.openDrawerForPath !== 'function') {
    window.openDrawerForPath = function(state){ window.openCategoryManager(state||{}); };
  }
  if (typeof window.openDrawerForCategory !== 'function') {
    window.openDrawerForCategory = function(cat, opts){ window.openCategoryManager({ level:'category', cat:cat||'', allowHidden:!!(opts&&opts.allowHidden) }); };
  }
  return;
}

  const urls = (window.CL_URLS || {});
  const PATH_TXN_URL = urls.PATH_TXN_URL || '/api/path/transactions';
  const KW_GET_URL    = (window.CL_URLS && window.CL_URLS.KW_GET_URL)    || '/admin/api/keywords_for_name';
  const KW_ADD_URL    = (window.CL_URLS && window.CL_URLS.KW_ADD_URL)    || '/admin/api/keyword_add';
  const KW_REMOVE_URL = (window.CL_URLS && window.CL_URLS.KW_REMOVE_URL) || '/admin/api/keyword_remove';

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
    if (!kids.length){ host.innerHTML = '<div class="text-muted">No child categories.</div>'; return; }
    host.innerHTML = kids.map(c => (
      '<button type="button" class="btn btn-sm btn-outline-secondary me-1" data-child="'+escapeHTML(c)+'">'+escapeHTML(c)+'</button>'
    )).join(' ');
    host.querySelectorAll('button[data-child]').forEach(btn => {
      btn.addEventListener('click', function(){
        const label = btn.getAttribute('data-child') || '';
        const order = ['','','sub','ssub','sss'];
        const k = order[['category','subcategory','subsubcategory','subsubsubcategory'].indexOf(state.ctx.level) + 1] || 'sub';
        state.ctx.level = (state.ctx.level === 'category') ? 'subcategory'
                         : (state.ctx.level === 'subcategory') ? 'subsubcategory'
                         : 'subsubsubcategory';
        state.ctx[k] = label;
        fetchPathTx(state.ctx).catch(()=>{});
      });
    });
  }

  function renderTx(){
    const body = $('drawer-tx-body'); if (!body) return;
    const rows = state.tx || [];
    if (!rows.length){ body.innerHTML = '<tr><td colspan="4" class="text-muted">No transactions.</td></tr>'; return; }

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
        '<tr class="month-divider" id="'+escapeHTML(monthId(k))+'">'+
        '  <td colspan="4"><span class="fw-bold">'+escapeHTML(g.label)+'</span> — '+
        '    <span class="'+netCls+'">Net: '+fmtUSD(net)+'</span>'+
        '  </td>'+
        '</tr>'
      );
      for (const t of g.items){
        const amt = Number(t.amount || 0);
        const amtCls = amt < 0 ? 'tx-neg' : 'tx-pos';
        parts.push(
          '<tr>'+
          '  <td class="mono">'+escapeHTML(fmtDate(t.date || ''))+'</td>'+
          '  <td>'+escapeHTML(t.description || '')+'</td>'+
          '  <td class="mono text-end '+amtCls+'">'+fmtUSD(amt)+'</td>'+
          '  <td><span class="badge text-bg-secondary">'+escapeHTML(t.cat || '')+'</span></td>'+
          '</tr>'
        );
      }
    }
    body.innerHTML = parts.join('');
  }

  // ---------- months select & glass emitter ----------
  function renderMonthsAndEmit(){
    const sel = $('drawer-months'); if (!sel) return;
    const arr = (state.months || []).slice().sort().reverse(); // desc
    sel.innerHTML = '';
    const opt = (v,lbl,selq) => `<option value="${escapeHTML(v)}"${selq?' selected':''}>${escapeHTML(lbl)}</option>`;
    const cur = String(state.ctx.month || '');
    const out = [];
    out.push(opt('', 'Latest month', cur === ''));
    out.push(opt('all', 'All months', cur === 'all'));
    arr.forEach(m => out.push(opt(m, monthLabelFromKey(m), cur === m)));
    sel.innerHTML = out.join('');

    // emit for Glass Select widgets listening
    try {
      document.dispatchEvent(new CustomEvent('cm:months', {
        detail: { months: arr, selected: cur, showAll: state.showAll }
      }));
    } catch {}
  }

  // ---------- keywords ----------
  function payloadForKeywords(){
    const parts = pathParts(); const last = parts[parts.length-1] || '';
    return { level: state.ctx.level||'category', cat:state.ctx.cat||'', sub:state.ctx.sub||'', ssub:state.ctx.ssub||'', sss:state.ctx.sss||'', name: last, last };
  }
  async function fetchKeywords(){
    const qp = new URLSearchParams(payloadForKeywords());
    qp.set('_', Date.now().toString());
    try {
      const res = await fetch(KW_GET_URL + '?' + qp.toString(), { headers:{'Accept':'application/json'} });
      return await res.json();
    } catch { return { keywords: [] }; }
  }
  async function addKeyword(kw){
    if (!kw) return;
    const payload = Object.assign({}, payloadForKeywords(), { keyword: kw });
    try { await fetch(KW_ADD_URL, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) }); } catch {}
  }
  async function removeKeyword(kw){
    if (!kw) return;
    const payload = Object.assign({}, payloadForKeywords(), { keyword: kw, remove: true });
    try { await fetch(KW_REMOVE_URL, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) }); } catch {}
  }
  async function refreshKeywords(){
    const host = $('kw-list'); if (!host) return;
    host.innerHTML = '<span class="text-muted">Loading…</span>';
    const j = await fetchKeywords();
    const kws = (j && j.keywords) || [];
    if (!kws.length){ host.innerHTML = '<span class="text-muted">No keywords yet.</span>'; return; }
    host.innerHTML = kws.map(k => (
      '<span class="badge text-bg-secondary me-1">'+escapeHTML(k)+' <a href="#" data-kw="'+encodeURIComponent(k)+'" class="text-reset text-decoration-none ms-1" title="Remove">×</a></span>'
    )).join('');
    host.querySelectorAll('a[data-kw]').forEach(a => {
      a.addEventListener('click', async function(e){
        e.preventDefault();
        await removeKeyword(decodeURIComponent(a.getAttribute('data-kw') || ''));
        refreshKeywords();
      });
    });
  }

  // ---------- data fetch ----------
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

    try {
      const res = await fetch(PATH_TXN_URL + '?' + params.toString(), { headers: { 'Accept': 'application/json' } });
      const j = await res.json();

      state.months = Array.isArray(j.months) ? j.months : [];
      state.tx = Array.isArray(j.tx) ? j.tx : [];
      state.children = Array.isArray(j.children) ? j.children : [];
      state.total = Number(j.total || 0);
      state.magnitude_total = Number(j.magnitude_total || 0);

      setText('drawer-total', fmtUSD(state.total));
      renderBreadcrumb();
      renderChildren();
      renderTx();
      renderMonthsAndEmit();

    } catch (e) {
      console.error('fetchPathTx failed', e);
      if (body) body.innerHTML = '<tr><td colspan="4" class="text-muted">Failed to load.</td></tr>';
    }
  }

  // ---------- tabs + month select ----------
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

  const monthSel = $('drawer-months');
  if (monthSel){
    monthSel.addEventListener('change', async function(e){
      const val = (e.target.value || '').toLowerCase();
      state.ctx.month = val || '';
      state.showAll = (val === 'all');
      await fetchPathTx(state.ctx);
    });
  }

  // ---- public opener ----
  function openCategoryManager(ctx){
    const c = ctx || {};
    state.ctx.level = c.level || 'category';
    state.ctx.cat   = c.cat || '';
    state.ctx.sub   = c.sub || '';
    state.ctx.ssub  = c.ssub || '';
    state.ctx.sss   = c.sss || '';
    state.ctx.month = c.month || '';
    state.ctx.allowHidden = !!c.allowHidden;

    renderBreadcrumb();
    fetchPathTx(state.ctx).catch(()=>{});

    const el = $('dashCategoryManager');
    if (!el) return;
    let oc = null;
    try { oc = ensureOC(); } catch(e) { oc = null; }
    if (oc && typeof oc.show === 'function') oc.show();
    else { el.classList.add('show'); el.style.display = 'block'; }
  }

  // export + back-compat
  window.openCategoryManager = openCategoryManager;
  if (typeof window.openDrawerForPath !== 'function') {
    window.openDrawerForPath = function(state){ openCategoryManager(state||{}); };
  }
  if (typeof window.openDrawerForCategory !== 'function') {
    window.openDrawerForCategory = function(cat, opts){ openCategoryManager({ level:'category', cat: cat||'', allowHidden: !!(opts && opts.allowHidden) }); };
  }
})();
