"""The live agent: Pydantic AI on Gemini, in-process, over the WellOps MCP tool registry.

The tools are the same functions the MCP server exposes (one registry, one set of descriptions); here they are called
directly with the session context in a contextvar, so there is no subprocess and no network hop between agent and
tools. Emits the bridge's event protocol: tool_start / tool_end / delta / final / error.
"""

from __future__ import annotations

import inspect
import json
from functools import lru_cache
from typing import Any, AsyncIterator

from pydantic_ai import Agent, Tool
from pydantic_ai.messages import FunctionToolCallEvent, FunctionToolResultEvent, PartDeltaEvent, TextPartDelta
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

MAX_MODEL_CALLS = 12  # per attempt: a loop of tool calls ends here and the turn answers from what it has

from wellops import mcp_server
from wellops.contracts import RunDeps


def _wrap(t) -> Tool:
    fn, ck = t.fn, t.context_kwarg

    async def call(**kw: Any) -> Any:
        if (d := mcp_server.CURRENT.get()) is not None:  # the tool path, for skills.on_turn_complete
            (log := mcp_server.CALL_LOG.setdefault(d.chat_id, [])).append({"name": t.name, "args": dict(kw)})
            del log[:-60]  # bounded even if no hook consumes it
        if ck:
            kw[ck] = None
        out = fn(**kw)
        return await out if inspect.isawaitable(out) else out

    return Tool.from_schema(call, name=t.name, description=t.description or "", json_schema=t.parameters)


@lru_cache(maxsize=1)
def tools() -> list[Tool]:
    return [_wrap(t) for t in mcp_server.mcp._tool_manager.list_tools()]


@lru_cache(maxsize=4)
def agent(model: str, base_url: str, api_key: str) -> Agent:
    m = OpenAIChatModel(model, provider=OpenAIProvider(base_url=base_url, api_key=api_key))
    return Agent(m, tools=tools(), retries=2, name="wellops")


async def run_native(deps: RunDeps, req: dict) -> AsyncIterator[dict]:
    import time
    a = agent(req["model"], req["base_url"], req["api_key"])
    token = mcp_server.CURRENT.set(deps)
    settings: ModelSettings = {"temperature": 0.2, "extra_body": {"reasoning_effort": "none"}}
    deadline = req.get("deadline")  # time.monotonic() value: stop streaming here, the caller answers from tool data
    try:
        async with a.run_stream_events(req["message"], message_history=req.get("history") or None,
                                       instructions=req.get("system"), model_settings=settings,
                                       usage_limits=UsageLimits(request_limit=req.get("max_calls", MAX_MODEL_CALLS))) as stream:
            async for ev in stream:
                if deadline and time.monotonic() > deadline:
                    yield {"ev": "budget", "message": "time budget reached"}
                    return
                if isinstance(ev, FunctionToolCallEvent):
                    yield {"ev": "tool_start", "id": ev.part.tool_call_id, "name": ev.part.tool_name, "args": ev.part.args_as_dict()}
                elif isinstance(ev, FunctionToolResultEvent):
                    c = ev.part.content  # the ToolReturnPart (or a RetryPromptPart on a validation error)
                    yield {"ev": "tool_end", "id": ev.part.tool_call_id, "name": ev.part.tool_name,
                           "result": c if isinstance(c, str) else json.dumps(c, default=str)}
                elif isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, TextPartDelta) and ev.delta.content_delta:
                    yield {"ev": "delta", "text": ev.delta.content_delta}
                elif type(ev).__name__ == "AgentRunResultEvent":
                    yield {"ev": "final", "text": str(ev.result.output), "messages": ev.result.all_messages()}
    except Exception as e:  # provider/tool failure: report, never hang the stream
        if type(e).__name__ == "UsageLimitExceeded":  # looping on tools: the caller answers from what was fetched
            yield {"ev": "budget", "message": str(e)}
        else:
            yield {"ev": "error", "message": f"{type(e).__name__}: {e}"}
    finally:
        mcp_server.CURRENT.reset(token)
