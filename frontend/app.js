let sid = null;
let sessions = [];
let allPersonas = [];
let busy = false;
const chat = document.getElementById('chat');
const list = document.getElementById('chatlist');
const meta = document.getElementById('chatmeta');
const personaSel = document.getElementById('persona');
const toastEl = document.getElementById('toast');
const sendBtn = document.getElementById('send');
const dialInk = document.getElementById('dialink');
const modeDesc = document.getElementById('modedesc');
const tools = ['btnContinue', 'btnRegen', 'btnUndo', 'btnSummary', 'btnExport']
  .map(id => document.getElementById(id));
const btnChoices = document.getElementById('btnChoices');
const suggestbar = document.getElementById('suggestbar');
let choicesOn = localStorage.getItem('anew_choices') !== '0';
let lastChoices = [];
let toastT = null;

const MODES = {
  oc: '◈ Operative — your custom dossier. New hire under Perlica & Team Z7.',
  admin: '⬢ Endministrator — command Endfield Industries. Give orders, bear consequences.',
  canon: '⬣ Canon operator — borrow Perlica, Chen, Wulfgard & co. The world reacts.',
};
const MODE_IDX = {oc: 0, admin: 1, canon: 2};
const SPLASH_LINES = [
  'Handshake with OMV Dijiang…',
  'Syncing AIC lattice…',
  'Calibrating Protocol-Originium…',
  'Verifying operative dossier…',
  'Link stable. Welcome to Talos-II.',
];

function toast(msg) {
  toastEl.textContent = msg;
  toastEl.classList.add('show');
  clearTimeout(toastT);
  toastT = setTimeout(() => toastEl.classList.remove('show'), 2600);
}

const radioVal = (name) => document.querySelector(`input[name=${name}]:checked`).value;
const setRadio = (name, v) => {
  const el = document.querySelector(`input[name=${name}][value="${v}"]`);
  if (!el) return;
  if (!el.checked) { el.checked = true; if (name === 'mode') syncModeUI(); }
  else if (name === 'mode') syncModeUI();
};
const setTools = (on) => tools.forEach(b => b.disabled = !on);

function syncModeUI() {
  const m = radioVal('mode');
  document.body.dataset.mode = m;
  dialInk.style.left = `calc(4px + ${MODE_IDX[m]} * (100% - 8px) / 3)`;
  modeDesc.classList.add('swap');
  setTimeout(() => { modeDesc.textContent = MODES[m]; modeDesc.classList.remove('swap'); }, 140);
  syncDossierRow();
}

function syncDossierRow() {
  document.body.classList.toggle('has-dossier',
    document.body.dataset.mode === 'oc' && !!personaSel.value);
}

for (const r of document.querySelectorAll('input[name=mode]')) r.addEventListener('change', syncModeUI);

document.getElementById('deploytoggle').onclick = () =>
  document.body.classList.toggle('deploy-collapsed');
document.getElementById('sidebartoggle').onclick = () =>
  document.body.classList.toggle('sb-hidden');

const fmtTime = (ts) => ts ? new Date(ts * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) : '';

