"""Retrieval: BM25 over name + description + triggers (+ manual body), with entity mentions normalized to slot names
so 'Northeast Block 7' and 'Southwest Block 23' both read as {area}. Relevance is then weighted by the skill's
Laplace success rate and decayed by failures, so reliable playbooks outrank flaky ones on ties."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from .distill import normalize_text

K1, B = 1.2, 0.75
STOP = set("""a an and are as at be by did do does for from had has have how i in is it its me my of on or our show
that the their them then there these they this to us was we were what when where which who why will with you your
give tell list please can could would should most many much""".split())
SYNONYMS = {"down": "downtime", "offline": "downtime", "outage": "downtime", "leak": "spills", "leaks": "spills",
            "spill": "spills", "repairs": "repair", "cost": "costs", "chart": "visual", "graph": "visual",
            "top": "rank", "highest": "rank", "worst": "rank", "ranking": "rank", "ranked": "rank", "sop": "obligations",
            "rules": "obligations", "breached": "obligations", "broken": "obligations", "rca": "root-cause",
            "status": "status", "producing": "status", "manual": "doc", "says": "doc"}


def tokens(text: str) -> list[str]:
    t = normalize_text(text or "").lower()
    t = re.sub(r"\{([\w.]+)\}", lambda m: " slot_" + m.group(1).split(".")[0] + " ", t)
    out = []
    for w in re.findall(r"[a-z_][a-z0-9_-]*", t):
        for part in ([w] if w.startswith("slot_") else w.split("-")):
            if len(part) < 2 or part in STOP:
                continue
            out.append(SYNONYMS.get(part, part.rstrip("s") if len(part) > 4 and not part.endswith("ss") else part))
    return out


def keywords_for(s: dict[str, Any]) -> list[str]:
    """The stored retrieval text of a skill, tokenized (name x2, description, triggers, tools, manual body)."""
    parts = [s.get("name") or "", s.get("name") or "", s.get("description") or "", " ".join(s.get("triggers") or []),
             " ".join(s.get("tools") or []), " ".join(st.get("tool", "") for st in s.get("steps") or [] if isinstance(st, dict)),
             (s.get("body") or "")[:1500]]
    return tokens(" ".join(parts))


def rank(question: str, skills: list[dict[str, Any]], k: int = 3) -> list[dict[str, Any]]:
    q = [t for t in dict.fromkeys(tokens(question))]
    if not q or not skills:
        return []
    docs = [Counter(s.get("keywords") or keywords_for(s)) for s in skills]
    n = len(docs)
    avg = sum(sum(d.values()) for d in docs) / n or 1.0
    df = Counter(t for d in docs for t in d)
    out = []
    for s, d in zip(skills, docs):
        dl = sum(d.values()) or 1
        bm = 0.0
        for t in q:
            if t not in d:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            bm += idf * d[t] * (K1 + 1) / (d[t] + K1 * (1 - B + B * dl / avg))
        if bm <= 0:
            continue
        rate = s.get("success_rate", 0.5)
        score = bm * (0.6 + 0.8 * rate)  # 0.6x for a skill that keeps failing .. 1.4x for a proven one
        out.append({**s, "score": round(score, 3), "relevance": round(bm, 3)})
    out.sort(key=lambda r: (-r["score"], -r["success_rate"], -(r.get("uses") or 0), r["skill_id"]))
    return out[:k]
