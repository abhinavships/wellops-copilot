# Trade-offs (v3)

Each row: what we gain, what we pay, how we limit the cost, and the **signal that makes us
revisit**. Linked to [`DECISIONS.md`](DECISIONS.md) and [`AUDIT.md`](AUDIT.md).

| # | Trade-off | We gain | We pay | Mitigation | Revisit if |
|---|---|---|---|---|---|
| T1 | **Generative capabilities vs fixed toolset** (ADR-001) | generalization; strong system story | admission pipeline to build; more surface to test | seed skills go through the same pipeline; `CAPABILITY_FREEZE` in the scored run | golden pass^3 < 90% after P5 |
| T2 | **Pydantic AI vs Hermes / Claude Agent SDK / OpenAI Agents SDK** (ADR-002) | async multi-tenant runs, typed outputs, AG-UI, HITL, evals, OTel in one package | no built-in skills loader, no self-created skills online | our agentskills toolset; Hermes/Claude Code do offline skill work (ADR-003) | spike S1 fails AG-UI streaming → OpenAI Agents SDK |
| T3 | **One DuckDB vs a graph DB** (ADR-004) | one store, no consistency checks, SQL that agents already write well, fast aggregates | no native Cypher; recursive CTEs are clumsier for deep paths; single writer | typed graph tools hide SQL; optional DuckPGQ; Postgres + AGE adapter path | variable-depth traversals appear in requirements, or concurrent writers are needed |
| T4 | **Stable meta-tools vs dynamic tool mounting** (ADR-005) | cache-stable prompts; works with any model; one policy point | one indirection (`call_capability`); the model sees schemas in results, not in native tool slots | top-k cards include an argument sketch; native `defer_loading` path on Claude | tool-call arg errors via `call_capability` > 5% |
| T5 | **Deterministic ranking vs learned / bandit routing** (ADR-005) | reproducible; debuggable | no online exploration | exploration happens in offline replays; weights tuned by GEPA | catalog grows > 200 and recall@5 drops < 0.9 |
| T6 | **Replay-based admission vs live shadow** (ADR-006) | real evidence with no traffic needed | admission costs eval minutes per capability | run only the cases matching the declared intents, plus held-out | admission > 10 min per capability |
| T7 | **DecisionService (LAYA or others) vs plain LLM** (ADR-007) | calibrated, fast typed decisions | one more component; LAYA unverified | spike S2 picks a backend; LLM structured output is always available | chosen backend accuracy < 90% on our labels |
| T8 | **Canonical views vs pure text-to-SQL** (ADR-008) | exact numbers; domain traps encoded | less "pure generative" | agent may compose SQL over canonical views; forged tools for new primitives | > 20% of questions need sandbox fallback |
| T9 | **Claims-bound answers + hard gates vs free prose** (ADR-009) | grounding is provable; retries are targeted | the model must emit structured claims (more output tokens, occasional format retries) | Pydantic output schema; prose rendered from blocks | format retries > 10% of turns |
| T10 | **Immediate commit of corrections vs HITL gate** (ADR-013) | matches expected behaviour (Q12–Q15); fast feedback loop | a wrong correction propagates until undone | attribution, original kept, Undo, conflict cards, audit trail | — |
| T11 | **In-house memory vs Letta / Mem0 / Zep** (ADR-012) | provable isolation; tiny; testable | no fancy consolidation or semantic dedup | episodes are small; summaries per turn; recall scoring | episode recall precision < 0.8 |
| T12 | **AG-UI + A2UI vs custom DSL** (ADR-016) | standards; less custom parser code; ecosystem renderers | protocols are young; A2UI subset only | pin versions; keep our catalog as the renderer | an A2UI spec change breaks rendering |
| T13 | **No default UI-coder LLM** (ADR-016) | saves 2–5 s per answer | less "creative" layouts | composer subagent for multi-panel answers | judges' questions need richer composites |
| T14 | **ACE + GEPA offline vs online self-learning** (ADR-011) | gated, labelled, no live regressions | improvements land between sessions, not mid-session | run between eval cycles; demo one promotion | — |
| T15 | **Strong closed orchestrator vs open models** (ADR-017) | best tool-use reliability | $ and vendor dependency | open configuration on the same Pareto chart | open configuration within 5 pts at < 30% cost |
| T16 | **Breadth vs hackathon time** | — | — | phase gates; cut order in PLAN §9 | any phase overruns > 50% |
| T17 | **Trust layer (provenance, dq flags, reconciliation) vs plain semantics** (ADR-020) | robust to traps and new data; honest answers | more columns and code; longer answers | flags only surface when relevant to the question | answers get noisy (judges or users complain) |
| T18 | **Opik platform vs code-only evals** (ADR-021) | traces + threads + experiments + online rules + feedback in one UI; strong demo | external service (cloud) or heavy docker stack | scorers live in our code; Opik is the sink and UI; offline JSON fallback | Opik unavailable during the final 2h → JSON results only |
| T19 | **Rules extracted by LLM at ingest vs hand-coded** (ADR-020) | new manuals in the test set work | extraction errors | verbatim-quote check + human review + frozen yaml | any extracted rule fails the quote check |
