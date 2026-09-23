"""ANEW FastAPI: Endfield RP web + SQLite + LiteRouter hybrid."""
import asyncio
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from . import memory
from .literouter import MODEL_CHEAP, MODEL_MAIN, chat_once, chat_stream, is_free_model

# :free models cap input at ~5,000 tokens, so the prompt shrinks to fit (see build_messages).
FREE_MAIN = is_free_model(MODEL_MAIN)

load_dotenv()
ROOT = Path(__file__).resolve().parent.parent

PORTRAIT_DIR = ROOT / "frontend" / "portraits"
# Railway: ANEW_DATA_DIR moves uploads onto the persistent volume; served at /media/.
if os.getenv("ANEW_DATA_DIR"):
    PORTRAIT_DIR = Path(os.getenv("ANEW_DATA_DIR")) / "portraits"
PORTRAIT_DIR.mkdir(parents=True, exist_ok=True)
PORTRAIT_URL_PREFIX = "/media" if os.getenv("ANEW_DATA_DIR") else "/static/portraits"
PORTRAIT_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
PORTRAIT_MAX = 5 * 1024 * 1024

def _read(p: Path, fallback=""):
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return fallback

GM = _read(ROOT / "prompts" / "gm_system.md")
MODES = {
    "oc": _read(ROOT / "prompts" / "modes" / "oc.md"),
    "admin": _read(ROOT / "prompts" / "modes" / "admin.md"),
    "canon": _read(ROOT / "prompts" / "modes" / "canon.md"),
}
LORE_DIR = ROOT / "lore"
LORE_BUDGET = 6000  # chars per prompt; protects Standard premium credits


def _section(filename, heading, max_chars=1500):
    """Extract a '## heading' section from a lore file (tier labels included)."""
    text = _read(LORE_DIR / filename)
    if not text:
        return ""
    lines = text.split("\n")
    start = None
    for i, ln in enumerate(lines):
        if ln.strip() == "## " + heading:
            start = i
            break
    if start is None:
        return ""
    out = []
    for ln in lines[start + 1:]:
        if ln.startswith("## "):
            break
        out.append(ln)
    return "\n".join(out).strip()[:max_chars]


ALWAYS_LORE = [
    ("timeline.md", "At a glance"),
    ("operators.md", "Key figures (always injected)"),
]
STARTER_LORE = {
    # A: Cold Wake — orbital HQ, valley alarm, protocol grid
    "A": [("regions.md", "OMV Dijiang"), ("regions.md", "Valley IV"),
          ("technology.md", "Protocol-Originium and Tele-Protocol")],
    # B: Valley IV Contract — frontier production, Aggeloi raid, AIC kit
    "B": [("regions.md", "Valley IV"), ("threats.md", "Aggeloi"),
          ("technology.md", "AIC — Automated Industry Complex")],
    # C: Snowy Loop — forest investigation, Blight effects, Reconverer thread
    "C": [("regions.md", "Snowy Forest and Wuling"), ("threats.md", "Blight"),
          ("technology.md", "Reconveners")],
    # D: Ankhorfall Ridge — fresh fall, SAC assessment, Arts-forward combat
    "D": [("regions.md", "Valley IV"), ("threats.md", "Aggeloi"),
          ("technology.md", "Originium and the Arts")],
    # E: Jinlong Ledger — trade dispute, human scheming, protocol logistics
    "E": [("regions.md", "Jinlong"), ("threats.md", "Human threats"),
          ("technology.md", "Protocol-Originium and Tele-Protocol")],
    # F: Wuling Samples — scholar escort, Blight edge, AIC field lab
    "F": [("regions.md", "Snowy Forest and Wuling"), ("threats.md", "Blight"),
          ("technology.md", "AIC — Automated Industry Complex")],
    # G: Far North Expedition — frontier ruins, Static Blight, northern magnet
    "G": [("regions.md", "Northern Frontier"), ("threats.md", "Blight"),
          ("technology.md", "Reconveners")],
    # H: Orientation Day — civilian Valley life, working AIC, first meetings
    "H": [("regions.md", "Valley IV"),
          ("technology.md", "AIC — Automated Industry Complex"),
          ("technology.md", "Protocol-Originium and Tele-Protocol")],
    # I: Night Market — Jinlong trade rows, social webs, quiet schemes
    "I": [("regions.md", "Jinlong"), ("threats.md", "Human threats"),
          ("technology.md", "Protocol-Originium and Tele-Protocol")],
    # J: Quiet Watch — monitoring shift, Depth readings, slow dread
    "J": [("regions.md", "OMV Dijiang"), ("threats.md", "Blight"),
          ("technology.md", "Protocol-Originium and Tele-Protocol")],
    # K: High Orbit Rest — station downtime, comforts, letters, the view
    "K": [("regions.md", "OMV Dijiang"),
          ("technology.md", "Protocol-Originium and Tele-Protocol"),
          ("technology.md", "AIC — Automated Industry Complex")],
    # L: Valley Evening — civilian quarter, food, banter, Terra-longing
    "L": [("regions.md", "Valley IV"), ("regions.md", "Jinlong"),
          ("technology.md", "Reconveners")],
}
FACTIONS_REF = _read(LORE_DIR / "factions.md")


