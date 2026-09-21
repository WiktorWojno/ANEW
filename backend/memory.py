"""SQLite memory for ANEW sessions + personas. Local only, never commit .db."""
import sqlite3
import time
import uuid
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "anew.db"
SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  title TEXT DEFAULT '',
  mode TEXT DEFAULT 'oc',
  tone TEXT DEFAULT 'anime-heroic',
  starter TEXT DEFAULT 'B',
  character TEXT DEFAULT '',
  persona_id TEXT DEFAULT '',
  style TEXT DEFAULT '',
  summary TEXT DEFAULT '',
  created_at REAL,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT,
  role TEXT,
  content TEXT,
  created_at REAL,
  FOREIGN KEY(session_id) REFERENCES sessions(id)
);
CREATE TABLE IF NOT EXISTS personas (
  id TEXT PRIMARY KEY,
  name TEXT DEFAULT '',
  race TEXT DEFAULT '',
  faction TEXT DEFAULT '',
  role TEXT DEFAULT '',
  weapon TEXT DEFAULT '',
  element TEXT DEFAULT '',
  appearance TEXT DEFAULT '',
  personality TEXT DEFAULT '',
  backstory TEXT DEFAULT '',
  voice TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  created_at REAL,
  updated_at REAL
);
"""


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def _migrate():
    # Add columns to pre-existing DBs from v1 (no title/persona_id)
    with _conn() as c:
        cols = [r["name"] for r in c.execute("PRAGMA table_info(sessions)").fetchall()]
        if "title" not in cols:
            c.execute("ALTER TABLE sessions ADD COLUMN title TEXT DEFAULT ''")
        if "persona_id" not in cols:
            c.execute("ALTER TABLE sessions ADD COLUMN persona_id TEXT DEFAULT ''")
        if "style" not in cols:
            c.execute("ALTER TABLE sessions ADD COLUMN style TEXT DEFAULT ''")


def init_db():
    with _conn() as c:
        c.executescript(SCHEMA)
    _migrate()


# ---- sessions ----

def create_session(mode="oc", tone="anime-heroic", starter="B", character="", title="", persona_id=""):
    init_db()
    sid = "s_" + uuid.uuid4().hex[:10]
    now = time.time()
    if not title:
        title = f"{mode.upper()} · {starter} · {time.strftime('%m-%d %H:%M', time.localtime(now))}"
    with _conn() as c:
        c.execute(
            "INSERT INTO sessions (id, title, mode, tone, starter, character, persona_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (sid, title, mode, tone, starter, character, persona_id, now, now),
        )
    return sid


def get_session(sid):
    init_db()
    with _conn() as c:
        r = c.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        return dict(r) if r else None


def list_sessions(limit=50):
    init_db()
    with _conn() as c:
        rows = c.execute(
            """SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id) AS msg_count,
               (SELECT content FROM messages m2 WHERE m2.session_id=s.id ORDER BY id DESC LIMIT 1) AS preview
               FROM sessions s ORDER BY updated_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("preview"):
            d["preview"] = d["preview"][:120]
        out.append(d)
    return out


def delete_session(sid):
    with _conn() as c:
        c.execute("DELETE FROM messages WHERE session_id=?", (sid,))
        c.execute("DELETE FROM sessions WHERE id=?", (sid,))


def update_session(sid, **fields):
    allowed = {"title", "mode", "tone", "starter", "character", "persona_id", "style", "summary"}
    sets = [f"{k}=?" for k in fields if k in allowed]
    if not sets:
        return
    vals = [fields[k] for k in fields if k in allowed]
    with _conn() as c:
        c.execute(
            f"UPDATE sessions SET {', '.join(sets)}, updated_at=? WHERE id=?",
            (*vals, time.time(), sid),
        )


def add_message(sid, role, content):
    with _conn() as c:
        c.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?,?,?,?)",
            (sid, role, content, time.time()),
        )
        c.execute("UPDATE sessions SET updated_at=? WHERE id=?", (time.time(), sid))


