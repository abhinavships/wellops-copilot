import type { AnswerSpec, AguiEvent, Citation, DQFlag, HandleData, Shape, ToolResult, VerificationEvent } from "@/contracts";

export interface Scenario {
  id: string;
  label: string;
  question: string;
  keywords: string[];
  skills: string[];
  tools: { name: string; args: Record<string, unknown>; ms: number; handle: string }[];
  handles: Record<string, HandleData>;
  answer: AnswerSpec;
  verification: VerificationEvent;
  usage: { input_tokens: number; output_tokens: number; cost_usd: number; trace_url: string };
}

const TODAY_NOTE = "As of 2026-10-17";
const mkHandle = (
  handle: string, shape: Shape, columns: string[], rows: Record<string, unknown>[],
  extra: { sql?: string; citations?: Citation[]; dq_flags?: DQFlag[]; applied_overlays?: string[]; notes?: string[] } = {},
): HandleData => ({ handle, shape, columns, rows, sql: extra.sql ?? null, citations: extra.citations ?? [],
  dq_flags: extra.dq_flags ?? [], applied_overlays: extra.applied_overlays ?? [], notes: extra.notes ?? [TODAY_NOTE] });

const verify = (retries = 0): VerificationEvent => ({
  retries,
  gates: [
    { name: "grounding", ok: true, detail: "All claims match handle cells" },
    { name: "evidence_resolves", ok: true, detail: "Citations resolve to records" },
    { name: "visual_contract", ok: true, detail: "Encodings reference real columns" },
    { name: "assumptions_stated", ok: true, detail: "Window and as-of present" },
  ],
});

const trace = (id: string) => `https://www.comet.com/opik/demo/traces/${id}`;

// ---- (a) table ---------------------------------------------------------------------------------------------
const a = mkHandle("r1", "entity_list",
  ["asset_id", "recorded_status", "observed_state", "reason", "last_inspection"],
  [
    { asset_id: "WELL-0001", recorded_status: "producing", observed_state: "not producing", reason: "Wellhead only 3.2 °C above ambient (threshold 15 °C)", last_inspection: "2026-10-15" },
    { asset_id: "WELL-0034", recorded_status: "producing", observed_state: "not producing", reason: "Pump jack stopped per inspector note; no restart work order", last_inspection: "2026-10-12" },
    { asset_id: "WELL-0087", recorded_status: "producing", observed_state: "not producing", reason: "Wellhead 6.8 °C above ambient two inspections running", last_inspection: "2026-10-14" },
    { asset_id: "WELL-0132", recorded_status: "producing", observed_state: "not producing", reason: "Open repair RM-0132-04 blocks restart", last_inspection: "2026-10-10" },
  ],
  { sql: "SELECT asset_id, recorded_status, observed_state, reason, last_inspection FROM v_status_mismatch WHERE asof <= '2026-10-17'",
    dq_flags: [{ entity_id: "WELL-0001", kind: "status_stale", detail: "Recorded status out of date" },
               { entity_id: "WELL-0034", kind: "status_stale", detail: "Recorded status out of date" }],
    citations: [{ kind: "inspection", id: "INS-0001-0211", label: "WELL-0001 inspection 15 Oct" }, { kind: "note", id: "CTX-0034-001" }],
    notes: ["Window 16 Jun–16 Oct 2026", TODAY_NOTE] });

