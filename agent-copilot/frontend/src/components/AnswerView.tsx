"use client";
import { Children, cloneElement, isValidElement, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import type { AnswerSpec, Claim } from "@/contracts";
import { Badge, CitationChip, useUi } from "@/components/ui";
import { VisualFrame } from "@/components/VisualFrame";
import { ConflictBody } from "@/components/visuals/ConflictCard";
import { Card } from "@/components/ui";
import { ToolChip } from "@/components/ToolChip";
import type { Turn } from "@/lib/chat";

const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function ClaimChip({ claim, children }: { claim: Claim; children: ReactNode }) {
  const { openEvidence } = useUi();
  return (
    <button type="button" onClick={() => openEvidence({ claim })}
      title={`Grounded in ${claim.handle}${claim.column ? `.${claim.column}` : ""}. Click for evidence.`}
      className="rounded border-b border-dotted border-accent bg-accent/10 px-0.5 font-medium text-inherit hover:bg-accent/20">
      {children}
    </button>
  );
}

function withClaims(node: ReactNode, claims: Claim[]): ReactNode {
  if (!claims.length) return node;
  // Whole tokens only, so a claim "26" does not light up inside "2026".
  const re = new RegExp(`(?<![\\w.])(${[...claims].sort((a, b) => b.text.length - a.text.length).map((c) => esc(c.text)).join("|")})(?![\\w])`, "g");
  const used = new Set<string>();
  const walk = (n: ReactNode): ReactNode =>
    Children.map(n, (ch) => {
      if (typeof ch === "string") {
        return ch.split(re).map((part, i) => {
          const cl = claims.find((c) => c.text === part);
          if (cl && !used.has(part)) { used.add(part); return <ClaimChip key={i} claim={cl}>{part}</ClaimChip>; }
          return part;
        });
      }
      if (isValidElement<{ children?: ReactNode }>(ch) && ch.props.children !== undefined && ch.type !== ClaimChip) return cloneElement(ch, undefined, walk(ch.props.children));
      return ch;
    });
  return walk(node);
}

/** Split markdown into the lead (first paragraph) and the rest, which goes behind "Show work". */
export function splitLead(md: string): [string, string] {
  const parts = md.trim().split(/\n\s*\n/);
  const first = parts[0] ?? "";
  if (/^\s*([-*]|\d+\.)\s/m.test(first)) return [first, parts.slice(1).join("\n\n")];
  const [lead, ...more] = first.split(/(?<=[.!?])\s+(?=[A-Z*])/); // first sentence only
  return [lead, [more.join(" "), ...parts.slice(1)].filter(Boolean).join("\n\n")];
}

function Md({ md, claims }: { md: string; claims: Claim[] }) {
  const wrap = (Tag: "p" | "li" | "td") => function Wrapped({ children }: { children?: ReactNode }) {
    return <Tag>{withClaims(children, claims)}</Tag>;
  };
  return <ReactMarkdown components={{ p: wrap("p"), li: wrap("li"), td: wrap("td") }}>{md}</ReactMarkdown>;
}

/**
 * Minimal answer: the lead sentence and the generated UI. Everything else (detail prose, tool calls with SQL,
 * assumptions, citations, overlays, verification) sits behind one collapsed "Show work" per answer.
 */
export function AnswerView({ answer, text, streaming, turn }: { answer?: AnswerSpec; text: string; streaming: boolean; turn?: Turn }) {
  const { send } = useUi();
  const md = answer?.markdown ?? text;
  const [lead, rest] = answer ? splitLead(md) : [md, ""];
  const claims = answer?.claims ?? [];
  const visuals = (answer?.visuals ?? []).filter((v) => v.type !== "none");
  const hasConflictVisual = visuals.some((v) => v.type === "conflict_card");
  const extraConflicts = hasConflictVisual ? [] : answer?.conflicts ?? [];
  const tools = turn?.tools ?? [];
  const gates = turn?.verification?.gates ?? [];
  const work = answer && (rest || tools.length || (answer.assumptions ?? []).length || (answer.citations ?? []).length || gates.length);
  return (
    <div className="space-y-4">
      {lead && (
        <div className="prose-chat text-[15px] leading-relaxed" aria-live={streaming ? "polite" : "off"}>
          <Md md={lead} claims={claims} />
          {streaming && <span className="dots text-muted" aria-hidden><span>●</span><span>●</span><span>●</span></span>}
        </div>
      )}
      {visuals.map((v, i) => <VisualFrame key={i} visual={v} answer={answer!} />)}
      {extraConflicts.map((c, i) => <Card key={i} className="rounded-2xl p-4"><ConflictBody c={c} /></Card>)}
      {work ? (
        <details className="group text-sm" data-testid="show-work">
          <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 rounded-full px-2 py-0.5 text-xs text-muted transition hover:bg-raised hover:text-fg">
            <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden className="transition group-open:rotate-90"><path d="M4 2l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.5" /></svg>
            Show work{gates.length ? ` · ${gates.every((g) => g.ok) ? "verified" : "check failed"}` : ""}{tools.length ? ` · ${tools.length} tool call${tools.length > 1 ? "s" : ""}` : ""}
          </summary>
          <div className="mt-2 space-y-3 rounded-2xl bg-raised/50 p-4">
            {rest && <div className="prose-chat text-sm"><Md md={rest} claims={claims} /></div>}
            {tools.length > 0 && (
              <div className="space-y-1.5">
                <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted">Tools</h4>
                {tools.map((c) => (
                  <div key={c.id}>
                    <ToolChip call={c} />
                    {c.result?.sql && <pre className="mt-1 max-h-32 overflow-auto rounded-lg bg-bg p-2 font-mono text-[11px] text-muted">{c.result.sql}</pre>}
                  </div>
                ))}
              </div>
            )}
            {gates.length > 0 && (
              <div className="flex flex-wrap gap-1.5">{gates.map((g) => <Badge key={g.name} tone={g.ok ? "ok" : "bad"} title={g.detail ?? undefined}>{g.ok ? "✓" : "✕"} {g.name}</Badge>)}</div>
            )}
            <div className="flex flex-wrap items-center gap-1.5">
              {(answer.assumptions ?? []).map((x, i) => <Badge key={i} tone="neutral" title="Assumption">{x}</Badge>)}
              {(answer.applied_overlays ?? []).map((o) => <Badge key={o} tone="info">overlay {o}</Badge>)}
              {(answer.citations ?? []).map((c) => <CitationChip key={`${c.kind}${c.id}${c.span?.join("-")}`} c={c} />)}
            </div>
            {(answer.follow_ups ?? []).length > 0 && (
              <div className="flex flex-wrap gap-2">
                {answer.follow_ups!.map((f) => <button key={f} type="button" onClick={() => send(f)} className="rounded-full border border-line bg-panel px-3 py-1 text-xs hover:border-accent hover:text-accent">{f}</button>)}
              </div>
            )}
          </div>
        </details>
      ) : null}
    </div>
  );
}
