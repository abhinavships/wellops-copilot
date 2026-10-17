---
name: sop-compliance-audit
description: Which rules were broken / are still open for a well or area (spill SOP, regulatory SOP, manual obligations).
---
# SOP compliance audit playbook
- [sc-01] Call obligations(scope, start, end). It returns met | breached | pending per rule with due dates and evidence ids; thresholds come from params, never from memory.
- [sc-02] SR-01: written notice for reportable/major spills within the limit from the inspection time. hours_to_report measures the written notice. Never derive notice timing from the NOV date.
- [sc-03] SR-02 containment time exists only in repair report text (report_facts, with spans). Cite the report.
- [sc-04] SR-03: agency verification by a passing, closed site inspection within 60 days; an open site inspection past the due date is a breach still open.
- [sc-05] RC-04: a notice of violation needs a nov_response_filed within 30 days; none recorded and past due = breached.
- [sc-06] RC-02: one flowline integrity test per calendar year (maintenance issue "Annual flowline integrity test").
- [sc-07] Manual section 10 (RCA) and section 4 (planned shut-in must be recorded in the maintenance system before work) are open actions: report them as pending, not breached.
- [sc-08] Separate "broken" (breached) from "still open" (pending, or breached with an open regulator event). List fines and event status.
- [sc-09] Use evidence_chain(id) to show inspection -> repair -> report -> regulatory events for any row. Read the SOP text with read_doc when a rule is unclear.
- [sc-10] Reports may be hand-written outliers with nonstandard filenames; follow report_file, and quote the report.
- [sc-11] Visual: obligation_timeline or table.