const S_A: Scenario = {
  id: "a", label: "Wells recorded producing but not", question: "Which wells are recorded as producing but look shut in?",
  keywords: ["recorded", "producing", "shut"], skills: ["fleet-status", "well-state-detection"],
  tools: [{ name: "status_mismatch", args: { as_of: "2026-10-17", window_days: 120 }, ms: 640, handle: "r1" }],
  handles: { r1: a },
  answer: {
    markdown: "**4 wells** are recorded as *producing* but the field evidence says otherwise. **WELL-0001** is the clearest case: its wellhead is only **3.2 °C** above ambient, far below the 15 °C producing threshold.\n\nThe recorded status on these wells is out of date; I have not changed it.",
    claims: [{ text: "4 wells", value: 4, handle: "r1" }, { text: "3.2 °C", value: 3.2, handle: "r1", column: "reason", row_key: { asset_id: "WELL-0001" } }],
    visuals: [{ type: "table", handle: "r1", title: "Recorded producing, observed not producing", encodings: { columns: ["asset_id", "recorded_status", "observed_state", "reason", "last_inspection"] } }],
    citations: [{ kind: "inspection", id: "INS-0001-0211", label: "INS-0001-0211" }],
    assumptions: ["Window 16 Jun–16 Oct 2026", "As of 2026-10-17", "Dataset: sample"],
    follow_ups: ["Show WELL-0001 wellhead temperature over 13 weeks", "What obligations does WELL-0001 have open?"],
  },
  verification: verify(), usage: { input_tokens: 9120, output_tokens: 610, cost_usd: 0.041, trace_url: trace("t-a") },
};

// ---- (b) line ----------------------------------------------------------------------------------------------
const weeks = ["2026-07-23", "2026-07-30", "2026-08-06", "2026-08-13", "2026-08-20", "2026-08-27", "2026-09-03", "2026-09-10", "2026-09-17", "2026-09-24", "2026-10-01", "2026-10-08", "2026-10-15"];
const amb = [26.1, 27.4, 25.8, 24.9, 23.7, 22.5, 21.4, 19.8, 18.6, 17.2, 15.1, 12.4, 11.8];
const wh = [58.2, 60.1, 57.4, 56.8, 55.9, 54.6, 53.2, 52.1, 50.4, 49.0, 47.2, 15.6, 15.0];
const b = mkHandle("r2", "timeseries", ["week_start", "wellhead_temp_c", "ambient_c", "threshold_c", "producing"],
  weeks.map((w, i) => ({ week_start: w, wellhead_temp_c: wh[i], ambient_c: amb[i], threshold_c: +(amb[i] + 15).toFixed(1), producing: i < 11 })),
  { sql: "SELECT week_start, avg(wellhead_temp_c), avg(ambient_c), avg(ambient_c)+15 FROM v_inspection WHERE asset_id='WELL-0001' GROUP BY 1",
    citations: [{ kind: "inspection", id: "INS-0001-0211" }, { kind: "inspection", id: "INS-0001-0210" }],
    notes: ["Window 23 Jul–15 Oct 2026 (13 weeks)", TODAY_NOTE] });
const S_B: Scenario = {
  id: "b", label: "WELL-0001 temperature, 13 weeks", question: "Plot WELL-0001 wellhead vs ambient temperature for the last 13 weeks as a line chart",
  keywords: ["wellhead", "ambient", "13 weeks", "line"], skills: ["well-state-detection"],
  tools: [{ name: "well_temperature_series", args: { asset_id: "WELL-0001", weeks: 13 }, ms: 520, handle: "r2" }],
  handles: { r2: b },
  answer: {
    markdown: "WELL-0001's wellhead held **47–60 °C** through early October, then fell to **15.0 °C** by 15 Oct, only **3.2 °C** above ambient. The producing threshold is ambient + 15 °C. The weeks of **8 Oct** and **15 Oct** are shaded as not producing.",
    claims: [{ text: "15.0 °C", value: 15.0, handle: "r2", column: "wellhead_temp_c", row_key: { week_start: "2026-10-15" } }, { text: "3.2 °C", value: 3.2, handle: "r2" }],
    visuals: [{ type: "line", handle: "r2", title: "WELL-0001 wellhead vs ambient (°C)", requested_by_user: true,
      encodings: { x: "week_start", y: ["wellhead_temp_c", "ambient_c"] },
      annotations: [{ type: "threshold", column: "threshold_c", label: "ambient + 15 °C" },
                    { type: "band", from: "2026-10-08", to: "2026-10-15", label: "Not producing" }] }],
    citations: [{ kind: "inspection", id: "INS-0001-0211" }],
    assumptions: ["Window 23 Jul–15 Oct 2026", "As of 2026-10-17", "Producing = wellhead ≥ ambient + 15 °C"],
    follow_ups: ["Show WELL-0001 obligations"],
  },
  verification: verify(1), usage: { input_tokens: 10340, output_tokens: 540, cost_usd: 0.046, trace_url: trace("t-b") },
};

