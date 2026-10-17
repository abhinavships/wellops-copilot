"""Extract facts that exist only in free-text maintenance reports, keeping character spans for citation.

Regex covers the templated reports; hand-written outliers (e.g. "contained about 7 hours after the
inspection flight") are matched by the looser patterns. Fields a regex can't find are left for the
LLM fallback extractor, which writes rows with extractor='llm'.
"""

from __future__ import annotations

import re

PATTERNS: dict[str, re.Pattern] = {
    "containment_hours": re.compile(r"contained about \**\s*([\d.]+) hours", re.I),
    "hours_to_report": re.compile(r"filed ([\d.]+) hours after detection", re.I),
    "spill_class_stated": re.compile(r"\((minor|moderate|reportable|major) class\)", re.I),
    "triggered_by": re.compile(r"Triggered by:\*\*\s*inspection (INS-\d{4}-\d{4})"),
    "report_written": re.compile(r"Report written:\*\*\s*([^\n]+)"),
    "total_cost_usd": re.compile(r"\*\*Total\*\*\s*\|\s*\*\*([\d,]+)\*\*"),
    "hse_notice_hours": re.compile(r"HSE notified ([\d.]+) hours after discovery", re.I),
    "max_tph_pct": re.compile(r"highest result after clean-up was ([\d.]+)% TPH", re.I),
    "soil_samples": re.compile(r"(\d+) soil samples taken", re.I),
    "root_cause": re.compile(r"## Root cause\s*\n\s*\n(.+?)(?:\n\s*\n|\Z)", re.S),
}
COMPLIANCE_ROW = re.compile(
    r"^\|\s*(?P<proto>[A-Z]{2}-\d\d|Manual §\d+|HSE notice)\s*\|\s*\**(?P<result>[^|*]+?)\**\s*\|\s*(?P<detail>[^|]*)\|",
    re.M)


def _num(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def extract(text: str) -> list[tuple]:
    """Return (field, value, num, span_start, span_end, extractor) rows."""
    rows = []
    for field, pat in PATTERNS.items():
        m = pat.search(text)
        if m:
            v = m.group(1).strip()
            rows.append((field, v, _num(v), m.start(), m.end(), "regex"))
    for m in COMPLIANCE_ROW.finditer(text):
        rows.append((f"compliance:{m['proto']}", f"{m['result'].strip()} — {m['detail'].strip()}", None,
                     m.start(), m.end(), "regex"))
    return rows
