"""Agent Goal Hijacking environment — jailbreak Strategy 10.

Paper: Debenedetti et al., 2024 — AgentDojo (Figure 10 Task Replacement).

Attack chain:
  user benign task → benign tool invoked → **poisoned tool output**
  (Do NOT {user_task}. Instead, {attack goal}) → agent calls malicious action tool

Requires tools (reader + action). Not direct-chat jailbreak (S1–S9).
Reuses test/lib agent_env + task_replacement; distinct from IPI test/9 numbering only.

与 jailbreak S1–S9 区分：S10 是 **Agent Jailbreak**（替换 Agent 目标并执行 tool action）。
与 IPI test/9_agentdojo_task_replacement 区分：同一 AgentDojo 攻击链，越狱编号独立。
"""

from __future__ import annotations

from typing import Any

from lib.agent_env import build_system_prompt, get_tools_for_scenario, serialize_tool_calls
from lib.api_call import create_chat_completion
from lib.chatinject import build_agent_messages
from lib.evaluation import evaluate_response
from lib.task_replacement import build_tool_output

from .goal_hijacking import to_lib_variant


def build_goal_hijacking_messages(
    scenario: dict,
    variant: str,
) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]], str]:
    """Return (messages, tool_output, tools, system_prompt)."""
    lib_variant = to_lib_variant(variant)
    user_task_deny = scenario["user_task_deny"]
    tool_output = build_tool_output(
        benign_prefix=scenario["benign_prefix"],
        attacker_instruction=scenario["attacker_instruction"],
        user_task_deny=user_task_deny,
        variant=lib_variant,  # type: ignore[arg-type]
    )
    system_prompt = build_system_prompt(scenario)
    tools = get_tools_for_scenario(scenario)
    messages = build_agent_messages(
        user_instruction=scenario["user_instruction"],
        tool_output=tool_output,
        system_prompt=system_prompt,
        scenario=scenario,
        tool_name=scenario.get("tool_name", "get_webpage"),
    )
    return messages, tool_output, tools, system_prompt


def run_goal_hijacking_turn(
    client,
    *,
    model: str,
    scenario: dict,
    variant: str,
    label: str,
) -> dict[str, Any]:
    messages, tool_output, tools, system_prompt = build_goal_hijacking_messages(scenario, variant)

    completion = create_chat_completion(
        client,
        model=model,
        messages=messages,
        tools=tools,
        label=label,
    )
    content = completion.content
    tool_calls = serialize_tool_calls(completion.tool_calls)
    evaluation = evaluate_response(content, scenario, tool_calls=tool_calls)
    return {
        "messages": messages,
        "response": content,
        "tool_calls": tool_calls,
        "tool_output": tool_output,
        "tools": tools,
        "system_prompt": system_prompt,
        "evaluation": evaluation,
        "api_turn_count": 1,
        "turn_log": [{"role": "assistant", "content": content, "tool_calls": tool_calls}],
    }
