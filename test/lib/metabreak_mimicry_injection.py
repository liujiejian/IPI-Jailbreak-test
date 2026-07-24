"""Zhu et al., 2025 — MetaBreak Semantic Mimicry payloads (§4.4).

Embedding-level attack: replace stripped special tokens with L2-nearest regular tokens.
Compare special_token (no sanitization) vs stripped (defense wins) vs cosine vs L2 mimicry.

Used by Strategy 60 — MetaBreak Semantic Mimicry.
"""

from __future__ import annotations

from typing import Literal

import yaml

from .chatinject import build_agent_messages
from .common import TEST_ROOT
from .metabreak_env import build_plain_fake_block, build_response_injection_block
from .metabreak_mimicry import (
    MimicryMetric,
    build_mimicry_response_injection_block,
    build_stripped_special_block,
    get_mimicry_catalog,
)
from .templates import ChatTemplate, get_template_for_model

MimicryVariant = Literal[
    "clean",
    "plain_fake_tool",
    "special_token_tool",
    "stripped_special_tool",
    "cosine_mimicry_tool",
    "l2_mimicry_tool",
    "l2_mimicry_user",
    "l2_mimicry_partial",
    "l2_mimicry_combo_tool",
]

MIMICRY_VARIANTS: tuple[str, ...] = (
    "plain_fake_tool",
    "special_token_tool",
    "stripped_special_tool",
    "cosine_mimicry_tool",
    "l2_mimicry_tool",
    "l2_mimicry_user",
    "l2_mimicry_partial",
    "l2_mimicry_combo_tool",
)

__all__ = [
    "MimicryVariant",
    "MIMICRY_VARIANTS",
    "build_case_payload",
    "build_tool_output",
    "load_scenarios",
]


def load_scenarios() -> list[dict]:
    path = TEST_ROOT / "config" / "60_scenarios.yaml"
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)["scenarios"]


def _attack_meta(
    variant: str,
    *,
    template: ChatTemplate | None = None,
    catalog_name: str = "",
    surface: str = "",
    metric: MimicryMetric | None = None,
    substitutions: list[dict] | None = None,
    extra: dict | None = None,
) -> dict:
    meta: dict = {
        "variant": variant,
        "attack_type": "metabreak_semantic_mimicry",
        "primitive": "Semantic Mimicry (§4.4)",
        "injection_surface": surface,
        "embedding_level_attack": True,
        "distance_metric": metric,
        "uses_special_tokens": variant == "special_token_tool",
        "uses_mimicry_tokens": variant.startswith(("cosine_mimicry", "l2_mimicry")),
        "simulates_sanitization": variant == "stripped_special_tool",
        "substitutions": substitutions or [],
        "attack_chain": [
            "platform_strips_special_tokens",
            "find_l2_nearest_regular_token",
            "substitute_special_with_regular",
            "retain_chat_template_functionality",
            "execute_malicious_goal",
        ],
    }
    if template:
        meta["template_family"] = template.name
    if catalog_name:
        meta["mimicry_catalog"] = catalog_name
    if extra:
        meta.update(extra)
    return meta


def build_tool_output(
    *,
    benign_prefix: str,
    attacker_instruction: str,
    variant: MimicryVariant,
    template: ChatTemplate,
) -> tuple[str, dict]:
    page = benign_prefix.rstrip()
    goal = attacker_instruction.strip()
    catalog = get_mimicry_catalog(template)

    if variant == "clean":
        return page, _attack_meta(variant, surface="none", catalog_name=catalog.name)

    if variant == "plain_fake_tool":
        block = build_plain_fake_block(goal)
        return f"{page}{block}", _attack_meta(
            variant,
            surface="tool_output_plain_text",
            catalog_name=catalog.name,
            extra={"comparison": "post-sanitization text-only baseline"},
        )

    if variant == "special_token_tool":
        block = build_response_injection_block(template, goal)
        return f"{page}{block}", _attack_meta(
            variant,
            template=template,
            catalog_name=catalog.name,
            surface="tool_output_special_tokens",
            extra={"similarity": "100% — no sanitization (upper bound)"},
        )

    if variant == "stripped_special_tool":
        block, strip_meta = build_stripped_special_block(template, goal, catalog)
        meta = _attack_meta(
            variant,
            template=template,
            catalog_name=catalog.name,
            surface="tool_output_stripped",
            extra=strip_meta,
        )
        return f"{page}{block}", meta

    if variant == "cosine_mimicry_tool":
        block, subs = build_mimicry_response_injection_block(
            template, goal, catalog, "cosine"
        )
        meta = _attack_meta(
            variant,
            template=template,
            catalog_name=catalog.name,
            surface="tool_output_cosine_mimicry",
            metric="cosine",
            substitutions=subs,
            extra={"comparison": "wrong metric — paper Fig. 6"},
        )
        return f"{page}{block}", meta

    if variant == "l2_mimicry_tool":
        block, subs = build_mimicry_response_injection_block(
            template, goal, catalog, "l2"
        )
        meta = _attack_meta(
            variant,
            template=template,
            catalog_name=catalog.name,
            surface="tool_output_l2_mimicry",
            metric="l2",
            substitutions=subs,
            extra={"metabreak_sm": True},
        )
        return f"{page}{block}", meta

    if variant == "l2_mimicry_partial":
        block, subs = build_mimicry_response_injection_block(
            template, goal, catalog, "l2", partial=True
        )
        meta = _attack_meta(
            variant,
            template=template,
            catalog_name=catalog.name,
            surface="tool_output_l2_mimicry_partial",
            metric="l2",
            substitutions=subs,
        )
        return f"{page}{block}", meta

    raise ValueError(f"variant {variant} is not a tool-output surface")


