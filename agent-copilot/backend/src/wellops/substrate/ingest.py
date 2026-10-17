"""Deterministic, idempotent ingest of one dataset folder into DuckDB.

No LLM is used here. Every row is tagged with `dataset_id` so the sample and test datasets can
live side by side. Dataset correction notes are imported as overlay assertions, so dataset notes and
runtime feedback are the same kind of knowledge.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import duckdb
import yaml

from wellops.settings import settings
from wellops.substrate import report_facts

HERE = Path(__file__).parent
INS_ID = re.compile(r"INS-\d{4}-\d{4}")
RAW_TABLES = ["areas", "teams", "users", "wells", "equipment", "inspections", "maintenance", "reg_events",
              "notes", "reports", "report_facts", "docs", "type_inherits", "dq_flags"]


def _many(con, sql: str, rows: list) -> None:
    if rows:
        con.executemany(sql, rows)


def connect(db_path: Path | None = None) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(db_path or settings.db_path))
    con.execute((HERE / "schema.sql").read_text())
    return con


def _jsonl(con, ds: str, data: Path, name: str):
    return con.sql(
        f"SELECT * FROM read_json('{data}/assets/*/{name}.jsonl', format='newline_delimited', "
        f"union_by_name=true, filename=true)"
    )


def ingest(data_dir: Path, ds: str, con: duckdb.DuckDBPyConnection) -> dict:
    data_dir = data_dir.resolve()
    for t in RAW_TABLES:
        con.execute(f"DELETE FROM {t} WHERE dataset_id = ?", [ds])
    con.execute("DELETE FROM assertions WHERE dataset_id = ? AND source = 'dataset_note'", [ds])

    fleet = json.loads((data_dir / "fleet/areas.json").read_text())
    listed = {w: a["area_id"] for a in fleet["areas"] for w in a["asset_ids"]}
    _many(con, "INSERT INTO areas VALUES (?,?,?,?,?,?)", [
        (ds, a["area_id"], a["name"], a["team_id"], a["center"]["lat"], a["center"]["lon"]) for a in fleet["areas"]])
    _many(con, "INSERT INTO teams VALUES (?,?,?,?)", [
        (ds, t["team_id"], t["name"], t.get("lead_user_id")) for t in fleet.get("teams", [])])
    users = json.loads((data_dir / "fleet/users.json").read_text())["users"]
    _many(con, "INSERT INTO users VALUES (?,?,?,?,?)", [
        (ds, u["user_id"], u["name"], u["role"], u.get("team_id")) for u in users])

    wells, equip = [], []
    for meta in sorted(data_dir.glob("assets/*/metadata.json")):
        m = json.loads(meta.read_text())
        li = m.get("last_inspection") or {}
        wells.append((ds, m["asset_id"], m["name"], m["well_type"], m["area_id"], m["team_id"],
                      m.get("in_charge_user_id"), m["location"]["lat"], m["location"]["lon"], m.get("installed_on"),
                      m.get("recorded_status"), li.get("inspection_id"), li.get("inspected_at"),
                      listed.get(m["asset_id"])))
        equip += [(ds, m["asset_id"], e["equipment_id"], e["kind"], e.get("model")) for e in m.get("equipment", [])]
    _many(con, f"INSERT INTO wells VALUES ({','.join('?' * 14)})", wells)
    _many(con, "INSERT INTO equipment VALUES (?,?,?,?,?)", equip)

    con.execute(f"""INSERT INTO inspections SELECT '{ds}', inspection_id, asset_id, inspected_at, inspector_user_id,
        method, ambient_temp_c, wellhead_temp_c, spill_detected, spill_area_pct, spill_est_volume_bbl,
        evidence_images, notes FROM read_json('{data_dir}/assets/*/inspections.jsonl', format='newline_delimited',
        union_by_name=true)""")
    con.execute(f"""INSERT INTO maintenance SELECT '{ds}', maintenance_id, asset_id, started_at, completed_at,
        maintenance_type, equipment_kind, issue, action_taken, technician_user_id, contractor, downtime_hours,
        cost_usd.labor, cost_usd.parts, cost_usd.total, triggered_by_inspection_id, report_file
        FROM read_json('{data_dir}/assets/*/maintenance.jsonl', format='newline_delimited', union_by_name=true)""")
    if list(data_dir.glob("assets/*/regulatory-events.jsonl")):
        con.execute(f"""INSERT INTO reg_events SELECT '{ds}', event_id, asset_id, date, agency, event_type,
            protocol_id, related_inspection_id, detail, hours_to_report, fine_usd, status
            FROM read_json('{data_dir}/assets/*/regulatory-events.jsonl', format='newline_delimited',
            union_by_name=true)""")

    notes, overlays = [], []
    for f in sorted(data_dir.glob("assets/*/context.jsonl")):
        for line in f.read_text().splitlines():
            if not line.strip():
                continue
            n = json.loads(line)
            in_text = INS_ID.search(n.get("text", ""))
            notes.append((ds, n["note_id"], n["asset_id"], n["created_at"], n["author_user_id"], n["note_type"],
                          n.get("inspection_id"), in_text.group(0) if in_text else None, n["text"],
                          n.get("valid_from"), n.get("valid_to")))
            # Only formal corrections override a detection (SOP-SR §7); feedback notes stay evidence.
            if n["note_type"] == "correction" and n.get("inspection_id"):
                overlays.append((f"{ds}:{n['note_id']}", ds, "detection_correction", "inspection",
                                 n["inspection_id"], "spill_is_real", 0.0, n["text"], 1.0, n["author_user_id"],
                                 n["text"], "dataset_note", n["note_id"], n.get("valid_from"), n.get("valid_to"),
                                 n["created_at"], "confirmed", "fleet", None))
    _many(con, f"INSERT INTO notes VALUES ({','.join('?' * 11)})", notes)
    _many(con, f"INSERT INTO assertions VALUES ({','.join('?' * 19)})", overlays)

    reports, facts = [], []
    for asset_id, mid, rel in con.execute(
            "SELECT asset_id, maintenance_id, report_file FROM maintenance WHERE dataset_id=? AND report_file IS NOT NULL",
            [ds]).fetchall():
        p = data_dir / "assets" / asset_id / rel
        if not p.exists():
            continue
        text = p.read_text()
        reports.append((ds, mid, asset_id, str(p.relative_to(data_dir)), text))
        facts += [(ds, mid, *f) for f in report_facts.extract(text)]
    _many(con, "INSERT INTO reports VALUES (?,?,?,?,?)", reports)
    _many(con, "INSERT INTO report_facts VALUES (?,?,?,?,?,?,?,?)", facts)

    cfg = yaml.safe_load((HERE / "rules.yaml").read_text())
    docs = []
    for doc_id, rel in cfg["docs"].items():
        docs += [(ds, doc_id, rel, *s) for s in split_sections((data_dir / rel).read_text())]
    for p in sorted((data_dir / "fleet/well-types").glob("*.md")):  # new manuals in test data still load
        rel = str(p.relative_to(data_dir))
        if rel not in cfg["docs"].values():
            docs += [(ds, f"OM-{p.stem}", rel, *s) for s in split_sections(p.read_text())]
    _many(con, "INSERT INTO docs VALUES (?,?,?,?,?,?)", docs)
    _many(con, "INSERT INTO type_inherits VALUES (?,?,?)",
                    [(ds, k, v) for k, v in cfg.get("inherits", {}).items()])
    con.execute("DELETE FROM rules")
    _many(con, "INSERT INTO rules VALUES (?,?,?,?,?,?,?,?)", [
        (r["param"], r["scope_level"], r["scope_key"], r["value"], r["unit"], r["doc_id"], r["section"], r["quote"])
        for r in cfg["rules"]])

    from wellops.substrate import views
    views.install(con)
    from wellops.trust import dq
    dq.compute(con, ds)
    return {t: con.execute(f"SELECT count(*) FROM {t} WHERE dataset_id=?", [ds]).fetchone()[0] for t in RAW_TABLES}


HEADING = re.compile(r"^(#{1,4})\s+(.*)$")
SECNUM = re.compile(r"^(\d+(?:\.\d+)*)\.?\s")


def split_sections(md: str) -> list[tuple[str, str, str]]:
    """Split markdown into (section_number, title, text) on headings; preamble is section '0'."""
    out, cur, title, buf = [], "0", "", []
    for line in md.splitlines():
        h = HEADING.match(line)
        if h and h.group(1) != "#":
            out.append((cur, title, "\n".join(buf).strip()))
            title = h.group(2).strip()
            num = SECNUM.match(title)
            cur, buf = (num.group(1) if num else title), []
        else:
            buf.append(line)
    out.append((cur, title, "\n".join(buf).strip()))
    return [s for s in out if s[2]]


def main() -> None:
    data = Path(sys.argv[1]) if len(sys.argv) > 1 else settings.data_dir
    ds = sys.argv[2] if len(sys.argv) > 2 else settings.dataset_id
    con = connect()
    counts = ingest(data, ds, con)
    print(json.dumps({"dataset_id": ds, **counts}, indent=2))


if __name__ == "__main__":
    main()
