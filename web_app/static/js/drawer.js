// Drawer UI — blue pills + glass month bar, with Overview/Keywords/Actions and legacy openers
(function(){
  'use strict';

  if (window.openCategoryManager && window.openCategoryManager.__cl_neon_fit === true) {
    if (!window.openDrawerForPath)      window.openDrawerForPath      = (state)=>window.openCategoryManager(state||{});
    if (!window.openDrawerForCategory)  window.openDrawerForCategory  = (cat,opts)=>window.openCategoryManager({ level:'category', cat:cat||'', allowHidden:!!(opts&&opts.allowHidden) });
    return;
  }

  const urls = window.CL_URLS || {};
  const PATH_TXN_URL  = urls.PATH_TXN_URL  || '/api/path/transactions';
  const KW_GET_URL    = urls.KW_GET_URL    || '/admin/api/keywords_for_name';
  const KW_ADD_URL    = urls.KW_ADD_URL    || '/admin/api/keyword_add';
  const KW_REMOVE_URL = urls.KW_REMOVE_URL || '/admin/api/keyword_remove';
  const ACTION_URL    = urls.ACTION_URL    || '/admin/api/category_action';

  const QS  = s=>document.querySelector(s);
  const QSA = s=>Array.from(document.querySelectorAll(s));
  const $   = id=>document.getElementById(id);
  const on  = (el,ev,fn,opts)=>el&&el.addEventListener(ev,fn,opts||false);
  const fmtUSD = n => { try { return new Intl.NumberFormat(undefined,{style:'currency',currency:'USD'}).format(+n||0); } catch { return '$'+Number(+n||0).toFixed(2);} };
  const esc = s => String(s||'').replace(/[&<>"']/g,c=>c==='&'?'&amp;':c==='<'?'&lt;':c==='>'?'&gt;':c==='"'?'&quot;':'&#39;');
  const fmtDate = s => s||'';

  const state = {
    ctx: { level:'category', cat:'', sub:'', ssub:'', sss:'', month:'', allowHidden:false },
    months: [], tx: [], children: [], total:0, magnitude_total:0, showAll:false
  };
  const pathParts = ()=>[state.ctx.cat,state.ctx.sub,state.ctx.ssub,state.ctx.sss].filter(Boolean);

  function monthKeyFromDateStr(s){
    if (!s) return '0000-00';
    const t=String(s).trim();
    if (t.includes('-')) return t.slice(0,7);
    const [mm,,yy]=t.split('/'); return (yy&&mm)?`${yy}-${String(mm).padStart(2,'0')}`:'0000-00';
  }
  function monthLabelFromKey(k){
    const [yy,mm]=String(k||'').split('-').map(Number); const d=new Date(yy||0,(mm||1)-1,1);
    return isFinite(+d)?d.toLocaleString(undefined,{month:'short',year:'numeric'}):k;
  }
  const monthId = k => 'm-'+String(k||'').replace(/[^0-9-]/g,'');

  // Breadcrumb (blue links)
  function renderBreadcrumb(){
    const host=$('drawer-breadcrumb'); if(!host) return;
    const parts=pathParts();
    if (!parts.length){ host.innerHTML='<a class="link-primary" href="#" data-bc-index="-1">All Categories</a>'; return; }
    const segs=['<a class="link-primary" href="#" data-bc-index="-1">All Categories</a>'];
    parts.forEach((p,i)=>{ segs.push('<span class="sep">›</span>'); segs.push('<a class="link-primary" href="#" data-bc-index="'+i+'">'+esc(p)+'</a>'); });
    host.innerHTML = segs.join(' ');
    host.querySelectorAll('a[data-bc-index]').forEach(a=>{
      on(a,'click',e=>{
        e.preventDefault();
        const idx=Number(a.getAttribute('data-bc-index')||-1);
        if (idx<0){ state.ctx.level='category'; state.ctx.cat=state.ctx.sub=state.ctx.ssub=state.ctx.sss=''; }
        else { ['cat','sub','ssub','sss'].slice(idx+1).forEach(k=>state.ctx[k]=''); state.ctx.level=['category','subcategory','subsubcategory','subsubsubcategory'][idx]||'category'; }
        fetchPathTx(state.ctx);
      },{passive:false});
    });
  }

  // Children — blue pills
  function renderChildren(){
    const host=$('drawer-children'); if(!host) return;
    const kids=state.children||[];
    if(!kids.length){ host.innerHTML='<span class="text-muted">No children.</span>'; return; }
    host.innerHTML = kids.map(name =>
      `<a class="pill" data-child="${encodeURIComponent(name)}"><span class="label">${esc(name)}</span><span class="chev">›</span></a>`
    ).join('');
    host.querySelectorAll('.pill').forEach(p=>{
      on(p,'click',()=>{ const name=decodeURIComponent(p.dataset.child||'');
        if (!state.ctx.cat) state.ctx.cat=name, state.ctx.level='category';
        else if (!state.ctx.sub) state.ctx.sub=name, state.ctx.level='subcategory';
        else if (!state.ctx.ssub) state.ctx.ssub=name, state.ctx.level='subsubcategory';
        else state.ctx.sss=name, state.ctx.level='subsubsubcategory';
        fetchPathTx(state.ctx);
      },{passive:true});
    });
  }

  // Transactions — Cat column blue pill
  function renderTx(){
    const body=$('drawer-tx-body'); if(!body) return;
    const rows=state.tx||[];
    if(!rows.length){ body.innerHTML='<tr><td colspan="4" class="text-muted">No transactions.</td></tr>'; return; }

    const groups=new Map();
    for(const t of rows){
      const key=monthKeyFromDateStr(t.date);
      if(!groups.has(key)) groups.set(key,{label:monthLabelFromKey(key),items:[],net:0});
      const g=groups.get(key); const amt=Number(t.amount||0); g.items.push(t); g.net+=amt;
    }
    const keys=Array.from(groups.keys()).sort().reverse();

    body.innerHTML = keys.map(k=>{
      const g=groups.get(k); const net=Number(g.net||0); const netCls=net<0?'tx-neg':'tx-pos';
      const header = `<tr class="month-divider" id="${esc(monthId(k))}"><td colspan="4"><span class="fw-bold">${esc(g.label)}</span> — <span class="${netCls}">Net: ${fmtUSD(net)}</span></td></tr>`;
      const items = g.items.map(t=>{
        const amt=Number(t.amount||0), cls=amt<0?'tx-neg':'tx-pos';
        const catPath = t.sssubcategory || t.subsubcategory || t.subcategory || t.category || t.cat || '';
        return `<tr>
          <td class="mono">${esc(fmtDate(t.date||''))}</td>
          <td>${esc(t.description||'')}</td>
          <td class="mono text-end ${cls}">${fmtUSD(amt)}</td>
          <td><span class="pill pill-sm">${esc(catPath)}</span></td>
        </tr>`;
      }).join('');
      return header + items;
    }).join('');
  }

  // Glass Month control
  function setMonthLabel(v){
    const lbl = !v ? 'Latest month' : (String(v).toLowerCase()==='all'?'All months':monthLabelFromKey(v));
    const el=$('drawer-months-label'); if (el) el.textContent=lbl;
  }
  function renderMonthsAndEmit(){
    const sel=$('drawer-months'); if(!sel) return;
    const arr = Array.from(new Set((state.months||[]).filter(Boolean))).sort().reverse();
    sel.innerHTML='';
    [['','Latest month'],['all','All months']].forEach(([val,lbl])=>{
      const o=document.createElement('option'); o.value=val; o.textContent=lbl; sel.appendChild(o);
    });
    arr.forEach(m=>{ const o=document.createElement('option'); o.value=m; o.textContent=monthLabelFromKey(m); sel.appendChild(o); });
    sel.value = state.showAll ? 'all' : (state.ctx.month || sel.value || '');
    setMonthLabel(sel.value);

    // Build glass menu list
    const list=$('drawer-months-list');
    if (list){
      list.innerHTML = arr.map(m=>`<button class="glass-item" data-value="${m}">${monthLabelFromKey(m)}</button>`).join('');
    }

    // Emit event for any external listeners (your month overlay, etc.)
    try {
      document.dispatchEvent(new CustomEvent('cm:months', { detail: { months: arr, selected: sel.value||'', showAll: state.showAll }}));
    } catch {}
  }
  (function bindGlass(){
    const btn=$('drawer-months-btn'), menu=$('drawer-months-menu'), sel=$('drawer-months');
    if (!btn || !menu || !sel) return;

    on(btn,'click',e=>{
      e.preventDefault();
      menu.hidden = !menu.hidden;
    });

    on(document,'click',e=>{
      if (!menu.hidden) {
        const within = e.target.closest && e.target.closest('#drawer-months-glass');
        if (!within) menu.hidden = true;
      }
    });

    on(menu,'click',e=>{
      const it=e.target.closest('.glass-item');
      if (!it) return;
      const v=it.getAttribute('data-value')||'';
      sel.value = v;
      setMonthLabel(v);
      menu.hidden = true;
      sel.dispatchEvent(new Event('change',{bubbles:true}));
    });
  })();

  // Keywords
  function payloadForKeywords(){
    const parts=pathParts(); const last=parts[parts.length-1]||'';
    return { level:state.ctx.level||'category', cat:state.ctx.cat||'', sub:state.ctx.sub||'', ssub:state.ctx.ssub||'', sss:state.ctx.sss||'', name:last, last:last };
  }
  async function fetchKeywords(){ const qp=new URLSearchParams(payloadForKeywords()); qp.set('_',Date.now()); try{ const r=await fetch(KW_GET_URL+'?'+qp.toString(),{headers:{'Accept':'application/json'}}); return await r.json(); }catch{return{keywords:[]}}}
  async function addKeyword(kw){ if(!kw) return; const p=Object.assign({},payloadForKeywords(),{keyword:kw}); try{ await fetch(KW_ADD_URL,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});}catch{} }
  async function removeKeyword(kw){ if(!kw) return; const p=Object.assign({},payloadForKeywords(),{keyword:kw,remove:true}); try{ await fetch(KW_REMOVE_URL,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});}catch{} }
  async function refreshKeywords(){
    const host=$('kw-list'); if(!host) return;
    host.innerHTML='<span class="text-muted">Loading…</span>';
    const j=await fetchKeywords(); const kws=(j&&j.keywords)||[];
    host.innerHTML = kws.length ? kws.map(k => `<span class="pill pill-sm">${esc(k)} <a href="#" data-kw="${encodeURIComponent(k)}" class="text-reset ms-1" title="Remove">×</a></span>`).join('') : '<span class="text-muted">No keywords yet.</span>';
    host.querySelectorAll('a[data-kw]').forEach(a=>on(a,'click',async e=>{ e.preventDefault(); await removeKeyword(decodeURIComponent(a.dataset.kw||'')); refreshKeywords(); }));
  }

  // Actions (kept)
  function payloadForActions(){ return Object.assign({}, state.ctx); }
  async function runAction(kind){
    const payload=Object.assign({kind},payloadForActions());
    try{ const r=await fetch(ACTION_URL,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); return await r.json(); }catch{return{ok:false}}
  }
  // Example hook:
  // document.addEventListener('click',e=>{ const b=e.target.closest('[data-action]'); if(!b) return; runAction(b.dataset.action); });

  // Fetcher
  async function fetchPathTx(ctx){
    const body=$('drawer-tx-body'); if(body) body.innerHTML='<tr><td colspan="4" class="text-muted">Loading…</td></tr>';
    const q=new URLSearchParams();
    q.set('level',ctx.level||'category');
    if (ctx.cat)  q.set('cat',ctx.cat);
    if (ctx.sub)  q.set('sub',ctx.sub);
    if (ctx.ssub) q.set('ssub',ctx.ssub);
    if (ctx.sss)  q.set('sss',ctx.sss);
    if (ctx.allowHidden) q.set('allow_hidden','1');
    const ml=String(ctx.month||'').toLowerCase();
    if (ml==='all') q.set('month','all'); else if (ctx.month) q.set('month',ctx.month);
    q.set('months','all'); q.set('_',Date.now());

    let j;
    try { const r=await fetch(PATH_TXN_URL+'?'+q.toString(),{headers:{'Accept':'application/json'}}); if(!r.ok) throw new Error(r.status); j=await r.json(); }
    catch(e){ console.error('fetchPathTx',e); if(body) body.innerHTML='<tr><td colspan="4" class="text-danger">Failed to load.</td></tr>'; return; }

    state.months = Array.isArray(j.months)?j.months:(Array.isArray(j.month_list)?j.month_list:[]);
    state.tx = Array.isArray(j.tx)?j.tx:(Array.isArray(j.transactions)?j.transactions:[]);
    state.children = Array.isArray(j.children)?j.children:(Array.isArray(j.kids)?j.kids:(Array.isArray(j.child)?j.child:[]));
    state.total = Number(j.total ?? j.net_total ?? j.sum ?? 0);
    state.magnitude_total = Number(j.magnitude_total ?? j.abs_total ?? 0);
    state.showAll = (String(j.month||'').toLowerCase()==='all') || (ml==='all');

    const totalEl=$('drawer-total'); if (totalEl) totalEl.textContent=fmtUSD(state.total);
    renderBreadcrumb();
    renderChildren();
    renderTx();
    renderMonthsAndEmit();
  }

  // Tabs (overview/keywords/actions — matches HTML data attributes)
  QSA('.drawer-tab').forEach(tab=>{
    on(tab,'click',e=>{
      e.preventDefault();
      const target = tab.getAttribute('data-tab') || 'overview';
      QSA('.drawer-tab').forEach(t=>t.classList.remove('active'));
      QSA('.drawer-pane').forEach(p=>p.style.display='none');
      tab.classList.add('active');
      const pane = QS(`.drawer-pane[data-pane="${target}"]`); if (pane) pane.style.display='block';
      if (target==='keywords') refreshKeywords();
    });
  });

  // Month select change (Glass buttons dispatch this too)
  const monthSel=$('drawer-months');
  if (monthSel){
    on(monthSel,'change',async e=>{
      const v=(e.target.value||'').trim();
      state.ctx.month=v||''; state.showAll=(String(v).toLowerCase()==='all');
      await fetchPathTx(state.ctx);
      if (v && v!=='all'){ const anchor=$(`#${monthId(v)}`); if (anchor) anchor.scrollIntoView({behavior:'smooth', block:'start'}); }
    });
  }

  // Keyword add
  const addInput=$('kw-add-input'), addBtn=$('kw-add-btn');
  if (addInput && addBtn){
    const commit=()=>{ const kw=(addInput.value||'').trim(); if(!kw) return; addKeyword(kw).then(()=>{ addInput.value=''; refreshKeywords();}); };
    on(addBtn,'click',e=>{ e.preventDefault(); commit(); });
    on(addInput,'keydown',e=>{ if(e.key==='Enter') commit(); });
  }

  // Open/Close
  let offcanvas=null;
  function ensureOC(){
    const el=$('dashCategoryManager'); if(!el) return null;
    if (window.bootstrap && window.bootstrap.Offcanvas){
      offcanvas = offcanvas || new bootstrap.Offcanvas(el);
      return offcanvas;
    }
    return null;
  }
  function openDrawer(ctx){
    const c=ctx||{};
    state.ctx.level=c.level||'category';
    state.ctx.cat=c.cat||''; state.ctx.sub=c.sub||''; state.ctx.ssub=c.ssub||''; state.ctx.sss=c.sss||'';
    state.ctx.month=c.month||''; state.ctx.allowHidden=!!c.allowHidden;
    renderBreadcrumb();
    fetchPathTx(state.ctx);
    const el=$('dashCategoryManager'); if(!el) return;
    const oc=ensureOC(); if(oc&&oc.show) oc.show(); else { el.classList.add('show'); el.style.display='block'; }
  }
  function closeDrawer(){ if(offcanvas&&offcanvas.hide) offcanvas.hide(); else { const el=$('dashCategoryManager'); if(el){ el.classList.remove('show'); el.style.display='none'; } } }

  function openCategoryManager(ctx){ openDrawer(ctx||{}); }
  openCategoryManager.__cl_neon_fit = true;
  window.openCategoryManager = openCategoryManager;

  if (!window.openDrawerForPath)     window.openDrawerForPath     = (state)=>openCategoryManager(state||{});
  if (!window.openDrawerForCategory) window.openDrawerForCategory = (cat,opts)=>openCategoryManager({ level:'category', cat:cat||'', allowHidden:!!(opts&&opts.allowHidden) });
})();