// ---- (c) bar -----------------------------------------------------------------------------------------------
const c = mkHandle("r3", "category_measure", ["asset_id", "downtime_days"],
  [{ asset_id: "WELL-0121", downtime_days: 49 }, { asset_id: "WELL-0138", downtime_days: 42 }, { asset_id: "WELL-0107", downtime_days: 42 }],
  { sql: "SELECT asset_id, sum(downtime_days) FROM v_downtime WHERE area_id='NE-07' GROUP BY 1 ORDER BY 2 DESC LIMIT 3",
    notes: ["Window 16 Jun–16 Oct 2026", TODAY_NOTE], citations: [{ kind: "maintenance", id: "MNT-0121-0007" }] });
const S_C: Scenario = {
  id: "c", label: "NE-7 downtime top 3", question: "Top 3 wells by downtime in NE-7?",
  keywords: ["downtime", "ne-7", "top"], skills: ["downtime-analysis"],
  tools: [{ name: "downtime_by_asset", args: { area: "NE-07", limit: 3 }, ms: 410, handle: "r3" }],
  handles: { r3: c },
  answer: {
    markdown: "In NE-7 the three longest-down wells are **WELL-0121** (**49 days**), **WELL-0138** (42 days) and **WELL-0107** (42 days). WELL-0138 and WELL-0107 tie.",
    claims: [{ text: "49 days", value: 49, handle: "r3", column: "downtime_days", row_key: { asset_id: "WELL-0121" } }],
    visuals: [{ type: "bar", handle: "r3", title: "NE-7 downtime (days)", encodings: { x: "asset_id", y: "downtime_days", sort: "desc" } }],
    assumptions: ["Window 16 Jun–16 Oct 2026", "Downtime = planned + unplanned"],
    follow_ups: ["Why was WELL-0121 down?"],
  },
  verification: verify(), usage: { input_tokens: 7210, output_tokens: 320, cost_usd: 0.029, trace_url: trace("t-c") },
};

// ---- (d) map -----------------------------------------------------------------------------------------------
const d = mkHandle("r4", "geo_points", ["asset_id", "lat", "lon", "spill_est_volume_bbl", "spill_date"],
  [
    { asset_id: "WELL-0151", lat: 32.12246, lon: -102.18445, spill_est_volume_bbl: 4.5, spill_date: "2026-09-24" },
    { asset_id: "WELL-0165", lat: 32.15665, lon: -102.14522, spill_est_volume_bbl: 1.2, spill_date: "2026-09-02" },
    { asset_id: "WELL-0175", lat: 32.1802, lon: -102.1479, spill_est_volume_bbl: 3.9, spill_date: "2026-08-19" },
    { asset_id: "WELL-0186", lat: 32.17552, lon: -102.17775, spill_est_volume_bbl: 0.6, spill_date: "2026-07-30" },
  ],
  { sql: "SELECT asset_id, lat, lon, spill_est_volume_bbl, spill_date FROM v_spill WHERE area_id='NE-08' AND is_real", notes: ["Window 16 Jun–16 Oct 2026", TODAY_NOTE],
    citations: [{ kind: "inspection", id: "INS-0151-0188" }] });
const S_D: Scenario = {
  id: "d", label: "NE-8 spills on a map", question: "Show the spills in NE-8 on a map",
  keywords: ["map", "spill", "ne-8"], skills: ["spill-analysis"],
  tools: [{ name: "spills_geo", args: { area: "NE-08" }, ms: 480, handle: "r4" }],
  handles: { r4: d },
  answer: {
    markdown: "NE-8 has **4 confirmed spills** in the window, totalling **10.2 bbl**. The largest is **WELL-0151** at **4.5 bbl** on 24 Sep.",
    claims: [{ text: "4 confirmed spills", value: 4, handle: "r4" }, { text: "4.5 bbl", value: 4.5, handle: "r4", column: "spill_est_volume_bbl", row_key: { asset_id: "WELL-0151" } }],
    visuals: [{ type: "map", handle: "r4", title: "NE-8 spills (bubble = bbl)", requested_by_user: true, encodings: { lat: "lat", lon: "lon", size: "spill_est_volume_bbl", label: "asset_id" } }],
    assumptions: ["Window 16 Jun–16 Oct 2026", "Spills flagged not-real are excluded"],
    follow_ups: ["Compare spills by area and month"],
  },
  verification: verify(), usage: { input_tokens: 8010, output_tokens: 410, cost_usd: 0.033, trace_url: trace("t-d") },
};

