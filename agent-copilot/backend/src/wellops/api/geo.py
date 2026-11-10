"""Well locations for the UI maps: GET /api/wells/locations?ids=WELL-0001,WELL-0002 (or ?area=NE-08 / an area name).

Read-only lookup from DuckDB. A map pin needs id, name, area, coordinates and recorded status, plus the latest
effective reading (producing per the delta rule) so its popup can say something useful.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from wellops.tools.core import ToolError, area_ids, ds, get_db, in_list, mac

router = APIRouter()
MAX_IDS = 500


@router.get("/api/wells/locations")
def well_locations(request: Request, ids: str | None = Query(None, description="comma-separated asset ids"),
                   area: str | None = Query(None, description="area id or name")) -> dict:
    from .app import deps_from  # late import: app includes this router
    deps = deps_from(request)
    db = get_db()
    preds = []
    if ids:
        wanted = [i.strip().upper() for i in ids.split(",") if i.strip()][:MAX_IDS]
        if wanted:
            preds.append(f"w.asset_id IN {in_list(wanted)}")
    if area:
        try:
            preds.append(f"w.area_id IN {in_list(area_ids(db, deps, [area]))}")
        except ToolError as e:
            raise HTTPException(404, str(e)) from e
    if not preds:
        raise HTTPException(400, "pass ids=WELL-0001,... and/or area=<area id or name>")
    sql = f"""SELECT w.asset_id, w.name, w.area_id, a.name AS area, w.lat, w.lon, w.recorded_status,
  s.inspected_at AS last_inspected_at, round(s.delta_c, 1) AS last_delta_c, s.delta_min_c AS threshold_c, s.producing
FROM wells w LEFT JOIN areas a ON a.dataset_id = w.dataset_id AND a.area_id = w.area_id
LEFT JOIN (SELECT asset_id, inspected_at, delta_c, delta_min_c, producing FROM {mac(deps, 'ins_eff')}
           QUALIFY row_number() OVER (PARTITION BY asset_id ORDER BY inspected_at DESC) = 1) s ON s.asset_id = w.asset_id
WHERE w.dataset_id = {ds(deps)} AND {' AND '.join(preds)} AND w.lat IS NOT NULL AND w.lon IS NOT NULL
ORDER BY w.asset_id"""
    rows = db.rows(sql)
    for r in rows:
        if r.get("last_inspected_at") is not None:
            r["last_inspected_at"] = str(r["last_inspected_at"])
    return {"wells": rows, "count": len(rows)}
