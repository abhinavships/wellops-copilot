"use client";
import { createContext, useContext } from "react";
import type { Citation, Claim } from "@/contracts";
import type { Ctx } from "@/lib/api";

export type EvidenceTarget = { citation: Citation } | { claim: Claim };
export interface UiCtx {
  ctx: Ctx;
  openEvidence: (t: EvidenceTarget) => void;
  send: (text: string) => void;
}
export const UiContext = createContext<UiCtx | null>(null);
export const useUi = () => {
  const v = useContext(UiContext);
  if (!v) throw new Error("UiContext missing");
  return v;
};

type Tone = "neutral" | "ok" | "warn" | "bad" | "info";
const TONES: Record<Tone, string> = {
  neutral: "bg-raised text-muted border-line",
  ok: "bg-ok/10 text-ok border-ok/30",
  warn: "bg-warn/10 text-warn border-warn/30",
  bad: "bg-bad/10 text-bad border-bad/30",
  info: "bg-info/10 text-info border-info/30",
};
export function Badge({ children, tone = "neutral", title, icon }: { children: React.ReactNode; tone?: Tone; title?: string; icon?: React.ReactNode }) {
  return (
    <span title={title} className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-medium leading-4 ${TONES[tone]}`}>
      {icon}{children}
    </span>
  );
}

export function CitationChip({ c }: { c: Citation }) {
  const { openEvidence } = useUi();
  return (
    <button type="button" onClick={() => openEvidence({ citation: c })}
      className="inline-flex items-center gap-1 rounded border border-line bg-panel px-1.5 py-0.5 font-mono text-[11px] text-muted hover:border-accent hover:text-accent"
      aria-label={`Open evidence ${c.kind} ${c.id}`}>
      <span className="text-[9px] uppercase tracking-wide opacity-70">{c.kind}</span>{c.label && c.label !== c.id ? c.id : c.id}
    </button>
  );
}

export function stateTone(s: string): Tone {
  const x = s.toLowerCase();
  if (/(breach|overdue|fail|bad|not producing|shut|miss|expired|not approved)/.test(x)) return "bad";
  if (/(pending|no response|no_response|stale|unconfirmed|warn|disputed)/.test(x)) return "warn";
  if (/(ok|met|producing|pass|confirmed|done|allowed)/.test(x)) return "ok";
  return "neutral";
}

export const Card = ({ children, className = "" }: { children: React.ReactNode; className?: string }) => (
  <div className={`rounded-xl border border-line bg-panel ${className}`}>{children}</div>
);
