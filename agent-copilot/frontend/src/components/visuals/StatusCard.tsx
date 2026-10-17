"use client";
import type { VisualProps } from ".";
import { Badge, CitationChip, stateTone } from "@/components/ui";
import { fmtVal, num, titleCase } from "@/lib/format";

export function StatusCard({ visual, data, rows }: VisualProps) {
  const e = visual.encodings ?? {};
  const r = rows[0] ?? {};
  const col = (k: string, fb: string) => (typeof e[k] === "string" ? (e[k] as string) : fb);
  const title = String(r[col("title", data?.columns[0] ?? "")] ?? visual.title ?? "");
  const raw = r[col("status", "state")];
  // well_status gives `producing` as a boolean; show it as words
  const status = typeof raw === "boolean" ? (raw ? "producing" : "not producing") : raw;
  const value = num(r[col("value", "value")]);
  const threshold = num(r[col("threshold", "threshold")]);
  const unit = typeof e.unit === "string" ? e.unit : "";
  const note = r[col("note", "note")];
  const noteId = r["note_id"];
  const shown = new Set([col("title", ""), col("status", "state"), col("value", "value"), col("threshold", "threshold"), col("note", "note"), "note_id", "note_text"]);
  const extra = (data?.columns ?? []).filter((c) => !shown.has(c));
  const pct = value !== null && threshold ? Math.min(100, Math.max(0, (value / threshold) * 100)) : null;
  const flags = (data?.dq_flags ?? []).filter((f) => f.entity_id === title);
  const tone = typeof status === "string" ? stateTone(status) : "neutral";

  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-stretch">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-lg font-semibold">{title}</span>
          {typeof status === "string" && <Badge tone={tone}>{status}</Badge>}
        </div>
        {flags.map((f, i) => <p key={i} className="mt-1 text-xs text-warn">⚠ {f.detail}</p>)}
        {typeof note === "string" && note && (
          <blockquote className="mt-3 border-l-2 border-line pl-3 text-sm italic text-muted">
            “{note}” {typeof noteId === "string" && <CitationChip c={{ kind: "note", id: noteId }} />}
          </blockquote>
        )}
        {extra.length > 0 && (
          <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
            {extra.map((c) => (<div key={c}><dt className="text-[11px] uppercase tracking-wide text-muted">{titleCase(c)}</dt><dd>{fmtVal(r[c])}</dd></div>))}
          </dl>
        )}
      </div>
      {value !== null && (
        <div className="shrink-0 rounded-lg bg-raised p-3 sm:w-52">
          <div className="text-[11px] uppercase tracking-wide text-muted">Δ above ambient</div>
          <div className="mt-0.5 text-3xl font-semibold tabular-nums">{fmtVal(value)}<span className="ml-1 text-base font-normal text-muted">{unit}</span></div>
          {threshold !== null && pct !== null && (
            <>
              <div className="mt-2 h-2 rounded-full bg-line" role="img" aria-label={`${fmtVal(value)} of ${fmtVal(threshold)} ${unit} threshold`}>
                <div className={`h-2 rounded-full ${value >= threshold ? "bg-ok" : "bg-bad"}`} style={{ width: `${pct}%` }} />
              </div>
              <div className="mt-1 text-xs text-muted">vs {fmtVal(threshold)} {unit} needed to count as producing</div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
