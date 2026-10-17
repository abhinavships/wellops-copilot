"use client";
import { useState } from "react";
import type { VisualProps } from ".";
import { useUi } from "@/components/ui";
import { resolveDispute, undoAssertion } from "@/lib/api";
import { fmtVal } from "@/lib/format";

type Ev = { source: string; label: string; value?: string; quote?: string; author?: string; at?: string; method?: string; kind?: string; id?: string };
type Change = { field: string; label: string; unit?: string; current: unknown; proposed: unknown; status: string; reason?: string;
  assertion_id?: string; claim_status?: string };
type Dispute = { dispute_id: string; record: { kind: string; id: string }; asset_id: string; attr?: string; current_value?: unknown;
  claimed_value?: unknown; changes?: Change[]; evidence: Ev[]; conflicts?: { subject?: string; sides?: { source?: string; author?: string; says?: string }[] }[];
  effects?: string[]; verdict_hint?: string; ask?: string };
const STATUS: Record<string, [string, string]> = {
  applied: ["Applied", "bg-hl/70 text-fg"], pending: ["Needs your OK", "border border-fg text-fg"], rejected: ["Rejected", "bg-bad/15 text-bad"],
  computed: ["Computed", "bg-raised text-muted"], unchanged: ["Unchanged", "bg-raised text-muted"], claim: ["Claim recorded", "bg-raised text-fg"],
  kept: ["Kept record", "bg-raised text-muted"],
};

const ATTR: Record<string, string> = { producing: "Producing status", wellhead_temp_c: "Wellhead temperature", ambient_temp_c: "Ambient temperature",
  spill_est_volume_bbl: "Spill volume", spill: "Spill detection" };
const show = (v: unknown) => (typeof v === "boolean" ? (v ? "producing" : "not producing") : v === null || v === undefined ? "—" : fmtVal(v));