/** Minimal markdown: escape HTML, then ***bold+italic***, **bold**, *italic*. */
function mdLite(src) {
  let h = src.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  h = h.replace(/\*\*\*([\s\S]+?)\*\*\*/g, '<b><i>$1</i></b>');
  h = h.replace(/\*\*([\s\S]+?)\*\*/g, '<b>$1</b>');
  h = h.replace(/(^|[\s(>"'—–-])\*([^*\n]+?)\*(?=[\s).,!?;:'"—–-]|$)/g, '$1<i>$2</i>');
  return h;
}

function add(text, cls, mid = null, ts = null) {
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  d._raw = text;
  if (mid) d.dataset.mid = mid;
  const who = document.createElement('div');
  who.className = 'who';
  const label = document.createElement('span');
  label.textContent = cls === 'user' ? '▸ Operative uplink' : '◆ Endfield GM';
  const time = document.createElement('span');
  time.className = 'ts';
  time.textContent = fmtTime(ts || Date.now() / 1000);
  who.append(label, time);
  const body = document.createElement('div');
  body.className = 'body';
  if (cls === 'user') body.textContent = text;
  else body.innerHTML = mdLite(text);
  const acts = document.createElement('div');
  acts.className = 'acts';
  if (cls !== 'user') {
    const cp = document.createElement('button');
    cp.textContent = '⧉ copy';
    cp.onclick = async (e) => {
      e.stopPropagation();
      const raw = e.target.closest('.msg')._raw || body.innerText;
      try { await navigator.clipboard.writeText(raw); toast('Copied to clipboard.'); }
      catch { toast('Copy blocked by browser.'); }
    };
    acts.appendChild(cp);
  }
  if (mid) {
    const del = document.createElement('button');
    del.textContent = '× delete';
    del.className = 'danger';
    del.onclick = async (e) => {
      e.stopPropagation();
      const r = await fetch(`/api/message?session_id=${sid}&id=${mid}`, {method: 'DELETE'});
      if (r.ok) {
        d.classList.add('removing');
        setTimeout(() => d.remove(), 300);
        toast('Transmission deleted.');
        loadChats();
      } else toast('Delete failed.');
    };
    acts.appendChild(del);
  }
  d.append(who, body, acts);
  chat.appendChild(d);
  d.scrollIntoView({block: 'end', behavior: 'smooth'});
  return {root: d, body};
}

function typingBubble() {
  const b = add('', 'gm typing');
  b.body.innerHTML = '<span class="dots">UPLINK</span>';
  return b;
}

/** Stream an SSE GM reply into a fresh bubble. Returns {text, choices}. */
async function streamReply(url, payload) {
  const bubble = typingBubble();
  const finish = (text, choices) => ({text, choices});
  try {
    const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      bubble.body.textContent = '[Link refused: ' + (j.error || r.status) + ']';
      return finish('', []);
    }
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = '', full = '', choices = [], raw = '';
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      const parts = buf.split('\n\n'); buf = parts.pop();
      for (const p of parts) {
        if (!p.startsWith('data: ')) continue;
        const j = JSON.parse(p.slice(6));
        if (j.delta) {
          if (!full) { bubble.root.classList.remove('typing'); bubble.body.textContent = ''; }
          full += j.delta; bubble.body.innerHTML = mdLite(full);
          bubble.root.scrollIntoView({block: 'end'});
        } else if (j.done) {
          choices = j.choices || [];
          raw = j.raw || '';
        }
      }
    }
    if (!full) {
      bubble.body.textContent = '[No signal — check uplink / credits.]';
      return finish('', []);
    }
    if (raw) full = full.replace(raw, '').trimEnd();
    if (full) {
      // Self-heal: server (possibly stale) sent no choices — salvage locally
      if (!choices.length) {
        const salv = clientChoices(full);
        if (salv.choices.length >= 2) {
          full = salv.cleaned;
          choices = salv.choices;
        }
      }
      bubble.root._raw = full;
      bubble.body.innerHTML = mdLite(full);
    }
    return finish(full, choices);
  } catch (err) {
    bubble.body.textContent = '[Link error: ' + String(err).slice(0, 120) + ']';
    return finish('', []);
  }
}

function showChoices(arr) {
  lastChoices = arr || [];
  suggestbar.innerHTML = '';
  if (!lastChoices.length || !choicesOn) return;
  const head = document.createElement('div');
  head.className = 'sughead';
  head.textContent = 'Tactical options — send or edit';
  suggestbar.appendChild(head);
  for (const c of lastChoices) {
    const wrap = document.createElement('div');
    wrap.className = 'sug';
    const send = document.createElement('button');
    send.className = 'sug-send';
    send.textContent = c;
    send.onclick = () => sendUserText(c);
    const edit = document.createElement('button');
    edit.className = 'sug-edit';
    edit.title = 'Load into composer to edit';
    edit.textContent = '✎';
    edit.onclick = () => {
      document.getElementById('input').value = c;
      document.getElementById('input').focus();
      toast('Loaded — edit, then Send.');
    };
    wrap.append(send, edit);
    suggestbar.appendChild(wrap);
  }
  suggestbar.scrollIntoView({block: 'nearest', behavior: 'smooth'});
}

