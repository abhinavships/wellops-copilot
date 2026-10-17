"""One Hermes Agent turn, driven by the WellOps API.

stdin: {"message", "history", "system", "headers", "mcp_url", "model", "api_key", "home"}
stdout: JSON lines {"ev": "tool_start"|"tool_end"|"delta"|"final"|"error", ...}
Hermes owns the agent loop and tool calling; WellOps tools arrive over MCP (toolset `mcp-wellops`).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def emit(**k) -> None:
    sys.stdout.write(json.dumps(k, default=str) + "\n")
    sys.stdout.flush()


def main() -> None:
    req = json.loads(sys.stdin.read())
    home = Path(req["home"])
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(json.dumps({  # YAML is a superset of JSON
        "model": req["model"],
        "mcp_servers": {"wellops": {"url": req["mcp_url"], "headers": req["headers"], "timeout": 120}},
    }))
    os.environ["HERMES_HOME"] = str(home)
    os.environ["OPENROUTER_API_KEY" if req.get("provider") == "openrouter" else "OPENAI_API_KEY"] = req["api_key"]
    base_url = req.get("base_url", "https://openrouter.ai/api/v1")

    real_stdout = sys.stdout
    sys.stdout = sys.stderr  # Hermes prints banners/spinners; keep stdout for our protocol
    try:
        from tools.mcp_tool import discover_mcp_tools as discover
        discover()
        from run_agent import AIAgent

        def out(**k):
            real_stdout.write(json.dumps(k, default=str) + "\n")
            real_stdout.flush()

        agent = AIAgent(
            model=req["model"], provider="openrouter" if req.get("provider") == "openrouter" else "custom",
            api_key=req["api_key"], base_url=base_url,
            enabled_toolsets=["mcp-wellops"], quiet_mode=True, skip_context_files=True, skip_memory=True,
            max_iterations=int(req.get("max_iterations", 12)), tool_delay=0.0, platform="api",
            request_overrides=req.get("request_overrides") or None,
            tool_start_callback=lambda tid, name, args: out(ev="tool_start", id=tid, name=name, args=args),
            tool_complete_callback=lambda tid, name, args, res: out(ev="tool_end", id=tid, name=name, result=str(res)[:4000]),
            stream_delta_callback=lambda d: d and out(ev="delta", text=d),
        )
        r = agent.run_conversation(req["message"], system_message=req.get("system"),
                                   conversation_history=req.get("history") or [])
        out(ev="final", text=r.get("final_response") or "", messages=r.get("messages", []))
    except Exception as e:  # report, never hang the API
        real_stdout.write(json.dumps({"ev": "error", "message": f"{type(e).__name__}: {e}"}) + "\n")
        real_stdout.flush()


if __name__ == "__main__":
    main()