def lore_for(starter, budget=LORE_BUDGET, sec_cap=1500):
    """Assemble the tier-labeled lore block for a deployment. See lore/canon-policy.md."""
    parts = ["[Factions]\n" + FACTIONS_REF.strip()]
    for filename, heading in ALWAYS_LORE:
        sec = _section(filename, heading, sec_cap)
        if sec:
            parts.append(f"[{filename} — {heading}]\n{sec}")
    for filename, heading in STARTER_LORE.get(starter, STARTER_LORE["B"]):
        sec = _section(filename, heading, sec_cap)
        if sec:
            parts.append(f"[{filename} — {heading}]\n{sec}")
    return "\n\n".join(parts)[:budget]

STARTER_TEXT = {
    "A": "Cold Wake: the player wakes in OMV Dijiang, Perlica briefs, Valley IV alarm sounds.",
    "B": "Valley IV Contract: the player arrives as a new hire, AIC orientation fails, Aggeloi attack.",
    "C": "Snowy Loop: investigation in the Snowy Forest time-loop with the Typhoeus/Purrchena thread.",
    "D": "Ankhorfall Ridge: a fresh Ankhorfall on the Valley fringe. Z7 needs SAC assessment before it spreads. Combat-forward.",
    "E": "Jinlong Ledger: a TGCC contract dispute chokes Valley supply lines. Avywenna's roads, deniable deals. Diplomacy-forward.",
    "F": "Wuling Samples: escort HAS scholars sampling a fresh Blight edge. Science under hazard lights.",
    "G": "Far North Expedition (endgame): ruins and Static Blight beyond the fallen frontier. For seasoned operatives; the Reactor magnet pulls north.",
    "H": "Orientation Day: a working AIC orientation, dorm assignment, and introductions around Valley IV. No alarm — just people, work, and first impressions. Slow burn.",
    "I": "Night Market: an off-duty evening in a Jinlong trade row — food stalls, small gifts, rumors, and bonding. Social start; trouble only if you go looking.",
    "J": "Quiet Watch: a routine night shift monitoring Depth readings and TP relays. Strange signals, still air, rising numbers. Atmospheric mystery, no combat promised.",
    "K": "High Orbit Rest: downtime aboard OMV Dijiang — observation deck over the storms, canteen, rec room, letters between outposts. Zero stakes; rest is the mission.",
    "L": "Valley Evening: off-shift hours in Valley IV's civilian quarter — food stalls, river air, off-duty banter, homesickness for a Terra nobody remembers. Pure downtime.",
}

