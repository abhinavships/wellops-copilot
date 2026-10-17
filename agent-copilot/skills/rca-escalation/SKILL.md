---
name: rca-escalation
description: Which wells need a root-cause analysis (repeat corrective repairs on the same equipment) and rule scope for well types.
---
# RCA and escalation playbook
- [rc-01] Use rca_candidates(scope, equipment_kind). Count = corrective repairs by started_at on the same equipment kind in the 12 months before today.
- [rc-02] The threshold per well comes from params: runtime rule override (most specific scope) > the well type's manual > inherited manual (T350 defers to T200 section 10).
- [rc-03] A rule given by a user for one well type applies to that type only. Do not apply it fleet-wide; explain why a same-count well of another type is not included (include_below=True shows it).
- [rc-04] When the answer depends on a user rule, say it follows that user's rule, not the manual (see the threshold_source / overlay).
- [rc-05] RCA outcomes are open actions (pending). Cite the repair ids and any report marked "Action required".
- [rc-06] More than 14 consecutive days down must be reported to the area supervisor (RC-03); check downtime dates if asked.
