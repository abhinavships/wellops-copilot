# WellOps Copilot: 5-minute video script

Total 5:00. Screen capture of the live UI against the real API (never mock mode: the mock fixtures contain made-up
IDs). The narration is spoken text. Clock set to 17 Oct 2026, dataset `sample`.

| # | Time | Screen | Narration |
|---|---|---|---|
| 1 | 0:00–0:20 | Title card, then the UI: chat, user picker (Michael Turner), clock control, X-ray panel collapsed. | "This is WellOps Copilot, an asset-intelligence assistant for an oil field. Two hundred wells, forty thousand inspections, repair reports, manuals. The hard part isn't answering. It's answering *honestly* when the data is stale, people disagree, and the test dataset is one we've never seen." |
| 2 | 0:20–0:50 | Ask: "Is WELL-0001 producing oil right now?" The status card renders with Δ vs threshold. Hover the number: claim → handle. | "Producing isn't a column. It's a rule from the T200 manual: wellhead minus air at least 15 °C. Every number on screen is a claim bound to a query result. Here's the row it came from, and the manual section the threshold came from." |
| 3 | 0:50–1:20 | Open the X-ray panel: skill `well-status` loaded, tools called, six gates green, 0 retries, tokens. Then show the stale-status flag. | "The X-ray shows what happened: which playbook the agent loaded, which tools ran, and six verifier gates. The record says 'active', but the latest reading says otherwise. The copilot flags the stale record instead of repeating it." |
| 4 | 1:20–2:00 | Ask Q2 (not-producing wells in NE Block 8, table), then Q3 (line chart with threshold line and not-producing bands), then Q8 (heatmap). | "Visuals are chosen by the result's shape, or by what you ask for. A table for a list, a line with the threshold band for a time series, a heatmap for area by month. The data loads by handle, so it never passes through the model." |
| 5 | 2:00–2:40 | Q12: Michael says the 15 Oct reading for WELL-0164 is wrong. The correction diff card appears (original kept, attributed, Undo). Re-ask Q2: WELL-0164 is gone, with disclosure "corrected by Michael Turner". | "Corrections commit immediately, attributed, and the original is kept. Re-ask, and WELL-0164 drops out. The answer has to say who changed the data. That's a hard gate, not a style choice." |
| 6 | 2:40–3:10 | Switch user to a field operator. Ask the same status question: the correction applies. Then ask "what did we discuss yesterday?": no history leaks. | "Shared knowledge reaches everyone: the correction applies for a different user. Private history doesn't. This user never had that conversation, and the copilot says so instead of borrowing Michael's." |
| 7 | 3:10–3:40 | Q17: "WELL-0175 is running fine." The conflict card shows the claim, unconfirmed, next to the 0.4 °C reading. | "When a person disagrees with the sensor, we don't pick a winner. The claim is stored as unconfirmed and both sides are shown with their sources." |
| 8 | 3:40–4:25 | Terminal: `evals.run --target fake` report, then the meta-eval table (100 % recall, 0 % false alarms), key audit (199 agree / 0 disagree), the live `--target api` slice, and the Opik experiment view with threads. | "Evaluation: eighteen multi-turn, multi-user episodes with a simulated clock, plus seventy-two synthetic cases. Scoring is deterministic: facts, evidence, visual, memory, and a leak probe that zeroes the score. We test the scorers too, by injecting faults. And we audit the answer key instead of trusting it." |
| 9 | 4:25–4:50 | `evals/results/mutated_checks.json`: new T500 wells, renamed reports, shifted horizon, ID collision. Highlight the T500 finding. | "For the unseen dataset we built a mutated one: new wells, a new well type, renamed files, a shifted clock. It caught a real bug for us. A new well type disappears from the views until its rule exists. That's now in our runbook." |
| 10 | 4:50–5:00 | Architecture diagram (`docs/architecture.svg`). | "Data as evidence, answers as verified claims, memory with boundaries, and evals that check themselves. That's WellOps Copilot." |

## Recording notes
- Before recording, reset the shared assertions so scenes 5–7 start clean (Undo or a fresh ingest).
- Scene 8 must use real numbers from the latest `evals/results/` run. Re-check them on recording day.
- If a live turn is slow (free models), cut the wait in editing and say so on screen ("sped up").
