// static/js/drawer.js — matches original _drawer.html (tabs: overview/keywords/actions), restores months + children pills
(function () {
  'use strict';

  // If already loaded, keep legacy openers and exit
  if (window.openCategoryManager && window.openCategoryManager.__cl_neon_fit === true) {
    if (typeof window.openDrawerForPath !== 'function') {
      window.openDrawerForPath = function (state) { try { window.openCategoryManager(state || {}); } catch (e) { console.error(e); } };
    }
    if (typeof window.openDrawerForCategory !== 'function') {
      window.openDrawerForCategory = function (cat, opts) { try { window.openCategoryManager({ level: 'category', cat: cat || '', allowHidden: !!(opts && opts.allowHidden) }); } catch (e) { console.error(e); } };
    }
    return;
  }

  // URLs (server can override)
  const urls = (window.CL_URLS || {});
  const PATH_TXN_URL  = urls.PATH_TXN_URL  || '/api/path/transactions';
  const KW_GET_URL    = urls.KW_GET_URL    || '/admin/api/keywords_for_name';
  const KW_ADD_URL    = urls.KW_ADD_URL    || '/admin/api/keyword_add';
  const KW_REMOVE_URL = urls.KW_REMOVE_URL || '/admin/api/keyword_remove';
  const ACTION_URL    = urls.ACTION_URL    || '/admin/api/category_action';

  // DOM helpers
  const QS  = s => document.querySelector(s);
  const QSA = s => Array.from(document.querySelectorAll(s));
  const $   = id => document.getElementById(id);
  const on  = (el, ev, fn, opts) => el && el.addEventListener(ev, fn, opts || false);

  const state = {
    ctx: { level:'category', cat:'', sub:'', ssub:'', sss:'', month:'', allowHidden:false },
    months: [],
    tx: [],
    children: [],
    total: 0,
    magnitude_total: 0,
    showAll: false
  };

  // ---------- utils ----------
  function fmtUSD(n){ try { return new Intl.NumberFormat(undefined,{style:'currency',currency:'USD'}).format(+n||0); } catch { return '$'+Number(+n||0).toFixed(2); } }
  function escapeHTML(s){ return String(s||'').replace(/[&<>"']/g, c => c==='&'?'&amp;':c==='<'?'&lt;':c==='>'?'&gt;':c==='"'?'&quot;':'&#39;'); }
  function pathParts(){ return [state.ctx.cat, state.ctx.sub, state.ctx.ssub, state.ctx.sss].filter(Boolean); }
  function fmtDate(s){ return s || ''; }
  function monthKeyFromDateStr(s){
    if (!s) return '0000-00';
    const t = String(s).trim();
    if (t.includes('-')) return t.slice(0,7);
    const [mm, , yyyy] = t.split('/');
    return (yyyy && mm) ? `${yyyy}-${String(mm).padStart(2,'0')}` : '0000-00';
  }
  function monthLabelFromKey(k){
    const [yy, mm] = String(k||'').split('-').map(Number);
    const d = new Date(yy||0, (mm||1)-1, 1);
    return isFinite(+d) ? d.toLocaleString(undefined,{month:'short',year:'numeric'}) : k;
  }
  function monthId(k){ return 'm-' + String(k||'').replace(/[^0-9-]/g,''); }

  // ---------- breadcrumb ----------
  function renderBreadcrumb(){
    const host = $('drawer-breadcrumb'); if (!host) return;
    const parts = pathParts();
    if (!parts.length){ host.innerHTML = '<a href="#" data-bc-index="-1">All Categories</a>'; return; }
    const segs = ['<a href="#" data-bc-index="-1">All Categories</a>'];
    parts.forEach((p,i)=>{ segs.push('<span class="sep">›</span>'); segs.push('<a href="#" data-bc-index="'+i+'">'+escapeHTML(p)+'</a>'); });
    host.innerHTML = segs.join(' ');
    host.querySelectorAll('a[data-bc-index]').forEach(a=>{
      a.addEventListener('click', function(e){
        e.preventDefault();
        const idx = Number(a.getAttribute('data-bc-index')||-1);
        if (idx < 0){ state.ctx.level='category'; state.ctx.cat=state.ctx.sub=state.ctx.ssub=state.ctx.sss=''; }
        else {
          const order = ['cat','sub','ssub','sss'];
          order.slice(idx+1).forEach(k=> state.ctx[k]='');
          state.ctx.level = ['category','subcategory','subsubcategory','subsubsubcategory'][idx] || 'category';
        }
        fetchPathTx(state.ctx).catch(()=>{});
      }, {passive:false});
    });
  }

  // ---------- children pills (use .child-pill markup that your CSS styles) ----------
  function renderChildren(){
    const host = $('drawer-children'); if (!host) return;
    const kids = state.children || [];
    if (!kids.length){ host.innerHTML = '<span class="text-muted">No children.</span>'; return; }
    host.innerHTML = kids.map(name => (
      '<button type="button" class="child-pill" data-child="'+encodeURIComponent(name)+'" title="Drill into '+escapeHTML(name)+'">' +
        '<span class="dot" aria-hidden="true"></span>' +
        '<span class="label">'+escapeHTML(name)+'</span>' +
        '<span class="chev" aria-hidden="true">›</span>' +
      '</button>'
    )).join('');
    host.querySelectorAll('.child-pill').forEach(btn=>{
      btn.addEventListener('click', function(){
        const name = decodeURIComponent(btn.getAttribute('data-child')||'');
        if (!state.ctx.cat)       { state.ctx.cat = name;  state.ctx.level = 'category'; }
        else if (!state.ctx.sub)  { state.ctx.sub = name;  state.ctx.level = 'subcategory'; }
        else if (!state.ctx.ssub) { state.ctx.ssub = name; state.ctx.level = 'subsubcategory'; }
        else                      { state.ctx.sss = name;  state.ctx.level = 'subsubsubcategory'; }
        fetchPathTx(state.ctx).catch(()=>{});
      }, {passive:true});
    });
  }

  // ---------- transactions ----------
  function renderTx(){
    const body = $('drawer-tx-body'); if (!body) return;
    const rows = state.tx || [];
    if (!rows.length){ body.innerHTML = '<tr><td colspan="4" class="text-muted">No transactions.</td></tr>'; return; }

    const groups = new Map();
    for (const t of rows){
      const key = monthKeyFromDateStr(t.date);
      if (!groups.has(key)) groups.set(key, { label: monthLabelFromKey(key), items: [], net: 0 });
      const g = groups.get(key); const amt = Number(t.amount||0);
      g.items.push(t); g.net += amt;
    }
    const keys = Array.from(groups.keys()).sort().reverse();

    body.innerHTML = keys.map(k=>{
      const g = groups.get(k);
      const net = Number(g.net||0); const netCls = net < 0 ? 'tx-neg' : 'tx-pos';
      const header = (
        '<tr class="month-divider" id="'+escapeHTML(monthId(k))+'">' +
          '<td colspan="4"><span class="fw-bold">'+escapeHTML(g.label)+'</span> — ' +
          '<span class="'+netCls+'">Net: '+fmtUSD(net)+'</span></td></tr>'
      );
      const items = g.items.map(t=>{
        const amt = Number(t.amount||0); const amtCls = amt < 0 ? 'tx-neg' : 'tx-pos';
        const path = t.sssubcategory || t.subsubcategory || t.subcategory || t.category || t.cat || '';
        return (
          '<tr>'+
            '<td class="mono">'+escapeHTML(fmtDate(t.date||''))+'</td>'+
            '<td>'+escapeHTML(t.description||'')+'</td>'+
            '<td class="mono text-end '+amtCls+'">'+fmtUSD(amt)+'</td>'+
            '<td><span class="badge text-bg-secondary">'+escapeHTML(path)+'</span></td>'+
          '</tr>'
        );
      }).join('');
      return header + items;
    }).join('');
  }

  // ---------- months (feeds your GlassSelect) ----------
  function renderMonthsAndEmit(){
    const sel = $('drawer-months'); if (!sel) return;
    const uniq = new Set((state.months||[]).filter(Boolean));
    const arr = Array.from(uniq).sort().reverse();
    const cur = String(state.ctx.month||'');
    sel.innerHTML = '';

    // Build options that your GlassSelect wrapper will read
    const opt = (v, lbl, selected)=> `<option value="${v}">${lbl}</option>`;
    sel.insertAdjacentHTML('beforeend', opt('',   'Latest month', false));
    sel.insertAdjacentHTML('beforeend', opt('all','All months',  false));
    arr.forEach(m => sel.insertAdjacentHTML('beforeend', opt(m, monthLabelFromKey(m), false)));
    sel.value = (cur || (state.showAll ? 'all' : sel.value));

    // Let your month UI know the choices + selection
    try {
      document.dispatchEvent(new CustomEvent('cm:months', {
        detail: { months: arr, selected: sel.value || '', showAll: state.showAll }
      }));
    } catch {}
  }

  // ---------- keywords ----------
  function payloadForKeywords(){
    const parts = pathParts(); const last = parts[parts.length-1] || '';
    return { level:state.ctx.level||'category', cat:state.ctx.cat||'', sub:state.ctx.sub||'', ssub:state.ctx.ssub||'', sss:state.ctx.sss||'', name:last, last:last };
  }
  async function fetchKeywords(){
    const qp = new URLSearchParams(payloadForKeywords()); qp.set('_', Date.now());
    try { const r = await fetch(KW_GET_URL + '?' + qp.toString(), { headers:{'Accept':'application/json'} }); return await r.json(); }
    catch { return { keywords: [] }; }
  }
  async function addKeyword(kw){ if (!kw) return;
    const payload = Object.assign({}, payloadForKeywords(), { keyword: kw });
    try { await fetch(KW_ADD_URL, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) }); } catch {}
  }
  async function removeKeyword(kw){ if (!kw) return;
    const payload = Object.assign({}, payloadForKeywords(), { keyword: kw, remove:true });
    try { await fetch(KW_REMOVE_URL, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) }); } catch {}
  }
  async function refreshKeywords(){
    const host = $('kw-list'); if (!host) return;
    host.innerHTML = '<span class="text-muted">Loading…</span>';
    const j = await fetchKeywords();
    const kws = (j && j.keywords) || [];
    if (!kws.length){ host.innerHTML = '<span class="text-muted">No keywords yet.</span>'; return; }
    host.innerHTML = kws.map(k =>
      '<span class="badge text-bg-secondary me-1">'+escapeHTML(k)+' '+
      '<a href="#" data-kw="'+encodeURIComponent(k)+'" class="text-reset text-decoration-none ms-1" title="Remove">×</a></span>'
    ).join('');
    host.querySelectorAll('a[data-kw]').forEach(a=>{
      a.addEventListener('click', async function(e){ e.preventDefault(); await removeKeyword(decodeURIComponent(a.getAttribute('data-kw')||'')); refreshKeywords(); });
    });
  }

  // ---------- actions (preserved) ----------
  function payloadForActions(){ return Object.assign({}, state.ctx); }
  async function runAction(kind){
    const payload = Object.assign({ kind }, payloadForActions());
    try { const res = await fetch(ACTION_URL, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) }); return await res.json(); }
    catch { return { ok:false }; }
  }
  // Example: document.addEventListener('click', e => { const b=e.target.closest('[data-action]'); if(!b) return; runAction(b.dataset.action); });

  // ---------- fetch ----------
  async function fetchPathTx(ctx){
    const body = $('drawer-tx-body'); if (body) body.innerHTML = '<tr><td colspan="4" class="text-muted">Loading…</td></tr>';

    const params = new URLSearchParams();
    params.set('level', ctx.level || 'category');
    if (ctx.cat)  params.set('cat',  ctx.cat);
    if (ctx.sub)  params.set('sub',  ctx.sub);
    if (ctx.ssub) params.set('ssub', ctx.ssub);
    if (ctx.sss)  params.set('sss',  ctx.sss);
    if (ctx.allowHidden) params.set('allow_hidden', '1');
    const ml = String(ctx.month||'').toLowerCase();
    if (ml === 'all') params.set('month','all'); else if (ctx.month) params.set('month', ctx.month);
    params.set('months','all');                 // <-- ensure months list is returned
    params.set('_', Date.now());

    let j;
    try {
      const res = await fetch(PATH_TXN_URL + '?' + params.toString(), { headers:{'Accept':'application/json'} });
      if (!res.ok) throw new Error(String(res.status));
      j = await res.json();
    } catch (err) {
      console.error('fetchPathTx failed:', err);
      if (body) body.innerHTML = '<tr><td colspan="4" class="text-danger">Failed to load.</td></tr>';
      return;
    }

    // Accept both naming styles
    state.months = Array.isArray(j.months) ? j.months : (Array.isArray(j.month_list) ? j.month_list : []);
    state.tx      = Array.isArray(j.tx) ? j.tx : (Array.isArray(j.transactions) ? j.transactions : []);
    state.children = Array.isArray(j.children) ? j.children : (Array.isArray(j.kids) ? j.kids : (Array.isArray(j.child) ? j.child : []));
    state.total   = Number(j.total ?? j.net_total ?? j.sum ?? 0);
    state.magnitude_total = Number(j.magnitude_total ?? j.abs_total ?? 0);

    const serverMonth = String(j.month || '').toLowerCase();
    state.showAll = (serverMonth === 'all') || (ml === 'all');

    const totalEl = $('drawer-total'); if (totalEl) totalEl.textContent = fmtUSD(state.total);
    renderBreadcrumb();
    renderChildren();
    renderTx();
    renderMonthsAndEmit();
  }

  // ---------- tabs (match your original: data-tab / data-pane with values overview/keywords/actions) ----------
  QSA('.drawer-tab').forEach(function(tab){
    tab.addEventListener('click', function(e){
      e.preventDefault();
      const target = tab.getAttribute('data-tab') || 'overview';
      QSA('.drawer-tab').forEach(t => t.classList.remove('active'));
      QSA('.drawer-pane').forEach(p => p.style.display = 'none');
      tab.classList.add('active');
      const pane = QS('.drawer-pane[data-pane="'+target+'"]');
      if (pane) pane.style.display = 'block';
      if (target === 'keywords') refreshKeywords();
    });
  });

  // ---------- month selector (native select; your GlassSelect dispatches change) ----------
  const monthSel = $('drawer-months');
  if (monthSel){
    monthSel.addEventListener('change', async function(e){
      const val = (e.target.value || '').trim();
      state.ctx.month = val || '';
      state.showAll = (val.toLowerCase() === 'all');
      await fetchPathTx(state.ctx);
      if (val && val !== 'all'){
        const anchor = document.getElementById(monthId(val));
        if (anchor) anchor.scrollIntoView({ behavior:'smooth', block:'start' });
      }
    });
  }

  // ---------- keyword add ----------
  const addInput = $('kw-add-input'), addBtn = $('kw-add-btn');
  if (addInput && addBtn){
    const commit = ()=>{ const kw=(addInput.value||'').trim(); if(!kw) return; addKeyword(kw).then(()=>{ addInput.value=''; refreshKeywords(); }); };
    on(addBtn,'click',e=>{ e.preventDefault(); commit(); });
    on(addInput,'keydown',e=>{ if (e.key==='Enter') commit(); });
  }

  // ---------- open / close ----------
  let offcanvas = null;
  function ensureOC(){
    const el = $('dashCategoryManager');
    if (!el) return null;
    if (window.bootstrap && window.bootstrap.Offcanvas){
      offcanvas = offcanvas || new bootstrap.Offcanvas(el);
      return offcanvas;
    }
    return null;
  }
  function openDrawer(ctx){
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
    const el = $('dashCategoryManager'); if (!el) return;
    const oc = ensureOC(); if (oc && oc.show) oc.show(); else { el.classList.add('show'); el.style.display = 'block'; }
  }
  function closeDrawer(){
    if (offcanvas && offcanvas.hide) offcanvas.hide();
    else { const el=$('dashCategoryManager'); if (el){ el.classList.remove('show'); el.style.display='none'; } }
  }

  function openCategoryManager(ctx){ openDrawer(ctx||{}); }
  openCategoryManager.__cl_neon_fit = true;
  window.openCategoryManager = openCategoryManager;

  // Legacy names for older pages (Index)
  if (typeof window.openDrawerForPath !== 'function') {
    window.openDrawerForPath = function(state){ try { openCategoryManager(state || {}); } catch(e){ console.error(e); } };
  }
  if (typeof window.openDrawerForCategory !== 'function') {
    window.openDrawerForCategory = function(cat, opts){ try { openCategoryManager({ level:'category', cat: cat || '', allowHidden: !!(opts && opts.allowHidden) }); } catch(e){ console.error(e); } };
  }
})();
