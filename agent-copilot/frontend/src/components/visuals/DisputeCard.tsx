"use client";
import { useState } from "react";
import type { VisualProps } from ".";
import { useUi } from "@/components/ui";
import { resolveDispute, undoAssertion } from "@/lib/api";
import { fmtVal } from "@/lib/format";

type Ev = { source: string; label: string; value?: string; quote?: string; author?: string; at?: string; method?: string; kind?: string; id?: string };
type Dispute = { dispute_id: string; record: { kind: string; id: string }; asset_id: string; attr: string; current_value: unknown;
  claimed_value: unknown; evidence: Ev[]; verdict_hint?: string; ask?: string };

const ATTR: Record<string, string> = { producing: "Producing status", wellhead_temp_c: "Wellhead temperature", ambient_temp_c: "Ambient temperature",
  spill_est_volume_bbl: "Spill volume", spill: "Spill detection" };
const show = (v: unknown) => (typeof v === "boolean" ? (v ? "producing" : "not producing") : v === null || v === undefined ? "—" : fmtVal(v));

/** Proof before correction: the value's provenance, then the user decides. Nothing changes until they do. */
export function DisputeCard({ visual }: VisualProps) {
  const d = visual.encodings as unknown as Dispute;
  const { ctx, openEvidence } = useUi();
  const [state, setState] = useState<{ status: string; assertion?: string; error?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  if (!d?.dispute_id) return null;
  const act = async (action: "correct" | "keep") => {
    setBusy(true);
    try { const r = await resolveDispute(d.dispute_id, action, ctx); setState({ status: String(r.status), assertion: r.assertion_id as string | undefined }); }
    catch (e) { setState({ status: "error", error: (e as Error).message }); }
    finally { setBusy(false); }
  };
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="label">Disputed · {ATTR[d.attr] ?? d.attr}</span>
        <span className="font-mono text-xs text-muted">{d.record.id}</span>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-xl border border-line bg-raised/60 p-3">
          <div className="label">On record</div>
          <div className="display mt-1 text-2xl font-semibold"><span className="hl">{show(d.current_value)}</span></div>
        </div>
        <div className="rounded-xl border border-dashed border-line p-3">
          <div className="label">You say</div>
          <div className="display mt-1 text-2xl font-semibold text-muted">{show(d.claimed_value)}</div>
        </div>
      </div>
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
      {!state && (
        <div className="space-y-3 border-t border-line pt-3">
          <p className="display text-lg font-semibold">{d.ask ?? "Does this convince you, or should we correct it?"}</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" disabled={busy} onClick={() => act("correct")} data-testid="dispute-correct"
              className="rounded-full bg-fg px-4 py-2 text-sm font-medium text-bg transition hover:opacity-90 disabled:opacity-50">
              Correct to {show(d.claimed_value)} →</button>
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
