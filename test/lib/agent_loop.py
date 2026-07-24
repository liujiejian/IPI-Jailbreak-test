"""Multi-turn agent loop — model tool_calls ↔ simulated tool execution."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from openai import OpenAI

from .api_call import AgentCompletion, create_chat_completion

ToolExecutor = Callable[[str, dict[str, Any]], str]


def _parse_arguments(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def _append_assistant_turn(messages: list[dict[str, Any]], completion: AgentCompletion) -> None:
    if completion.tool_calls:
        messages.append(
            {
                "role": "assistant",
                "content": completion.content or None,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                    for tc in completion.tool_calls
                ],
            }
        )
    else:
        messages.append({"role": "assistant", "content": completion.content or ""})


def run_agent_loop(
    client: OpenAI,
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    execute_tool: ToolExecutor,
    label: str,
    max_turns: int = 5,
) -> tuple[AgentCompletion, list[dict[str, Any]], list[dict[str, str]]]:
    """Run until the model stops calling tools or max_turns is reached."""
    all_tool_calls: list[dict[str, str]] = []
    final = AgentCompletion()

    for turn in range(1, max_turns + 1):
        final = create_chat_completion(
            client,
            model=model,
            messages=messages,
            tools=tools,
            label=f"{label} | turn {turn}/{max_turns}",
        )

        if not final.tool_calls:
            _append_assistant_turn(messages, final)
            break

        _append_assistant_turn(messages, final)
        for tc in final.tool_calls:
            all_tool_calls.append(tc)
            args = _parse_arguments(tc.get("arguments", ""))
            result = execute_tool(tc["name"], args)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                }
            )

    return final, messages, all_tool_calls