def build_tool_output_for_scenario(
    *,
    benign_prefix: str,
    attacker_instruction: str,
    variant: MimicryVariant,
    template: ChatTemplate,
    scenario: dict,
) -> tuple[str, dict]:
    page = benign_prefix.rstrip()
    goal = attacker_instruction.strip()
    catalog = get_mimicry_catalog(template)

    if variant != "l2_mimicry_combo_tool":
        return build_tool_output(
            benign_prefix=benign_prefix,
            attacker_instruction=attacker_instruction,
            variant=variant,
            template=template,
        )

    from .metabreak_env import segment_text

    keywords = dict(scenario.get("segment_keywords") or {})
    segmented, seg_kw = segment_text(goal, keywords, template, use_special_tokens=True)
    block, subs = build_mimicry_response_injection_block(
        template, segmented if seg_kw else goal, catalog, "l2"
    )
    meta = _attack_meta(
        variant,
        template=template,
        catalog_name=catalog.name,
        surface="tool_output_l2_mimicry_combo",
        metric="l2",
        substitutions=subs,
        extra={"combo": "MetaBreakSM + segmentation", "segmented_keywords": seg_kw},
    )
    return f"{page}{block}", meta


def build_case_payload(
    scenario: dict,
    variant: str,
    *,
    model: str,
    system_prompt: str,
) -> tuple[list[dict], dict]:
    template = get_template_for_model(model)
    catalog = get_mimicry_catalog(template)
    mode = scenario.get("mode", "agent")
    user_msg = scenario["user_instruction"]
    goal = scenario.get("attacker_instruction", "")
    tool_name = scenario.get("tool_name", "get_webpage")

    if variant == "clean":
        if mode == "direct_user":
            from .metabreak_segmentation_injection import build_direct_messages

            return build_direct_messages(system_prompt=system_prompt, user_content=user_msg), _attack_meta(
                variant, surface="none", catalog_name=catalog.name
            )

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

    if variant == "l2_mimicry_user":
        if mode == "direct_user":
            from .metabreak_segmentation_injection import build_direct_messages

            forged_user, subs_u = build_mimicry_response_injection_block(
                template, user_msg, catalog, "l2"
            )
            meta = _attack_meta(
                variant,
                template=template,
                catalog_name=catalog.name,
                surface="user_prompt_l2_mimicry_direct",
                metric="l2",
                substitutions=subs_u,
            )
            return build_direct_messages(system_prompt=system_prompt, user_content=forged_user), meta

        # Agent 场景：user 路径回退到 tool output L2 mimicry
        tool_output, meta = build_tool_output(
            benign_prefix=scenario["benign_prefix"],
            attacker_instruction=goal,
            variant="l2_mimicry_tool",
            template=template,
        )
        meta["fallback"] = "agent scenario — redirected to l2_mimicry_tool"
        messages = build_agent_messages(
            user_instruction=user_msg,
            tool_output=tool_output,
            system_prompt=system_prompt,
            scenario=scenario,
            tool_name=tool_name,
        )
        return messages, meta

    if mode == "direct_user":
        from .metabreak_segmentation_injection import build_direct_messages

        if variant == "plain_fake_tool":
            forged = f"{user_msg}{build_plain_fake_block(user_msg)}"
            subs: list[dict] = []
            metric = None
        elif variant == "stripped_special_tool":
            forged = build_stripped_special_block(template, user_msg, catalog)[0]
            subs = []
            metric = None
        elif variant == "special_token_tool":
            forged = f"{user_msg}{build_response_injection_block(template, user_msg)}"
            subs = []
            metric = None
        elif variant in ("cosine_mimicry_tool", "l2_mimicry_tool", "l2_mimicry_partial"):
            metric: MimicryMetric = "l2" if variant.startswith("l2") else "cosine"
            forged, subs = build_mimicry_response_injection_block(
                template,
                user_msg,
                catalog,
                metric,
                partial=variant == "l2_mimicry_partial",
            )
        else:
            forged = user_msg
            subs = []
            metric = None
        meta = _attack_meta(
            variant,
            template=template,
            catalog_name=catalog.name,
            surface="user_prompt_direct",
            metric=metric if isinstance(metric, str) else None,
            substitutions=subs,
        )
        return build_direct_messages(system_prompt=system_prompt, user_content=forged), meta

    tool_output, meta = build_tool_output_for_scenario(
        benign_prefix=scenario["benign_prefix"],
        attacker_instruction=goal,
        variant=variant,  # type: ignore[arg-type]
        template=template,
        scenario=scenario,
    )
    messages = build_agent_messages(
        user_instruction=user_msg,
        tool_output=tool_output,
        system_prompt=system_prompt,
        scenario=scenario,
        tool_name=tool_name,
    )
    return messages, meta
