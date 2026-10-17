---
name: conflict-handling
description: Sources disagree (note vs sensor, claim vs data, two people). Surface it instead of picking a side silently.
---
# Conflict handling playbook
- [ch-01] Tools return conflicts. Copy each into AnswerSpec.conflicts (subject, sides with source/author/says/ref, resolution, ask). The verifier blocks answers that hide them.
- [ch-02] Precedence: explicit correction > sensor/record > AI estimate. Claims stay unconfirmed until evidence agrees. Feedback notes are evidence, never corrections.
- [ch-03] Inspection notes can imply a state ("Pump jack not moving", "cold to touch" = not producing). If a correction or claim disagrees with such a note, report both sides and ask the corrector to confirm.
- [ch-04] Two notes about the same inspection may disagree; a note may carry the inspection id only in its text. Show both with authors and note ids; the structured detection stands unless a formal correction exists.
- [ch-05] Always name who said what and where it is recorded. Keep both statements on record.
- [ch-06] Do not resolve a human disagreement yourself; state what the data supports and ask.
