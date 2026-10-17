"use client";
import { Children, cloneElement, isValidElement, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import type { AnswerSpec, Claim } from "@/contracts";
import { Badge, CitationChip, useUi } from "@/components/ui";
import { VisualFrame } from "@/components/VisualFrame";
import { ConflictBody } from "@/components/visuals/ConflictCard";
import { Card } from "@/components/ui";

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

export function AnswerView({ answer, text, streaming }: { answer?: AnswerSpec; text: string; streaming: boolean }) {
  const { send } = useUi();
  const md = answer?.markdown ?? text;
  const claims = answer?.claims ?? [];
  const visuals = (answer?.visuals ?? []).filter((v) => v.type !== "none");
  const hasConflictVisual = visuals.some((v) => v.type === "conflict_card");
  const extraConflicts = hasConflictVisual ? [] : answer?.conflicts ?? [];
  const wrap = (Tag: "p" | "li" | "td" | "strong" | "em") => function Wrapped({ children }: { children?: ReactNode }) {
    return <Tag>{withClaims(children, claims)}</Tag>;
  };
  return (
    <div className="space-y-3">
      {md && (
        <div className="prose-chat text-[15px]" aria-live={streaming ? "polite" : "off"}>
          <ReactMarkdown components={{ p: wrap("p"), li: wrap("li"), td: wrap("td") }}>{md}</ReactMarkdown>
          {streaming && <span className="dots text-muted" aria-hidden><span>●</span><span>●</span><span>●</span></span>}
        </div>
      )}
      {visuals.map((v, i) => <VisualFrame key={i} visual={v} answer={answer!} />)}
      {extraConflicts.map((c, i) => <Card key={i} className="p-3"><ConflictBody c={c} /></Card>)}
      {answer && (
        <div className="flex flex-wrap items-center gap-1.5">
          {(answer.assumptions ?? []).map((a, i) => <Badge key={i} tone="neutral" title="Assumption">{a}</Badge>)}
          {(answer.applied_overlays ?? []).map((o) => <Badge key={o} tone="info">overlay {o}</Badge>)}
          {(answer.citations ?? []).map((c) => <CitationChip key={`${c.kind}${c.id}${c.span?.join("-")}`} c={c} />)}
        </div>
      )}
      {answer && (answer.follow_ups ?? []).length > 0 && (
        <div className="flex flex-wrap gap-2 pt-1">
          {answer.follow_ups!.map((f) => (
            <button key={f} type="button" onClick={() => send(f)} className="rounded-full border border-line bg-panel px-3 py-1 text-xs hover:border-accent hover:text-accent">{f}</button>
          ))}
        </div>
      )}
    </div>
  );
}
