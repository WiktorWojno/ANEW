"""ANEW FastAPI: Endfield RP web + SQLite + LiteRouter hybrid."""
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from . import memory
from .literouter import MODEL_CHEAP, MODEL_MAIN, chat_once, chat_stream

load_dotenv()
ROOT = Path(__file__).resolve().parent.parent

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
LORE = _read(ROOT / "lore" / "talos2.md")[:4000] + "\n" + _read(ROOT / "lore" / "factions.md")[:2000]

STARTER_TEXT = {
    "A": "Cold Wake: the player wakes in OMV Dijiang, Perlica briefs, Valley IV alarm sounds.",
    "B": "Valley IV Contract: the player arrives as a new hire, AIC orientation fails, Aggeloi attack.",
    "C": "Snowy Loop: investigation in the Snowy Forest time-loop with the Typhoeus/Purrchena thread.",
}

app = FastAPI(title="ANEW Endfield RP")
app.mount("/static", StaticFiles(directory=str(ROOT / "frontend")), name="static")


@app.get("/")
def index():
    return FileResponse(str(ROOT / "frontend" / "index.html"))


@app.get("/personas")
def personas_page():
    return FileResponse(str(ROOT / "frontend" / "personas.html"))


@app.get("/api/config")
def config():
    return {"model_main": MODEL_MAIN, "model_cheap": MODEL_CHEAP, "build": "choices-v4"}


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


@app.get("/api/state")
def state(session_id: str):
    s = memory.get_session(session_id)
    if not s:
        return JSONResponse({"error": "no session"}, status_code=404)
    return {"session": s, "history": memory.last_messages(session_id, 50)}


def build_messages(s, history, user_text):
    mode_txt = MODES.get(s.get("mode", "oc"), MODES["oc"])
    starter = STARTER_TEXT.get(s.get("starter", "B"), STARTER_TEXT["B"])
    persona_txt = ""
    if s.get("persona_id"):
        p = memory.get_persona(s["persona_id"])
        if p:
            persona_txt = "\n\n--- PERSONA ---\n" + memory.persona_block(p)
    style_txt = s.get("style") or memory.STYLE_UNKNOWN
    system = (
        f"{GM}\n\n--- MODE [{s.get('mode')}] ---\n{mode_txt}\n\n"
        f"--- LORE ---\n{LORE}\n\nTone: {s.get('tone')}. "
        f"Character: {s.get('character','')}. Starter: {starter}\n"
        f"{persona_txt}\n"
        f"--- PLAYER STYLE ---\n{style_txt}\n"
        f"Summary so far: {s.get('summary','(new game)')}\n"
        "Keep turns 150-300 words. End EVERY response with the choice block:\n"
        "@@CHOICES@@\n1. <action, max 12 words>\n2. <action, max 12 words>\n"
        "3. <action, max 12 words>\n@@END@@\n"
        "Rules: exactly 3 options (4 only at life-or-death stakes). Option 1 must suit "
        "PLAYER STYLE. Always include one lateral/clever option. Nothing after @@END@@. "
        "FORBIDDEN: a prose 'Choices:'/'What do you do?' section or an 'Or what do you do?' line — "
        "choices live ONLY in the block. The UI renders **bold** and *italic*, so use them."
    )
    msgs = [{"role": "system", "content": system}]
    for m in history[-10:]:
        msgs.append({"role": m["role"], "content": m["content"][-3000:]})
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
        async for ch in chat_stream(MODEL_MAIN, msgs, max_tokens=1000, temperature=0.9):
            buf.append(ch)
            yield f"data: {json.dumps({'delta': ch})}\n\n"
        full = "".join(buf)
        choices, raw = [], ""
        if full and not full.startswith("\n[LiteRouter error"):
            cleaned, choices, raw = extract_choices(full)
            memory.add_message(sid, "assistant", cleaned)
            await maybe_summarize(sid)
        yield f"data: {json.dumps({'done': True, 'choices': choices, 'raw': raw})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


CONTINUE_NUDGE = (
    "Continue the scene from exactly where it stopped. Advance action, consequences and NPC "
    "reactions. Do not recap or repeat yourself. Keep 150-300 words. "
    "End with the @@CHOICES@@ block (option 1 suits PLAYER STYLE, one lateral option)."
)
REGEN_NUDGE = (
    "Rewrite your last response with a fresh take: different beats and details, same continuity. "
    "Keep 150-300 words. End with the @@CHOICES@@ block "
    "(option 1 suits PLAYER STYLE, one lateral option)."
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
