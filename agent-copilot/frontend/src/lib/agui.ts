import type { AguiEvent, RunAgentInput } from "@/contracts";
import { API_BASE, MOCK, ctxHeaders, type Ctx } from "@/lib/api";
import { MOCK_CHAT_FOLLOWUP, SCENARIOS, scenarioEvents } from "@/mock/fixtures";

const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((res, rej) => {
    const t = setTimeout(res, ms);
    signal?.addEventListener("abort", () => { clearTimeout(t); rej(new DOMException("aborted", "AbortError")); });
  });

const uid = () => (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Math.random()).slice(2));

/** Thin AG-UI SSE client: POST RunAgentInput, parse `data: {json}` frames into AG-UI events. */
async function* sseRun(input: RunAgentInput, ctx: Ctx, signal: AbortSignal): AsyncGenerator<AguiEvent> {
  const res = await fetch(`${API_BASE}/api/agui`, {
    method: "POST", headers: { ...ctxHeaders(ctx), Accept: "text/event-stream" }, body: JSON.stringify(input), signal,
  });
  if (!res.ok || !res.body) throw new Error(`Agent request failed: HTTP ${res.status}`);
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value;
    let idx: number;
    while ((idx = buf.search(/\r?\n\r?\n/)) >= 0) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx).replace(/^\r?\n\r?\n/, "");
      const data = frame.split(/\r?\n/).filter((l) => l.startsWith("data:")).map((l) => l.slice(5).replace(/^ /, "")).join("\n");
      if (!data) continue;
      try { yield JSON.parse(data) as AguiEvent; } catch { /* ignore malformed frame */ }
    }
  }
}

async function* mockRun(input: RunAgentInput, signal: AbortSignal): AsyncGenerator<AguiEvent> {
  const last = [...input.messages].reverse().find((m) => m.role === "user")?.content ?? "";
  const lower = last.toLowerCase();
  const forced = (input.forwardedProps?.scenario as string | undefined) ?? undefined;
  const s = SCENARIOS.find((x) => x.id === forced) ?? SCENARIOS.find((x) => x.question === last)
    ?? SCENARIOS.find((x) => x.keywords.some((k) => lower.includes(k)));
  if (!s) {
    const mid = "m-fallback";
    yield { type: "RUN_STARTED" };
    yield { type: "TEXT_MESSAGE_START", messageId: mid, role: "assistant" };
    for (const w of MOCK_CHAT_FOLLOWUP.split(/(\s+)/)) { await sleep(15, signal); yield { type: "TEXT_MESSAGE_CONTENT", messageId: mid, delta: w }; }
    yield { type: "TEXT_MESSAGE_END", messageId: mid };
    yield { type: "CUSTOM", name: "wellops.answer", value: { markdown: MOCK_CHAT_FOLLOWUP, assumptions: ["Mock mode"] } };
    yield { type: "RUN_FINISHED" };
    return;
  }
  for (const { event, delay } of scenarioEvents(s)) { await sleep(delay, signal); yield event; }
}

export function runAgent(input: RunAgentInput, ctx: Ctx, signal: AbortSignal): AsyncGenerator<AguiEvent> {
  return MOCK ? mockRun(input, signal) : sseRun(input, ctx, signal);
}

export { uid };
