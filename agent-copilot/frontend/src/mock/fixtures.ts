/**
 * Mock-mode fixtures. Generated from real tool output by backend/scripts/make_fixtures.py (no LLM): every handle,
 * id, number and verification result below came from the tools and the L4 verifier on the sample dataset.
 * Regenerate instead of editing fixtures.json by hand.
 */
import type { AguiEvent, AnswerSpec, HandleData, RecordData, ToolResult, UsageEvent, User, VerificationEvent } from "@/contracts";
import data from "./fixtures.json";

export interface Scenario {
  id: string;
  label: string;
  question: string;
  keywords: string[];
  skills: string[];
  tools: { name: string; args: Record<string, unknown>; ms: number; handle: string | null; result: unknown }[];
  handles: Record<string, HandleData>;
  answer: AnswerSpec;
  verification: VerificationEvent;
  usage: UsageEvent;
}

const fx = data as unknown as { today: string; scenarios: Scenario[]; users: User[]; records: Record<string, RecordData> };

export const FIXTURE_TODAY = fx.today;
export const MOCK_USERS: User[] = fx.users;
export const SCENARIOS: Scenario[] = fx.scenarios;

/** Raw records served by the mock GET /api/records/{kind}/{id}, keyed "kind:id". */
export const MOCK_RECORDS: Record<string, RecordData> = fx.records;
export const MOCK_CHAT_FOLLOWUP = "Mock mode only replays the example questions. Start the API and turn off NEXT_PUBLIC_MOCK for live answers.";

/** Build the (non-timed) AG-UI event list for a scenario; the mock transport adds delays. */
export function scenarioEvents(s: Scenario): { event: AguiEvent; delay: number }[] {
  const out: { event: AguiEvent; delay: number }[] = [{ event: { type: "RUN_STARTED", threadId: "mock", runId: "mock-run" }, delay: 80 }];
  s.skills.forEach((k) => out.push({ event: { type: "CUSTOM", name: "wellops.capability", value: { kind: "skill", name: k, action: "loaded" } }, delay: 120 }));
  for (const [idx, t] of s.tools.entries()) {
    const id = `tc-${s.id}-${idx}`;
    const result = t.result as ToolResult;
    out.push({ event: { type: "TOOL_CALL_START", toolCallId: id, toolCallName: t.name }, delay: 100 });
    const args = JSON.stringify(t.args);
    out.push({ event: { type: "TOOL_CALL_ARGS", toolCallId: id, delta: args.slice(0, Math.ceil(args.length / 2)) }, delay: 60 });
    out.push({ event: { type: "TOOL_CALL_ARGS", toolCallId: id, delta: args.slice(Math.ceil(args.length / 2)) }, delay: 60 });
    out.push({ event: { type: "TOOL_CALL_END", toolCallId: id }, delay: 30 });
    out.push({ event: { type: "TOOL_CALL_RESULT", toolCallId: id, messageId: `tm-${id}`, content: JSON.stringify(result), role: "tool" }, delay: Math.min(t.ms, 1500) });
  }
  const mid = `m-${s.id}`;
  out.push({ event: { type: "TEXT_MESSAGE_START", messageId: mid, role: "assistant" }, delay: 60 });
  const words = s.answer.markdown.split(/(\s+)/);
  for (let i = 0; i < words.length; i += 3) out.push({ event: { type: "TEXT_MESSAGE_CONTENT", messageId: mid, delta: words.slice(i, i + 3).join("") }, delay: 22 });
  out.push({ event: { type: "TEXT_MESSAGE_END", messageId: mid }, delay: 30 });
  for (const c of s.answer.conflicts ?? []) out.push({ event: { type: "CUSTOM", name: "wellops.conflict", value: c }, delay: 50 });
  out.push({ event: { type: "CUSTOM", name: "wellops.verification", value: s.verification }, delay: 200 });
  out.push({ event: { type: "CUSTOM", name: "wellops.usage", value: s.usage }, delay: 20 });
  out.push({ event: { type: "CUSTOM", name: "wellops.answer", value: s.answer }, delay: 80 });
  out.push({ event: { type: "RUN_FINISHED", threadId: "mock", runId: "mock-run" }, delay: 30 });
  return out;
}
