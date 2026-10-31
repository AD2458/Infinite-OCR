'use strict';
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

let FILE = null, JOB = null, DATA = null, CUR = null;

/* ---------------- health ---------------- */
async function health() {
  try {
    const h = await (await fetch('/api/health')).json();
    $('health').innerHTML = h.key_loaded
      ? `<span class="dot on"></span>API key loaded &middot;
         ${h.cache.cached_pages} page(s) cached &mdash; cached pages are never billed again`
      : `<span class="dot off"></span>No API key. Put <code>BODHAN_API_KEY=...</code> in a
         <code>.env</code> file in the project root, then restart.`;
    const fs = $('footer-stats');
    if (fs) fs.textContent = `${h.cache.cached_pages} page(s) cached`;
  } catch {
    $('health').textContent = 'Backend unreachable.';
  }
}

/* ---------------- upload ---------------- */
let TOTAL_PAGES = 0;

function pick(f) {
  if (!f) return;
  if (!f.name.toLowerCase().endsWith('.pdf')) { alert('Please choose a PDF.'); return; }
  FILE = f;
  $('picked').textContent = `${f.name}  ·  ${(f.size/1048576).toFixed(2)} MB`;
  $('picked-row').classList.remove('hidden');
  $('drop').classList.add('hidden');
  $('go').disabled = false;

  // Probe the PDF to get the page count — free, no OCR.
  const fd = new FormData(); fd.append('file', f);
  fetch('/api/probe', { method: 'POST', body: fd })
    .then(r => r.json())
    .then(d => {
      TOTAL_PAGES = d.total_pages || 0;
      $('page-total-label').textContent = `${TOTAL_PAGES} page(s) detected`;
      $('page-selector').classList.remove('hidden');
      updateCreditNote();
    })
    .catch(() => {
      $('page-selector').classList.add('hidden');
    });
}
$('pick-clear').onclick = (e) => {
  e.stopPropagation();
  FILE = null; TOTAL_PAGES = 0;
  $('picked-row').classList.add('hidden');
  $('page-selector').classList.add('hidden');
  $('drop').classList.remove('hidden');
  $('go').disabled = true;
  $('file').value = '';
};

/* page selection logic */
function getPageSpec() {
  const mode = document.querySelector('input[name="pg-mode"]:checked')?.value || 'all';
  if (mode === 'all') return '';
  if (mode === 'first') return `1-${$('pg-first-n').value || 3}`;
  return ($('pg-spec').value || '').trim();
}

function countSelected() {
  const spec = getPageSpec();
  if (!spec || !TOTAL_PAGES) return TOTAL_PAGES;
  try {
    const parts = spec.split(',');
    const s = new Set();
    for (const p of parts) {
      const t = p.trim();
      if (!t) continue;
      if (t.includes('-')) {
        const [a, b] = t.split('-').map(Number);
        for (let i = Math.max(1, a); i <= Math.min(TOTAL_PAGES, b); i++) s.add(i);
      } else {
        const n = Number(t);
        if (n >= 1 && n <= TOTAL_PAGES) s.add(n);
      }
    }
    return s.size || TOTAL_PAGES;
  } catch { return TOTAL_PAGES; }
}

function updateCreditNote() {
  const n = countSelected();
  $('pg-credit-note').innerHTML = `<b>${n}</b> page(s) selected — <b>${n}</b> credit(s)`;
  renderChips();
}

function renderChips() {
  const spec = getPageSpec();
  if (!spec) { $('pg-chips').innerHTML = ''; return; }
  const parts = spec.split(',').map(s => s.trim()).filter(Boolean);
  $('pg-chips').innerHTML = parts.map(p =>
    `<span class="pg-chip">📄 ${p}</span>`
  ).join('');
}

document.querySelectorAll('input[name="pg-mode"]').forEach(r => {
  r.onchange = () => {
    $('pg-custom-area').classList.toggle('hidden',
      document.querySelector('input[name="pg-mode"]:checked')?.value !== 'custom');
    updateCreditNote();
  };
});
$('pg-first-n').oninput = updateCreditNote;
$('pg-spec').oninput = updateCreditNote;
$('browse').onclick = () => $('file').click();
$('file').onchange = (e) => pick(e.target.files[0]);
const drop = $('drop');
drop.onclick = (e) => { if (e.target.tagName !== 'BUTTON') $('file').click(); };
['dragenter','dragover'].forEach(ev => drop.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.add('over');
}));
['dragleave','drop'].forEach(ev => drop.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.remove('over');
}));
drop.addEventListener('drop', e => pick(e.dataTransfer.files[0]));

