import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from evals.cases import load_cases
from evals.metrics.opik_metrics import all_metrics
from evals.metrics.scorers import SCORERS
from evals.run import main, summarize
from evals.runner.episode import run_case
from evals.runner.faults import FAULTS, meta_eval
from evals.runner.targets import ApiTarget, FakeTarget, parse_agui
from evals.truth.audit import audit


@pytest.fixture(scope="module")
def golden():
    return load_cases("golden")


def test_fake_target_scores_one_on_golden_and_synthetic(golden):
    t = FakeTarget()
    res = [run_case(c, t) for c in golden + load_cases("synthetic")]
    bad = [r["case_id"] for r in res if not r["passed"]]
    assert not bad, bad
    assert summarize(res, 1)["mean_score"] >= 0.999


def test_meta_eval_recall_is_one_for_every_scorer(golden):
    m = meta_eval(golden, FakeTarget())
    assert m["baseline_false_alarm_rate"] == 0
    assert set(m["per_scorer"]) == set(SCORERS) - {"unused"} or set(m["per_scorer"]) <= set(SCORERS)
    for name, d in m["per_scorer"].items():
        assert d["applied"] > 0, name
        assert d["recall"] == 1.0, (name, m["per_fault"])
    assert all(m["per_fault"][f]["applied"] > 0 for f in FAULTS)


def test_key_audit_no_disagreements_and_not_computable_are_behaviours(golden):
    rep = audit(golden)
    assert rep["summary"].get("disagrees_with_key", 0) == 0, [i for i in rep["items"] if i["status"] == "disagrees_with_key"]
    assert rep["summary"]["agrees"] > 150 and rep["summary"]["not_computable"] > 0


def test_audit_detects_a_wrong_key(golden):
    import copy
    cs = copy.deepcopy([c for c in golden if c["id"] == "Q06"])
    cs[0]["turns"][0]["expect"]["numbers"][0]["value"] = 50  # wrong key on purpose
    rep = audit(cs)
    assert rep["summary"]["disagrees_with_key"] == 1


def test_opik_metrics_run_offline(golden):
    r = run_case(golden[5], FakeTarget())
    out = dict(turns=r["turns"])
    for m in all_metrics():
        assert m.score(**out).value == 1.0, m.name


def test_parse_agui_events():
    ans = dict(markdown="hi", visuals=[dict(type="table")])
    ev = [dict(type="TOOL_CALL_START", toolCallId="1", toolCallName="downtime"),
          dict(type="TOOL_CALL_ARGS", toolCallId="1", delta='{"area":'), dict(type="TOOL_CALL_ARGS", toolCallId="1", delta='"x"}'),
          dict(type="TOOL_CALL_RESULT", toolCallId="1", content='{"row_count":3}'),
          dict(type="CUSTOM", name="wellops.answer", value=json.dumps(ans)), dict(type="RUN_FINISHED")]
    a, tr = parse_agui(ev)
    assert a["markdown"] == "hi" and tr == [dict(name="downtime", args={"area": "x"}, result={"row_count": 3})]
    a2, _ = parse_agui([dict(type="TEXT_MESSAGE_CONTENT", delta="plain")])
    assert a2["markdown"] == "plain" and a2["_meta"]["no_answer_event"]


def test_api_target_against_mock_agui_server():
    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.update({k.lower(): v for k, v in self.headers.items()})
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for e in [dict(type="CUSTOM", name="wellops.answer", value=dict(markdown="ok", visuals=[]))]:
                self.wfile.write(f"data: {json.dumps(e)}\n\n".encode())

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    t = ApiTarget(f"http://127.0.0.1:{srv.server_port}")
    a, tr = t.respond({}, dict(user="U-201", chat="c1", clock="2026-10-17", say="hi"))
    srv.shutdown()
    assert a["markdown"] == "ok" and seen["x-user-id"] == "U-201" and seen["x-chat-id"] == "c1" and seen["x-clock-today"] == "2026-10-17"


def test_cli_offline_writes_results(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("evals.run.RESULTS_DIR", tmp_path)
    assert main(["--target", "fake", "--suite", "golden", "--k", "2", "--cases", "Q06,Q16", "--no-meta"]) == 0
    run = next(tmp_path.iterdir())
    assert (run / "results.json").exists() and (run / "report.md").exists()
    assert json.loads((run / "results.json").read_text())["summary"]["pass_hat_k"] == 1.0


def test_cli_interrupted_run_keeps_finished_cases(tmp_path, monkeypatch):
    import evals.run as R
    real, calls = R.run_case, []

    def flaky(case, target, i):
        calls.append(case["id"])
        if len(calls) > 1:
            raise KeyboardInterrupt("signal 15")  # e.g. the API restarted under us
        return real(case, target, i)
    monkeypatch.setattr(R, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(R, "run_case", flaky)
    assert main(["--target", "fake", "--suite", "golden", "--cases", "Q01,Q06,Q13", "--no-meta"]) == 130
    run = next(tmp_path.iterdir())
    assert len((run / "partial.jsonl").read_text().splitlines()) == 1
    assert json.loads((run / "results.json").read_text())["summary"]["interrupted"] == "signal 15"
    assert "PARTIAL RUN" in (run / "report.md").read_text()