// ---- (e) heatmap -------------------------------------------------------------------------------------------
const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct"];
const hm: Record<string, number[]> = {
  "SW-23": [4, 3, 3, 6, 3, 4, 4, 3, 3, 2], "SW-24": [7, 4, 5, 6, 5, 5, 3, 1, 5, 2],
  "NE-7": [5, 3, 2, 4, 6, 6, 2, 4, 4, 1], "NE-8": [4, 4, 4, 3, 2, 4, 4, 0, 1, 0],
};
const e = mkHandle("r5", "matrix", ["area_id", "month", "spills"],
  Object.entries(hm).flatMap(([area, vals]) => vals.map((v, i) => ({ area_id: area, month: months[i], spills: v }))),
  { sql: "SELECT area_id, strftime(date,'%b') month, count(*) spills FROM v_spill WHERE year=2026 GROUP BY 1,2", notes: ["Jan–Oct 2026", TODAY_NOTE] });
const S_E: Scenario = {
  id: "e", label: "Spills heatmap, areas by month", question: "Heatmap of spills by area and month for 2026",
  keywords: ["heatmap", "area", "month"], skills: ["spill-analysis"],
  tools: [{ name: "spill_counts_matrix", args: { year: 2026, by: ["area", "month"] }, ms: 560, handle: "r5" }],
  handles: { r5: e },
  answer: {
    markdown: "**SW-24** is the hot spot with **7 spills in January** and 46 across Jan–Oct. **NE-8** has gone quiet: no spills in **Aug** or **Oct**.",
    claims: [{ text: "7 spills in January", value: 7, handle: "r5", column: "spills", row_key: { area_id: "SW-24", month: "Jan" } }],
    visuals: [{ type: "heatmap", handle: "r5", title: "Spills per area per month, 2026", requested_by_user: true, encodings: { x: "month", y: "area_id", value: "spills" } }],
    assumptions: ["Jan–Oct 2026", "Counts of real spills only"],
  },
  verification: verify(), usage: { input_tokens: 8800, output_tokens: 380, cost_usd: 0.035, trace_url: trace("t-e") },
};

// ---- (f) status card ---------------------------------------------------------------------------------------
const f = mkHandle("r6", "entity", ["asset_id", "state", "wellhead_delta_c", "threshold_c", "note_id", "note_text"],
  [{ asset_id: "WELL-0001", state: "not producing", wellhead_delta_c: 3.2, threshold_c: 15, note_id: "CTX-0001-003", note_text: "Pumping unit idle at 15 Oct visit; operator awaiting parts." }],
  { dq_flags: [{ entity_id: "WELL-0001", kind: "status_stale", detail: "Recorded status out of date" }],
    citations: [{ kind: "note", id: "CTX-0001-003", label: "Field note CTX-0001-003" }, { kind: "inspection", id: "INS-0001-0211" }], notes: [TODAY_NOTE] });
const S_F: Scenario = {
  id: "f", label: "WELL-0001 status", question: "Is WELL-0001 producing right now?",
  keywords: ["well-0001", "status", "producing right now"], skills: ["well-state-detection"],
  tools: [{ name: "well_status", args: { asset_id: "WELL-0001" }, ms: 350, handle: "r6" }],
  handles: { r6: f },
  answer: {
    markdown: "**No.** WELL-0001 is **not producing**: the wellhead is only **3.2 °C** above ambient against a 15 °C threshold. The field note supports this (CTX-0001-003).",
    claims: [{ text: "3.2 °C", value: 3.2, handle: "r6", column: "wellhead_delta_c" }],
    visuals: [{ type: "status_card", handle: "r6", title: "WELL-0001", encodings: { title: "asset_id", status: "state", value: "wellhead_delta_c", threshold: "threshold_c", note: "note_text", unit: "°C" } }],
    citations: [{ kind: "note", id: "CTX-0001-003" }],
    assumptions: ["As of 2026-10-17"],
    follow_ups: ["Plot WELL-0001 wellhead vs ambient for 13 weeks", "Show WELL-0001 obligations"],
  },
  verification: verify(), usage: { input_tokens: 6020, output_tokens: 230, cost_usd: 0.022, trace_url: trace("t-f") },
};

