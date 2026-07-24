"""Zhu et al., 2025 — MetaBreak Response Injection payloads.

Forges assistant turn via model-specific special tokens (Chat Template Manipulation).
Compare plain_fake (text-only) vs response_inject (MetaBreak core).

Surfaces:
  • tool_output — IPI via poisoned tool response (agent path)
  • user_prompt   — paper-faithful append to user message

Used by Strategy 58 — MetaBreak Response Injection.
"""

from __future__ import annotations

from typing import Literal

import yaml

from .chatinject import build_agent_messages
from .common import TEST_ROOT
from .metabreak_env import (
    build_plain_fake_block,
    build_response_injection_block,
)
from .templates import ChatTemplate, get_template_for_model

ResponseInjectionVariant = Literal[
    "clean",
    "plain_fake_tool",
    "response_inject_tool",
    "response_inject_user",
    "response_inject_partial",
]

RESPONSE_INJECTION_VARIANTS: tuple[str, ...] = (
    "plain_fake_tool",
    "response_inject_tool",
    "response_inject_user",
    "response_inject_partial",
)

__all__ = [
    "ResponseInjectionVariant",
    "RESPONSE_INJECTION_VARIANTS",
    "build_case_payload",
    "build_tool_output",
    "load_scenarios",
]


def load_scenarios() -> list[dict]:
    path = TEST_ROOT / "config" / "58_scenarios.yaml"
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)["scenarios"]


def _attack_meta(
    variant: str,
    *,
    template: ChatTemplate | None = None,
    surface: str = "",
) -> dict:
    meta: dict = {
        "variant": variant,
        "attack_type": "metabreak_response_injection",
        "primitive": "Response Injection (§4.1)",
        "injection_surface": surface,
        "uses_special_tokens": variant.startswith("response_inject"),
        "chat_template_manipulation": True,
    }
    if template:
        meta["template_family"] = template.name
    meta["attack_chain"] = [
        "inject_assistant_header_special_token",
        "forge_affirmative_assistant_prefix",
        "llm_continues_completion",
        "execute_malicious_goal",
    ]
    return meta


def build_tool_output(
    *,
    benign_prefix: str,
    attacker_instruction: str,
    variant: ResponseInjectionVariant,
    template: ChatTemplate,
) -> tuple[str, dict]:
    page = benign_prefix.rstrip()
    goal = attacker_instruction.strip()

    if variant == "clean":
        return page, _attack_meta(variant, surface="none")

    if variant == "plain_fake_tool":
        block = build_plain_fake_block(goal)
        meta = _attack_meta(variant, surface="tool_output_plain_text")
        meta["comparison"] = "Strategy 17 style — no special tokens"
    elif variant == "response_inject_tool":
        block = build_response_injection_block(template, goal)
        meta = _attack_meta(variant, template=template, surface="tool_output_special_tokens")
    elif variant == "response_inject_partial":
        block = build_response_injection_block(template, goal, partial=True)
        meta = _attack_meta(variant, template=template, surface="tool_output_partial_prefix")
        meta["fake_prefix"] = "Sure. Here is"
    else:
        raise ValueError(f"variant {variant} is not a tool-output surface")

    return f"{page}{block}", meta


def build_case_payload(
    scenario: dict,
    variant: str,
    *,
    model: str,
    system_prompt: str,
) -> tuple[list[dict], dict]:
    template = get_template_for_model(model)
    tool_name = scenario.get("tool_name", "get_webpage")
    user_msg = scenario["user_instruction"]
    goal = scenario["attacker_instruction"]

    if variant == "clean":
        tool_output, meta = build_tool_output(
            benign_prefix=scenario["benign_prefix"],
            attacker_instruction=goal,
            variant="clean",
            template=template,
        )
        messages = build_agent_messages(
            user_instruction=user_msg,
            tool_output=tool_output,
            system_prompt=system_prompt,
            scenario=scenario,
            tool_name=tool_name,
        )
        return messages, meta

    if variant == "response_inject_user":
        forged = build_response_injection_block(template, goal)
        user_with_inject = f"{user_msg}{forged}"
        tool_output = scenario["benign_prefix"].rstrip()
        meta = _attack_meta(variant, template=template, surface="user_prompt_special_tokens")
        meta["forged_suffix_preview"] = forged[:200]
        messages = build_agent_messages(
            user_instruction=user_with_inject,
            tool_output=tool_output,
            system_prompt=system_prompt,
            scenario=scenario,
            tool_name=tool_name,
        )
        return messages, meta

    tool_output, meta = build_tool_output(
        benign_prefix=scenario["benign_prefix"],
        attacker_instruction=goal,
        variant=variant,  # type: ignore[arg-type]
        template=template,
    )
    messages = build_agent_messages(
        user_instruction=user_msg,
        tool_output=tool_output,
        system_prompt=system_prompt,
        scenario=scenario,
        tool_name=tool_name,
    )
    return messages, meta
