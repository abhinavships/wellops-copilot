---
name: spill-accounting
description: Counting spills, volumes, classes, maps and heatmaps of spills, corrected detections, reportable spills.
---
# Spill accounting playbook
- [sp-01] A real spill = detected AND not corrected by a correction note/assertion. Use the spills tool; corrected detections are listed under the related handle and are excluded from counts and totals.
- [sp-02] Counts and totals: call spills(group_by="none") (or area/month/area_month) and bind the numbers with claims. Do not add numbers by hand.
- [sp-03] Class comes from the effective (possibly corrected) volume: minor <1, moderate 1..<5, reportable 5..<25, major >=25 bbl. Thresholds come from params; if a runtime rule changed them, say so.
- [sp-04] Volume is an AI estimate from images; a human correction wins and the estimate stays visible (estimated_bbl vs volume_bbl).
- [sp-05] Reportability is recomputed after every correction. A newly reportable spill implies SR-01 and SR-03 obligations: call obligations.
- [sp-06] A repair report that classes the spill by the old estimate is stale after a correction; say so.
- [sp-07] Notes on a spill ("Pooled fluid near flowline") give context only, never a volume.
- [sp-08] Disagreeing notes about whether a spill was real are NOT corrections. The detection stands unless a formal correction exists; show both notes with authors.
- [sp-09] Windows are inclusive date ranges; "last 90 days" = today-90d..today. Heatmap: area x month via group_by="area_month". Map: geo_points rows with lat/lon.