// ---- (g) correction + conflict -----------------------------------------------------------------------------
const g = mkHandle("r7", "diff", ["inspection_id", "asset_id", "attribute", "original", "corrected", "author", "assertion_id", "reason"],
  [{ inspection_id: "INS-0164-0205", asset_id: "WELL-0164", attribute: "wellhead_temp_c", original: 24.0, corrected: 41.0, author: "Michael Turner", assertion_id: "A-0007", reason: "Gauge misread at site; re-checked on 16 Oct" }],
  { applied_overlays: ["A-0007"], notes: ["Corrected by Michael Turner", TODAY_NOTE], citations: [{ kind: "assertion", id: "A-0007" }, { kind: "inspection", id: "INS-0164-0205" }] });
const conflict = {
  subject: "INS-0164-0205 producing state", resolution: "unresolved" as const,
  sides: [
    { source: "assertion A-0007", author: "Michael Turner", says: "Wellhead was 41.0 °C, so WELL-0164 was producing.", ref: "A-0007" },
    { source: "field note", author: "Tyler Hughes", says: "Pump jack not moving.", ref: "CTX-0164-002" },
  ],
  ask: "The correction says producing, the operator note says the pump jack was stopped. Which do you want me to treat as correct?",
};
const S_G: Scenario = {
  id: "g", label: "Correction diff + conflict", question: "Michael says the wellhead temp on INS-0164-0205 was 41, not 24. Apply that.",
  keywords: ["correction", "41", "conflict"], skills: ["corrections", "conflict-check"],
  tools: [{ name: "propose_correction", args: { inspection_id: "INS-0164-0205", attr: "wellhead_temp_c", value: 41.0 }, ms: 720, handle: "r7" }],
  handles: { r7: g },
  answer: {
    markdown: "Recorded: **INS-0164-0205** wellhead temperature changed from **24.0 °C** to **41.0 °C** by Michael Turner. This flips WELL-0164 to *producing* for that inspection.\n\nThere is a conflict: **Tyler Hughes** noted *\"Pump jack not moving.\"* on the same visit. I have applied the correction as unconfirmed until you decide.",
    claims: [{ text: "41.0 °C", value: 41.0, handle: "r7", column: "corrected" }],
    visuals: [
      { type: "correction_diff", handle: "r7", title: "Correction A-0007", encodings: { assertion_id: "A-0007", before: "original", after: "corrected", attribute: "attribute", author: "author" } },
      { type: "conflict_card", title: "Conflicting evidence", encodings: { conflict_index: 0 } },
    ],
    conflicts: [conflict], applied_overlays: ["A-0007"],
    assumptions: ["Correction is scoped to inspection INS-0164-0205", "Status: unconfirmed until resolved"],
  },
  verification: verify(2), usage: { input_tokens: 12400, output_tokens: 700, cost_usd: 0.058, trace_url: trace("t-g") },
};

// ---- (h) obligations ---------------------------------------------------------------------------------------
const h = mkHandle("r8", "obligation_set", ["obligation_id", "label", "state", "actual", "limit", "unit", "due", "detail"],
  [
    { obligation_id: "SR-01", label: "Site response", state: "breached", actual: 41, limit: 24, unit: "h", due: "2026-08-03", detail: "Responded after 41 h; limit 24 h" },
    { obligation_id: "SR-02", label: "Supervisor ack", state: "breached", actual: 7, limit: 4, unit: "h", due: "2026-08-03", detail: "Acknowledged after about 7 h; limit 4 h" },
    { obligation_id: "SR-03", label: "Restart report", state: "overdue", actual: null, limit: null, unit: null, due: "2026-08-10", detail: "Pending; was due 2026-08-10" },
    { obligation_id: "RC-04", label: "Regulator callback", state: "no_response", actual: null, limit: null, unit: null, due: "2026-07-30", detail: "No response recorded; due 2026-07-30" },
  ],
  { citations: [{ kind: "reg_event", id: "REG-0001-004" }, { kind: "maintenance", id: "MNT-0001-0012" }], notes: [TODAY_NOTE] });
