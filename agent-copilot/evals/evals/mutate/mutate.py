"""Write a mutated copy of the dataset to a scratch dir (the original dataset/ is never modified).

Mutations (mirroring how the hidden test set may differ from the sample):
  1. rename some report files and update maintenance.report_file (E6 non-standard names)
  2. add N new wells by cloning + perturbing existing ones under fresh IDs (more wells)
  3. add a new well type T500 (manual with a verbatim-quotable 25 °C rule) and some T500 wells
  4. shift the whole horizon by +4 weeks (new "today")
  5. optional colliding-ID scenario: a cloned well that keeps the source's inspection/maintenance IDs
"""
from __future__ import annotations

import argparse
import json
import random
import re
import shutil
from datetime import date, timedelta
from pathlib import Path

from evals import CACHE_DIR, SAMPLE_DATA

T500_MANUAL = """# T500 Ultra-Deep Rod-Lift Oil Well — Operating & Maintenance Manual

> Fictional manual added by the evals mutation harness. Not for real-world use.

Document ID: OM-T500 · Revision 1 · Applies to all wells with `well_type = "T500"`

## 1. Scope

The T500 is an ultra-deep, high-rate rod-lift well. Procedures not listed here follow the T200 manual.

## 3. Production temperature rule

A T500 well is considered **producing (active)** only when its wellhead temperature is at least **25 °C above
ambient** air temperature at the time of the reading.

## 4. Temperature alarm

The wellhead must stay **at or below 105 °C**. Above 105 °C, shut in and inspect within 24 hours.

## 6. Maintenance escalation

Same as T200 section 10.
"""
T500_RULE = dict(param="production_delta_min_c", value=25.0, doc_id="OM-T500", section="3", quote="at least 25 °C above ambient")
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def _shift_dates(text: str, days: int) -> str:
    def f(m):
        try:
            return (date(int(m[1]), int(m[2]), int(m[3])) + timedelta(days=days)).isoformat()
        except ValueError:
            return m[0]
    return ISO_DATE.sub(f, text)


def _renumber(text: str, src: int, dst: int) -> str:
    return re.sub(rf"\b(WELL|INS|MNT|CTX|REG)-{src:04d}", lambda m: f"{m[1]}-{dst:04d}", text)