def last_messages(sid, n=10):
    with _conn() as c:
        rows = c.execute(
            "SELECT id, role, content, created_at FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (sid, n),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def count_messages(sid):
    with _conn() as c:
        r = c.execute("SELECT COUNT(*) c FROM messages WHERE session_id=?", (sid,)).fetchone()
        return r["c"] if r else 0


def delete_message(sid, mid):
    """Delete one message if it belongs to the session. Returns True if removed."""
    with _conn() as c:
        cur = c.execute("DELETE FROM messages WHERE id=? AND session_id=?", (mid, sid))
        if cur.rowcount:
            c.execute("UPDATE sessions SET updated_at=? WHERE id=?", (time.time(), sid))
        return cur.rowcount > 0


def undo_last_turn(sid):
    """Remove the latest assistant message plus the user message before it. Returns removed ids."""
    with _conn() as c:
        rows = c.execute(
            "SELECT id, role FROM messages WHERE session_id=? ORDER BY id DESC LIMIT 2",
            (sid,),
        ).fetchall()
    removed = []
    if rows and rows[0]["role"] == "assistant":
        if delete_message(sid, rows[0]["id"]):
            removed.append(rows[0]["id"])
        if len(rows) > 1 and rows[1]["role"] == "user":
            if delete_message(sid, rows[1]["id"]):
                removed.append(rows[1]["id"])
    return removed


STYLE_KEYWORDS = {
    "combat": ("attack", "charge", "fight", "shoot", "fire", "strike", "kill", "slash", "blast", "ambush", "assault", "duel", "sword", "blade"),
    "bold": ("rush", "leap", "dive", "tackle", "confront", "challenge", "taunt", "charge", "reckless", "dash"),
    "stealth": ("sneak", "stealth", "shadow", "quietly", "silently", "infiltrat", "eavesdrop", "disguise", "unseen", "lurk"),
    "diplomacy": ("talk", "speak", "ask", "negotiat", "persuad", "convinc", "calm", "befriend", "plead", "threaten", "demand", "question"),
    "build": ("build", "construct", "deploy", "aic", "wall", "turret", "craft", "repair", "fortify", "trap", "device", "beacon", "barricade"),
    "caution": ("retreat", "withdraw", "cover", "wait", "watch", "observ", "listen", "careful", "slow", "scout", "hide", "hold position"),
}

STYLE_UNKNOWN = (
    "Play style unknown yet — offer one bold option, one cautious option, one clever lateral option."
)


def style_profile(sid):
    """Derive a human-readable play-style line from the player's messages. Local, no model call."""
    with _conn() as c:
        rows = c.execute(
            "SELECT content FROM messages WHERE session_id=? AND role='user'", (sid,)
        ).fetchall()
    texts = [r["content"] for r in rows if (r["content"] or "").strip()]
    if not texts:
        return STYLE_UNKNOWN
    counts = {k: 0 for k in STYLE_KEYWORDS}
    words = 0
    dialogue = 0
    for t in texts:
        low = t.lower()
        words += len(t.split())
        if '"' in t or '"' in t or "'" in t and "i " in low:
            dialogue += 1
        for k, kws in STYLE_KEYWORDS.items():
            if any(kw in low for kw in kws):
                counts[k] += 1
    n = len(texts)
    top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    top = [(k, v) for k, v in top if v > 0][:2]
    avg = words / max(n, 1)
    verb = "terse" if avg < 10 else ("verbose" if avg > 25 else "balanced")
    dlg = "often" if dialogue / n > 0.5 else ("rarely" if dialogue == 0 else "sometimes")
    if top:
        mix = ", ".join(f"{k} {int(v / n * 100)}%" for k, v in top)
        line = (f"Play style leans {mix}; messages {verb} (~{int(avg)} words); "
                f"in-character dialogue {dlg}.")
    else:
        line = f"No strong tactical lean yet; messages {verb}; in-character dialogue {dlg}."
    return line + " Lead @@CHOICES@@ option 1 toward this style; always include one lateral/clever option."


# ---- personas ----

PERSONA_FIELDS = ("name", "race", "faction", "role", "weapon", "element",
                  "appearance", "personality", "backstory", "voice", "notes")


def create_persona(data: dict):
    init_db()
    pid = "p_" + uuid.uuid4().hex[:10]
    now = time.time()
    vals = [data.get(k, "") for k in PERSONA_FIELDS]
    with _conn() as c:
        c.execute(
            f"INSERT INTO personas (id, {', '.join(PERSONA_FIELDS)}, created_at, updated_at) VALUES (?,{','.join('?'*len(PERSONA_FIELDS))},?,?)",
            (pid, *vals, now, now),
        )
    return pid


def get_persona(pid):
    init_db()
    with _conn() as c:
        r = c.execute("SELECT * FROM personas WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None


def list_personas():
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT * FROM personas ORDER BY updated_at DESC").fetchall()
    return [dict(r) for r in rows]


def update_persona(pid, data: dict):
    sets = [f"{k}=?" for k in PERSONA_FIELDS if k in data]
    if not sets:
        return
    vals = [data[k] for k in PERSONA_FIELDS if k in data]
    with _conn() as c:
        c.execute(f"UPDATE personas SET {', '.join(sets)}, updated_at=? WHERE id=?", (*vals, time.time(), pid))


def delete_persona(pid):
    with _conn() as c:
        c.execute("DELETE FROM personas WHERE id=?", (pid,))


def persona_block(p):
    """Render persona as GM prompt block."""
    if not p:
        return ""
    lines = [f"PLAYER PERSONA: {p.get('name','(unnamed)')}",
             f"Race: {p.get('race','')} | Faction: {p.get('faction','')} | Role: {p.get('role','')}",
             f"Weapon: {p.get('weapon','')} | Element: {p.get('element','')}"]
    for k in ("appearance", "personality", "backstory", "voice", "notes"):
        if p.get(k):
            lines.append(f"{k.capitalize()}: {p[k]}")
    lines.append("Play the world around this persona. Never speak or act for them.")
    return "\n".join(lines)