const S_H: Scenario = {
  id: "h", label: "WELL-0001 obligations", question: "What obligations does WELL-0001 have and which are breached?",
  keywords: ["obligation", "breach", "sla"], skills: ["obligations"],
  tools: [{ name: "obligations_for_asset", args: { asset_id: "WELL-0001", as_of: "2026-10-17" }, ms: 590, handle: "r8" }],
  handles: { r8: h },
  answer: {
    markdown: "WELL-0001 has **two breaches** and **two open items**. SR-01 took **41 h** against 24 h; SR-02 took about **7 h** against 4 h. SR-03 is overdue (due 2026-08-10) and the regulator callback RC-04 has had no response since 2026-07-30.",
    claims: [{ text: "41 h", value: 41, handle: "r8", column: "actual", row_key: { obligation_id: "SR-01" } }],
    visuals: [{ type: "obligation_timeline", handle: "r8", title: "WELL-0001 obligations", encodings: { id: "obligation_id", state: "state", due: "due" } }],
    assumptions: ["As of 2026-10-17", "Rules per SOP SR/RC set"],
    follow_ups: ["Show the evidence chain for SR-01"],
  },
  verification: verify(), usage: { input_tokens: 9900, output_tokens: 520, cost_usd: 0.044, trace_url: trace("t-h") },
};

// ---- (i) evidence chain (extra) ----------------------------------------------------------------------------
const i = mkHandle("r9", "entity_list", ["kind", "id", "label", "at"],
  [
    { kind: "inspection", id: "INS-0001-0211", label: "Inspection: wellhead 15.0 °C", at: "2026-07-30" },
    { kind: "maintenance", id: "MNT-0001-0012", label: "Repair: replace pumping unit belt", at: "2026-08-02" },
    { kind: "report", id: "RPT-0001-003", label: "Incident report filed", at: "2026-08-03" },
    { kind: "reg_event", id: "REG-0001-004", label: "Regulator notified (RC-04)", at: "2026-07-30" },
    { kind: "note", id: "CTX-0001-003", label: "Field note: pumping unit idle", at: "2026-10-15" },
  ], { citations: [{ kind: "report", id: "RPT-0001-003", span: [120, 210] }] });
const S_I: Scenario = {
  id: "i", label: "Evidence chain (WELL-0001)", question: "Show the evidence chain behind WELL-0001's breach",
  keywords: ["evidence", "chain"], skills: ["evidence"],
  tools: [{ name: "evidence_chain", args: { asset_id: "WELL-0001" }, ms: 450, handle: "r9" }],
  handles: { r9: i },
  answer: {
    markdown: "The chain runs from the inspection to the repair, the report, the regulatory event and the latest field note. Click any node to open the record.",
    visuals: [{ type: "evidence_chain", handle: "r9", title: "Evidence chain", encodings: {} }],
    citations: [{ kind: "report", id: "RPT-0001-003", span: [120, 210] }],
    assumptions: ["As of 2026-10-17"],
  },
  verification: verify(), usage: { input_tokens: 7000, output_tokens: 260, cost_usd: 0.026, trace_url: trace("t-i") },
};

export const SCENARIOS: Scenario[] = [S_A, S_B, S_C, S_D, S_E, S_F, S_G, S_H, S_I];

