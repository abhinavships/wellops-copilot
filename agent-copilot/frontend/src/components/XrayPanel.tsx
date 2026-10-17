"use client";
import type { Turn } from "@/lib/chat";
import { argsSummary } from "@/components/ToolChip";
import { fmtMs } from "@/lib/format";

const H = ({ children }: { children: React.ReactNode }) => <h3 className="mb-1.5 mt-4 text-[11px] font-semibold uppercase tracking-wide text-muted">{children}</h3>;

export function XrayPanel({ turns, selected, onSelect }: { turns: Turn[]; selected: string | null; onSelect: (id: string) => void }) {
  const t = turns.find((x) => x.id === selected) ?? turns[turns.length - 1];
  if (!t) return <p className="p-4 text-sm text-muted">Ask a question to see how the answer was produced: skills, tools, verification gates, cost and trace.</p>;
  const skills = t.capabilities.filter((c) => c.kind === "skill");
  const other = t.capabilities.filter((c) => c.kind !== "skill");
  const trace = t.usage?.trace_url ?? t.verification?.trace_url ?? (t.capabilities.find((c) => typeof c.trace_url === "string")?.trace_url as string | undefined);
  const total = t.endedAt ? t.endedAt - t.startedAt : null;
  return (
    <div className="p-4 text-sm">
      <label className="block text-[11px] font-semibold uppercase tracking-wide text-muted" htmlFor="turn-sel">Turn</label>
      <select id="turn-sel" value={t.id} onChange={(e) => onSelect(e.target.value)} className="mt-1 w-full rounded-md border border-line bg-panel px-2 py-1.5">
        {turns.map((x, i) => <option key={x.id} value={x.id}>{i + 1}. {x.userText.slice(0, 40)}</option>)}
      </select>
      <p className="mt-2 text-xs text-muted">{t.status === "running" ? "Running…" : t.status === "error" ? `Error: ${t.error}` : `Finished${total ? ` in ${fmtMs(total)}` : ""}`}</p>

      <H>Skills loaded</H>
      {skills.length ? <ul className="flex flex-wrap gap-1.5">{skills.map((s, i) => <li key={i} className="rounded-md border border-line bg-panel px-2 py-0.5 font-mono text-xs">{s.name}</li>)}</ul> : <p className="text-xs text-muted">None reported.</p>}

      {other.length > 0 && <><H>Capabilities</H><ul className="space-y-1">{other.map((c, i) => <li key={i} className="text-xs"><span className="font-mono font-semibold">{c.name}</span> <span className="text-muted">{c.kind} · {c.action}</span></li>)}</ul></>}

      <H>Tools called ({t.tools.length})</H>
      {t.tools.length ? (
        <ol className="space-y-2">{t.tools.map((c) => (
          <li key={c.id} className="rounded-lg border border-line bg-panel p-2">
            <div className="flex items-center justify-between gap-2"><span className="font-mono text-xs font-semibold">{c.name}</span><span className="tabular-nums text-xs text-muted">{c.endedAt ? fmtMs(c.endedAt - c.startedAt) : "…"}</span></div>
            <div className="mt-0.5 break-words text-xs text-muted">{argsSummary(c.argsText)}</div>
            {c.result && <div className="mt-1 text-xs"><span className="rounded bg-accent/15 px-1 font-mono text-accent">{c.result.handle}</span> <span className="text-muted">{c.result.shape} · {c.result.row_count} rows</span></div>}
          </li>))}</ol>
      ) : <p className="text-xs text-muted">No tool calls.</p>}

      <H>Verification gates</H>
      {t.verification ? (
        <>
          <ul className="space-y-1">{t.verification.gates.map((g) => (
            <li key={g.name} className="flex items-start gap-2 text-xs">
              <span aria-hidden className={`mt-0.5 font-bold ${g.ok ? "text-ok" : "text-bad"}`}>{g.ok ? "✓" : "✗"}</span>
              <span><span className="font-semibold">{g.name}</span><span className="sr-only">{g.ok ? " passed" : " failed"}</span>{g.detail && <span className="block text-muted">{g.detail}</span>}</span>
            </li>))}</ul>
          <p className="mt-1.5 text-xs text-muted">Retries: <b className="text-fg">{t.verification.retries}</b></p>
        </>
      ) : <p className="text-xs text-muted">{t.status === "running" ? "Pending…" : "Not reported."}</p>}

      <H>Usage</H>
      {t.usage ? (
        <dl className="grid grid-cols-2 gap-1 text-xs">
          {t.usage.model && <><dt className="text-muted">Model</dt><dd>{t.usage.model}</dd></>}
          <dt className="text-muted">Tokens in / out</dt><dd className="tabular-nums">{t.usage.input_tokens?.toLocaleString() ?? "–"} / {t.usage.output_tokens?.toLocaleString() ?? "–"}</dd>
          <dt className="text-muted">Cost</dt><dd className="tabular-nums">{t.usage.cost_usd !== undefined ? `$${t.usage.cost_usd.toFixed(3)}` : "–"}</dd>
        </dl>
      ) : <p className="text-xs text-muted">Not provided.</p>}

      {trace && <a href={trace} target="_blank" rel="noreferrer" className="mt-4 inline-block rounded-md border border-line bg-panel px-3 py-1.5 text-xs font-semibold text-accent hover:border-accent">Open Opik trace ↗</a>}
    </div>
  );
}
