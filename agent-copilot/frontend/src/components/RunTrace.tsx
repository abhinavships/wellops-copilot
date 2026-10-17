"use client";
import { useState } from "react";
import type { Turn } from "@/lib/chat";

type Node = { name: string; ms?: number; ok?: boolean; live?: boolean; detail?: string };

const pretty = (n: string) => n.replace(/^mcp__wellops__/, "");

/** The agent run as a pipeline, built from AG-UI tool-call events (live) or the measured wellops.trace (final). */
function nodesOf(t: Turn): Node[] {
  if (t.trace?.steps?.length) {
    return [...t.trace.steps.map((s) => ({ name: pretty(s.name), ms: s.ms, ok: s.ok, detail: s.detail })),
      ...(t.answer ? [{ name: "render", ok: true, detail: (t.answer.visuals ?? []).map((v) => v.type).filter((x) => x !== "none").join(" + ") || "text" }] : [])];
  }
  const nodes: Node[] = t.tools.map((c) => ({ name: pretty(c.name), ms: c.endedAt ? c.endedAt - c.startedAt : undefined,
    ok: c.status === "error" ? false : c.status === "done" ? true : undefined, live: c.status === "running" }));
  if (t.status === "running") nodes.push({ name: nodes.length ? "thinking" : "planning", live: true });
  return nodes;
}

function Dot({ n }: { n: Node }) {
  if (n.live) return <span className="dot-live h-3 w-3 rounded-full border-2 border-fg bg-hl" />;
  if (n.ok === false) return <span className="grid h-3.5 w-3.5 place-items-center rounded-full bg-bad text-[9px] font-bold text-white">×</span>;
  return <span className="grid h-3.5 w-3.5 place-items-center rounded-full border-2 border-fg bg-panel"><span className="h-1 w-1 rounded-full bg-fg" /></span>;
}

export function RunTrace({ turn, index }: { turn: Turn; index: number }) {
  const [open, setOpen] = useState(false);
  const nodes = nodesOf(turn);
  const running = turn.status === "running";
  if (!nodes.length) return null;
  const total = turn.cache?.hit ? turn.cache.ms : turn.trace?.total_ms ?? (turn.endedAt ? turn.endedAt - turn.startedAt : undefined);
  const steps = nodes.filter((n) => !["render", "planning", "thinking"].includes(n.name)).length;
  const expanded = running || open;
  return (
    <div className="select-none" data-testid="run-trace">
      <button type="button" onClick={() => setOpen((o) => !o)} disabled={running} aria-expanded={expanded}
        className="label flex w-full items-center gap-2 border-b border-line pb-1.5 text-left hover:text-fg disabled:cursor-default">
        <span className="text-fg">Fig. {index}</span><span>— run trace</span>
        <span className="flex-1" />
        {turn.cache?.hit ? <span className="rounded bg-hl/70 px-1.5 text-fg">⚡ cached · {total} ms</span>
          : <span>{steps} step{steps === 1 ? "" : "s"}{total !== undefined && !running ? ` · ${(total / 1000).toFixed(1)} s` : ""}</span>}
        {!running && <span aria-hidden>{expanded ? "▴" : "▾"}</span>}
      </button>
      {expanded && (
        <div className="overflow-x-auto pb-1 pt-4">
          <ol className="flex min-w-max items-start">
            {nodes.map((n, i) => (
              <li key={`${n.name}${i}`} className="trace-node flex items-start" style={{ animationDelay: `${i * 60}ms` }}>
                {i > 0 && <span className={`mt-[6px] h-px w-10 sm:w-14 ${n.live ? "border-t border-dashed border-muted" : "bg-fg"}`} />}
                <span className="flex w-28 flex-col items-center text-center">
                  <Dot n={n} />
                  <span className="mt-1.5 font-mono text-[12px] text-fg">{n.name}</span>
                  <span className={`font-mono text-[10.5px] ${n.ok === false ? "text-bad" : "text-muted"}`}>
                    {n.live ? "running…" : n.ok === false ? "× failed" : n.detail ? n.detail : n.ms !== undefined ? `✓ ${n.ms} ms` : "✓"}
                  </span>
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
