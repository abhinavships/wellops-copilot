"use client";
import type { VisualProps } from ".";
import { useUi } from "@/components/ui";
import type { Citation } from "@/contracts";

const KIND: Record<string, Citation["kind"]> = { doc: "doc", report: "report", note: "note", media: "doc", inspection: "inspection" };

/** Retrieved passages (manuals, SOPs, repair reports, notes) as readable source cards, not a table. */
export function SourcesList({ rows }: VisualProps) {
  const { openEvidence } = useUi();
  const seen = new Set<string>();
  const items = rows.filter((r) => { const k = `${r.id}|${r.section}`; if (seen.has(k)) return false; seen.add(k); return true; }).slice(0, 6);
  return (
    <ol className="divide-y divide-line">
      {items.map((r, i) => {
        const id = String(r.id ?? ""), kind = String(r.kind ?? "doc");
        const span = Array.isArray(r.span) ? (r.span as number[]) : undefined;
        const snippet = String(r.snippet ?? "").replace(/^#+\s*/gm, "").replace(/\s+/g, " ").trim();
        return (
          <li key={i} className="py-3 first:pt-0 last:pb-0">
            <button type="button" className="group block w-full text-left"
              onClick={() => openEvidence({ citation: { kind: KIND[kind] ?? "doc", id, span: span as [number, number] | undefined } })}>
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                <span className="label text-fg">{kind}</span>
                <span className="font-medium group-hover:underline">{String(r.title ?? id)}</span>
                {r.section !== undefined && r.section !== null && String(r.section) !== "0" && <span className="font-mono text-xs text-muted">§{String(r.section)}</span>}
              </div>
              <p className="mt-1 line-clamp-3 text-[14px] leading-relaxed text-fg/85">{snippet}</p>
              <div className="mt-1 font-mono text-[11px] text-muted">
                {[id, r.asset_id, r.author, r.date && String(r.date).slice(0, 10)].filter((x) => x && x !== "–").join(" · ")}
              </div>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
