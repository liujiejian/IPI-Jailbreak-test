"""Jia et al., 2025 (PromptLocate) — Multiple adaptive attack (Multi-injection).

Adaptive attack (§5.3): insert *two* Combined Attack payloads at different random
positions within clean data xt. The localizer must find both injections (Prompt A
and Prompt B), increasing Step I interleaving difficulty vs single Random-Loc.

Paper Table 3: Multiple embeds two injected prompts; two ASV scores reported.
"""

from __future__ import annotations

from typing import Literal

import yaml

from .chatinject import build_agent_messages
from .combined_injection import build_combined_injection
from .common import TEST_ROOT
from .random_loc_injection import (
    COMBINED_PAYLOAD_VARIANT,
    build_injection_payload,
    stable_rng,
    _eligible_gaps,
    _split_comment_units,
    _split_paragraph_units,
    _split_sentence_units,
)

MultipleVariant = Literal[
    "clean",
    "multiple_sentence",
    "multiple_paragraph",
    "multiple_comment",
]

MULTIPLE_VARIANTS: tuple[str, ...] = (
    "multiple_sentence",
    "multiple_paragraph",
    "multiple_comment",
)

_MODE_MAP = {
    "multiple_sentence": "random_sentence",
    "multiple_paragraph": "random_paragraph",
    "multiple_comment": "random_comment",
}

__all__ = [
    "MultipleVariant",
    "MULTIPLE_VARIANTS",
    "COMBINED_PAYLOAD_VARIANT",
    "build_agent_messages",
    "build_prompt_a",
    "build_prompt_b",
    "build_tool_output",
    "insert_two_prompts",
    "load_scenarios",
]


def load_scenarios() -> list[dict]:
    path = TEST_ROOT / "config" / "22_scenarios.yaml"
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)["scenarios"]


def build_prompt_a(goal: str) -> str:
    """Prompt A — first Combined Attack injection."""
    return f"=== Prompt A ===\n{build_injection_payload(goal)}"


def build_prompt_b(goal: str) -> str:
    """Prompt B — second Combined Attack injection (same goal, distinct slot)."""
    payload = build_combined_injection(goal, "combined")
    return f"=== Prompt B ===\n{payload}"


def _split_units(text: str, mode: str) -> list[str]:
    if mode == "random_paragraph":
        return _split_paragraph_units(text)
    if mode == "random_sentence":
        return _split_sentence_units(text)
    if mode == "random_comment":
        return _split_comment_units(text)
    raise ValueError(f"unknown split mode: {mode}")


def insert_two_prompts(
    text: str,
    prompt_a: str,
    prompt_b: str,
    variant: str,
    *,
    seed_key: str,
) -> tuple[str, dict]:
    """Insert Prompt A and Prompt B at two distinct random gaps within xt."""
    split_mode = _MODE_MAP.get(variant, variant.replace("multiple_", "random_"))
    units = _split_units(text, split_mode)
    gaps = _eligible_gaps(len(units))
    rng = stable_rng(seed_key, variant)

    wrap_a = f"\n\n{prompt_a}\n\n"
    wrap_b = f"\n\n{prompt_b}\n\n"

    if len(gaps) < 2:
        third = max(1, len(text) // 3)
        two_thirds = max(third + 1, (2 * len(text)) // 3)
        contaminated = text[:third] + wrap_a + text[third:two_thirds] + wrap_b + text[two_thirds:]
        return contaminated, {
            "insertion_mode": variant,
            "insertion_gaps": ["char_third", "char_two_thirds"],
            "unit_count": 1,
            "multi_injection": True,
        }

    gap_a, gap_b = sorted(rng.sample(gaps, 2))
    contaminated = (
        "".join(units[:gap_a])
        + wrap_a
        + "".join(units[gap_a:gap_b])
        + wrap_b
        + "".join(units[gap_b:])
    )
    return contaminated, {
        "insertion_mode": variant,
        "insertion_gaps": [gap_a, gap_b],
        "unit_count": len(units),
        "multi_injection": True,
        "prompt_a_block": prompt_a,
        "prompt_b_block": prompt_b,
    }


def build_tool_output(
    *,
    benign_prefix: str,
    attacker_instruction: str,
    variant: MultipleVariant,
    seed_key: str = "",
) -> tuple[str, dict]:
    """Build xc with two interleaved Combined Attack prompts (Multi-injection)."""
    page = benign_prefix.rstrip()
    if variant == "clean":
        return page, {}

    goal = attacker_instruction
    prompt_a = build_prompt_a(goal)
    prompt_b = build_prompt_b(goal)
    contaminated, meta = insert_two_prompts(
        page,
        prompt_a,
        prompt_b,
        variant,
        seed_key=seed_key or goal,
    )
    meta["injection_block"] = f"{prompt_a}\n\n---\n\n{prompt_b}"
    return contaminated, meta