$('go').onclick = async () => {
  if (!FILE) return;
  $('go').disabled = true;
  const fd = new FormData();
  fd.append('file', FILE);
  fd.append('page_spec', getPageSpec());
  fd.append('table_format', $('table_format').value);
  fd.append('concurrency', $('concurrency').value || '4');

  $('progress-pane').classList.remove('hidden');
  $('result-pane').classList.add('hidden');
  $('status-line').textContent = 'Uploading...';

  try {
    const r = await fetch('/api/upload', { method:'POST', body: fd });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    JOB = (await r.json()).id;
    poll();
  } catch (e) {
    $('status-line').innerHTML = `<div class="error-box">Upload failed: ${esc(e.message)}</div>`;
    $('go').disabled = false;
  }
};

/* ---------------- poll ---------------- */
async function poll() {
  const s = await (await fetch(`/api/jobs/${JOB}`)).json();
  $('bar-fill').style.width = (s.progress*100).toFixed(1) + '%';
  $('status-line').textContent = `${s.status} \u2014 ${s.message || ''}`;
  if (s.pages_total) {
    $('cost-line').textContent =
      `${s.pages_done}/${s.pages_total} pages \u00b7 ${s.pages_billed} billed \u00b7 ${s.pages_cached} from cache`;
  }
  if (s.status === 'done') { $('go').disabled = false; return load(); }
  if (s.status === 'error') {
    $('status-line').innerHTML = `<div class="error-box">Error: ${esc(s.error)}</div>`;
    $('go').disabled = false; return;
  }
  setTimeout(poll, 900);
}

/* ---------------- results ---------------- */
async function load() {
  DATA = await (await fetch(`/api/jobs/${JOB}/result`)).json();
  $('result-pane').classList.remove('hidden');
  document.querySelectorAll('.dl').forEach(a => {
    a.href = `/api/jobs/${JOB}/export/${a.dataset.fmt}`;
    a.setAttribute('download', '');
  });
  const r = DATA.report;
  const cr = (r.clean_rate * 100).toFixed(0);
  $('summary').innerHTML = `
    <div class="stat">
      <span class="stat-icon">📄</span><b>${DATA.pages.length}</b>pages
    </div>
    <div class="stat">
      <span class="stat-icon">🧱</span><b>${r.total_blocks}</b>blocks
    </div>
    <div class="stat">
      <span class="stat-icon">🔗</span><b>${DATA.links.length}</b>stitches
    </div>
    <div class="stat ${r.flagged_blocks ? '' : 'highlight'}">
      <span class="stat-icon">${r.flagged_blocks ? '⚠️' : '✅'}</span>
      <b>${r.flagged_blocks}</b>flagged
    </div>
    <div class="stat highlight">
      <span class="stat-icon">🎯</span><b>${cr}%</b>clean
    </div>
    <div class="stat">
      <span class="stat-icon">🔤</span><b>${esc(r.doc_script)}</b>script
    </div>`;
  renderDoc(); renderLinks(); renderQueue(); renderBlocks(); renderSide();
}

/* ---------------- side-by-side ---------------- */
let SBS_PAGE = 0, SEL = null;

function pageBlocks(p) {
  return DATA.blocks.filter(b => b.page === p && !b.furniture && b.text.trim());
}

let SBS_ZOOM = 100;

function renderThumbs() {
  if (!DATA) return;
  $('sbs-thumbs').innerHTML = DATA.pages.map((p, i) =>
    `<img class="thumb${i === SBS_PAGE ? ' active' : ''}" src="/api/pages/${p.sha256}.png"
          data-idx="${i}" alt="p${i + 1}" loading="lazy">`
  ).join('');
  $('sbs-thumbs').querySelectorAll('.thumb').forEach(t =>
    t.onclick = () => { SBS_PAGE = +t.dataset.idx; renderSide(); }
  );
}

