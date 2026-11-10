"""SQLite hot cache in front of the agent (DuckDB stays the analytics engine).

- turns: a verified answer to the same question (same clock, dataset and data version) replays instantly.
- tools: MCP tool results by canonical args, so agent retries and repeated lookups cost nothing.
data_version() changes whenever an assertion or an upload lands, which invalidates both.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from typing import Any

from wellops.settings import ROOT, settings

_LOCK = threading.Lock()
_CON: sqlite3.Connection | None = None
MEMORY_WORDS = re.compile(r"\b(we|our|my|me|i|yesterday|earlier|last time|discussed|remember)\b", re.I)


def con() -> sqlite3.Connection:
    global _CON
    if _CON is None:
        path = settings.db_path.with_suffix(".cache.sqlite")  # one cache per database, never shared across servers
        _CON = sqlite3.connect(path, check_same_thread=False)
        _CON.execute("PRAGMA journal_mode=WAL")
        _CON.execute("CREATE TABLE IF NOT EXISTS turns (k TEXT PRIMARY KEY, question TEXT, payload TEXT, ms INTEGER, at REAL)")
        _CON.execute("CREATE TABLE IF NOT EXISTS tools (k TEXT PRIMARY KEY, payload TEXT, at REAL)")
        _CON.execute("CREATE TABLE IF NOT EXISTS chat_turns (chat TEXT, principal TEXT, at REAL, question TEXT, events TEXT)")
    return _CON


def data_version() -> str:
    from wellops.tools.core import get_db
    db = get_db()
    # every assertion's id, status and value: a new correction, an undo (status flip) or a supersede all change it
    a = db.rows("""SELECT count(*) n, coalesce(md5(string_agg(assertion_id || ':' || coalesce(status, '') || ':' ||
                   coalesce(value_num::VARCHAR, '') || ':' || coalesce(value_text, '') || ':' || coalesce(superseded_by, ''),
                   ',' ORDER BY assertion_id)), '') t FROM assertions""")[0]
    try:
        m = db.rows("SELECT count(*) n FROM media")[0]["n"]
    except Exception:
        m = 0
    try:
        u = db.rows("SELECT count(*) n FROM information_schema.tables WHERE table_name LIKE 'user_%'")[0]["n"]
    except Exception:
        u = 0
    return f"{a['n']}:{a['t']}:{m}:{u}"


def norm(q: str) -> str:
    return re.sub(r"[^\w\s-]", "", re.sub(r"\s+", " ", q.strip().lower()))


def code_version() -> str:
    """Prompt + tool definitions: any change to how answers are produced invalidates cached answers."""
    global _CODE
    if _CODE is None:
        from wellops import mcp_server
        from wellops.api import turn_stream
        tools = [(t.name, t.description, json.dumps(t.parameters, sort_keys=True)) for t in mcp_server.mcp._tool_manager.list_tools()]
        _CODE = hashlib.sha1(json.dumps([turn_stream.SYSTEM, tools]).encode()).hexdigest()[:12]
    return _CODE


_CODE: str | None = None


def turn_key(question: str, principal: str, today: Any, model: str = "") -> str:
    who = principal if MEMORY_WORDS.search(question) else "*"
    if who != "*":  # memory questions also depend on what this user said since (new episodes)
        from wellops.tools.core import get_db
        try:
            who += ":" + str(get_db().rows("SELECT count(*) n FROM episodes WHERE principal=? AND NOT forgotten", [principal])[0]["n"])
        except Exception:  # noqa: BLE001
            pass
    try:  # a user with saved preferences gets answers formatted for them: never someone else's cached copy
        from wellops.api.turn_stream import _prefs
        from wellops.contracts import RunDeps
        p = _prefs(RunDeps(principal=principal, chat_id="-", today=today, active_dataset=settings.active_dataset))
        if p:
            who = f"{principal}:{hashlib.sha1(p.encode()).hexdigest()[:8]}:{who}"
    except Exception:  # noqa: BLE001
        pass
    raw = f"{norm(question)}|{today}|{settings.active_dataset}|{data_version()}|{who}|{model}|{code_version()}"
    return hashlib.sha1(raw.encode()).hexdigest()


def get_turn(k: str) -> dict | None:
    with _LOCK:
        r = con().execute("SELECT payload, ms FROM turns WHERE k=?", [k]).fetchone()
    return {**json.loads(r[0]), "ms": r[1]} if r else None


def put_turn(k: str, question: str, payload: dict, ms: int) -> None:
    with _LOCK:
        con().execute("INSERT OR REPLACE INTO turns VALUES (?,?,?,?,?)",
                      [k, question, json.dumps(payload, default=str), ms, time.time()])
        con().commit()


def tool_key(name: str, args: tuple, kwargs: dict, today: Any) -> str:
    raw = json.dumps([name, args, kwargs, str(today), settings.active_dataset, data_version()], default=str, sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()


def get_tool(k: str) -> str | None:
    with _LOCK:
        r = con().execute("SELECT payload FROM tools WHERE k=?", [k]).fetchone()
    return r[0] if r else None


def put_tool(k: str, payload: str) -> None:
    with _LOCK:
        con().execute("INSERT OR REPLACE INTO tools VALUES (?,?,?)", [k, payload, time.time()])
        con().commit()


def save_chat_turn(chat: str, principal: str, question: str, events: list[dict]) -> None:
    """The transcript of one turn, so a chat opens from any browser (the sidebar lists server chats too)."""
    with _LOCK:
        con().execute("INSERT INTO chat_turns VALUES (?,?,?,?,?)", [chat, principal, time.time(), question, json.dumps(events, default=str)])
        con().commit()


def chat_turns(chat: str, principal: str) -> list[dict]:
    with _LOCK:
        rows = con().execute("SELECT question, events, at FROM chat_turns WHERE chat=? AND principal=? ORDER BY at", [chat, principal]).fetchall()
    return [{"question": q, "events": json.loads(e), "at": at} for q, e, at in rows]