function syncChoicesBtn() {
  btnChoices.classList.toggle('on', choicesOn);
  btnChoices.textContent = choicesOn ? '👁 Choices' : 'Choices off';
}

btnChoices.onclick = () => {
  choicesOn = !choicesOn;
  localStorage.setItem('anew_choices', choicesOn ? '1' : '0');
  syncChoicesBtn();
  showChoices(lastChoices);
  if (!choicesOn) toast('Suggestions hidden.');
};

async function sendUserText(text) {
  if (busy || !sid || !text) return;
  const inp = document.getElementById('input');
  inp.value = '';
  busy = true; sendBtn.disabled = true; setTools(false);
  suggestbar.innerHTML = '';
  add(text, 'user');
  const res = await streamReply('/api/chat', {session_id: sid, text, persona_id: personaSel.value});
  busy = false; sendBtn.disabled = false; setTools(true);
  showChoices(res.choices);
  refreshTrack();
  loadChats();
}

/** Client mirror of the server fallback: salvage buttons from prose choice tails
    (covers turns saved before the parser learned a shape). */
function clientChoices(full) {
  const headRe = /^\s*\*{0,3}\s*["'“”‘’]*\s*(choices|options|tactical options|what do you do)\b[^a-z0-9]*$/i;
  const owdyd = /or what do you do\??/i;
  const optRe = /^\s*(?:\d{1,2}\s*[.)\-:]\s*|[-•*]\s+)(.+)$/;
  const clean = (c) => c.replace(/\*\*|__/g, '').replace(/[*`]/g, '')
    .replace(/\s+/g, ' ').trim().replace(/^["'“”‘’–—-]+|["'“”‘’–—-]+$/g, '');
  const lines = full.split('\n');
  let head = -1;
  lines.forEach((ln, i) => { if (headRe.test(ln)) head = i; });
  const fromTail = (tail) => {
    const out = [];
    for (const ln of tail) {
      const s = ln.trim();
      if (!s || owdyd.test(s)) continue;
      const m = s.match(optRe);
      const c = clean(m ? m[1] : s.replace(/^[-•*]\s+/, ''));
      if (c) out.push(c);
    }
    return out;
  };
  if (head >= 0) {
    const got = fromTail(lines.slice(head + 1)).slice(0, 4);
    if (got.length >= 2) return {cleaned: lines.slice(0, head).join('\n').trimEnd(), choices: got};
  }
  // numbered-tail safety net (never dash-led: those may be dialogue)
  const numbered = [];
  let start = -1;
  for (let i = lines.length - 1; i >= 0; i--) {
    const s = lines[i].trim();
    if (!s || owdyd.test(s)) continue;
    const m = s.match(/^\s*\d{1,2}\s*[.)]\s*(.+)$/);
    if (m) { const c = clean(m[1]); if (c) { numbered.unshift(c); start = i; } }
    else break;
  }
  if (numbered.length >= 2) {
    return {cleaned: lines.slice(0, start).join('\n').trimEnd(), choices: numbered.slice(0, 4)};
  }
  return {cleaned: full, choices: []};
}

function splash() {
  const sp = document.getElementById('splash');
  const fill = document.getElementById('splashfill');
  const status = document.getElementById('splashstatus');
  sp.hidden = false;
  sp.classList.remove('fade');
  let i = 0;
  fill.style.width = '8%';
  status.textContent = SPLASH_LINES[0];
  const t = setInterval(() => {
    i++;
    if (i < SPLASH_LINES.length) {
      status.textContent = SPLASH_LINES[i];
      fill.style.width = (8 + (i / (SPLASH_LINES.length - 1)) * 92) + '%';
    } else {
      clearInterval(t);
      sp.classList.add('fade');
      setTimeout(() => { sp.hidden = true; }, 550);
    }
  }, 340);
}

async function loadPersonas(selected = '') {
  const r = await fetch('/api/personas').then(r => r.json());
  allPersonas = r.personas || [];
  personaSel.innerHTML = '<option value="">— none —</option>';
  for (const p of r.personas) {
    const o = document.createElement('option');
    o.value = p.id;
    o.textContent = `${p.name} · ${p.race || '?'} · ${p.role || '?'}`;
    if (p.id === selected) o.selected = true;
    personaSel.appendChild(o);
  }
  syncDossierRow();
}

function renderChats(filter = '') {
  const f = filter.trim().toLowerCase();
  list.innerHTML = '';
  const shown = sessions.filter(s =>
    !f || (s.title || s.id).toLowerCase().includes(f) || (s.preview || '').toLowerCase().includes(f));
  if (!shown.length) {
    const e = document.createElement('div');
    e.className = 'empty';
    e.textContent = sessions.length ? 'No deployments match filter.' : 'No deployments yet. Configure above and hit Deploy.';
    list.appendChild(e);
    return;
  }
  for (const s of shown) {
    const b = document.createElement('div');
    b.className = 'chatitem' + (s.id === sid ? ' active' : '');
    const del = document.createElement('button');
    del.className = 'del'; del.title = 'delete'; del.textContent = '×';
    del.onclick = async (e) => {
      e.stopPropagation();
      if (!del.classList.contains('armed')) {
        del.classList.add('armed'); del.textContent = 'confirm?';
        setTimeout(() => { del.classList.remove('armed'); del.textContent = '×'; }, 3000);
        return;
      }
      await fetch('/api/session?session_id=' + s.id, {method: 'DELETE'});
      toast('Deployment deleted.');
      if (sid === s.id) { sid = null; chat.innerHTML = ''; meta.innerHTML = ''; renderTrack({}); setTools(false); }
      await loadChats();
    };
    const tags = document.createElement('div');
    const t1 = document.createElement('span'); t1.className = 'tag'; t1.textContent = s.mode;
    const t2 = document.createElement('span'); t2.className = 'tag'; t2.textContent = 'zone ' + s.starter;
    const t3 = document.createElement('span'); t3.className = 'tag'; t3.textContent = s.msg_count + ' msgs';
    tags.append(t1, t2, t3);
    const title = document.createElement('b'); title.textContent = s.title || s.id;
    const sub = document.createElement('small');
    sub.textContent = s.preview || '—';
    b.append(del, tags, title, sub);
    b.onclick = () => openChat(s.id);
    list.appendChild(b);
  }
}

async function loadChats() {
  const r = await fetch('/api/sessions').then(r => r.json());
  sessions = r.sessions;
  renderChats(document.getElementById('search').value);
}

function renderMeta(s) {
  meta.innerHTML = '';
  const t = document.createElement('span'); t.className = 'm-title'; t.textContent = s.title || s.id;
  meta.appendChild(t);
  for (const [txt, amber] of [[s.mode, true], ['zone ' + s.starter, true], [s.tone, false]]) {
    const c = document.createElement('span'); c.className = 'chip' + (amber ? ' amber' : '');
    c.textContent = txt; meta.appendChild(c);
  }
  const p = personaSel.options[personaSel.selectedIndex];
  if (personaSel.value && p) {
    const dossier = (allPersonas.find(x => x.id === personaSel.value) || {});
    if (dossier.portrait_url) {
      const img = document.createElement('img');
      img.className = 'thumb'; img.src = dossier.portrait_url; img.alt = '';
      meta.appendChild(img);
    }
    const c = document.createElement('span'); c.className = 'chip'; c.textContent = '◈ ' + p.textContent.split(' · ')[0];
    meta.appendChild(c);
  }
  if (s.summary) {
    const c = document.createElement('span'); c.textContent = s.summary.slice(0, 110) + '…';
    meta.appendChild(c);
  }
}

const TRACK_KEYS = ['location', 'party', 'injuries', 'inventory', 'threads'];

function renderTrack(track) {
  track = track || {};
  for (const el of document.querySelectorAll('#tracker .tk')) {
    const v = (track[el.dataset.key] || '').trim() || '—';
    const i = el.querySelector('i');
    i.textContent = v;
    i.classList.toggle('none', v === '—');
  }
}

async function refreshTrack() {
  if (!sid) return;
  try {
    const r = await fetch('/api/state?session_id=' + sid).then(r => r.json());
    renderTrack(r.track);
  } catch {}
}

for (const el of document.querySelectorAll('#tracker .tk')) {
  el.onclick = async () => {
    if (!sid) { toast('Open or deploy a chat first.'); return; }
    const key = el.dataset.key;
    const cur = el.querySelector('i').textContent;
    const v = prompt(`Set ${key} (empty clears):`, cur === '—' ? '' : cur);
    if (v === null) return;
    const r = await fetch('/api/track', {method: 'PATCH', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: sid, [key]: v.trim()})});
    if (r.ok) { renderTrack((await r.json()).track); toast('Tracker updated.'); }
    else toast('Tracker update failed.');
  };
}

async function openChat(id) {
  sid = id;
  setTools(true);
  suggestbar.innerHTML = '';
  lastChoices = [];
  const r = await fetch('/api/state?session_id=' + id).then(r => r.json());
  chat.innerHTML = '';
  setRadio('mode', r.session.mode);
  setRadio('starter', r.session.starter);
  setRadio('tone', r.session.tone);
  document.getElementById('title').value = r.session.title || '';
  document.getElementById('character').value = r.session.character || '';
  await loadPersonas(r.session.persona_id || '');
  renderMeta(r.session);
  renderTrack(r.track);
  if (!r.history.length) add('Uplink established. Transmit your first action to begin the operation.', 'gm');
  let lastBubble = null;
  for (const m of r.history) lastBubble = add(m.content, m.role === 'user' ? 'user' : 'gm', m.id, m.created_at);
  // Backfill: older turns saved before the parser learned a shape still get buttons
  const last = r.history[r.history.length - 1];
  if (last && last.role !== 'user' && lastBubble) {
    const salv = clientChoices(last.content);
    if (salv.choices.length >= 2) {
      lastBubble.root._raw = salv.cleaned;
      lastBubble.body.innerHTML = mdLite(salv.cleaned);
      showChoices(salv.choices);
    }
  }
  document.body.classList.add('deploy-collapsed');
  await loadChats();
}

fetch('/api/config').then(r => r.json()).then(c => {
  document.getElementById('modelstext').textContent = `${c.model_main} · ${c.model_cheap}`;
  document.getElementById('models').title = 'backend build: ' + (c.build || 'unknown — restart server');
  if (!c.build) toast('Backend predates buttons — restart your server.');
}).catch(() => {
  document.getElementById('modelstext').textContent = 'link offline';
});

document.getElementById('search').oninput = (e) => renderChats(e.target.value);

document.getElementById('newchat').onclick = () => {
  sid = null; setTools(false);
  suggestbar.innerHTML = '';
  lastChoices = [];
  renderTrack({});
  chat.innerHTML = ''; meta.innerHTML = '';
  document.getElementById('title').value = '';
  document.body.classList.remove('deploy-collapsed');
  add('New operation. Set command role, drop zone and dossier, then Deploy.', 'gm');
  loadChats();
};

document.getElementById('start').onclick = async (e) => {
  const btn = e.target;
  const mode = radioVal('mode');
  if (mode === 'oc' && !personaSel.value && !document.getElementById('character').value.trim()) {
    toast('Operative needs a dossier — pick one or enter a callsign.');
    return;
  }
  btn.disabled = true;
  splash();
  try {
    const body = {
      title: document.getElementById('title').value,
      mode,
      persona_id: mode === 'oc' ? personaSel.value : '',
      starter: radioVal('starter'),
      tone: radioVal('tone'),
      character: document.getElementById('character').value,
    };
    const r = await fetch('/api/new-game', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const j = await r.json();
    toast('Deployed. Uplink live.');
    await openChat(j.session_id);
  } finally { btn.disabled = false; }
};

personaSel.onchange = async () => {
  const p = personaSel.options[personaSel.selectedIndex];
  if (personaSel.value && p && !document.getElementById('character').value.trim()) {
    document.getElementById('character').value = p.textContent.split(' · ')[0];
  }
  syncDossierRow();
  if (!sid) { toast('Dossier will attach to your next deployment.'); return; }
  await fetch('/api/session', {method: 'PATCH', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({session_id: sid, persona_id: personaSel.value})});
  const st = sessions.find(s => s.id === sid);
  if (st) { st.persona_id = personaSel.value; renderMeta({...st, summary: st.summary || ''}); }
  toast(personaSel.value ? 'Dossier attached.' : 'Dossier detached.');
};

document.getElementById('form').onsubmit = async (e) => {
  e.preventDefault();
  if (busy) return;
  const inp = document.getElementById('input');
  const text = inp.value.trim();
  if (!text || !sid) { toast('Open or deploy a chat first.'); return; }
  sendUserText(text);
};

document.getElementById('btnContinue').onclick = async () => {
  if (busy || !sid) return;
  busy = true; setTools(false);
  suggestbar.innerHTML = '';
  const res = await streamReply('/api/continue', {session_id: sid});
  busy = false; setTools(true);
  showChoices(res.choices);
  refreshTrack();
  loadChats();
};

document.getElementById('btnRegen').onclick = async () => {
  if (busy || !sid) return;
  busy = true; setTools(false);
  suggestbar.innerHTML = '';
  const lastGm = [...chat.querySelectorAll('.msg.gm')].pop();
  if (lastGm) { lastGm.classList.add('removing'); setTimeout(() => lastGm.remove(), 250); }
  const res = await streamReply('/api/regenerate', {session_id: sid});
  busy = false; setTools(true);
  showChoices(res.choices);
  refreshTrack();
  loadChats();
};

document.getElementById('btnUndo').onclick = async () => {
  if (busy || !sid) return;
  const r = await fetch('/api/undo', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({session_id: sid})});
  if (!r.ok) { toast('Nothing to undo.'); return; }
  toast('Turn undone.');
  await openChat(sid);
};

document.getElementById('btnSummary').onclick = async () => {
  if (busy || !sid) return;
  toast('Recompiling briefing…');
  const r = await fetch('/api/summarize', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({session_id: sid})});
  if (!r.ok) { toast('Briefing failed — check uplink.'); return; }
  toast('Briefing current.');
  await openChat(sid);
};

document.getElementById('btnExport').onclick = () => {
  if (!sid) { toast('Open a chat first.'); return; }
  window.open('/api/export?session_id=' + sid, '_blank');
};

document.getElementById('exportall').onclick = () => {
  window.open('/api/export-all', '_blank');
  toast('Archive downloading.');
};

setTools(false);
syncChoicesBtn();
syncModeUI();
loadPersonas().then(() => {
  const pre = localStorage.getItem('anew_persona');
  if (pre) {
    setRadio('mode', 'oc');
    personaSel.value = pre;
    localStorage.removeItem('anew_persona');
    syncDossierRow();
    toast('Dossier attached.');
  }
  loadChats();
});
