---
name: feedback-intake
description: A user states a correction, a rule, a shared fact or a claim. Decide the kind, scope and commit it safely.
---
# Feedback intake playbook
- [fi-01] Classify first. Raw-data correction (a reading is wrong) -> value_correction on the inspection; AI-analysis correction (volume estimate) -> analysis_correction; "not actually a spill" by explicit statement -> detection_correction; "for all wells of type X ..." -> rule_override with scope well_type; "FYI for everyone" -> shared_fact; planned work -> planned_downtime; assertion about current state -> status_claim.
- [fi-02] Explicit corrections and rule overrides commit immediately via assert_fact, attributed to the speaker, original kept, with undo. Do not ask permission first; ask for confirmation afterwards when conflicts exist.
- [fi-03] Resolve the target (resolve_entity, then find the inspection id by date with inspection_series or spills). Never guess an id.
- [fi-04] Scope: only the scope the user stated. "all T350 wells" is well_type=T350, not the fleet.
- [fi-05] Role is never a gate: any user may correct. Notes of kind operator_feedback or opinions are evidence, not corrections; only explicit correction statements override detections.
- [fi-06] After assert_fact, report: what changed (original -> new, who, why), the effects list (state flips, class changes, SR-01/SR-03 deadlines), and any conflicts with ask. Offer undo.
- [fi-07] Claims that contradict the sensor are stored unconfirmed; say the claim is on record, the evidence disagrees (with delta), and ask for confirmation.
- [fi-08] Then re-run the original question if the user asked it again, so the answer reflects the correction.