app = FastAPI(title="ANEW Endfield RP")
app.mount("/static", StaticFiles(directory=str(ROOT / "frontend")), name="static")
if os.getenv("ANEW_DATA_DIR"):
    # Volume-backed portraits (Railway): served at /media/<name>.
    app.mount("/media", StaticFiles(directory=str(PORTRAIT_DIR)), name="media")


ANEW_PASSWORD = os.getenv("ANEW_PASSWORD", "")
COOKIE_NAME = "anew_key"
_GATE_OPEN = ("/api/login", "/api/logout", "/api/config")


NO_STORE = {"Cache-Control": "no-store"}


@app.middleware("http")
async def gate(request: Request, call_next):
    """Shared-secret gate. Set ANEW_PASSWORD to lock the whole site (Railway)."""
    if ANEW_PASSWORD:
        path = request.url.path
        if not (path.startswith("/static/") or path.startswith("/media/") or path in _GATE_OPEN):
            key = request.headers.get("x-anew-key", "") or request.cookies.get(COOKIE_NAME, "")
            if key != ANEW_PASSWORD:
                if path.startswith("/api/") or path in ("/openapi.json", "/docs", "/redoc"):
                    return JSONResponse({"error": "locked"}, status_code=401, headers=NO_STORE)
                # no-store: browsers must never cache the lock page under /, /personas or
                # /lore, or it keeps showing after a successful login.
                return FileResponse(str(ROOT / "frontend" / "lock.html"), headers=NO_STORE)
    resp = await call_next(request)
    # Revalidate app pages and static assets so a redeploy reaches phones immediately.
    if not request.url.path.startswith(("/api/", "/media/")):
        resp.headers.setdefault("Cache-Control", "no-cache")
    return resp


@app.post("/api/login")
async def login(req: Request):
    if not ANEW_PASSWORD:
        return {"ok": True}
    try:
        b = await req.json()
    except Exception:
        b = {}
    if b.get("password", "") != ANEW_PASSWORD:
        return JSONResponse({"error": "wrong password"}, status_code=401)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(COOKIE_NAME, ANEW_PASSWORD, httponly=True, samesite="lax",
                    max_age=90 * 24 * 3600, path="/")
    return resp


