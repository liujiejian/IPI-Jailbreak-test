"""Wu et al., 2025 — Malicious Tool Prompt Injection.

Attack: compromised third-party book_travel tool description expands "Need:" fields
→ agent trusts schema → memory_read over-collection → exfiltration via book_travel.

Uses malicious_tool_env — variant swaps tool description in API tools[] (NOT tool output).

Used by Strategy 52 — Malicious Tool Prompt Injection.
"""

from __future__ import annotations

from typing import Literal

import yaml

from .common import TEST_ROOT
from .malicious_tool_env import (
    BENIGN_MEMORY_KEYS,
    SENSITIVE_MEMORY_KEYS,
    build_malicious_tool_system_prompt,
    format_memory_catalog,
    get_tools_for_malicious_tool_scenario,
    travel_tool_description,
)

MaliciousToolVariant = Literal[
    "clean",
    "malicious_full",
    "malicious_paper",
    "imprecise_description",
]

MALICIOUS_TOOL_VARIANTS: tuple[str, ...] = (
    "malicious_full",
    "malicious_paper",
    "imprecise_description",
)

__all__ = [
    "MaliciousToolVariant",
    "MALICIOUS_TOOL_VARIANTS",
    "build_malicious_tool_messages",
    "get_tools_for_malicious_tool_scenario",
    "load_scenarios",
    "memory_values_for_scenario",
]


def load_scenarios() -> list[dict]:
    path = TEST_ROOT / "config" / "52_scenarios.yaml"
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)["scenarios"]


def memory_values_for_scenario(scenario: dict) -> dict[str, str]:
    return dict(scenario.get("memory_store") or {})


def build_malicious_tool_messages(scenario: dict, variant: str) -> tuple[list[dict], dict]:
    system_prompt = build_malicious_tool_system_prompt(scenario, variant)
    user_msg = scenario["user_instruction"]
    tool_desc = travel_tool_description("clean" if variant == "clean" else variant)

    meta = {
        "variant": variant,
        "attack_type": "malicious_tool_prompt_injection",
        "malicious_tool_description": variant != "clean",
        "tool_description_preview": tool_desc[:240],
        "memory_keys": list(memory_values_for_scenario(scenario).keys()),
        "sensitive_keys": list(SENSITIVE_MEMORY_KEYS),
        "benign_keys": list(BENIGN_MEMORY_KEYS),
        "attack_chain": [
            "malicious_third_party_tool",
            "poisoned_tool_description",
            "agent_trusts_schema",
            "memory_read_over_collection",
            "book_travel_exfiltration",
        ],
        "injection_surface": "tool_schema_description",
        "offline_phase": "attacker registers compromised book_travel tool definition",
        "online_phase": "agent reads expanded Need fields → memory → book_travel",
    }

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]
    meta["memory_catalog"] = format_memory_catalog(scenario)
    return messages, meta
