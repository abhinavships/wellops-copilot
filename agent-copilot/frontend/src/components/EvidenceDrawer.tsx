"use client";
import { useEffect, useState } from "react";
import type { Claim, Citation, HandleData } from "@/contracts";
import { fetchHandle, fetchRecord, rowsAsObjects, type Ctx } from "@/lib/api";
import type { EvidenceTarget } from "@/components/ui";

function textOf(rec: unknown): string | null {
  if (rec && typeof rec === "object") for (const k of ["text", "body", "content", "markdown"]) { const v = (rec as Record<string, unknown>)[k]; if (typeof v === "string") return v; }
  return null;
}

function Highlighted({ text, span }: { text: string; span: [number, number] }) {
  const [a, b] = span;
  return <p className="whitespace-pre-wrap rounded-lg bg-raised p-3 text-sm leading-relaxed">{text.slice(0, a)}<mark className="rounded bg-warn/30 px-0.5 text-fg" id="span-hl">{text.slice(a, b)}</mark>{text.slice(b)}</p>;
}

export function EvidenceDrawer({ target, ctx, onClose }: { target: EvidenceTarget | null; ctx: Ctx; onClose: () => void }) {
  const [rec, setRec] = useState<unknown>(null);
  const [err, setErr] = useState<string | null>(null);
  const [cell, setCell] = useState<{ claim: Claim; data: HandleData | null } | null>(null);
  const cit: Citation | null = target && "citation" in target ? target.citation : null;
  const claim: Claim | null = target && "claim" in target ? target.claim : null;

  useEffect(() => {
    setRec(null); setErr(null); setCell(null);
    if (cit) fetchRecord(cit.kind, cit.id, ctx).then(setRec).catch((e: Error) => setErr(e.message));
    if (claim) fetchHandle(claim.handle, ctx.chatId).then((d) => setCell({ claim, data: d })).catch((e: Error) => setErr(e.message));
  }, [cit, claim, ctx]);
  useEffect(() => {
    if (!target) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h); return () => window.removeEventListener("keydown", h);
  }, [target, onClose]);
  useEffect(() => { document.getElementById("span-hl")?.scrollIntoView({ block: "center" }); }, [rec]);

  if (!target) return null;
  const rows = cell?.data ? rowsAsObjects(cell.data) : [];
  const match = claim?.row_key ? rows.find((r) => Object.entries(claim.row_key!).every(([k, v]) => String(r[k]) === String(v))) : rows[0];
  const text = textOf(rec);
  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-modal="true" aria-label="Evidence">
      <button type="button" aria-label="Close evidence" className="absolute inset-0 bg-black/40" onClick={onClose} />
      <aside className="relative flex h-full w-full max-w-md flex-col overflow-y-auto border-l border-line bg-panel p-4 shadow-2xl">
        <div className="mb-3 flex items-start justify-between gap-2">
          <div>
            <p className="text-[11px] uppercase tracking-wide text-muted">{cit ? `Evidence · ${cit.kind}` : "Grounded claim"}</p>
            <h2 className="font-mono text-base font-semibold">{cit ? cit.id : claim!.text}</h2>
          </div>
          <button type="button" onClick={onClose} autoFocus className="rounded-md border border-line px-2 py-1 text-sm hover:border-accent">Close</button>
        </div>
        {claim && (
          <div className="space-y-3 text-sm">
            <dl className="grid grid-cols-[6rem_1fr] gap-y-1">
              <dt className="text-muted">Stated</dt><dd>“{claim.text}”</dd>
              <dt className="text-muted">Value</dt><dd className="font-mono">{String(claim.value)}</dd>
              <dt className="text-muted">Handle</dt><dd className="font-mono">{claim.handle}{claim.column ? `.${claim.column}` : ""}</dd>
              {claim.row_key && (<><dt className="text-muted">Row</dt><dd className="font-mono">{JSON.stringify(claim.row_key)}</dd></>)}
            </dl>
            {claim.column && match && <p className="rounded-lg bg-ok/10 p-2 text-ok">Matches cell value: <b className="font-mono">{String(match[claim.column])}</b></p>}
            {match && <><h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Source row</h3><pre className="overflow-x-auto rounded-lg bg-raised p-3 font-mono text-xs">{JSON.stringify(match, null, 2)}</pre></>}
            {cell?.data?.sql && <><h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Query</h3><pre className="overflow-x-auto rounded-lg bg-raised p-3 font-mono text-xs">{cell.data.sql}</pre></>}
          </div>
        )}
        {cit && (
          <div className="space-y-3 text-sm">
            {cit.label && <p className="text-muted">{cit.label}</p>}
            {err && <p role="alert" className="rounded-lg bg-warn/10 p-2 text-warn">Raw record not available: {err}</p>}
            {text && cit.span ? <Highlighted text={text} span={cit.span} /> : text ? <p className="whitespace-pre-wrap rounded-lg bg-raised p-3">{text}</p> : null}
            {cit.span && <p className="text-xs text-muted">Highlighted characters {cit.span[0]}–{cit.span[1]}</p>}
            {rec != null && <><h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Raw record</h3><pre className="overflow-x-auto rounded-lg bg-raised p-3 font-mono text-xs">{JSON.stringify(rec, null, 2)}</pre></>}
            {rec == null && !err && <div className="h-24 animate-pulse rounded-lg bg-raised" role="status" aria-label="Loading record" />}
          </div>
        )}
      </aside>
    </div>
  );
}