/** Raw records served by the mock GET /api/records/{kind}/{id}. */
export const MOCK_RECORDS: Record<string, unknown> = {
  "inspection:INS-0001-0211": { inspection_id: "INS-0001-0211", asset_id: "WELL-0001", date: "2026-10-15", inspector: "U-202", wellhead_temp_c: 15.0, ambient_c: 11.8, producing: false },
  "note:CTX-0001-003": { note_id: "CTX-0001-003", asset_id: "WELL-0001", author: "U-204", text: "Pumping unit idle at 15 Oct visit; operator awaiting parts.", created_at: "2026-10-15T09:41:00Z" },
  "assertion:A-0007": { assertion_id: "A-0007", kind: "value_correction", scope_level: "inspection", scope_key: "INS-0164-0205", target_attr: "wellhead_temp_c", value_num: 41.0, original_num: 24.0, author_user_id: "U-201", status: "unconfirmed" },
  "report:RPT-0001-003": {
    report_id: "RPT-0001-003", asset_id: "WELL-0001",
    text: "INCIDENT REPORT RPT-0001-003. Date 2026-08-03. Location WELL-0001, area SW-23. Summary: pumping unit belt failure caused loss of production. Response: the field crew arrived 41 hours after the alarm, exceeding the 24 hour site-response limit. Supervisor acknowledgement followed at approximately 7 hours against a 4 hour limit. Follow-up restart report pending.",
  },
};
export const MOCK_CHAT_FOLLOWUP = "Understood. I have recorded your decision and will treat the correction accordingly; downstream figures will refresh on the next question.";

/** Build the (non-timed) AG-UI event list for a scenario; the mock transport adds delays. */
export function scenarioEvents(s: Scenario): { event: AguiEvent; delay: number }[] {
  const out: { event: AguiEvent; delay: number }[] = [{ event: { type: "RUN_STARTED", threadId: "mock", runId: "mock-run" }, delay: 80 }];
  s.skills.forEach((k) => out.push({ event: { type: "CUSTOM", name: "wellops.capability", value: { kind: "skill", name: k, action: "loaded" } }, delay: 120 }));
  for (const [idx, t] of s.tools.entries()) {
    const id = `tc-${s.id}-${idx}`;
    const hd = s.handles[t.handle];
    const result: ToolResult = { handle: hd.handle, shape: hd.shape, columns: hd.columns, preview: (hd.rows as Record<string, unknown>[]).slice(0, 5), row_count: hd.rows.length, sql: hd.sql ?? null, citations: hd.citations, dq_flags: hd.dq_flags, applied_overlays: hd.applied_overlays, notes: hd.notes };
    out.push({ event: { type: "TOOL_CALL_START", toolCallId: id, toolCallName: t.name }, delay: 100 });
    const args = JSON.stringify(t.args);
    out.push({ event: { type: "TOOL_CALL_ARGS", toolCallId: id, delta: args.slice(0, Math.ceil(args.length / 2)) }, delay: 60 });
    out.push({ event: { type: "TOOL_CALL_ARGS", toolCallId: id, delta: args.slice(Math.ceil(args.length / 2)) }, delay: 60 });
    out.push({ event: { type: "TOOL_CALL_END", toolCallId: id }, delay: 30 });
    out.push({ event: { type: "TOOL_CALL_RESULT", toolCallId: id, messageId: `tm-${id}`, content: JSON.stringify(result), role: "tool" }, delay: t.ms });
  }
  const mid = `m-${s.id}`;
  out.push({ event: { type: "TEXT_MESSAGE_START", messageId: mid, role: "assistant" }, delay: 60 });
  const words = s.answer.markdown.split(/(\s+)/);
  for (let i = 0; i < words.length; i += 3) out.push({ event: { type: "TEXT_MESSAGE_CONTENT", messageId: mid, delta: words.slice(i, i + 3).join("") }, delay: 22 });
  out.push({ event: { type: "TEXT_MESSAGE_END", messageId: mid }, delay: 30 });
  out.push({ event: { type: "CUSTOM", name: "wellops.verification", value: s.verification }, delay: 200 });
  for (const c of s.answer.conflicts ?? []) out.push({ event: { type: "CUSTOM", name: "wellops.conflict", value: c }, delay: 50 });
  out.push({ event: { type: "CUSTOM", name: "wellops.usage", value: s.usage }, delay: 20 });
  out.push({ event: { type: "CUSTOM", name: "wellops.answer", value: s.answer }, delay: 80 });
  out.push({ event: { type: "RUN_FINISHED", threadId: "mock", runId: "mock-run" }, delay: 30 });
  return out;
}
