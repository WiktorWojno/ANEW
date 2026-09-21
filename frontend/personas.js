let editing = null;
const F = ['name','race','faction','role','weapon','appearance','personality','backstory','voice','notes'];
const val = k => document.getElementById('f_' + k).value.trim();
const set = (k, v) => document.getElementById('f_' + k).value = v || '';
const elVal = () => (document.querySelector('input[name=el]:checked') || {}).value || '';
const setEl = (v) => {
  const el = document.querySelector(`input[name=el][value="${v || ''}"]`);
  if (el) el.checked = true;
};
const toastEl = document.getElementById('toast');
let toastT = null;
function toast(msg) {
  toastEl.textContent = msg;
  toastEl.classList.add('show');
  clearTimeout(toastT);
  toastT = setTimeout(() => toastEl.classList.remove('show'), 2400);
}

function updatePreview() {
  const p = document.getElementById('preview');
  const name = val('name') || '(unnamed operative)';
  const bits = [val('race'), val('role'), val('faction')].filter(Boolean).join(' · ');
  const el = elVal();
  p.innerHTML = '';
  const b = document.createElement('b'); b.textContent = '◈ ' + name;
  p.appendChild(b);
  if (bits || el || val('weapon')) {
    const s = document.createElement('div');
    s.textContent = [bits, el, val('weapon')].filter(Boolean).join(' — ');
    p.appendChild(s);
  }
  if (val('personality')) {
    const s = document.createElement('div'); s.textContent = val('personality').slice(0, 140);
    p.appendChild(s);
  }
}
for (const k of F) document.getElementById('f_' + k).addEventListener('input', updatePreview);
for (const r of document.querySelectorAll('input[name=el]')) r.addEventListener('change', updatePreview);

async function load() {
  const r = await fetch('/api/personas').then(r => r.json());
  const box = document.getElementById('cards');
  box.innerHTML = '';
  if (!r.personas.length) {
    const e = document.createElement('div');
    e.className = 'empty';
    e.textContent = 'No characters forged. Create one above — race and faction fields suggest canon values.';
    box.appendChild(e);
    return;
  }
  for (const p of r.personas) {
    const d = document.createElement('div');
    d.className = 'card' + (p.element ? ' el-' + p.element : '');
    const h = document.createElement('h3'); h.textContent = '◈ ' + (p.name || '(unnamed)'); d.appendChild(h);
    const sub = document.createElement('div'); sub.className = 'sub';
    sub.textContent = [p.race, p.role, p.faction].filter(Boolean).join(' · ') || '—';
    d.appendChild(sub);
    if (p.element || p.weapon) {
      const e = document.createElement('p');
      e.textContent = [p.element, p.weapon].filter(Boolean).join(' · ');
      d.appendChild(e);
    }
    for (const k of ['personality', 'backstory']) {
      if (p[k]) { const e = document.createElement('p'); e.textContent = p[k].slice(0, 150); d.appendChild(e); }
    }
    const row = document.createElement('div'); row.className = 'row';
    const eb = document.createElement('button'); eb.textContent = 'Edit'; eb.className = 'ghost';
    eb.onclick = () => {
      editing = p.id;
      for (const k of F) set(k, p[k]);
      setEl(p.element);
      updatePreview();
      window.scrollTo(0, 0);
    };
    const db = document.createElement('button'); db.textContent = 'Delete'; db.className = 'ghost';
    db.onclick = async () => {
      if (!db.dataset.armed) {
        db.dataset.armed = '1'; db.textContent = 'Confirm?';
        setTimeout(() => { delete db.dataset.armed; db.textContent = 'Delete'; }, 3000);
        return;
      }
      await fetch('/api/personas?id=' + p.id, {method: 'DELETE'});
      toast('Character removed.');
      if (editing === p.id) { editing = null; clear(); }
      load();
    };
    const ub = document.createElement('button'); ub.textContent = 'Deploy with →';
    ub.onclick = () => { localStorage.setItem('anew_persona', p.id); location.href = '/'; };
    row.append(eb, db, ub); d.appendChild(row);
    box.appendChild(d);
  }
}
function clear() {
  editing = null;
  for (const k of F) set(k, '');
  setEl('');
  updatePreview();
}
document.getElementById('clear').onclick = clear;
document.getElementById('save').onclick = async () => {
  const body = Object.fromEntries(F.map(k => [k, val(k)]));
  body.element = elVal();
  if (!body.name) { toast('Codename is required.'); return; }
  if (editing) {
    await fetch('/api/personas', {method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: editing, ...body})});
    toast('Character updated.');
  } else {
    await fetch('/api/personas', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)});
    toast('Character filed.');
  }
  clear(); load();
};
updatePreview();
load();
