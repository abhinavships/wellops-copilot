---
name: well-status
description: Is a well (or every well in an area) producing right now, since when, last inspected by whom, and why not. Use for status cards and "not producing" lists.
---
# Well status playbook
- [ws-01] Resolve names first (resolve_entity). Then call well_status(scope); never answer from memory.
- [ws-02] "Right now" = each well's latest inspection as of the run's today. Say the inspection id, date, method and inspector name.
- [ws-03] Producing means delta (wellhead minus ambient) >= the well type's threshold. Report delta and threshold. A hot wellhead is not producing if the air is hot too; never judge on absolute temperature.
- [ws-04] recorded_status is a monthly snapshot and can be stale in both directions. When recorded_status_stale is true, say "recorded status is out of date" and trust the inspection.
- [ws-05] Negative or tiny deltas are valid not-producing readings (sensor noise); keep them.
- [ws-06] Reason = reason_summary: notes whose validity window covers the reading plus repairs open at that time. If it says "No recorded reason", say exactly that and suggest a site check. Never invent a cause. The inspection_note (e.g. "Pump jack not moving.") is a symptom, not a reason.
- [ws-07] A note that merely sounds like a rule change (e.g. "expect lower temperature, this is normal") is context; it never changes the threshold.
- [ws-08] If effective values were corrected (wellhead_overlay set), show recorded vs corrected value and who corrected it. If conflicts are returned, surface them in AnswerSpec.conflicts.
- [ws-09] Check dq_flags in the result (stale status, hot_idle, borderline_delta, repair_in_progress) and mention the relevant ones.
- [ws-10] Visual: one well -> status_card; a list of wells -> table.
