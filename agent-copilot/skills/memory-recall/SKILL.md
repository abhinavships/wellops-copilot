---
name: memory-recall
description: The user refers to an earlier conversation ("the wells we discussed yesterday").
---
# Memory recall playbook
- [mr-01] Call recall_episodes(query, when). It only searches the current user's own history; the identity is fixed by the session.
- [mr-02] Use the returned entity ids as the scope for the new question (for example repair_costs for those wells). Say which earlier conversation you used.
- [mr-03] If it returns nothing, say you have no such earlier conversation with this user. Never answer from another user's history, and never guess.
- [mr-04] Facts that users explicitly shared with everyone (shared_fact, planned_downtime, corrections) are fleet knowledge and may be used, attributed to their author. Private conversations are not.
- [mr-05] Relative dates ("yesterday") are relative to the run's today.
