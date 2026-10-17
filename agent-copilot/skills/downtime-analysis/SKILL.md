---
name: downtime-analysis
description: How many days a well/area was down, ranked downtime, downtime by month/area. Use for bar charts, rankings and totals over a period.
---
# Downtime playbook
- [dt-01] Downtime = 7 days x inspections in the window where the well was not producing. Use the downtime tool. NEVER use maintenance.downtime_hours (that is one repair job's duration).
- [dt-02] A missed inspection adds 0. Wells drilled mid-window have shorter history; do not treat that as downtime.
- [dt-03] Windows: "last N months" ends today and starts N months earlier; compare by date. Use resolve_entity for the dates and state them in assumptions. Windows are inclusive.
- [dt-04] Ranking: top_k with group_by="well"; ties are broken by well id; show the next one if the cut-off ties.
- [dt-05] Explain a down period by listing non_producing_dates and, when asked why, the explanations from well_status or the repair that overlaps (evidence_chain). Do not guess causes.
- [dt-06] Corrected readings change downtime: the tools already apply confirmed overlays; disclose them.
- [dt-07] Comparing a few wells/areas -> bar; value over time -> line; two dimensions -> heatmap.