function renderSide() {
  if (!DATA || !DATA.pages.length) return;
  SBS_PAGE = Math.max(0, Math.min(SBS_PAGE, DATA.pages.length - 1));
  const pg = DATA.pages[SBS_PAGE];
  $('sbs-label').textContent = `page ${SBS_PAGE + 1} / ${DATA.pages.length}`;
  $('sbs-img').src = `/api/pages/${pg.sha256}.png`;
  $('sbs-wrap').style.transform = `scale(${SBS_ZOOM / 100})`;
  $('sbs-zoom').textContent = SBS_ZOOM + '%';
  renderThumbs();

  const blocks = pageBlocks(SBS_PAGE);

  // Overlay boxes are positioned in percentages of the page, so they stay
  // aligned no matter how the browser scales the image.
  $('sbs-boxes').innerHTML = blocks.map(b => {
    const [x0, y0, x1, y1] = b.bbox;
    const L = (x0 / pg.width) * 100, T = (y0 / pg.height) * 100;
    const W = ((x1 - x0) / pg.width) * 100, H = ((y1 - y0) / pg.height) * 100;
    const flag = b.flags.length ? ' flagged' : '';
    return `<div class="obox${flag}" data-id="${esc(b.id)}" title="${esc(b.id)}"
      style="left:${L}%;top:${T}%;width:${W}%;height:${H}%"></div>`;
  }).join('');

  $('sbs-text').innerHTML = blocks.map(b => {
    const lock = b.locked ? '<span class="lockpill">locked</span>' : '';
    const body = b.text.includes('<table')
      ? b.text
      : esc(b.text);
    return `<div class="tblk" data-id="${esc(b.id)}" dir="${b.direction}">
        <div class="tmeta">${esc(b.id)} &middot; ${esc(b.type)} &middot;
             conf ${Number(b.conf).toFixed(2)}${lock}</div>${body}</div>`;
  }).join('');

  document.querySelectorAll('#sbs-boxes .obox, #sbs-text .tblk').forEach(el => {
    el.onclick = () => selectBlock(el.dataset.id);
    el.ondblclick = () => openBlock(el.dataset.id);
  });
  if (SEL) selectBlock(SEL, false);
}