def make_mutated(out_dir: Path, seed: int = 11, n_new: int = 12, n_t500: int = 6, shift_days: int = 28,
                 n_rename: int = 25, collide: bool = False, src: Path = SAMPLE_DATA) -> dict:
    rnd = random.Random(seed)
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(src, out_dir)
    assets = out_dir / "assets"
    areas = json.loads((out_dir / "fleet/areas.json").read_text())
    manifest: dict = dict(seed=seed, shift_days=shift_days, new_wells=[], t500_wells=[], renamed=[], collide=collide)

    # 2+3: clone wells -------------------------------------------------------------------------------
    existing = sorted(int(p.name.split("-")[1]) for p in assets.glob("WELL-*"))
    next_n = max(existing) + 1
    sources = rnd.sample(existing, n_new)
    for k, s in enumerate(sources):
        n = next_n + k
        is_t500 = k < n_t500
        sd, dd = assets / f"WELL-{s:04d}", assets / f"WELL-{n:04d}"
        dd.mkdir()
        meta = json.loads(_renumber((sd / "metadata.json").read_text(), s, n))
        meta["name"] = re.sub(r"\d{3}$", f"{n % 1000:03d}", meta["name"])
        meta["location"]["lat"] = round(meta["location"]["lat"] + rnd.uniform(-0.01, 0.01), 5)
        meta["location"]["lon"] = round(meta["location"]["lon"] + rnd.uniform(-0.01, 0.01), 5)
        meta["installed_on"] = "2024-06-01"
        if is_t500:
            meta["well_type"] = "T500"
        (dd / "metadata.json").write_text(json.dumps(meta, indent=2))
        for fn in ("inspections.jsonl", "maintenance.jsonl", "regulatory-events.jsonl", "context.jsonl"):
            sf = sd / fn
            if not sf.exists():
                continue
            lines = []
            for line in sf.read_text().splitlines():
                if not line.strip():
                    continue
                o = json.loads(_renumber(line, s, n))
                if fn == "inspections.jsonl" and o.get("wellhead_temp_c") is not None:
                    o["wellhead_temp_c"] = round(o["wellhead_temp_c"] + rnd.uniform(-1.5, 1.5), 1)
                if fn == "maintenance.jsonl" and o.get("cost_usd"):
                    f = rnd.uniform(0.9, 1.1)
                    c = o["cost_usd"]
                    c["labor"], c["parts"] = round(c["labor"] * f), round(c["parts"] * f)
                    c["total"] = c["labor"] + c["parts"]
                lines.append(json.dumps(o))
            (dd / fn).write_text("\n".join(lines) + "\n")
        if (sd / "maintenance-reports").exists():
            (dd / "maintenance-reports").mkdir()
            for rp in (sd / "maintenance-reports").glob("*.md"):
                (dd / "maintenance-reports" / _renumber(rp.name, s, n)).write_text(_renumber(rp.read_text(), s, n))
        area = next(a for a in areas["areas"] if a["area_id"] == meta["area_id"])
        area["asset_ids"].append(meta["asset_id"])
        manifest["new_wells"].append(dict(asset_id=meta["asset_id"], cloned_from=f"WELL-{s:04d}", well_type=meta["well_type"]))
        if is_t500:
            manifest["t500_wells"].append(meta["asset_id"])
    (out_dir / "fleet/areas.json").write_text(json.dumps(areas, indent=2))
    if n_t500:
        (out_dir / "fleet/well-types/T500.md").write_text(T500_MANUAL)

    # 5: colliding IDs ------------------------------------------------------------------------------
    if collide:
        s = rnd.choice(existing)
        n = next_n + n_new
        sd, dd = assets / f"WELL-{s:04d}", assets / f"WELL-{n:04d}"
        shutil.copytree(sd, dd)
        meta = json.loads((dd / "metadata.json").read_text())
        meta["asset_id"], meta["name"] = f"WELL-{n:04d}", meta["name"] + " (collision clone)"
        (dd / "metadata.json").write_text(json.dumps(meta, indent=2))
        for fn in ("inspections.jsonl", "maintenance.jsonl", "regulatory-events.jsonl", "context.jsonl"):
            if (dd / fn).exists():  # asset_id changes, inspection_id / maintenance_id deliberately stay identical
                rows = [json.loads(x) for x in (dd / fn).read_text().splitlines() if x.strip()]
                for r in rows:
                    r["asset_id"] = f"WELL-{n:04d}"
                (dd / fn).write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        areas["areas"][0]["asset_ids"].append(f"WELL-{n:04d}")
        (out_dir / "fleet/areas.json").write_text(json.dumps(areas, indent=2))
        manifest["collision"] = dict(clone=f"WELL-{n:04d}", source=f"WELL-{s:04d}")

    # 4: horizon shift ------------------------------------------------------------------------------
    if shift_days:
        for p in list(assets.rglob("*.jsonl")) + list(assets.rglob("metadata.json")) + list(assets.rglob("*.md")):
            t = p.read_text()
            if p.name == "metadata.json":
                m = json.loads(t)
                keep = m.get("installed_on")
                m = json.loads(_shift_dates(t, shift_days))
                m["installed_on"] = keep
                p.write_text(json.dumps(m, indent=2))
            else:
                p.write_text(_shift_dates(t, shift_days))
        manifest["suggested_clock_today"] = (date(2026, 10, 17) + timedelta(days=shift_days)).isoformat()

    # 1: rename report files + update report_file ---------------------------------------------------
    pool = [p for p in assets.glob("*/maintenance.jsonl")]
    rnd.shuffle(pool)
    done = 0
    for mp in pool:
        if done >= n_rename:
            break
        rows = [json.loads(x) for x in mp.read_text().splitlines() if x.strip()]
        changed = False
        for r in rows:
            if r.get("report_file") and done < n_rename and rnd.random() < 0.2:
                old = mp.parent / r["report_file"]
                if not old.exists():
                    continue
                new_rel = f"maintenance-reports/{r['maintenance_id']}-renamed-{rnd.choice(['jan', 'mar', 'aug'])}-{r['equipment_kind']}.md"
                old.rename(mp.parent / new_rel)
                manifest["renamed"].append(dict(maintenance_id=r["maintenance_id"], old=r["report_file"], new=new_rel))
                r["report_file"] = new_rel
                changed = True
                done += 1
        if changed:
            mp.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    (out_dir / "MUTATION_MANIFEST.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=CACHE_DIR / "mutated" / "base")
    ap.add_argument("--collide", action="store_true")
    ap.add_argument("--seed", type=int, default=11)
    a = ap.parse_args()
    m = make_mutated(a.out, seed=a.seed, collide=a.collide)
    print(f"wrote {a.out}: +{len(m['new_wells'])} wells ({len(m['t500_wells'])} T500), {len(m['renamed'])} renamed reports, "
          f"shift +{m['shift_days']}d, collide={m['collide']}")


if __name__ == "__main__":
    main()
