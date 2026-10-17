"use client";
import type { VisualProps } from ".";
import { Badge, stateTone } from "@/components/ui";
import { fmtDate, fmtVal, num, titleCase } from "@/lib/format";

const DOT: Record<string, string> = { bad: "bg-bad", warn: "bg-warn", ok: "bg-ok", neutral: "bg-muted", info: "bg-info" };

export function ObligationTimeline({ rows }: VisualProps) {
  const sorted = [...rows].sort((a, b) => String(a.due ?? "").localeCompare(String(b.due ?? "")));
  return (
    <ol className="relative space-y-3 border-l-2 border-line pl-5" aria-label="Obligations by due date">
      {sorted.map((r, i) => {
        const state = String(r.state ?? ""); const tone = stateTone(state);
        const actual = num(r.actual), limit = num(r.limit); const unit = r.unit ? String(r.unit) : "";
        const scaleMax = actual !== null && limit !== null ? Math.max(actual, limit) * 1.1 : 0;
        return (
          <li key={i} className="relative">
            <span className={`absolute -left-[1.78rem] top-1.5 h-3 w-3 rounded-full ring-4 ring-panel ${DOT[tone]}`} aria-hidden />
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-sm font-semibold">{String(r.obligation_id ?? r.id ?? "")}</span>
              <span className="text-sm">{String(r.label ?? "")}</span>
              <Badge tone={tone}>{titleCase(state)}</Badge>
              {typeof r.due === "string" && <span className="ml-auto text-xs text-muted">due {fmtDate(r.due)} {r.due.slice(0, 4)}</span>}
            </div>
            {actual !== null && limit !== null && (
              <div className="mt-1.5">
                <div className="relative h-2.5 rounded-full bg-raised" role="img" aria-label={`Actual ${fmtVal(actual)} ${unit} against limit ${fmtVal(limit)} ${unit}`}>
                  <div className={`h-2.5 rounded-full ${actual > limit ? "bg-bad" : "bg-ok"}`} style={{ width: `${(actual / scaleMax) * 100}%` }} />
                  <div className="absolute -top-0.5 h-3.5 border-l-2 border-fg" style={{ left: `${(limit / scaleMax) * 100}%` }} title={`Limit ${limit}${unit}`} />
                </div>
                <div className="mt-0.5 text-xs text-muted"><b className="text-fg">{fmtVal(actual)}{unit}</b> actual vs <b className="text-fg">{fmtVal(limit)}{unit}</b> limit</div>
              </div>
            )}
            {typeof r.detail === "string" && <p className="mt-1 text-xs text-muted">{r.detail}</p>}
          </li>
        );
      })}
    </ol>
  );
}
