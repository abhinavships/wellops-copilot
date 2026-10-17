# Self-improving skills

WellOps keeps a library of **skills**: playbooks that say which tools to call, in what order, with which arguments,
which visual to show, and what has gone wrong before. Some are written by people (`skills/*/SKILL.md`). The rest are
**distilled automatically from turns the verifier passed**. The agent looks them up before acting, and each outcome
feeds back into the library.

```
  turn_stream ──(verified turn: question, trace steps, tool calls + args, visuals)──► skills.on_turn_complete
                                                                                      │
             ┌──────────────── observe ─────────────── distill ─────────────── reinforce / decay ──┐
             │  CALL_LOG (native agent)   literals -> {slots}, signature     uses / successes / failures,
             │  + wellops.trace steps     name from intent, description      triggers, pitfalls, deprecate
             └──────────────────────────────────────────┬────────────────────────────────────────────┘
                                                        ▼
                                   DuckDB: skills, skill_runs  (same file as the substrate)
                                                        │
             retrieve: find_skills(question) = BM25(triggers, description, name) x success rate
                                                        │
      ┌─────────────────────────────┬───────────────────┴──────────┬──────────────────────────────┐
  /mcp (main registry):        /skills-mcp (own FastMCP):      /api/skills (REST, for people)   SKILL.md export
  find_skills, get_skill       list/get/find/create/           GET list|?q=, GET {id},
  -> the live Gemini agent     record_outcome/export_skill_md  POST create/edit, GET {id}/md
```

## Code

| Path | Role |
|---|---|
| `backend/src/wellops/skills/__init__.py` | Public API: `find_skills`, `get_skill`, `list_skills`, `create_skill`, `record_outcome`, `export_skill_md`, `learn_from_turn`, `on_turn_complete` |
| `backend/src/wellops/skills/store.py` | DuckDB tables, CRUD, seeding from `skills/*/SKILL.md` |
| `backend/src/wellops/skills/distill.py` | Generalizing literals into slots, signatures, similarity, deterministic naming |
| `backend/src/wellops/skills/retrieve.py` | Tokenizer (entity mentions normalized to slots) and BM25 ranking weighted by success rate |
| `backend/src/wellops/skills/server.py` | `skills_mcp` FastMCP server, `install()` mounts it at `/skills-mcp` |
| `backend/src/wellops/api/skills_api.py` | REST router; its `install(app)` adds the router and the MCP mount (one line in `app.py`) |
| `backend/src/wellops/mcp_server.py` | `find_skills` / `get_skill` in the main tool registry; `CALL_LOG` (tool path per chat) |
| `backend/src/wellops/api/native_agent.py` | Appends every in-process tool call `{name, args}` to `CALL_LOG` |
| `backend/tests/test_skills.py` | Seeding, Q6 distillation, dedupe, failure -> pitfall, decay, retrieval, MCP/REST |

## Store

`skills`: `skill_id`, `name`, `description`, `triggers` (example questions, JSON), `steps` (JSON
`[{"tool", "args"}]` with `{slot}` templates), `visual`, `pitfalls` (JSON), `body` (manual markdown), `tools`,
`source` (`manual` | `auto`), `status` (`active` | `deprecated`), `signature`, `content_sha`, `version`, `uses`,
`successes`, `failures`, `last_used`, `created_at`, `updated_at`, `keywords` (normalized retrieval tokens).

`skill_runs`: one row per event (`created`, `reinforced`, `failed`, `skipped`, `edited`) with the question, user,
chat, the templated steps and a detail. `GET /api/skills/{id}` returns the last 20.

The first access creates the tables and imports `skills/*/SKILL.md` as `source="manual"`. Seeding is idempotent: an
unchanged file is skipped, and a changed file updates the content and bumps the version but keeps the stats. Bullets
that say "never" or "do not" become the manual skill's pitfalls.

## Lifecycle

1. **Observe.** `on_turn_complete(deps, question, steps, ok, visuals)` runs after the verifier. The tool path comes
   from `mcp_server.CALL_LOG[chat_id]`, which the native agent fills. The function pops that log, keeps only the last N
   entries (N = number of tool steps in the trace), and never raises.
2. **Distill** (`distill.py`, deterministic, no LLM).
   - Literals become slots: `WELL-0151` -> `{well}`, a list of wells -> `{wells}`, area names and ids -> `{area}`,
     `scope={"kind":"area",...}` or `scope="Northeast Block 7"` -> `{area_scope}` (`{well_scope}`,
     `{well_type_scope}` likewise), `start`/`end` dates -> `{window.start}`/`{window.end}`, other dates -> `{date}`,
     relative periods in free text -> `{period}`, INS-/MNT-/CTX-/REG- ids -> `{record_id}`, `top_k` -> `{n}`,
     search queries and reasons -> `{query}`/`{reason}`.
   - Choices that define the method stay literal: `group_by="well"`, `show_visual(type="bar")`. Per-turn plumbing
     (`handle`, `title`, `x`, `y`) is dropped.
   - Meta tools (`find_skills`, `get_skill`) are removed, and repeated calls are collapsed. A path with no data step,
     an unregistered tool, or a one-off action (`generate_image`, `ingest_file`) is not learned.
   - **Signature**: the tool sequence plus the method arguments. The free text passed to `resolve_entity` and how
     many times it is called are ignored. A turn matches a skill when the signatures are equal, or when both share the
     same primary data tool and the step-token Jaccard similarity is at least 0.75.
   - **Name** comes from the intent of the primary step: `rank-` when `top_k` is set, `-trend` for month/year
     grouping, `-by-<group>`, `list-`/`total-` otherwise, then the measure (`downtime`, `spills`, `repair-costs`, ...)
     and the scope (`in-area`, `for-well`, `for-wells`, `for-well-type`, `across-fleet`). Collisions get `-2`, `-3`.
     The description is assembled from the same parts plus the visual and the tool path.
   - Optional polish: when `SKILLS_LLM_POLISH=true` and a Gemini key is set, a background thread asks
     `gemini-3.5-flash-lite` (OpenAI-compatible endpoint) to rewrite the name and description. It never changes the
     steps or the id. The flag defaults to False, and tests never enable it.
