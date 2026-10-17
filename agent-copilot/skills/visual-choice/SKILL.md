---
name: visual-choice
description: Pick the visual for an answer; honour an explicit request.
---
# Visual choice playbook
- [vc-01] If the user names a visual (card, table, chart, map, heatmap) use it and set requested_by_user=true.
- [vc-02] Otherwise: one well now -> status_card; list of wells/events/repairs -> table; a value over time -> line; compare a few wells or areas -> bar; where -> map; two dimensions (area x month) -> heatmap; obligations -> obligation_timeline; a plain fact -> none.
- [vc-03] Bind the visual to a result handle (visual.handle); never copy rows into the visual. The result shape must fit the visual type.
- [vc-04] Line charts of readings: encodings x=date, y=wellhead_c and ambient_c; annotate the threshold and the not-producing readings.
- [vc-05] Corrections: correction_diff; disagreements: conflict_card; provenance: evidence_chain.
