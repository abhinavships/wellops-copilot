"""Each deterministic scorer wrapped as an Opik BaseMetric (track=False so they work offline).

Opik passes dataset-item fields + task output as kwargs. Our items are whole episodes:
  task output = {"turns": [{"answer": AnswerSpec dict, "trace": [...], "expect": {...}, "setup": bool}, ...]}
A metric returns the mean over scored turns where it applies (leak: min, i.e. any leak => 0).
"""
from __future__ import annotations

from opik.evaluation.metrics import base_metric
from opik.evaluation.metrics.score_result import ScoreResult

from evals.metrics.scorers import SCORERS, aggregate, score_turn


class ScorerMetric(base_metric.BaseMetric):
    scorer_name = ""

    def __init__(self, name: str | None = None):
        super().__init__(name=name or f"wellops_{self.scorer_name}", track=False)

    def score(self, turns: list[dict] | None = None, **ignored) -> ScoreResult:
        vals, reasons = [], []
        for i, t in enumerate(turns or []):
            if t.get("setup"):
                continue
            s = SCORERS[self.scorer_name](t.get("answer"), t.get("expect") or {}, t.get("trace"))
            if s.applies:
                vals.append(s.value)
                if s.value < 0.999:
                    reasons.append(f"t{i}: {s.reason}")
        if not vals:
            return ScoreResult(name=self.name, value=1.0, reason="not applicable", scoring_failed=False)
        v = min(vals) if self.scorer_name == "leak" else sum(vals) / len(vals)
        return ScoreResult(name=self.name, value=float(v), reason="; ".join(reasons) or "ok")


def _mk(n: str):
    return type(f"{n.title().replace('_', '')}Metric", (ScorerMetric,), {"scorer_name": n})


METRIC_CLASSES = {n: _mk(n) for n in SCORERS}
for _n, _c in METRIC_CLASSES.items():
    globals()[_c.__name__] = _c


class AggregateMetric(base_metric.BaseMetric):
    """0.5 facts + 0.25 evidence + 0.25 visual; leak => 0."""

    def __init__(self, name: str = "wellops_aggregate"):
        super().__init__(name=name, track=False)

    def score(self, turns: list[dict] | None = None, **ignored) -> ScoreResult:
        vals = []
        for t in turns or []:
            if t.get("setup"):
                continue
            vals.append(aggregate(score_turn(t.get("answer"), t.get("expect") or {}, t.get("trace")))["score"])
        return ScoreResult(name=self.name, value=sum(vals) / len(vals) if vals else 1.0)


class PassMetric(base_metric.BaseMetric):
    """1.0 if every scored turn passed every applicable scorer (building block for pass^k)."""

    def __init__(self, name: str = "wellops_pass"):
        super().__init__(name=name, track=False)

    def score(self, turns: list[dict] | None = None, **ignored) -> ScoreResult:
        ok = all(aggregate(score_turn(t.get("answer"), t.get("expect") or {}, t.get("trace")))["passed"]
                 for t in (turns or []) if not t.get("setup"))
        return ScoreResult(name=self.name, value=1.0 if ok else 0.0)


def all_metrics() -> list[base_metric.BaseMetric]:
    return [c() for c in METRIC_CLASSES.values()] + [AggregateMetric(), PassMetric()]
