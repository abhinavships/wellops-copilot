# WellOps as harness engineering

Framing: [Harness Engineering primer (aman.ai)](https://aman.ai/primers/harness-engineering/). The primer defines a
harness as the software layer around a foundation model: prompts and skills, tools, memory and context, state,
verification, retries, routing and control flow. Its core loop is *run tasks → collect traces → diagnose failures →
modify the stack → re-evaluate*. This page maps WellOps onto it.

## 1. Score = Eval(M, H, E, B): we report configs, not models

M is the model, H the harness, E the environment (the dataset plus "today"), and B the budget (verifier retries ≤ 2,
tool retries 2). We hold H, E and B fixed and change M. Results belong to a model+harness config:

| Config (M + same H) | Live result |
|---|---|
| `ollama:gemma4-e2b-32k` + WellOps H | Failed both cases that completed: Q01 and Q06 ran out of verifier retries. Q13 never ran. |
| `openrouter:nvidia/nemotron-3-super-120b-a12b:free` + WellOps H | Q1 passed all 6 gates on the first attempt (~30 s). |

The harness doesn't make a 2B model correct. It makes a wrong answer fail loudly instead of reaching the user.

## 2. Frozen substrate vs editable layers (the primer's three strata)

| Stratum | WellOps |
|---|---|
| **Frozen substrate** | L0 DuckDB built by deterministic ingest (`backend/src/wellops/substrate/ingest.py`). Rules are in `substrate/rules.yaml`, each with a verbatim quote from its manual or SOP. Data-quality flags are in the `dq_flags` table. The agent can read these but never write them. |
| **Editable research code** | Typed tools (`backend/src/wellops/tools/`) and 9 playbook skills (`skills/*/SKILL.md`, loaded by progressive disclosure through `list_skills` / `load_skill`). |
| **Instruction layer** | `backend/src/wellops/agent/prompt.py`: objectives, the citation and disclosure contract, failure handling. |

## 3. Separate generation from verification

- **Generation:** the Pydantic AI agent produces an `AnswerSpec` with markdown, claims bound to result handles, visuals,
  citations and assumptions.
- **Verification:** `backend/src/wellops/verify/gates.py` runs as the `output_validator`. It checks 6 gates: grounding,
  claims (re-executed against the handle), disclosure, conflicts, visual_fit and assumptions. A failure becomes a
  `ModelRetry` with a diff of what's wrong, at most 2 retries (`MAX_RETRIES`), and then the turn fails.
- **Evaluators are outside the agent's reach:** `evals/` is a separate project with its own truth computation and
  scorers. It includes a fault-injection meta-eval (`evals/evals/runner/faults.py`), which plants known faults into
  good answers (wrong number, wrong visual, …) and checks that the right scorer catches each one. The primer calls
  this "freeze validation, metric computation and the result parser".

## 4. Held-in vs held-out split

The sample dataset is the held-in split. A **mutated dataset** (`evals/evals/mutate/`) serves as the held-out split:
it renames, re-types and perturbs the data before any test data arrives. It found two problems:
- the T500 silent drop: a well type whose manual had no extracted rule was dropped without warning. Fixed in
  `5a98a76`: rules are extracted from unseen manuals, and a type with no rule gets a fallback plus a dq flag.
- inspection-ID collisions: duplicate ids are now flagged instead of overwritten.

## 5. Observability: traces as the debugging surface

- **X-ray panel** (`frontend/src/components/XrayPanel.tsx`): each turn shows its tool calls with args, the skills
  loaded and the gate results for each attempt.
- **Opik sink** (`evals/evals/runner/opik_sink.py`): eval runs become Opik experiments, with scores per case.
- **Crash-safe runner:** partial runs are kept, so crashes become data, as the primer recommends.

## 6. Memory as policy

- **Shared assertions** (`memory/assertions.py`): corrections, rule overrides and planned downtime are visible to the
  whole fleet. Each is attributed to its author, scoped (inspection, well, area, well_type or fleet), undoable
  through supersession, and checked for conflicts.
- **Private episodes** (`memory/episodes.py`): conversation memory is filtered by principal in SQL, using `RunDeps`
  and never model arguments. One user can't recall another user's chats.

## 7. Honest gaps (what the primer asks for that we don't have yet)

- **No regression-gated self-improvement loop.** Skill and prompt edits are made by hand. We don't yet promote an
  edit only when Δ_in ≥ 0 and Δ_ho ≥ 0, as the Self-Harness acceptance gate requires.
- **No context compaction.** Chats are short, and history is passed in raw.
- **No Pareto tracking** of quality against tokens and latency. We record pass/fail and gates, but not a cost
  frontier per config.