/** Proof before correction: the value's provenance, then the user decides. Nothing changes until they do. */
export function DisputeCard({ visual }: VisualProps) {
  const d = visual.encodings as unknown as Dispute;
  const { ctx, openEvidence } = useUi();
  const [state, setState] = useState<{ status: string; assertion?: string; error?: string; changes?: unknown } | null>(null);
  const [busy, setBusy] = useState(false);
  if (!d?.dispute_id) return null;
  const changes: Change[] = (state?.changes as Change[] | undefined) ?? d.changes ?? [{ field: d.attr ?? "", label: ATTR[d.attr ?? ""] ?? d.attr ?? "",
    current: d.current_value, proposed: d.claimed_value, status: "pending" }];
  const rowStatus = (c: Change) => c.status;
  const pending = changes.filter((c) => c.status === "pending");
  const applied = changes.filter((c) => c.status === "applied");
  const act = async (action: "correct" | "keep") => {
    setBusy(true);
    try { const r = await resolveDispute(d.dispute_id, action, ctx); setState({ status: String(r.status), assertion: r.assertion_id as string | undefined, changes: r.changes }); }
    catch (e) { setState({ status: "error", error: (e as Error).message }); }
    finally { setBusy(false); }
  };
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="label">Proposed changes · {d.record.id}</span>
        <span className="font-mono text-xs text-muted">{d.asset_id}</span>
      </div>
      <table className="w-full border-collapse text-sm">
        <thead><tr className="label text-left"><th className="pb-1 font-normal">Field</th><th className="pb-1 font-normal">On record</th>
          <th className="pb-1 font-normal">Proposed</th><th className="pb-1 text-right font-normal">Status</th></tr></thead>
        <tbody>
          {changes.map((c, i) => {
            const st = rowStatus(c);
            const [txt, cls] = STATUS[st] ?? [st, "bg-raised"];
            return (
              <tr key={i} className="border-t border-line align-top">
                <td className="py-2 pr-2 font-medium">{c.label}</td>
                <td className="py-2 pr-2 font-mono"><span className={st === "applied" ? "line-through decoration-muted" : "hl"}>{show(c.current)}</span>{c.unit && c.current !== null ? ` ${c.unit}` : ""}</td>
                <td className={`py-2 pr-2 font-mono ${st === "rejected" ? "text-bad line-through" : ""}`}>{show(c.proposed)}{c.unit && typeof c.proposed === "number" ? ` ${c.unit}` : ""}</td>
                <td className="py-2 text-right">
                  <span className={`inline-block rounded px-1.5 py-0.5 font-mono text-[11px] ${cls}`}>{txt}{c.claim_status ? ` · ${c.claim_status}` : ""}</span>
                  {c.reason && st !== "applied" && <div className="mt-1 max-w-[260px] text-left text-xs text-muted sm:ml-auto">{c.reason}</div>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {(d.conflicts ?? []).length > 0 && (
        <div className="rounded-xl border border-warn/50 bg-hl/15 p-3 text-sm">
          <div className="label text-fg">Conflicting evidence</div>
          {(d.conflicts ?? []).map((c, i) => <p key={i} className="mt-1">{c.subject ? <b>{c.subject}: </b> : null}
            {(c.sides ?? []).map((x) => `${x.author ? x.author + ": " : ""}${x.says ?? x.source ?? ""}`).join("  ·  ")}</p>)}
        </div>
      )}
      {(d.effects ?? []).length > 0 && <ul className="text-sm">{d.effects!.map((e, i) => <li key={i}>→ {e}</li>)}</ul>}
      <ol className="relative ml-1.5 space-y-3 border-l border-fg/80 pl-5">
        {d.evidence.map((e, i) => (
          <li key={i} className="relative">
            <span className="absolute -left-[27px] top-1 grid h-3.5 w-3.5 place-items-center rounded-full border-2 border-fg bg-panel"><span className="h-1 w-1 rounded-full bg-fg" /></span>
            <div className="flex flex-wrap items-baseline gap-x-2">
              <span className="text-sm font-semibold">{e.label}</span>
              {e.id && e.kind ? (
                <button type="button" onClick={() => openEvidence({ citation: { kind: e.kind as never, id: e.id! } })}
                  className="font-mono text-[11px] text-muted underline decoration-dotted hover:text-fg">{e.source}</button>
              ) : <span className="font-mono text-[11px] text-muted">{e.source}</span>}
            </div>
            {e.value && <p className="font-mono text-[13px]">{e.value}</p>}
            {e.quote && <blockquote className="mt-0.5 text-sm italic">“{e.quote}”</blockquote>}
            {(e.author || e.at) && <p className="text-xs text-muted">{[e.author, e.method, e.at && String(e.at).slice(0, 16).replace("T", " ")].filter(Boolean).join(" · ")}</p>}
          </li>
        ))}
      </ol>
      {d.verdict_hint && <p className="text-sm text-muted">{d.verdict_hint}</p>}
      {!state && applied.length > 0 && (
        <p className="flex flex-wrap items-center gap-2 border-t border-line pt-3 text-sm" role="status">
          <span className="rounded bg-hl/70 px-1.5 font-medium">Applied · attributed to you</span>
          <span className="text-muted">original values kept</span>
          {applied.map((c) => c.assertion_id && <button key={c.assertion_id} type="button" className="underline decoration-dotted"
            onClick={() => undoAssertion(c.assertion_id!, ctx).then(() => setState({ status: "undone" }))}>Undo {c.label.toLowerCase()}</button>)}
        </p>
      )}
      {!state && pending.length > 0 && (
        <div className="space-y-3 border-t border-line pt-3">
          <p className="display text-lg font-semibold">{d.ask ?? "Does this convince you, or should we correct it?"}</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" disabled={busy} onClick={() => act("correct")} data-testid="dispute-correct"
              className="rounded-full bg-fg px-4 py-2 text-sm font-medium text-bg transition hover:opacity-90 disabled:opacity-50">
              Apply {pending.length === 1 ? `${pending[0].label.toLowerCase()} = ${show(pending[0].proposed)}` : `${pending.length} changes`} →</button>
            <button type="button" disabled={busy} onClick={() => act("keep")} data-testid="dispute-keep"
              className="rounded-full border border-line bg-panel px-4 py-2 text-sm font-medium transition hover:border-fg disabled:opacity-50">
              Evidence is right, keep it</button>
          </div>
        </div>
      )}
      {state?.status === "corrected" && (
        <p className="flex flex-wrap items-center gap-2 border-t border-line pt-3 text-sm" role="status">
          <span className="rounded bg-hl/70 px-1.5 font-medium">Corrected by you</span>
          <span className="text-muted">attributed, original kept{state.assertion ? ` · ${state.assertion}` : ""}</span>
          {state.assertion && <button type="button" className="underline decoration-dotted"
            onClick={() => undoAssertion(state.assertion!, ctx).then(() => setState({ status: "undone" }))}>Undo</button>}
        </p>
      )}
      {state?.status === "kept" && <p className="border-t border-line pt-3 text-sm text-muted" role="status">Kept the recorded value · your review is logged.</p>}
      {state?.status === "undone" && <p className="border-t border-line pt-3 text-sm text-muted" role="status">Correction undone.</p>}
      {state?.status === "error" && <p className="border-t border-line pt-3 text-sm text-bad" role="alert">Could not save: {state.error}</p>}
    </div>
  );
}
