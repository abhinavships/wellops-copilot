"use client";
import type { VisualProps } from ".";
import type { CitationKind } from "@/contracts";
import { useUi } from "@/components/ui";
import { fmtDate, titleCase, useWidth } from "@/lib/format";

const KIND_COLOR: Record<string, string> = { inspection: "--c1", maintenance: "--c2", report: "--c4", reg_event: "--bad", note: "--c3" };

function wrap(t: string, max: number): string[] {
  const out: string[] = []; let cur = "";
  for (const w of t.split(" ")) { if ((cur + " " + w).trim().length > max && cur) { out.push(cur); cur = w; } else cur = (cur + " " + w).trim(); }
  if (cur) out.push(cur); return out.slice(0, 2);
}

export function EvidenceChain({ rows }: VisualProps) {
  const { openEvidence } = useUi();
  const [ref, width] = useWidth<HTMLDivElement>();
  const n = rows.length, GAP = 26, horizontal = width >= n * 128 + (n - 1) * GAP;
  const NW = horizontal ? Math.min(170, Math.floor((width - (n - 1) * GAP) / n)) : 150, NH = 76, gap = GAP;
  const W = horizontal ? n * NW + (n - 1) * gap : Math.max(width, 240), H = horizontal ? NH + 8 : n * NH + (n - 1) * gap + 8;
  const pos = (i: number) => (horizontal ? { x: i * (NW + gap), y: 4 } : { x: 0, y: 4 + i * (NH + gap) });
  const nw = horizontal ? NW : Math.min(W, 340);
  return (
    <div ref={ref} className="w-full overflow-x-auto">
      {width > 0 && (
        <svg width={W} height={H} role="group" aria-label="Evidence chain">
          <defs><marker id="arr" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L8 4L0 8z" fill="rgb(var(--muted))" /></marker></defs>
          {rows.slice(1).map((_, i) => {
            const p = pos(i), q = pos(i + 1);
            const x1 = horizontal ? p.x + nw : p.x + nw / 2, y1 = horizontal ? p.y + NH / 2 : p.y + NH, x2 = horizontal ? q.x : q.x + nw / 2, y2 = horizontal ? q.y + NH / 2 : q.y;
            return <line key={i} x1={x1} y1={y1} x2={x2 - (horizontal ? 2 : 0)} y2={y2 - (horizontal ? 0 : 2)} stroke="rgb(var(--muted))" strokeWidth={1.5} markerEnd="url(#arr)" />;
          })}
          {rows.map((r, i) => {
            const p = pos(i); const kind = String(r.kind); const col = KIND_COLOR[kind] ?? "--muted";
            return (
              <g key={i} transform={`translate(${p.x},${p.y})`} tabIndex={0} role="button" aria-label={`${kind} ${r.id}: ${r.label}`} style={{ cursor: "pointer" }}
                onClick={() => openEvidence({ citation: { kind: kind as CitationKind, id: String(r.id) } })}
                onKeyDown={(e) => e.key === "Enter" && openEvidence({ citation: { kind: kind as CitationKind, id: String(r.id) } })}>
                <rect width={nw} height={NH} rx={10} fill="rgb(var(--raised))" stroke={`rgb(var(${col}))`} strokeWidth={1.5} />
                <rect width={5} height={NH} rx={2} fill={`rgb(var(${col}))`} />
                <text x={14} y={17} fontSize={10} fontWeight={700} fill={`rgb(var(${col}))`}>{titleCase(kind).toUpperCase()}{r.at ? ` · ${fmtDate(String(r.at))}` : ""}</text>
                <text x={14} y={34} fontSize={11.5} fontWeight={600} fontFamily="ui-monospace, monospace" fill="rgb(var(--fg))">{String(r.id)}</text>
                {wrap(String(r.label ?? ""), Math.floor((nw - 22) / 5.6)).map((ln, k) => <text key={k} x={14} y={51 + k * 13} fontSize={10.5} fill="rgb(var(--muted))">{ln}</text>)}
              </g>
            );
          })}
        </svg>
      )}
    </div>
  );
}