@app.post("/api/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


@app.get("/")
def index():
    return FileResponse(str(ROOT / "frontend" / "index.html"))


@app.get("/personas")
def personas_page():
    return FileResponse(str(ROOT / "frontend" / "personas.html"))


@app.get("/api/export-all")
def export_all():
    """Download every deployment + dossier as one JSON archive."""
    from fastapi.responses import PlainTextResponse

    sessions = []
    for s in memory.list_sessions(limit=500):
        sessions.append({
            "session": {k: s[k] for k in s if k not in ("preview",)},
            "track": memory.get_state(s["id"]),
            "history": memory.last_messages(s["id"], 2000),
        })
    archive = json.dumps({"build": "roadmap-v6", "personas": memory.list_personas(),
                          "sessions": sessions}, ensure_ascii=False)
    return PlainTextResponse(
        archive, media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=anew-archive.json"},
    )


@app.get("/lore")
def lore_page():
    return FileResponse(str(ROOT / "frontend" / "lore.html"))


def _lore_docs():
    docs = []
    for p in sorted((ROOT / "lore").glob("*.md")):
        text = _read(p)
        title = p.stem
        sections = []
        for ln in text.split("\n"):
            if ln.startswith("# "):
                title = ln[2:].strip()
            elif ln.startswith("## "):
                sections.append(ln[3:].strip())
        docs.append({"id": p.name, "title": title, "sections": sections})
    return docs


@app.get("/api/lore")
def lore_list():
    return {"docs": [{k: d[k] for k in ("id", "title", "sections")} for d in _lore_docs()]}


@app.get("/api/lore/search")
def lore_search(q: str = ""):
    q = q.strip().lower()
    if not q:
        return {"matches": []}
    out = []
    for d in _lore_docs():
        text = _read(ROOT / "lore" / d["id"])
        idx = text.lower().find(q)
        if idx >= 0:
            start = max(0, idx - 80)
            out.append({"id": d["id"], "title": d["title"],
                        "snippet": text[start:idx + 200].replace("\n", " ").strip()})
    return {"matches": out}


@app.get("/api/lore/{doc_id}")
def lore_doc(doc_id: str):
    allowed = {d["id"] for d in _lore_docs()}
    if doc_id not in allowed:
        return JSONResponse({"error": "no doc"}, status_code=404)
    text = _read(ROOT / "lore" / doc_id)
    title = next((d["title"] for d in _lore_docs() if d["id"] == doc_id), doc_id)
    return {"id": doc_id, "title": title, "content": text}


@app.get("/api/config")
def config():
    return {"model_main": MODEL_MAIN, "model_cheap": MODEL_CHEAP, "build": "free-v7", "free_main": FREE_MAIN}


@app.post("/api/new-game")
async def new_game(req: Request):
    b = await req.json()
    sid = memory.create_session(
        mode=b.get("mode", "oc"), tone=b.get("tone", "anime-heroic"),
        starter=b.get("starter", "B"), character=b.get("character", ""),
        title=b.get("title", ""), persona_id=b.get("persona_id", ""),
    )
    return {"session_id": sid}


@app.get("/api/sessions")
def sessions():
    return {"sessions": memory.list_sessions()}


@app.delete("/api/session")
def delete_session(session_id: str):
    memory.delete_session(session_id)
    return {"ok": True}


@app.patch("/api/session")
async def patch_session(req: Request):
    b = await req.json()
    sid = b.get("session_id", "")
    if not memory.get_session(sid):
        return JSONResponse({"error": "no session"}, status_code=404)
    patch = {k: b[k] for k in ("title", "mode", "tone", "persona_id") if k in b}
    if patch:
        memory.update_session(sid, **patch)
    return {"ok": True}


# ---- personas CRUD ----

@app.get("/api/personas")
def personas_list():
    return {"personas": memory.list_personas()}


@app.post("/api/personas")
async def personas_create(req: Request):
    b = await req.json()
    if not b.get("name", "").strip():
        return JSONResponse({"error": "name required"}, status_code=400)
    pid = memory.create_persona({k: str(b.get(k, "")) for k in memory.PERSONA_FIELDS})
    return {"id": pid}


@app.put("/api/personas")
async def personas_update(req: Request):
    b = await req.json()
    pid = b.get("id", "")
    if not pid or not memory.get_persona(pid):
        return JSONResponse({"error": "no persona"}, status_code=404)
    memory.update_persona(pid, {k: str(b[k]) for k in memory.PERSONA_FIELDS if k in b})
    return {"ok": True}


@app.delete("/api/personas")
def personas_delete(id: str):
    memory.delete_persona(id)
    return {"ok": True}


@app.post("/api/portraits")
async def portrait_upload(file: UploadFile = File(...)):
    """Store an operative portrait, return its URL. Dossier save links it via portrait_url."""
    import uuid as _uuid

    ext = ("." + (file.filename or "").rsplit(".", 1)[-1].lower()) if "." in (file.filename or "") else ""
    if ext not in PORTRAIT_EXTS:
        return JSONResponse({"error": "image type not allowed (png/jpg/webp/gif)"}, status_code=400)
    data = await file.read()
    if not data or len(data) > PORTRAIT_MAX:
        return JSONResponse({"error": "empty or over 5MB"}, status_code=400)
    name = f"p_{_uuid.uuid4().hex[:12]}{ext}"
    (PORTRAIT_DIR / name).write_bytes(data)
    return {"url": f"{PORTRAIT_URL_PREFIX}/{name}"}


@app.get("/api/state")
def state(session_id: str):
    s = memory.get_session(session_id)
    if not s:
        return JSONResponse({"error": "no session"}, status_code=404)
    return {"session": s, "history": memory.last_messages(session_id, 50),
            "track": memory.get_state(session_id)}


@app.patch("/api/track")
async def patch_track(req: Request):
    """Manual tracker correction. Body: {session_id, location?, party?, injuries?, inventory?, threads?}."""
    b = await req.json()
    sid = b.get("session_id", "")
    if not memory.get_session(sid):
        return JSONResponse({"error": "no session"}, status_code=404)
    memory.update_state(sid, **{k: b[k] for k in memory.STATE_FIELDS if k in b})
    return {"ok": True, "track": memory.get_state(sid)}


def build_messages(s, history, user_text):
    mode_txt = MODES.get(s.get("mode", "oc"), MODES["oc"])
    starter = STARTER_TEXT.get(s.get("starter", "B"), STARTER_TEXT["B"])
    persona_txt = ""
    if s.get("persona_id"):
        p = memory.get_persona(s["persona_id"])
        if p:
            persona_txt = "\n\n--- PERSONA ---\n" + memory.persona_block(p)
    style_txt = s.get("style") or memory.STYLE_UNKNOWN
    # Free-tier main model: fit the 5k-token input cap (short lore, recent history only).
    lore_txt = lore_for(s.get("starter", "B"),
                        budget=2500 if FREE_MAIN else LORE_BUDGET,
                        sec_cap=700 if FREE_MAIN else 1500)
    hist_n, hist_cap = (4, 800) if FREE_MAIN else (10, 3000)
    system = (
        f"{GM}\n\n--- MODE [{s.get('mode')}] ---\n{mode_txt}\n\n"
        f"--- LORE (tier-labeled canon; see lore/canon-policy.md) ---\n{lore_txt}\n\nTone: {s.get('tone')}. "
        f"Character: {s.get('character','')}. Starter: {starter}\n"
        f"{persona_txt}\n"
        f"--- PLAYER STYLE ---\n{style_txt}\n"
        f"Summary so far: {s.get('summary','(new game)')}\n"
        "Keep turns 150-300 words. End EVERY response with the choice block:\n"
        "@@CHOICES@@\n1. <action, max 12 words>\n2. <action, max 12 words>\n"
        "3. <action, max 12 words>\n@@END@@\n"
        "Rules: exactly 3 options (4 only at life-or-death stakes). Option 1 must suit "
        "PLAYER STYLE. Always include one lateral/clever option. "
        "After @@END@@ append the tracker block (nothing else may follow it):\n"
        "@@STATE@@\nlocation: <current place>\nparty: <allies present>\n"
        "injuries: <none or list>\ninventory: <notable items>\n"
        "threads: <open plot threads, comma separated>\n@@ENDSTATE@@\n"
        "Rules: 5 short lines, 'none' if empty. "
        "FORBIDDEN: a prose 'Choices:'/'What do you do?' section or an 'Or what do you do?' line — "
        "choices live ONLY in the block. The UI renders **bold** and *italic*, so use them."
    )
    msgs = [{"role": "system", "content": system}]
    for m in history[-hist_n:]:
        msgs.append({"role": m["role"], "content": m["content"][-hist_cap:]})
    msgs.append({"role": "user", "content": user_text})
    return msgs


async def do_summarize(sid):
    hist = memory.last_messages(sid, 30)
    if not hist:
        return ""
    blob = "\n".join(f"{m['role']}: {m['content'][:800]}" for m in hist)
    prompt = [
        {"role": "system", "content": "Summarize this Endfield RP session in 12 lines: location, party, goals, injuries, inventory, open threads."},
        {"role": "user", "content": blob[-12000:]},
    ]
    try:
        summary = await chat_once(MODEL_CHEAP, prompt, max_tokens=400, temperature=0.3)
        if summary.startswith("\n[LiteRouter error"):
            return ""
        memory.update_session(sid, summary=summary)
        # Backfill tracker state via the free model (best effort, never fatal)
        try:
            state_prompt = [
                {"role": "system", "content": (
                    "Given this roleplay transcript, output ONLY a JSON object with keys "
                    "location, party, injuries, inventory, threads (short strings, "
                    "'none' if empty). No other text.")},
                {"role": "user", "content": blob[-8000:]},
            ]
            raw_state = await chat_once(MODEL_CHEAP, state_prompt, max_tokens=250, temperature=0.2)
            m = re.search(r"\{[\s\S]*\}", raw_state)
            if m:
                vals = {k: str(v)[:300] for k, v in json.loads(m.group(0)).items()
                        if k in memory.STATE_FIELDS and v}
                if vals:
                    memory.update_state(sid, **vals)
        except Exception:
            pass
        return summary
    except Exception:
        return ""


async def maybe_summarize(sid):
    n = memory.count_messages(sid)
    if n == 0 or n % 12 != 0:
        return
    await do_summarize(sid)


CHOICE_START = "@@CHOICES@@"
CHOICE_END = "@@END@@"
STATE_START = "@@STATE@@"
STATE_END = "@@ENDSTATE@@"
_CHOICE_LINE = re.compile(r"^\s*(?:\d{1,2}\s*[.)\-:]\s*|[-•*]\s+)(.+)$")
_CHOICE_HEADING = re.compile(
    r"^\s*\*{0,3}\s*[\"'“”‘’]*\s*(?:choices|options|tactical options|what do you do)\b[^a-zA-Z0-9]*$",
    re.IGNORECASE,
)
_NUM_LINE = re.compile(r"^\s*\d{1,2}\s*[.)]\s*(.+)$")
_OWDYD = re.compile(r"or what do you do\??", re.IGNORECASE)


def _clean_label(c):
    c = c.replace("**", "").replace("__", "").replace("*", "").replace("`", "")
    c = re.sub(r"\s+", " ", c).strip().strip("\"'“”‘’-–—")
    return c


def extract_state(full):
    """Split out the @@STATE@@ tracker block. Returns (text_without, values, raw)."""
    if STATE_START not in full or STATE_END not in full:
        return full, {}, ""
    pre, rest = full.split(STATE_START, 1)
    mid, post = rest.split(STATE_END, 1)
    vals = {}
    for line in mid.strip().split("\n"):
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip().lower()
        if k in memory.STATE_FIELDS:
            vals[k] = v.strip()[:300]
    raw = STATE_START + mid + STATE_END
    cleaned = pre.rstrip()
    if post.strip():
        cleaned += "\n" + post.strip()
    return cleaned, vals, raw


def extract_choices(full):
    """Split a GM reply into (clean_text, choices, raw_block). No block -> ([], '')."""
    if CHOICE_START in full and CHOICE_END in full:
        pre, rest = full.split(CHOICE_START, 1)
        mid, post = rest.split(CHOICE_END, 1)
        choices = []
        for line in mid.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            m = _CHOICE_LINE.match(line)
            choices.append(_clean_label(m.group(1) if m else line))
        choices = [c for c in choices if c][:4]
        raw = CHOICE_START + mid + CHOICE_END
        cleaned = pre.rstrip()
        if post.strip():
            cleaned += "\n" + post.strip()
        return cleaned, choices, raw
    # Fallback: trailing prose section headed Choices:/What-do-you-do: (model ignored the block)
    lines = full.split("\n")
    head = None
    for i, ln in enumerate(lines):
        if _CHOICE_HEADING.match(ln):
            head = i
    choices = []
    if head is not None:
        for ln in lines[head + 1:]:
            s = ln.strip()
            if not s or _OWDYD.search(s):
                continue
            m = _CHOICE_LINE.match(s)
            c = _clean_label(m.group(1) if m else re.sub(r"^[-•*]\s+", "", s))
            if c:
                choices.append(c)
    if len(choices) >= 2:
        choices = choices[:4]
        raw = "\n".join(lines[head:])
        return "\n".join(lines[:head]).rstrip(), choices, raw
    # Second fallback: trailing numbered run (dash-led dialogue deliberately excluded
    # so in-character speech is never mistaken for options).
    numbered, start = [], None
    for i in range(len(lines) - 1, -1, -1):
        s = lines[i].strip()
        if not s or _OWDYD.search(s):
            continue
        m = _NUM_LINE.match(s)
        if m:
            c = _clean_label(m.group(1))
            if c:
                numbered.append(c)
                start = i
        else:
            break
    if len(numbered) < 2:
        return full, [], ""
    numbered.reverse()
    raw = "\n".join(lines[start:])
    return "\n".join(lines[:start]).rstrip(), numbered[:4], raw


def _streamer(sid, msgs):
    async def gen():
        buf = []
        # Bumped up from 800/1000: the system prompt only asks for 150-300
        # words + two short tag blocks (comfortably under the old cap), but
        # finish_reason=length has been observed with zero visible content —
        # extra headroom in case something is consuming budget invisibly.
        out_cap = 1200 if FREE_MAIN else 2000
        agen = chat_stream(MODEL_MAIN, msgs, max_tokens=out_cap, temperature=0.9).__aiter__()
        while True:
            # One task per item, reused across the wait below — asyncio.wait()
            # with a timeout does NOT cancel it when the timeout elapses, it
            # just returns control so we can send a keepalive and go back to
            # waiting on the SAME task. (asyncio.wait_for, used previously,
            # cancels the awaited call on timeout, which killed the retry
            # logic mid-backoff and produced empty streams — that's the bug
            # behind the "[No signal]" reports.)
            task = asyncio.ensure_future(agen.__anext__())
            while not task.done():
                _, pending = await asyncio.wait({task}, timeout=15)
                if pending:
                    # Still working (queued / retrying / slow model) — ping so
                    # mobile carriers / proxies don't kill the idle connection.
                    yield ": keepalive\n\n"
            try:
                ch = task.result()
            except StopAsyncIteration:
                break
            buf.append(ch)
            yield f"data: {json.dumps({'delta': ch})}\n\n"
        full = "".join(buf)
        choices, raw = [], ""
        if full and not full.startswith("\n[LiteRouter error"):
            no_state, vals, state_raw = extract_state(full)
            if vals:
                memory.update_state(sid, **vals)
            cleaned, choices, ch_raw = extract_choices(no_state)
            memory.add_message(sid, "assistant", cleaned)
            await maybe_summarize(sid)
            raw = (ch_raw + ("\n" + state_raw if state_raw else "")).strip()
        yield f"data: {json.dumps({'done': True, 'choices': choices, 'raw': raw})}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            # Belt-and-suspenders against any reverse proxy (Railway's edge,
            # nginx, etc.) buffering the whole response before forwarding it.
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


CONTINUE_NUDGE = (
    "Continue the scene from exactly where it stopped. Advance action, consequences and NPC "
    "reactions. Do not recap or repeat yourself. Keep 150-300 words. "
    "End with the @@CHOICES@@ block (option 1 suits PLAYER STYLE, one lateral option), "
    "then the @@STATE@@ block (location/party/injuries/inventory/threads, 'none' if empty)."
)
REGEN_NUDGE = (
    "Rewrite your last response with a fresh take: different beats and details, same continuity. "
    "Keep 150-300 words. End with the @@CHOICES@@ block "
    "(option 1 suits PLAYER STYLE, one lateral option), "
    "then the @@STATE@@ block (location/party/injuries/inventory/threads, 'none' if empty)."
)


@app.post("/api/chat")
async def chat(req: Request):
    b = await req.json()
    sid, text = b.get("session_id", ""), b.get("text", "").strip()
    s = memory.get_session(sid)
    if not s or not text:
        return JSONResponse({"error": "bad session or empty text"}, status_code=400)
    if b.get("mode") and b["mode"] in MODES and b["mode"] != s["mode"]:
        memory.update_session(sid, mode=b["mode"])
        s = memory.get_session(sid)
    if "persona_id" in b and b["persona_id"] != (s.get("persona_id") or ""):
        # empty string = no persona; otherwise must exist
        if b["persona_id"] and not memory.get_persona(b["persona_id"]):
            return JSONResponse({"error": "unknown persona"}, status_code=400)
        memory.update_session(sid, persona_id=b["persona_id"])
        s = memory.get_session(sid)
    hist = memory.last_messages(sid, 10)
    msgs = build_messages(s, hist, text)
    memory.add_message(sid, "user", text)
    memory.update_session(sid, style=memory.style_profile(sid))
    return _streamer(sid, msgs)


@app.post("/api/continue")
async def cont(req: Request):
    """Advance the story without saving a user message. Body: {session_id}."""
    b = await req.json()
    sid = b.get("session_id", "")
    s = memory.get_session(sid)
    if not s:
        return JSONResponse({"error": "no session"}, status_code=404)
    hist = memory.last_messages(sid, 10)
    if not hist:
        return JSONResponse({"error": "empty chat — send a message first"}, status_code=400)
    msgs = build_messages(s, hist, CONTINUE_NUDGE)
    return _streamer(sid, msgs)


@app.post("/api/regenerate")
async def regen(req: Request):
    """Drop the last GM reply and generate a fresh take. Body: {session_id}."""
    b = await req.json()
    sid = b.get("session_id", "")
    s = memory.get_session(sid)
    if not s:
        return JSONResponse({"error": "no session"}, status_code=404)
    hist = memory.last_messages(sid, 10)
    if hist and hist[-1]["role"] == "assistant":
        memory.delete_message(sid, hist[-1]["id"])
        hist = hist[:-1]
    if not hist:
        return JSONResponse({"error": "nothing to regenerate"}, status_code=400)
    msgs = build_messages(s, hist, REGEN_NUDGE)
    return _streamer(sid, msgs)


@app.post("/api/undo")
async def undo(req: Request):
    """Remove the latest GM reply + the user message before it. Body: {session_id}."""
    b = await req.json()
    sid = b.get("session_id", "")
    if not memory.get_session(sid):
        return JSONResponse({"error": "no session"}, status_code=404)
    removed = memory.undo_last_turn(sid)
    if not removed:
        return JSONResponse({"error": "nothing to undo"}, status_code=400)
    return {"ok": True, "removed": removed}


@app.delete("/api/message")
def delete_message(session_id: str, id: int):
    if not memory.get_session(session_id):
        return JSONResponse({"error": "no session"}, status_code=404)
    if not memory.delete_message(session_id, id):
        return JSONResponse({"error": "no message"}, status_code=404)
    return {"ok": True}


@app.post("/api/summarize")
async def summarize(req: Request):
    """Force a briefing refresh via the cheap model. Body: {session_id}."""
    b = await req.json()
    sid = b.get("session_id", "")
    if not memory.get_session(sid):
        return JSONResponse({"error": "no session"}, status_code=404)
    summary = await do_summarize(sid)
    if not summary:
        return JSONResponse({"error": "summarize failed"}, status_code=502)
    return {"ok": True, "summary": summary}


@app.get("/api/export")
def export(session_id: str):
    """Download the chat as markdown."""
    from fastapi.responses import PlainTextResponse

    s = memory.get_session(session_id)
    if not s:
        return JSONResponse({"error": "no session"}, status_code=404)
    hist = memory.last_messages(session_id, 500)
    who = s.get("character") or s.get("title") or "Operative"
    if s.get("persona_id"):
        p = memory.get_persona(s["persona_id"])
        if p and p.get("name"):
            who = p["name"]
    lines = [f"# {s.get('title') or 'Talos-II operation'}",
             f"_Mode {s.get('mode')} · Zone {s.get('starter')} · Tone {s.get('tone')} · Operative {who}_", ""]
    if s.get("summary"):
        lines += ["## Briefing", s["summary"], ""]
    for m in hist:
        lines.append(f"**{'Operative' if m['role'] == 'user' else 'Endfield GM'}:** {m['content']}")
        lines.append("")
    return PlainTextResponse(
        "\n".join(lines),
        headers={"Content-Disposition": f"attachment; filename=anew-{session_id}.md"},
    )
