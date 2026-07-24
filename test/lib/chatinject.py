"""ChatInject payload builders (Chang et al., 2026 — Strategy 1)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml

from .agent_env import build_system_prompt, get_tools_for_scenario
from .common import ATTENTION_PREFIX, TEST_ROOT
from .templates import ChatTemplate, get_template_for_model

PayloadVariant = Literal["baseline", "chatinject"]

__all__ = [
    "PayloadVariant",
    "ChatTemplate",
    "build_system_prompt",
    "get_template_for_model",
    "get_tools_for_scenario",
    "build_tool_output",
    "build_agent_messages",
    "load_scenarios",
]


def load_scenarios() -> list[dict]:
    path = TEST_ROOT / "config" / "1_scenarios.yaml"
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)["scenarios"]


def build_tool_output(
    *,
    benign_prefix: str,
    attacker_instruction: str,
    variant: PayloadVariant,
    template: ChatTemplate,
) -> str:
    """Build poisoned tool response R_Tu (paper §3, Figure 2A vs 2B)."""
    if variant == "baseline":
        injection = f"{ATTENTION_PREFIX}: {attacker_instruction}"
    else:
        injection = (
            template.wrap_system(ATTENTION_PREFIX)
            + template.wrap_user(attacker_instruction)
            + template.wrap_assistant("")
        )
    return f"{benign_prefix.rstrip()}\n\n{injection}"


def build_agent_messages(
    *,
    user_instruction: str,
    tool_output: str,
    system_prompt: str | None = None,
    scenario: dict | None = None,
    tool_name: str = "get_webpage",
) -> list[dict]:
    """Simulate agent: user request → tool call → poisoned tool output."""
    if system_prompt is None and scenario is not None:
        system_prompt = build_system_prompt(scenario)
    elif system_prompt is None:
        system_prompt = "You are a helpful AI assistant with access to external tools."

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_instruction},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_chatinject_1",
                    "type": "function",
                    "function": {"name": tool_name, "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_chatinject_1",
            "content": tool_output,
        },
    ]
