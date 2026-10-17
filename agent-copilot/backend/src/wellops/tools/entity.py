"""resolve_entity: map free text to ids (wells, areas, people, well types, evidence ids) and relative dates."""

from __future__ import annotations

import re
from datetime import date, timedelta

from wellops.contracts import RunDeps

from .core import ID_RE, Db, ToolOut, finish, get_db, month_add

MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
NUMW = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
        "nine": 9, "ten": 10, "twelve": 12}
UNIT = r"(day|week|month|quarter|year)s?"


def _num(s: str) -> int:
    return int(s) if s.isdigit() else NUMW[s]


def parse_dates(text: str, today: date) -> list[dict]:
    """Relative and absolute date expressions -> inclusive [start, end] windows. Convention: `last N units`
    ends today and starts N units earlier (so 'last 4 months' on 16 Oct = 16 Jun..16 Oct)."""
    t = text.lower()
    out: list[dict] = []

    def add(label, start, end, kind="window"):
        out.append({"kind": "date_range", "id": f"{start}..{end}", "name": label, "start": str(start), "end": str(end),
                    "match": kind})

    for m in re.finditer(rf"(?:last|past|previous)\s+(\d+|{'|'.join(NUMW)})\s+{UNIT}", t):
        n, u = _num(m[1]), m[2]
        start = {"day": today - timedelta(days=n), "week": today - timedelta(weeks=n),
                 "month": month_add(today, -n), "quarter": month_add(today, -3 * n),
                 "year": month_add(today, -12 * n)}[u]
        add(m[0], start, today)
    if re.search(r"\byesterday\b", t):
        add("yesterday", today - timedelta(days=1), today - timedelta(days=1), "day")
    if re.search(r"\btoday\b|\bright now\b|\bcurrent(ly)?\b", t):
        add("today", today, today, "day")
    if re.search(r"\blast week\b", t):
        add("last week", today - timedelta(days=7), today)
    if re.search(r"\blast month\b", t):
        add("last month", month_add(today, -1), today)
    if re.search(r"\bthis year\b|\byear to date\b|\bytd\b", t):
        add("this year", date(today.year, 1, 1), today)
    if re.search(r"\blast year\b", t):
        add("last year", date(today.year - 1, 1, 1), date(today.year - 1, 12, 31))
    for m in re.finditer(r"\b(?:in|during|for|of)\s+(20\d\d)\b", t):
        y = int(m[1])
        add(m[1], date(y, 1, 1), min(date(y, 12, 31), today))
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", t):
        d = date(int(m[1]), int(m[2]), int(m[3]))
        add(m[0], d, d, "day")
    for m in re.finditer(r"\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?(?:\s+(20\d\d))?", t):
        y = int(m[3]) if m[3] else today.year
        try:
            d = date(y, MONTHS[m[2]], int(m[1]))
        except ValueError:
            continue
        if d > today and not m[3]:
            d = date(y - 1, d.month, d.day)
        add(m[0].strip(), d, d, "day")
    return out


def resolve(db: Db, deps: RunDeps, text: str) -> list[dict]:
    ds, rows = deps.active_dataset, []
    for m in dict.fromkeys(re.findall(r"WELL-\d{4}", text, re.I)):
        rows.append({"kind": "well", "id": m.upper(), "name": m.upper(), "match": "id"})
    for m in re.finditer(r"\bwell\s+#?(\d{1,4})\b", text, re.I):
        rows.append({"kind": "well", "id": f"WELL-{int(m[1]):04d}", "name": f"WELL-{int(m[1]):04d}", "match": "short"})
    for m in dict.fromkeys(ID_RE.findall(text)):
        rows.append({"kind": {"INS": "inspection", "MNT": "maintenance", "REG": "reg_event", "CTX": "note"}[m[:3]],
                     "id": m, "name": m, "match": "id"})
    low = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    squashed = re.sub(r"[^a-z0-9]", "", text.lower())
    for a in db.rows("SELECT area_id, name FROM areas WHERE dataset_id=?", [ds]):
        nm = re.sub(r"[^a-z0-9]", "", a["name"].lower())
        short = re.sub(r"[^a-z0-9]", "", a["area_id"].lower())
        if nm in squashed or re.search(rf"\b{re.escape(a['area_id'].lower().replace('-', '[- ]?'))}\b", text.lower()) or \
                (short in squashed and len(short) >= 4):
            rows.append({"kind": "area", "id": a["area_id"], "name": a["name"], "match": "name"})
    users = db.rows("SELECT user_id, name, role FROM users WHERE dataset_id=?", [ds])
    for u in users:
        first, last = u["name"].lower().split()[0], u["name"].lower().split()[-1]
        if u["name"].lower() in low or re.search(rf"\b{u['user_id']}\b", text, re.I):
            rows.append({"kind": "user", "id": u["user_id"], "name": u["name"], "match": "name", "role": u["role"]})
        elif re.search(rf"\b{first}\b", low) and sum(1 for x in users if x["name"].lower().startswith(first + " ")) == 1:
            rows.append({"kind": "user", "id": u["user_id"], "name": u["name"], "match": "first_name", "role": u["role"]})
    if re.search(r"\b(i|me|my|myself)\b", low):
        me = next((u for u in users if u["user_id"] == deps.principal), None)
        if me:
            rows.append({"kind": "user", "id": me["user_id"], "name": me["name"], "match": "self", "role": me["role"]})
    for t in dict.fromkeys(re.findall(r"\bT\d{3}\b", text, re.I)):
        rows.append({"kind": "well_type", "id": t.upper(), "name": t.upper(), "match": "id"})
    rows += parse_dates(text, deps.today)
    seen, out = set(), []
    for r in rows:
        k = (r["kind"], r["id"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def resolve_entity(deps: RunDeps, text: str) -> ToolOut:
    db = get_db()
    rows = resolve(db, deps, text)
    cols = ["kind", "id", "name", "match", "start", "end", "role"]
    rows = [{c: r.get(c) for c in cols} for r in rows]
    return finish(db, deps, "entity_list", cols, rows, sql=None,
                  notes=[f"relative dates resolved against today={deps.today}; 'last N units' ends today"])