function selectBlock(id, scroll = true) {
  SEL = id;
  document.querySelectorAll('.obox,.tblk').forEach(el =>
    el.classList.toggle('sel', el.dataset.id === id));
  if (!scroll) return;
  const t = document.querySelector(`.tblk[data-id="${id}"]`);
  if (t) t.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  const o = document.querySelector(`.obox[data-id="${id}"]`);
  if (o) o.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

$('sbs-prev').onclick = () => { SBS_PAGE--; renderSide(); };
$('sbs-next').onclick = () => { SBS_PAGE++; renderSide(); };
$('sbs-zin').onclick = () => { SBS_ZOOM = Math.min(300, SBS_ZOOM + 25); renderSide(); };
$('sbs-zout').onclick = () => { SBS_ZOOM = Math.max(50, SBS_ZOOM - 25); renderSide(); };
$('sbs-showboxes').onchange = (e) => {
  $('sbs-boxes').classList.toggle('hide', !e.target.checked);
};

/* Source view uses textContent (never innerHTML): it must show markup
   literally, and OCR output is untrusted. The preview below is server-side
   sanitised before it is injected. */
function renderDoc() {
  $('doc-out').textContent = DATA.markdown;
  fetch(`/api/jobs/${JOB}/preview`)
    .then(r => r.text())
    .then(h => { $('doc-preview').innerHTML = h; })
    .catch(() => { $('doc-preview').textContent = 'Preview unavailable.'; });
}

$('v-preview').onclick = () => {
  $('v-preview').classList.add('active'); $('v-source').classList.remove('active');
  $('doc-preview').classList.remove('hidden'); $('doc-out').classList.add('hidden');
};
$('v-source').onclick = () => {
  $('v-source').classList.add('active'); $('v-preview').classList.remove('active');
  $('doc-out').classList.remove('hidden'); $('doc-preview').classList.add('hidden');
};
$('v-expand').onclick = () => {
  const tab = $('tab-doc');
  tab.classList.toggle('expanded');
  $('v-expand').textContent = tab.classList.contains('expanded') ? '⛶' : '⛶';
  $('v-expand').title = tab.classList.contains('expanded') ? 'Exit fullscreen' : 'Toggle fullscreen (F)';
};

function renderLinks() {
  const el = $('links-out');
  if (!DATA.links.length) {
    el.innerHTML = '<p class="empty">No cross-page merges were proposed.</p>'; return;
  }
  el.innerHTML = DATA.links.map(l => `
    <div class="row">
      <div class="grow">
        <span class="pill">${esc(l.kind)}</span>
        <span class="pill ${l.confidence>=0.8?'ok':'warn'}">conf ${l.confidence}</span>
        ${l.accepted?'':'<span class="pill off">rejected</span>'}
        <div class="meta"><span class="bid">${esc(l.from)}</span> &rarr;
             <span class="bid">${esc(l.to)}</span></div>
        <div class="reason">${esc(l.reason)}</div>
      </div>
      <button onclick="toggleLink('${esc(l.from)}')">${l.accepted?'Reject':'Restore'}</button>
    </div>`).join('');
}

function renderQueue() {
  const el = $('queue-out');
  if (!DATA.queue.length) {
    el.innerHTML = '<p class="empty">Nothing flagged \u2014 near-zero cleanup.</p>'; return;
  }
  el.innerHTML = DATA.queue.map(q => `
    <div class="row">
      <div class="grow">
        <span class="bid">${esc(q.id)}</span>
        <span class="pill">p${q.page}</span>
        <span class="pill">${esc(q.type)}</span>
        <span class="pill ${q.conf<0.6?'bad':'warn'}">conf ${Number(q.conf).toFixed(2)}</span>
        ${q.flags.map(f=>`<span class="pill warn">${esc(f)}</span>`).join('')}
        <div class="btext">${esc(q.preview)}</div>
      </div>
      <button onclick="openBlock('${esc(q.id)}')">Fix</button>
    </div>`).join('');
}

function renderBlocks() {
  $('blocks-out').innerHTML = DATA.blocks.map(b => `
    <div class="row">
      <div class="grow">
        <span class="bid">${esc(b.id)}</span>
        <span class="pill">${esc(b.type)}</span>
        <span class="pill">${esc(b.script)}${b.direction==='rtl'?' rtl':''}</span>
        ${b.furniture?'<span class="pill off">furniture</span>':''}
        ${b.source!=='ocr'?`<span class="pill ok">${esc(b.source)} v${b.version}</span>`:''}
        ${b.flags.map(f=>`<span class="pill warn">${esc(f)}</span>`).join('')}
        <div class="btext">${esc(b.text.slice(0,300))}</div>
      </div>
      <button onclick="openBlock('${esc(b.id)}')">Edit</button>
    </div>`).join('');
}

/* ---------------- tabs ---------------- */
document.querySelectorAll('.tab').forEach(t => t.onclick = () => {
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  t.classList.add('active');
  document.querySelectorAll('.tabpane').forEach(p => p.classList.add('hidden'));
  $('tab-' + t.dataset.tab).classList.remove('hidden');
});

/* ---------------- block editor ---------------- */
window.openBlock = (id) => {
  const b = DATA.blocks.find(x => x.id === id);
  if (!b) return;
  CUR = b;
  $('m-title').textContent = `${b.id} \u00b7 ${b.type}`;
  $('m-meta').innerHTML =
    `page ${b.page} \u00b7 conf ${Number(b.conf).toFixed(2)} \u00b7 ${esc(b.script)}
     (${esc(b.direction)}) \u00b7 v${b.version} (${esc(b.source)})`;
  $('m-text').value = b.text;
  $('m-text').dir = b.direction === 'rtl' ? 'rtl' : 'ltr';
  $('m-msg').textContent = '';

  // Show a rendered view of the text; toggle to raw for editing.
  const rendered = $('m-text-rendered');
  const textarea = $('m-text');
  const toggle = $('m-text-toggle');
  if (b.text.includes('<table') || b.text.includes('<')) {
    const parsed = new DOMParser().parseFromString(b.text, 'text/html');
    rendered.innerHTML = '';
    Array.from(parsed.body.childNodes).forEach(n => rendered.appendChild(n.cloneNode(true)));
  } else {
    rendered.textContent = b.text;
  }
  rendered.dir = b.direction === 'rtl' ? 'rtl' : 'ltr';
  rendered.classList.remove('hidden');
  textarea.classList.add('hidden');
  toggle.textContent = 'Edit raw';
  toggle.onclick = () => {
    const showRaw = textarea.classList.contains('hidden');
    textarea.classList.toggle('hidden', !showRaw);
    rendered.classList.toggle('hidden', showRaw);
    toggle.textContent = showRaw ? 'Show preview' : 'Edit raw';
    if (showRaw) textarea.focus();
  };
  // Crop to the block's bbox rather than showing the whole page -- verifying
  // OCR accuracy means comparing text against *that region*, not hunting for
  // it on a full page.
  $('m-img').src = `/api/jobs/${JOB}/blocks/${b.id}/crop.png?v=${b.version}`;
  // Tables are unreadable as raw HTML, so show them rendered too.
  const tbl = $('m-table');
  if (b.type === 'Table' && b.text.includes('<')) {
    fetch(`/api/jobs/${JOB}/preview`).then(() => {});
    tbl.innerHTML = '';
    const frame = document.createElement('div');
    frame.className = 'tprev';
    // Safe: built from a parsed document, not injected markup.
    const parsed = new DOMParser().parseFromString(b.text, 'text/html');
    const t = parsed.querySelector('table');
    if (t) { frame.appendChild(t); tbl.appendChild(frame); }
  } else { tbl.innerHTML = ''; }
  $('modal').classList.remove('hidden');
};
$('modal-close').onclick = () => {
  $('modal').classList.add('hidden');
  $('modal').classList.remove('expanded');
};
$('modal').onclick = (e) => {
  if (e.target === $('modal')) {
    $('modal').classList.add('hidden');
    $('modal').classList.remove('expanded');
  }
};
$('m-expand').onclick = () => {
  $('modal').classList.toggle('expanded');
};

/* --- tier 0: free --- */
$('m-save').onclick = async () => {
  const fd = new FormData(); fd.append('text', $('m-text').value);
  await act(`/api/jobs/${JOB}/blocks/${CUR.id}/text`, fd,
            'Saved and locked. No later pass can overwrite this.');
};
$('m-unlock').onclick = async () => {
  const fd = new FormData(); fd.append('locked', 'false');
  await act(`/api/jobs/${JOB}/blocks/${CUR.id}/lock`, fd,
            'Unlocked -- automated passes may overwrite it again.');
};
$('m-break').onclick = async () => {
  const r = await fetch(`/api/jobs/${JOB}/links/${CUR.id}/toggle`, { method: 'POST' });
  if (!r.ok) { $('m-msg').textContent = 'No stitch starts at this block.';
               $('m-msg').className = 'msg bad'; return; }
  const d = await r.json();
  $('m-msg').textContent = d.accepted ? 'Stitch restored.' : 'Stitch broken.';
  $('m-msg').className = 'msg ok';
  await load();
};
$('m-merge').onclick = async () => {
  // Merge into the next flow block in reading order.
  const flow = DATA.blocks.filter(b => !b.furniture && b.text.trim());
  const i = flow.findIndex(b => b.id === CUR.id);
  if (i < 0 || i + 1 >= flow.length) {
    $('m-msg').textContent = 'No following block to merge into.';
    $('m-msg').className = 'msg bad'; return;
  }
  const fd = new FormData();
  fd.append('from_block', CUR.id);
  fd.append('to_block', flow[i + 1].id);
  fd.append('kind', CUR.type === 'Table' ? 'table' : 'sentence');
  await act(`/api/jobs/${JOB}/links/merge`, fd,
            `Forced merge into ${flow[i + 1].id}.`);
};

/* --- tier 1/2: costs a credit --- */
async function redo(opts, label) {
  if (!confirm(`${label}\n\nThis costs one page-billing unit. Continue?`)) return;
  $('m-msg').textContent = 'Re-OCRing...'; $('m-msg').className = 'msg';
  const fd = new FormData();
  fd.append('table_format', 'markdown');
  Object.entries(opts).forEach(([k, v]) => fd.append(k, String(v)));
  const d = await act(`/api/jobs/${JOB}/blocks/${CUR.id}/redo`, fd, 'Re-OCR complete.');
  if (d && d.text !== undefined) $('m-text').value = d.text;
}
$('m-pad').onclick = () => redo({ pad: 0.12 },
  'Re-crop with 10% extra padding and re-OCR.');
$('m-hidpi').onclick = () => redo({ dpi: 600, pad: 0.04 },
  'Re-render this region from the PDF at 600 DPI and re-OCR.');

async function act(url, fd, okMsg) {
  const id = CUR ? CUR.id : null;
  try {
    const r = await fetch(url, { method:'POST', body: fd });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    $('m-msg').textContent = okMsg; $('m-msg').className = 'msg ok';
    await load();
    // Re-point CUR at the refreshed block so a second edit is not stale.
    if (id) CUR = DATA.blocks.find(x => x.id === id) || CUR;
    return d;
  } catch (e) {
    $('m-msg').textContent = e.message; $('m-msg').className = 'msg bad';
    return null;
  }
}

window.toggleLink = async (from) => {
  await fetch(`/api/jobs/${JOB}/links/${from}/toggle`, { method:'POST' });
  await load();
};

/* ---------------- keyboard shortcuts ---------------- */
document.addEventListener('keydown', e => {
  // Esc closes expanded preview, modal, or expanded modal (in that order)
  if (e.key === 'Escape') {
    if ($('modal').classList.contains('expanded')) {
      $('modal').classList.remove('expanded'); return;
    }
    if (!$('modal').classList.contains('hidden')) {
      $('modal').classList.add('hidden');
      $('modal').classList.remove('expanded'); return;
    }
    if ($('tab-doc').classList.contains('expanded')) {
      $('tab-doc').classList.remove('expanded'); return;
    }
    return;
  }
  // F toggles preview fullscreen (when not typing)
  if (e.key === 'f' && !$('modal').classList.contains('hidden') === false &&
      document.activeElement?.tagName !== 'TEXTAREA' &&
      document.activeElement?.tagName !== 'INPUT') {
    $('v-expand')?.click(); return;
  }
  // Ctrl+S in modal saves the correction
  if ((e.ctrlKey || e.metaKey) && e.key === 's' && !$('modal').classList.contains('hidden')) {
    e.preventDefault();
    $('m-save').click();
    return;
  }
  // Arrow keys navigate side-by-side pages (when not in a text field)
  if (document.activeElement?.tagName === 'TEXTAREA' || document.activeElement?.tagName === 'INPUT')
    return;
  if (e.key === 'ArrowLeft') { SBS_PAGE--; renderSide(); }
  if (e.key === 'ArrowRight') { SBS_PAGE++; renderSide(); }
});

/* ---------------- dark mode toggle ---------------- */
function applyDark(on) {
  document.body.classList.toggle('dark', on);
  $('dark-toggle').textContent = on ? '☀️' : '🌙';
  localStorage.setItem('dark', on ? '1' : '0');
}
$('dark-toggle').onclick = () => applyDark(!document.body.classList.contains('dark'));
// Restore preference
if (localStorage.getItem('dark') === '1' ||
    (!localStorage.getItem('dark') && matchMedia('(prefers-color-scheme:dark)').matches)) {
  applyDark(true);
}

/* ---------------- cache clearing ---------------- */
async function clearCache(scope, confirmMsg) {
  if (!confirm(confirmMsg)) return;
  const fd = new FormData(); fd.append('scope', scope);
  try {
    const r = await fetch('/api/cache/clear', { method: 'POST', body: fd });
    const d = await r.json();
    const rm = d.removed;
    $('clear-msg').textContent =
      `Removed ${rm.pages} image(s), ${rm.crops} crop(s)` +
      (rm.raw ? `, ${rm.raw} response(s)` : '');
    setTimeout(() => { $('clear-msg').textContent = ''; }, 5000);
    health();
  } catch (e) {
    $('clear-msg').textContent = 'Failed: ' + e.message;
  }
}
$('clear-images').onclick = () => clearCache('images',
  'Delete cached page images and crops?\n\n' +
  'These rebuild for free — OCR responses are kept, so no credits are spent.');
$('clear-all').onclick = () => clearCache('all',
  'Delete ALL cached data, including OCR responses?\n\n' +
  'Re-processing these PDFs will be BILLED AGAIN.');

/* ---------------- error display helper ---------------- */
function showError(el, msg) {
  el.innerHTML = `<div class="error-box">${esc(msg)}</div>`;
}

health();
