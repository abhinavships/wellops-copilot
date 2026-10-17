import { createElement } from "react";
import type { AnswerSpec, HandleData, Visual, VisualType } from "@/contracts";
import { StatusCard } from "./StatusCard";
import { DataTable } from "./DataTable";
import { LineChart } from "./LineChart";
import { BarChart } from "./BarChart";
import { MapView } from "./MapView";
import { Heatmap } from "./Heatmap";
import { ObligationTimeline } from "./ObligationTimeline";
import { CorrectionDiff } from "./CorrectionDiff";
import { ConflictCard } from "./ConflictCard";
import { EvidenceChain } from "./EvidenceChain";
import { DisputeCard } from "./DisputeCard";
import { Callout, Compare, Gauge, ImageView, IngestCard, Kpi, ListBoard, Progress, Tabs, Timeline } from "./Extra";

export interface VisualProps {
  visual: Visual; data: HandleData | null; rows: Record<string, unknown>[]; answer: AnswerSpec;
}

/** Component registry keyed by Visual.type (A2UI-style catalog). */
export const VISUALS: Partial<Record<VisualType, React.ComponentType<VisualProps>>> = {
  status_card: StatusCard, table: DataTable, line: LineChart, bar: BarChart, map: MapView, heatmap: Heatmap,
  obligation_timeline: ObligationTimeline, correction_diff: CorrectionDiff, conflict_card: ConflictCard, evidence_chain: EvidenceChain,
  kpi: Kpi, gauge: Gauge, progress: Progress, timeline: Timeline, list: ListBoard, compare: Compare, image: ImageView,
  ingest_report: IngestCard, callout: Callout, tabs: Tabs, dispute: DisputeCard,
};

// A correction (correction_diff) or a claim that conflicts with the data (conflict_card) produced by the feedback
// flow carries a dispute_id: render it with the proof card (fields, evidence, conflicts, undo).
for (const t of ["correction_diff", "conflict_card"] as const) {
  const Base = VISUALS[t]!;
  VISUALS[t] = function ProofAware(p: VisualProps) {
    return createElement((p.visual.encodings as { dispute_id?: string })?.dispute_id ? DisputeCard : Base, p);
  } as React.ComponentType<VisualProps>;
}
