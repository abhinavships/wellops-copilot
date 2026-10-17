"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { AguiEvent, AnswerSpec, CapabilityEvent, Conflict, ToolResult, UsageEvent, VerificationEvent } from "@/contracts";
import { runAgent, uid } from "@/lib/agui";
import type { Ctx } from "@/lib/api";

export interface ToolCall {
  id: string; name: string; argsText: string; startedAt: number; endedAt?: number;
  result?: ToolResult; resultRaw?: string; status: "running" | "done" | "error";
}
export interface Turn {
  id: string; userText: string; text: string; tools: ToolCall[]; capabilities: CapabilityEvent[];
  conflicts: Conflict[]; answer?: AnswerSpec; verification?: VerificationEvent; usage?: UsageEvent;
  status: "running" | "done" | "error"; error?: string; startedAt: number; endedAt?: number;
  stateDeltas: unknown[][];
}

const storeKey = (chatId: string) => `wellops.turns.${chatId}`;
export function loadTurns(chatId: string): Turn[] {
  try { const s = localStorage.getItem(storeKey(chatId)); return s ? (JSON.parse(s) as Turn[]).map((t) => ({ ...t, status: t.status === "running" ? "done" : t.status })) : []; } catch { return []; }
}
function saveTurns(chatId: string, turns: Turn[]) {
  try { localStorage.setItem(storeKey(chatId), JSON.stringify(turns)); } catch { /* storage unavailable */ }
}

export function reduceEvent(t: Turn, ev: AguiEvent, now = Date.now()): Turn {
  switch (ev.type) {
    // A verifier retry streams a fresh message; show the latest attempt, not all of them glued together.
    case "TEXT_MESSAGE_START": return { ...t, text: "" };
    case "TEXT_MESSAGE_CONTENT": return { ...t, text: t.text + ev.delta };
    case "TEXT_MESSAGE_CHUNK": return ev.delta ? { ...t, text: t.text + ev.delta } : t;
    case "TOOL_CALL_START":
      return { ...t, tools: [...t.tools, { id: ev.toolCallId, name: ev.toolCallName, argsText: "", startedAt: now, status: "running" }] };
    case "TOOL_CALL_ARGS":
      return { ...t, tools: t.tools.map((c) => (c.id === ev.toolCallId ? { ...c, argsText: c.argsText + ev.delta } : c)) };
    case "TOOL_CALL_RESULT": {
      let result: ToolResult | undefined;
      try { const p = JSON.parse(ev.content); if (p && typeof p === "object" && "handle" in p) result = p as ToolResult; } catch { /* non-JSON result */ }
      return { ...t, tools: t.tools.map((c) => (c.id === ev.toolCallId ? { ...c, endedAt: now, result, resultRaw: ev.content, status: "done" } : c)) };
    }
    case "STATE_DELTA": return { ...t, stateDeltas: [...(t.stateDeltas ?? []), ev.delta] };
    case "CUSTOM": {
      switch (ev.name) {
        case "wellops.answer": return { ...t, answer: ev.value as AnswerSpec };
        case "wellops.verification": return { ...t, verification: ev.value as VerificationEvent };
        case "wellops.capability": return { ...t, capabilities: [...t.capabilities, ev.value as CapabilityEvent] };
        case "wellops.conflict": return { ...t, conflicts: [...t.conflicts, ev.value as Conflict] };
        case "wellops.usage": return { ...t, usage: { ...t.usage, ...(ev.value as UsageEvent) } };
        default: return t;
      }
    }
    case "RUN_ERROR": return { ...t, status: "error", error: ev.message, endedAt: now };
    case "RUN_FINISHED": return { ...t, status: t.status === "error" ? "error" : "done", endedAt: now };
    default: return t;
  }
}

export function useChat(ctx: Ctx) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const turnsRef = useRef<Turn[]>([]);
  const ctxRef = useRef(ctx);
  ctxRef.current = ctx;

  const commit = useCallback((next: Turn[], persist: boolean) => {
    turnsRef.current = next; setTurns(next);
    if (persist) saveTurns(ctxRef.current.chatId, next);
  }, []);

  // Switch chat: abort any run and load stored turns.
  useEffect(() => {
    abortRef.current?.abort(); setBusy(false);
    commit(loadTurns(ctx.chatId), false);
  }, [ctx.chatId, commit]);

  const send = useCallback(async (text: string, scenario?: string) => {
    const c = ctxRef.current;
    const turn: Turn = { id: uid(), userText: text, text: "", tools: [], capabilities: [], conflicts: [], status: "running", startedAt: Date.now(), stateDeltas: [] };
    const history = turnsRef.current.flatMap((t) => [
      { id: `${t.id}-u`, role: "user" as const, content: t.userText },
      ...(t.answer || t.text ? [{ id: `${t.id}-a`, role: "assistant" as const, content: t.answer?.markdown ?? t.text }] : []),
    ]);
    commit([...turnsRef.current, turn], true);
    const ctl = new AbortController(); abortRef.current = ctl; setBusy(true);
    const patch = (fn: (t: Turn) => Turn, persist = false) => {
      commit(turnsRef.current.map((t) => (t.id === turn.id ? fn(t) : t)), persist);
    };
    try {
      const input = { threadId: c.chatId, runId: uid(), state: {}, messages: [...history, { id: uid(), role: "user" as const, content: text }], tools: [], context: [], forwardedProps: scenario ? { scenario } : {} };
      for await (const ev of runAgent(input, c, ctl.signal)) patch((t) => reduceEvent(t, ev), ev.type === "RUN_FINISHED" || ev.type === "RUN_ERROR");
      patch((t) => (t.status === "running" ? { ...t, status: "done", endedAt: Date.now() } : t), true);
    } catch (e) {
      if ((e as Error).name === "AbortError") patch((t) => ({ ...t, status: "done", endedAt: Date.now() }), true);
      else patch((t) => ({ ...t, status: "error", error: (e as Error).message, endedAt: Date.now() }), true);
    } finally { setBusy(false); }
  }, [commit]);

  /** A turn answered in the client (chart switches, upload reports): recorded, never sent to the agent. */
  const addLocal = useCallback((userText: string, answer: AnswerSpec) => {
    const now = Date.now();
    commit([...turnsRef.current, { id: uid(), userText, text: answer.markdown, tools: [], capabilities: [], conflicts: [], answer,
      status: "done", startedAt: now, endedAt: now, stateDeltas: [] }], true);
  }, [commit]);

  const stop = useCallback(() => abortRef.current?.abort(), []);
  return { turns, busy, send, stop, addLocal };
}