3. **Retrieve.** `find_skills(question, k=3)` tokenizes the question with the same entity normalization, so "Southwest
   Block 24" and "Northeast Block 7" both read as `slot_area`. It also applies light synonyms ("down" -> downtime,
   "top"/"worst" -> rank). Ranking is BM25 over name x2 + description + triggers + tools (+ manual body), multiplied by
   `0.6 + 0.8 x success_rate`. The Laplace rate `(s+1)/(s+f+2)` puts an unused skill at 0.5. Deprecated skills are
   excluded.
4. **Reinforce / decay.**

   | Turn | Matches a skill | Effect |
   |---|---|---|
   | verified | no | **create** `source=auto` skill, the question is its first trigger, uses = successes = 1 |
   | verified | yes | uses+1, successes+1, question added to triggers (case/space-insensitive dedupe, newest 25) |
   | failed | yes | uses+1, failures+1, failing step recorded as a pitfall (deduped, version+1) |
   | failed | no | nothing learned (`skill_runs` row `skipped`) |

   The pitfall text is the first trace step with `ok=false`, rendered as a template (for example `Step
   downtime(scope={area_scope}, ...) failed: <detail>`). If no step failed, it is "Answer failed verification after
   <primary step> (<gates detail>)". If the agent called `get_skill(X)` during the turn, X is credited or blamed as
   well. An auto skill with at least 3 failures and a success rate below 0.35 is **deprecated** and drops out of
   retrieval. A later success that brings its rate back to 0.5 or higher reactivates it.

## Example: what Q6 distills to

The question was "Which 3 wells in Northeast Block 7 had the most downtime in the last 4 months? Show a bar chart." The
tool path was `resolve_entity("Northeast Block 7")`, `resolve_entity("last 4 months")`, `downtime(scope={"kind":"area",
"ids":["NE-07"]}, start="2026-06-17", end="2026-10-17", group_by="well", top_k=3)`, `show_visual(type="bar",
handle="r3")`. It distills to:

```yaml
name: rank-downtime-in-area
description: Rank the top {n} wells by downtime in one area over a resolved date window, shown as a bar chart.
  Learned from a verified turn (resolve_entity -> downtime -> show_visual).
steps:
- resolve_entity(text={area})
- resolve_entity(text={period})
- downtime(scope={area_scope}, start={window.start}, end={window.end}, group_by="well", top_k={n})
- show_visual(type="bar")
visual: bar
```

"Top 5 wells by downtime in Southwest Block 23 over the past 6 months" then **reinforces** this skill and does not
create a new one. "Downtime per month in Northeast Block 8" creates `downtime-trend-in-area`.

## MCP tools

`/skills-mcp` is a separate FastMCP server (streamable HTTP, stateless, JSON responses):

| Tool | Purpose |
|---|---|
| `list_skills(source?, include_deprecated?)` | catalog with stats |
| `get_skill(skill_id)` | full playbook (rendered and structured steps, pitfalls, triggers, body) |
| `find_skills(question, k=3)` | retrieval as above |
| `create_skill(name, description, steps?, triggers?, visual?, pitfalls?, skill_id?)` | add, or edit by `skill_id` (version+1, stats kept); steps as `'tool(arg={slot}, k="literal")'` strings |
| `record_outcome(skill_id, ok, question?, pitfall?)` | external grading (another agent, an eval harness) |
| `export_skill_md(skill_id)` | agentskills.io `SKILL.md` (frontmatter + When to use / Steps / Visual / Pitfalls / Rules) |

The main registry at `/mcp`, which is also what the in-process agent sees, gains `find_skills(question, k)` and
`get_skill(skill_id)`.

```bash
curl -s -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  localhost:8000/skills-mcp/ -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
  "params":{"name":"find_skills","arguments":{"question":"worst wells by downtime in Northeast Block 8"}}}'
```

REST: `GET /api/skills` (`?q=` to search, `?source=auto`), `GET /api/skills/{id}` (+ recent runs),
`POST /api/skills` (create/edit), `POST /api/skills/{id}/outcome`, `GET /api/skills/{id}/md`.

## Wiring the turn hook

`turn_stream.stream_turn` needs one line, right after `yield _sse(trace)` on the live path:

```python
    __import__("wellops.skills", fromlist=["on_turn_complete"]).on_turn_complete(deps, message, steps, state.get("ok", False), state["spec"].visuals if state.get("spec") else [])
```

Cache hits return earlier and are never counted. `SKILLS_LEARN=false` turns learning off. Evals can leave it on: a
skill only changes what the agent sees when the agent itself calls `find_skills`.

## Settings

| Env | Default | |
|---|---|---|
| `SKILLS_LEARN` | `true` | distill and reinforce from finished turns |
| `SKILLS_LLM_POLISH` | `false` | Gemini rewrite of auto-skill name/description (background, best effort) |
| `SKILLS_LLM_MODEL` | `gemini-3.5-flash-lite` | model for polishing |
