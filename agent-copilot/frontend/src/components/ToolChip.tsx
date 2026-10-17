"use client";
import { useEffect, useState } from "react";
import type { ToolCall } from "@/lib/chat";
import { fmtMs } from "@/lib/format";

export function argsSummary(argsText: string): string {
  try {
    const o = JSON.parse(argsText) as Record<string, unknown>;
    return Object.entries(o).map(([k, v]) => `${k}=${Array.isArray(v) ? `[${v.join(",")}]` : String(v)}`).join(", ");
  } catch { return argsText; }
}

export function ToolChip({ call }: { call: ToolCall }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { if (call.status !== "running") return; const t = setInterval(() => setNow(Date.now()), 200); return () => clearInterval(t); }, [call.status]);
  const dur = (call.endedAt ?? now) - call.startedAt;
  return (
    <span className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-line bg-panel px-2 py-1 text-xs" title={`${call.name}(${argsSummary(call.argsText)})`}>
      <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${call.status === "running" ? "animate-pulse bg-warn" : "bg-ok"}`} />
      <span className="font-mono font-semibold">{call.name}</span>
      <span className="truncate text-muted">{argsSummary(call.argsText)}</span>
      <span className="shrink-0 tabular-nums text-muted">{fmtMs(dur)}</span>
      {call.result?.handle && <span className="shrink-0 rounded bg-accent/15 px-1 font-mono text-accent">{call.result.handle}</span>}
      <span className="sr-only">{call.status === "running" ? "running" : "finished"}</span>
    </span>
  );
}
