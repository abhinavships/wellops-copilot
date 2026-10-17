"""Canonical views, as DuckDB table macros parameterised by (dataset, as-of timestamp).

Each view is `raw ⊕ confirmed overlays as of at_ts`, and keeps the original values next to the effective
ones, so originals are never lost. These are the only relations agents are told to query.
"""

MACROS = r"""
-- Latest confirmed overlay per (inspection, attribute) as of a timestamp.
CREATE OR REPLACE MACRO ov(ds, at_ts, attr) AS TABLE
SELECT scope_key, arg_max(value_num, tx_time) AS value_num, arg_max(assertion_id, tx_time) AS assertion_id,
       arg_max(author_user_id, tx_time) AS author_user_id, arg_max(original_num, tx_time) AS original_num
FROM assertions
WHERE dataset_id = ds AND status = 'confirmed' AND superseded_by IS NULL AND tx_time <= at_ts
  AND scope_level = 'inspection' AND target_attr = attr
GROUP BY scope_key;

-- Effective parameter per well: most specific of runtime override > manual rule > inherited rule > fleet rule.
CREATE OR REPLACE MACRO params(ds, at_ts) AS TABLE
WITH w AS (SELECT asset_id, well_type, area_id FROM wells WHERE dataset_id = ds),
cand AS (
  SELECT w.asset_id, a.target_attr AS param, a.value_num AS value,
         CASE a.scope_level WHEN 'well' THEN 1 WHEN 'area' THEN 2 WHEN 'well_type' THEN 3 ELSE 4 END AS prio,
         'assertion:' || a.assertion_id AS source
  FROM w JOIN assertions a
    ON a.dataset_id = ds AND a.kind = 'rule_override' AND a.status = 'confirmed' AND a.superseded_by IS NULL
   AND a.tx_time <= at_ts
   AND ((a.scope_level = 'well' AND a.scope_key = w.asset_id) OR (a.scope_level = 'area' AND a.scope_key = w.area_id)
     OR (a.scope_level = 'well_type' AND a.scope_key = w.well_type) OR a.scope_level = 'fleet')
  UNION ALL
  SELECT w.asset_id, r.param, r.value, 5, 'rule:' || r.doc_id || ' §' || r.section
  FROM w JOIN rules r ON r.scope_level = 'well_type' AND r.scope_key = w.well_type
  UNION ALL
  SELECT w.asset_id, r.param, r.value, 6, 'rule:' || r.doc_id || ' §' || r.section || ' (inherited)'
  FROM w JOIN type_inherits t ON t.dataset_id = ds AND t.well_type = w.well_type
         JOIN rules r ON r.scope_level = 'well_type' AND r.scope_key = t.parent
  UNION ALL
  SELECT w.asset_id, r.param, r.value, 7, 'rule:' || r.doc_id || ' §' || r.section
  FROM w JOIN rules r ON r.scope_level = 'fleet')
SELECT asset_id, param, arg_min(value, prio) AS value, arg_min(source, prio) AS source
FROM cand GROUP BY asset_id, param;

-- Inspections with overlays applied; production derived from Δ vs the well's effective threshold.
CREATE OR REPLACE MACRO ins_eff(ds, at_ts) AS TABLE
SELECT i.inspection_id, i.asset_id, w.well_type, w.area_id, i.inspected_at, i.inspector_user_id, i.method,
       i.ambient_temp_c, i.wellhead_temp_c, i.spill_detected, i.spill_area_pct, i.spill_est_volume_bbl, i.notes,
       coalesce(wh.value_num, i.wellhead_temp_c) AS wellhead_eff, wh.assertion_id AS wellhead_overlay,
       coalesce(am.value_num, i.ambient_temp_c) AS ambient_eff, am.assertion_id AS ambient_overlay,
       coalesce(vo.value_num, i.spill_est_volume_bbl) AS volume_eff, vo.assertion_id AS volume_overlay,
       dc.assertion_id AS detection_correction,
       i.spill_detected AND dc.assertion_id IS NULL AS spill_real,
       wellhead_eff - ambient_eff AS delta_c,
       p.value AS delta_min_c, p.source AS delta_rule,
       delta_c >= delta_min_c AS producing
FROM inspections i
JOIN wells w ON w.dataset_id = ds AND w.asset_id = i.asset_id
JOIN params(ds, at_ts) p ON p.asset_id = i.asset_id AND p.param = 'production_delta_min_c'
LEFT JOIN ov(ds, at_ts, 'wellhead_temp_c') wh ON wh.scope_key = i.inspection_id
LEFT JOIN ov(ds, at_ts, 'ambient_temp_c') am ON am.scope_key = i.inspection_id
LEFT JOIN ov(ds, at_ts, 'spill_est_volume_bbl') vo ON vo.scope_key = i.inspection_id
LEFT JOIN ov(ds, at_ts, 'spill_is_real') dc ON dc.scope_key = i.inspection_id AND dc.value_num = 0
WHERE i.dataset_id = ds AND i.inspected_at <= at_ts;

-- "Right now" = each well's latest inspection as of the clock.
CREATE OR REPLACE MACRO latest_status(ds, at_ts) AS TABLE
SELECT e.*, w.recorded_status,
       (w.recorded_status = 'active') <> e.producing AS recorded_status_stale
FROM ins_eff(ds, at_ts) e JOIN wells w ON w.dataset_id = ds AND w.asset_id = e.asset_id
QUALIFY row_number() OVER (PARTITION BY e.asset_id ORDER BY e.inspected_at DESC) = 1;

-- Real spills with SOP class computed from the effective (possibly corrected) volume.
CREATE OR REPLACE MACRO spills_real(ds, at_ts) AS TABLE
SELECT e.*, w.lat, w.lon,
       CASE WHEN e.volume_eff >= pm.value THEN 'major' WHEN e.volume_eff >= pr.value THEN 'reportable'
            WHEN e.volume_eff >= pmod.value THEN 'moderate' ELSE 'minor' END AS spill_class
FROM ins_eff(ds, at_ts) e
JOIN wells w ON w.dataset_id = ds AND w.asset_id = e.asset_id
JOIN params(ds, at_ts) pm ON pm.asset_id = e.asset_id AND pm.param = 'spill_class_major_min_bbl'
JOIN params(ds, at_ts) pr ON pr.asset_id = e.asset_id AND pr.param = 'spill_class_reportable_min_bbl'
JOIN params(ds, at_ts) pmod ON pmod.asset_id = e.asset_id AND pmod.param = 'spill_class_moderate_min_bbl'
WHERE e.spill_real;

-- Human context whose validity window covers a given reading (notes + runtime shared facts/claims).
CREATE OR REPLACE MACRO context_for(ds, at_ts) AS TABLE
SELECT n.asset_id, n.note_id AS ref, n.note_type AS kind, n.author_user_id, n.text, n.valid_from, n.valid_to,
       coalesce(n.inspection_id, n.inspection_id_in_text) AS inspection_ref, 'dataset_note' AS source,
       'confirmed' AS status
FROM notes n WHERE n.dataset_id = ds AND n.created_at <= at_ts
UNION ALL
SELECT CASE WHEN a.scope_level = 'well' THEN a.scope_key END, a.assertion_id, a.kind, a.author_user_id,
       coalesce(a.value_text, a.reason), a.valid_from, a.valid_to,
       CASE WHEN a.scope_level = 'inspection' THEN a.scope_key END, a.source, a.status
FROM assertions a
WHERE a.dataset_id = ds AND a.source = 'chat' AND a.tx_time <= at_ts AND a.superseded_by IS NULL;

-- Repairs with open/closed state as of the clock.
CREATE OR REPLACE MACRO repairs(ds, at_ts) AS TABLE
SELECT m.*, m.completed_at > at_ts AS open_as_of
FROM maintenance m WHERE m.dataset_id = ds AND m.started_at <= at_ts;
"""


def install(con) -> None:
    con.execute(MACROS)
