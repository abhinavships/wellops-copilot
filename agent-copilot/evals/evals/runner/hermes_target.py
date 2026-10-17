"""Hermes Agent target: one turn = one `hermes chat -q ... --format stream-json` run with WellOps tools over MCP.

Hermes runs in an isolated HERMES_HOME (a temp dir with its own config.yaml), so the user's ~/.hermes config,
memories, skills and logins never leak into an eval. Its JSONL protocol is one object per line:
  {"type":"system","subtype":"init",...} {"type":"text","text":...} {"type":"tool_use","name","tool_call_id","input"}
  {"type":"tool_result","name","tool_call_id","output","is_error"} {"type":"result","text","exit_code","tokens",...}
MCP tools are exposed as mcp__<server>__<tool>; the prefix is stripped so traces match ApiTarget's tool names.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from evals.runner.targets import OUTPUT_TOOLS, Turn

ROOT = Path(__file__).resolve().parents[3]  # agent-copilot/
SERVER = "wellops"
PREFIX = f"mcp__{SERVER}__"
ANSWER_TOOLS = OUTPUT_TOOLS | {"answer", "submit_answer", "final_answer"}  # an MCP answer tool carries the AnswerSpec
DEFAULT_MCP_URL = "http://localhost:8000/mcp/"  # wellops.mcp_server mounted on the API (streamable HTTP)
FENCED = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def _dotenv(path: Path) -> dict[str, str]:
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() and not k.lstrip().startswith("#"):
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


class HermesTarget:
    name = "hermes"

    def __init__(self, model: str | None = None, provider: str = "openrouter", mcp_url: str | None = None,
                 timeout: float = 600.0, max_turns: int = 20, hermes_bin: str = "hermes"):
        self.model = model or os.environ.get("HERMES_EVAL_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
        self.provider = provider
        self.mcp_url = mcp_url or os.environ.get("WELLOPS_MCP_URL", DEFAULT_MCP_URL)
        self.timeout, self.max_turns, self.bin = timeout, max_turns, hermes_bin
        self.home = Path(tempfile.mkdtemp(prefix="hermes-eval-"))
        self.env_file = _dotenv(ROOT / ".env")

    def _config(self, turn: Turn) -> str:
        cfg = {
            "model": {"default": self.model, "provider": self.provider},
            "agent": {"max_turns": self.max_turns},
            "mcp_servers": {SERVER: {
                "url": self.mcp_url, "timeout": 120, "connect_timeout": 60,
                # the MCP server builds RunDeps (principal, chat, today) from the same headers ApiTarget sends
                "headers": {"X-User-Id": turn["user"], "X-Chat-Id": turn["chat"], "X-Clock-Today": turn["clock"]},
            }},
        }
        return json.dumps(cfg)  # JSON is valid YAML

    def command(self, turn: Turn) -> list[str]:
        return [self.bin, "chat", "-q", turn["say"], "--format", "stream-json", "-m", self.model,
                "--provider", self.provider, "-t", SERVER, "--max-turns", str(self.max_turns), "--yolo"]

    def respond(self, case: dict, turn: Turn) -> tuple[dict, list[dict]]:
        (self.home / "config.yaml").write_text(self._config(turn))
        env = {**os.environ, "HERMES_HOME": str(self.home)}
        for k in ("OPENROUTER_API_KEY",):
            if self.env_file.get(k) and not env.get(k):
                env[k] = self.env_file[k]
        p = subprocess.run(self.command(turn), capture_output=True, text=True, timeout=self.timeout, env=env,
                           cwd=self.home, stdin=subprocess.DEVNULL)
        events = []
        for line in p.stdout.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass
        answer, trace = parse_hermes(events)
        meta = answer["_meta"]
        meta["returncode"] = p.returncode
        if p.returncode and not events:
            raise RuntimeError(f"hermes exited {p.returncode}: {p.stderr.strip()[-800:]}")
        if meta.get("run_error") and not answer.get("markdown"):
            raise RuntimeError(f"RUN_ERROR: {meta['run_error']}")
        return answer, trace


def _tool(name: str) -> str:
    return name[len(PREFIX):] if name.startswith(PREFIX) else name


def _json_or_text(s):
    if isinstance(s, str):
        try:
            s = json.loads(s)
        except ValueError:
            return s
    if isinstance(s, dict) and set(s) == {"result"} and isinstance(s["result"], str):  # hermes wraps MCP text content
        return _json_or_text(s["result"])
    return s


def parse_hermes(events: list[dict]) -> tuple[dict, list[dict]]:
    """stream-json events -> (AnswerSpec-shaped dict, trace) in the same shape parse_agui gives for ApiTarget."""
    calls, order, text, final, result = {}, [], [], None, None
    for i, e in enumerate(events):
        t = e.get("type")
        if t == "tool_use":
            cid = e.get("tool_call_id") or f"{e.get('name')}#{i}"
            calls[cid] = dict(name=_tool(e.get("name", "")), args=e.get("input") or {}, result=None)
            order.append(cid)
        elif t == "tool_result":
            cid = e.get("tool_call_id") or next((c for c in reversed(order) if calls[c]["name"] == _tool(e.get("name", ""))
                                                 and calls[c]["result"] is None), None)
            if cid in calls:
                calls[cid]["result"] = _json_or_text(e.get("output"))
                if e.get("is_error"):
                    calls[cid]["error"] = True
        elif t == "text":
            text.append(e.get("text", ""))
        elif t == "result":
            result = e
            final = e.get("text")
    trace, output_attempts, answer = [], 0, None
    for cid in order:
        c = calls[cid]
        if c["name"] in ANSWER_TOOLS:
            output_attempts += 1
            if isinstance(c["args"], dict) and "markdown" in c["args"]:
                answer = dict(c["args"])
        else:
            trace.append(c)
    md = final if final is not None else "".join(text)
    if answer is None:  # no answer tool: accept an AnswerSpec JSON block in the final text, else plain markdown
        for blob in FENCED.findall(md or ""):
            try:
                cand = json.loads(blob)
            except ValueError:
                continue
            if isinstance(cand, dict) and "markdown" in cand:
                answer = cand
        answer = answer or dict(markdown=md or "")
        answer.setdefault("_meta", {})["no_answer_event"] = True
    if not answer.get("visuals"):  # visuals are side effects of show_visual calls on the MCP server
        answer["visuals"] = [{k: v for k, v in dict(type=c["args"].get("type"), handle=c["args"].get("handle"),
                                                     title=c["args"].get("title")).items() if v}
                             for c in trace if c["name"] == "show_visual" and isinstance(c["args"], dict)]
    meta = answer.setdefault("_meta", {})
    meta["output_attempts"] = output_attempts
    if result:
        meta["tokens"] = result.get("tokens")
        meta["duration_ms"] = result.get("duration_ms")
        if result.get("error") or result.get("exit_code"):
            meta["run_error"] = result.get("error") or f"exit_code {result.get('exit_code')}"
    return answer, trace
