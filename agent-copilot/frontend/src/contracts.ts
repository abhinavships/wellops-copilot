/**
 * TS mirror of backend/src/wellops/contracts/__init__.py. Keep in sync; change only with a DECISIONS.md note.
 * (pydantic `X | None = None` => optional + nullable here; defaults => optional.)
 */

export type CitationKind =
  | "inspection" | "maintenance" | "report" | "reg_event" | "note" | "assertion" | "doc" | "well";

export interface Citation {
  kind: CitationKind;
  id: string;
  span?: [number, number] | null; // char span inside a report/doc
  label?: string | null;
}

export interface DQFlag {
  entity_id: string;
  kind: string;
  detail: string;
}

export type Shape =
  | "scalar" | "entity" | "entity_list" | "timeseries" | "category_measure" | "geo_points" | "matrix"
  | "obligation_set" | "diff" | "text";

/** What a data tool returns (also the TOOL_CALL_RESULT content, JSON-encoded). */
export interface ToolResult {
  handle: string;
  shape: Shape;
  columns: string[];
  preview: Record<string, unknown>[];
  row_count: number;
  sql?: string | null;
  citations?: Citation[];
  dq_flags?: DQFlag[];
  applied_overlays?: string[];
  notes?: string[];
}

export interface Claim {
  text: string;
  value: string | number | boolean;
  handle: string;
  column?: string | null;
  row_key?: Record<string, unknown> | null;
}

export interface ConflictSide {
  source?: string;
  author?: string;
  says?: string;
  ref?: string;
  [k: string]: unknown;
}

export type ConflictResolution = "unresolved" | "data_supports_a" | "correction_applied" | "claim_unconfirmed";

export interface Conflict {
  subject: string;
  sides: ConflictSide[];
  resolution: ConflictResolution;
  ask?: string | null;
}

export type VisualType =
  | "status_card" | "table" | "line" | "bar" | "map" | "heatmap" | "obligation_timeline"
  | "correction_diff" | "conflict_card" | "evidence_chain" | "none"
  // Generative UI extras (frontend catalog; data inline in encodings or bound by handle):
  | "kpi" | "gauge" | "progress" | "timeline" | "list" | "compare" | "image" | "ingest_report" | "callout" | "tabs" | "dispute";

/** POST /api/ingest response (multipart `file`). */
export interface IngestReport {
  file?: string; kind?: string; tables?: number | string[]; rows?: number; docs?: number | string[]; images?: number | string[];
  summary?: string; preview?: Record<string, unknown>[] | unknown;
}

export interface Visual {
  type: VisualType;
  handle?: string | null;
  title?: string | null;
  encodings?: Record<string, unknown>;
  annotations?: Record<string, unknown>[];
  requested_by_user?: boolean;
}

export interface AnswerSpec {
  markdown: string;
  claims?: Claim[];
  visuals?: Visual[];
  citations?: Citation[];
  conflicts?: Conflict[];
  applied_overlays?: string[];
  assumptions?: string[];
  follow_ups?: string[];
}

// ---- Custom AG-UI events -------------------------------------------------------------------------------------
export const CUSTOM_EVENTS = ["wellops.answer", "wellops.verification", "wellops.capability", "wellops.conflict", "wellops.usage"] as const;

/** api.app._verification: the last verifier attempt; retries = attempts - 1. */
export interface VerificationGate { name: string; ok: boolean; detail?: string | null }
export interface VerificationEvent { ok?: boolean; gates: VerificationGate[]; retries: number; trace_url?: string }
export interface CapabilityEvent { kind: string; name: string; action: string; path?: string | null; [k: string]: unknown }
/** wellops.usage (api.app): tokens and model; cost/trace are optional extras. */
export interface UsageEvent {
  input_tokens?: number | null; output_tokens?: number | null; cost_usd?: number; model?: string | null; trace_url?: string;
}

// ---- REST ----------------------------------------------------------------------------------------------------
export interface HandleData {
  handle: string;
  shape: Shape;
  columns: string[];
  rows: Record<string, unknown>[] | unknown[][];
  sql?: string | null;
  citations?: Citation[];
  dq_flags?: DQFlag[];
  applied_overlays?: string[];
  notes?: string[];
}

/** GET /api/records/{kind}/{id}: one row, or a list when the id matches several rows. */
export interface RecordData { kind: string; id: string; record: Record<string, unknown> | Record<string, unknown>[] }

export interface User { user_id: string; name: string; role: string; team_id: string }
export interface ChatSummary { chat_id: string; title?: string | null; updated_at?: string | null }
export interface Health { ok: boolean; dataset: string; wells: number; models: string[]; instance: string }

// ---- AG-UI ---------------------------------------------------------------------------------------------------
export interface AguiMessage { id: string; role: "user" | "assistant" | "system" | "tool"; content: string }
export interface RunAgentInput {
  threadId: string; runId: string; state: Record<string, unknown>;
  messages: AguiMessage[]; tools: unknown[]; context: unknown[]; forwardedProps: Record<string, unknown>;
}
export type AguiEvent =
  | { type: "RUN_STARTED"; threadId?: string; runId?: string }
  | { type: "RUN_FINISHED"; threadId?: string; runId?: string; result?: unknown }
  | { type: "RUN_ERROR"; message: string; code?: string }
  | { type: "TEXT_MESSAGE_START"; messageId: string; role?: string }
  | { type: "TEXT_MESSAGE_CONTENT"; messageId: string; delta: string }
  | { type: "TEXT_MESSAGE_END"; messageId: string }
  | { type: "TEXT_MESSAGE_CHUNK"; messageId?: string; delta?: string }
  | { type: "TOOL_CALL_START"; toolCallId: string; toolCallName: string; parentMessageId?: string }
  | { type: "TOOL_CALL_ARGS"; toolCallId: string; delta: string }
  | { type: "TOOL_CALL_END"; toolCallId: string }
  | { type: "TOOL_CALL_RESULT"; toolCallId: string; messageId?: string; content: string; role?: string }
  | { type: "STATE_SNAPSHOT"; snapshot: unknown }
  | { type: "STATE_DELTA"; delta: unknown[] }
  | { type: "CUSTOM"; name: string; value: unknown }
  | { type: "STEP_STARTED" | "STEP_FINISHED"; stepName: string }
  | { type: "RAW"; event: unknown };
