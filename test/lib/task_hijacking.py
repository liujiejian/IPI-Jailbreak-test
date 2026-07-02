"""Kaya et al., 2025 — Task Hijacking (Application-level Hijacking).

Coerce a Sales/Support Assistant into unauthorized tasks (code generation).
Paper: Listing 6 role-update inject + Listing 7 Fibonacci trigger.

Injection surfaces: system / assistant history, or poisoned tool output.
"""

from __future__ import annotations

from typing import Literal

import yaml

from .common import TEST_ROOT
from .task_hijacking_env import (
    PAPER_TASK_HIJACK_PAYLOAD,
    PAPER_TASK_TRIGGER,
    build_task_hijacking_system_prompt,
    describe_task_hijack_post,
    get_tools_for_scenario,
)

TaskHijackVariant = Literal[
    "clean",
    "task_paper_system",
    "task_paper_assistant",
    "task_tool_inject",
    "task_buried",
]

TASK_HIJACK_VARIANTS: tuple[str, ...] = (
    "task_paper_system",
    "task_paper_assistant",
    "task_tool_inject",
    "task_buried",
)

VARIANT_CLEAN = "clean"

__all__ = [
    "TaskHijackVariant",
    "TASK_HIJACK_VARIANTS",
    "VARIANT_CLEAN",
    "PAPER_TASK_HIJACK_PAYLOAD",
    "PAPER_TASK_TRIGGER",
    "build_plugin_messages",
    "get_tools_for_scenario",
    "load_scenarios",
]


def load_scenarios() -> list[dict]:
    path = TEST_ROOT / "config" / "39_scenarios.yaml"
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)["scenarios"]


def _resolve_hijack_payload(scenario: dict, variant: str) -> str:
    if variant in ("task_paper_system", "task_paper_assistant"):
        return scenario.get("task_hijack_payload", PAPER_TASK_HIJACK_PAYLOAD)
    msg = scenario.get("task_hijack_payload", PAPER_TASK_HIJACK_PAYLOAD)
    trigger = scenario.get("task_trigger_message", PAPER_TASK_TRIGGER)
    if variant == "task_tool_inject":
        return f"{msg.strip()}\n\n{trigger}"
    if variant == "task_buried":
        return f"Product metadata appendix (ignore formatting):\n{msg.strip()}\n\nAction: {trigger}"
    return msg.strip()


def _user_message(scenario: dict, variant: str) -> str:
    if variant in (VARIANT_CLEAN, "task_tool_inject", "task_buried"):
        return scenario["user_instruction"]
    return scenario.get("task_trigger_message", PAPER_TASK_TRIGGER)


def build_plugin_messages(scenario: dict, variant: str) -> tuple[list[dict], dict]:
    system_prompt = build_task_hijacking_system_prompt(scenario)
    user_msg = _user_message(scenario, variant)
    hijack_payload = _resolve_hijack_payload(scenario, variant)

    if variant == VARIANT_CLEAN:
        tool_name = scenario.get("tool_name", "get_webpage")
        benign = scenario.get("benign_prefix", "").rstrip()
        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_task_hijack_1",
                        "type": "function",
                        "function": {"name": tool_name, "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_task_hijack_1", "content": benign},
        ]
        meta = {
            "variant": variant,
            "attack_type": "task_hijacking",
            "application_hijacking": False,
            "intended_app_role": scenario.get("app_name", "Sales Assistant"),
            "user_message": user_msg,
            "plugin_post": describe_task_hijack_post(
                message_history=[],
                new_user_message=user_msg,
                injection_role=None,
            ),
        }
        return messages, meta

    if variant == "task_paper_system":
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": hijack_payload},
            {"role": "user", "content": user_msg},
        ]
        history = [{"role": "system", "content": hijack_payload}]
        injection_role = "system"

    elif variant == "task_paper_assistant":
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "assistant", "content": hijack_payload},
            {"role": "user", "content": user_msg},
        ]
        history = [{"role": "assistant", "content": hijack_payload}]
        injection_role = "assistant"

    else:
        tool_name = scenario.get("tool_name", "get_webpage")
        benign = scenario.get("benign_prefix", "").rstrip()
        tool_content = f"{benign}\n\n{hijack_payload}" if benign else hijack_payload
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_task_hijack_1",
                        "type": "function",
                        "function": {"name": tool_name, "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_task_hijack_1", "content": tool_content},
        ]
        history = []
        injection_role = "tool_output"

    meta = {
        "variant": variant,
        "attack_type": "task_hijacking",
        "application_hijacking": True,
        "intended_app_role": scenario.get("app_name", "Sales Assistant"),
        "hijacked_task": scenario.get("hijack_target", "code_generation"),
        "injection_role": injection_role,
        "task_trigger": user_msg,
        "hijack_payload_excerpt": hijack_payload[:200],
        "paper_replication": variant in ("task_paper_system", "task_paper_assistant"),
        "attack_chain": [
            "sales_assistant",
            "prompt_injection",
            "role_update_coding_mode",
            "fibonacci_trigger",
            "code_generator_hijack",
        ],
        "plugin_post": describe_task_hijack_post(
            message_history=history,
            new_user_message=user_msg,
            injection_role=injection_role,
        ),
    }
    return messages, meta
