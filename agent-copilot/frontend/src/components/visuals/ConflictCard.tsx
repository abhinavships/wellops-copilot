"use client";
import { useState } from "react";
import type { VisualProps } from ".";
import type { Conflict } from "@/contracts";
import { Badge, CitationChip, useUi } from "@/components/ui";
import { titleCase } from "@/lib/format";

const RES_TONE = { unresolved: "warn", data_supports_a: "ok", correction_applied: "info", claim_unconfirmed: "warn" } as const;

export function ConflictCard({ visual, answer }: VisualProps) {
  const idx = typeof visual.encodings?.conflict_index === "number" ? visual.encodings.conflict_index : 0;
  const c = answer.conflicts?.[idx];
  return c ? <ConflictBody c={c} /> : <p className="text-sm text-muted">No conflict payload attached.</p>;
}

export function ConflictBody({ c }: { c: Conflict }) {
  const { send } = useUi();
  const [choice, setChoice] = useState<string | null>(null);
  const [commenting, setCommenting] = useState(false);
  const [text, setText] = useState("");
  const [a, b] = [c.sides[0], c.sides[1]];
  const label = (s?: Conflict["sides"][number]) => (s ? `${s.author ?? s.source ?? "source"}${s.ref ? ` (${s.ref})` : ""}` : "");
  const decide = (kind: string, msg: string) => { setChoice(kind); setCommenting(false); send(msg); };
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold">{c.subject}</span>
        <Badge tone={RES_TONE[c.resolution]}>{titleCase(c.resolution)}</Badge>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {c.sides.map((s, i) => (
          <div key={i} className="rounded-lg border border-line bg-raised p-3">
            <div className="flex items-center justify-between text-[11px] uppercase tracking-wide text-muted"><span>Side {String.fromCharCode(65 + i)} · {String(s.source ?? "")}</span>{s.ref && <CitationChip c={{ kind: String(s.ref).startsWith("A-") ? "assertion" : "note", id: String(s.ref) }} />}</div>
            <p className="mt-1 text-sm font-medium">{String(s.author ?? "")}</p>
            <blockquote className="mt-1 text-sm italic">“{String(s.says ?? "")}”</blockquote>
          </div>
        ))}
      </div>
      {c.ask && <p className="mt-3 text-sm">{c.ask}</p>}
      {choice ? (
        <p className="mt-2 text-xs text-muted" role="status">Your choice <b className="text-fg">{choice}</b> was sent to the assistant.</p>
      ) : (
        <div className="mt-2 flex flex-wrap gap-2">
          <button type="button" onClick={() => decide("Confirm", `Confirm: I confirm ${label(a)} for "${c.subject}". Treat it as correct.`)}
            className="rounded-md bg-ok px-3 py-1.5 text-xs font-semibold text-bg hover:opacity-90">Confirm {a?.author ? `${String(a.author).split(" ")[0]}'s correction` : "side A"}</button>
          <button type="button" onClick={() => decide("Reject", `Reject: ${label(a)} is wrong for "${c.subject}". Keep ${label(b)} as correct.`)}
            className="rounded-md bg-bad px-3 py-1.5 text-xs font-semibold text-bg hover:opacity-90">Reject</button>
          <button type="button" onClick={() => setCommenting((v) => !v)} aria-expanded={commenting}
            className="rounded-md border border-line bg-panel px-3 py-1.5 text-xs font-semibold hover:border-accent">Comment</button>
        </div>
      )}
      {commenting && !choice && (
        <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); if (text.trim()) { decide("Comment", `Comment on "${c.subject}": ${text.trim()}`); } }}>
          <label className="sr-only" htmlFor="cmt">Comment</label>
          <input id="cmt" value={text} onChange={(e) => setText(e.target.value)} placeholder="Add context for the assistant" className="min-w-0 flex-1 rounded-md border border-line bg-panel px-2 py-1 text-sm" />
          <button type="submit" className="rounded-md bg-accent px-3 py-1 text-xs font-semibold text-accent-fg">Send</button>
        </form>
      )}
    </div>
  );
}
