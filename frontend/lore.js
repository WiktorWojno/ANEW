const cards = document.getElementById('lorecards');
const reader = document.getElementById('lorereader');
const body = document.getElementById('lorebody');
const title = document.getElementById('loretitle');
const search = document.getElementById('loresearch');
const toastEl = document.getElementById('toast');
let toastT = null;
const toast = (m) => {
  toastEl.textContent = m; toastEl.classList.add('show');
  clearTimeout(toastT); toastT = setTimeout(() => toastEl.classList.remove('show'), 2400);
};

function renderLore(src) {
  let h = src.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  h = h.replace(/\*\*\*([\s\S]+?)\*\*\*/g, '<b><i>$1</i></b>');
  h = h.replace(/\*\*([\s\S]+?)\*\*/g, '<b>$1</b>');
  h = h.replace(/(^|[\s(>"'—–-])\*([^*\n]+?)\*(?=[\s).,!?;:'"—–-]|$)/gm, '$1<i>$2</i>');
  h = h.replace(/\[Confirmed\]/g, '<span class="tier-conf">[Confirmed]</span>');
  h = h.replace(/\[Community\]/g, '<span class="tier-comm">[Community]</span>');
  h = h.replace(/\[Unknown\]/g, '<span class="tier-unk">[Unknown]</span>');
  h = h.replace(/^### (.+)$/gm, '<h4>$1</h4>').replace(/^## (.+)$/gm, '<h3>$1</h3>').replace(/^# (.+)$/gm, '<h2>$1</h2>');
  h = h.replace(/^\|(.+)\|$/gm, '<div class="lrow">$1</div>');
  return h.split('\n').map(l => l.trim() ? `<p>${l}</p>` : '').join('')
    .replace(/<p><h([234])>/g, '<h$1>').replace(/<\/h([234])><\/p>/g, '</h$1>');
}

function card(doc, snippet) {
  const d = document.createElement('div');
  d.className = 'card';
  const h = document.createElement('h3'); h.textContent = '❖ ' + doc.title; d.appendChild(h);
  const sub = document.createElement('div'); sub.className = 'sub';
  sub.textContent = doc.id + (doc.sections ? ' · ' + doc.sections.length + ' sections' : '');
  d.appendChild(sub);
  if (snippet) { const p = document.createElement('p'); p.textContent = '…' + snippet + '…'; d.appendChild(p); }
  else if (doc.sections) {
    for (const s of doc.sections.slice(0, 5)) {
      const p = document.createElement('p'); p.textContent = '§ ' + s; d.appendChild(p);
    }
  }
  const row = document.createElement('div'); row.className = 'row';
  const open = document.createElement('button'); open.textContent = 'Open file →';
  open.onclick = async () => {
    const r = await fetch('/api/lore/' + doc.id).then(r => r.json());
    title.textContent = '❖ ' + r.title + '  (' + r.id + ')';
    body.innerHTML = renderLore(r.content);
    reader.hidden = false;
    reader.scrollIntoView({behavior: 'smooth'});
  };
  row.appendChild(open); d.appendChild(row);
  return d;
}

async function loadAll() {
  const r = await fetch('/api/lore').then(r => r.json());
  cards.innerHTML = '';
  for (const d of r.docs) cards.appendChild(card(d));
}

let deb = null;
search.oninput = () => {
  clearTimeout(deb);
  deb = setTimeout(async () => {
    const q = search.value.trim();
    if (!q) { loadAll(); return; }
    const r = await fetch('/api/lore/search?q=' + encodeURIComponent(q)).then(r => r.json());
    cards.innerHTML = '';
    if (!r.matches.length) {
      const e = document.createElement('div');
      e.className = 'empty'; e.textContent = 'No archive entries match.';
      cards.appendChild(e); return;
    }
    for (const m of r.matches) cards.appendChild(card(m, m.snippet));
  }, 250);
};

loadAll();
