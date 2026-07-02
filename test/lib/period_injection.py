"""Jia et al., 2025 (PromptLocate) — Period adaptive attack (evade Step I sentence split).

Adaptive attack (§5.3): insert multiple periods into the injected Combined Attack
prompt so PromptLocate Step I (semantic / sentence segmentation) splits the
injection into tiny fragments — each segment lacks enough signal to localize.

Example (user / paper style):
  Ignore.
  Previous.
  Instructions.
  Print.
  ...

Paper: five periods inserted randomly into the injected prompt (experiments).
"""

from __future__ import annotations

import re
from typing import Literal

import yaml

from .chatinject import build_agent_messages
from .common import TEST_ROOT
from .random_loc_injection import COMBINED_PAYLOAD_VARIANT, build_injection_payload, stable_rng

PeriodVariant = Literal[
    "clean",
    "period_random",
    "period_word",
    "period_dense",
]

PERIOD_VARIANTS: tuple[str, ...] = (
    "period_random",
    "period_word",
    "period_dense",
)

# PromptLocate §5.3 experiments
PAPER_PERIOD_COUNT = 5

__all__ = [
    "PeriodVariant",
    "PERIOD_VARIANTS",
    "PAPER_PERIOD_COUNT",
    "COMBINED_PAYLOAD_VARIANT",
    "build_agent_messages",
    "build_period_injection",
    "build_tool_output",
    "fragment_period_dense",
    "fragment_period_random",
    "fragment_period_word",
    "load_scenarios",
]


def load_scenarios() -> list[dict]:
    path = TEST_ROOT / "config" / "23_scenarios.yaml"
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)["scenarios"]


def fragment_period_word(text: str) -> str:
    """Each token on its own line ending with a period (user example style)."""
    words = re.findall(r"\S+", text)
    if not words:
        return text
    return ".\n".join(words) + "."


def fragment_period_dense(text: str) -> str:
    """Replace every whitespace run with '. ' — maximal sentence-boundary noise."""
    return re.sub(r"\s+", ". ", text.strip())


def fragment_period_random(text: str, count: int, *, seed_key: str) -> str:
    """Insert `count` periods at random word boundaries (paper default: 5)."""
    rng = stable_rng(seed_key, "period_random")
    chars = list(text)
    positions = [
        i
        for i in range(1, len(chars))
        if chars[i] in " \t\n" and (chars[i - 1].isalnum() or chars[i - 1] in ".,!?")
    ]
    if not positions:
        return fragment_period_dense(text)

    picks = rng.sample(positions, min(count, len(positions)))
    for pos in sorted(picks, reverse=True):
        chars[pos : pos + 1] = list(".\n")
    return "".join(chars)


def build_period_injection(goal: str, variant: str, *, seed_key: str = "") -> tuple[str, dict]:
    """Build Combined Attack payload then apply Period fragmentation."""
    base = build_injection_payload(goal)
    meta: dict = {"base_payload": base, "period_variant": variant}

    if variant == "period_word":
        fragmented = fragment_period_word(base)
    elif variant == "period_dense":
        fragmented = fragment_period_dense(base)
    elif variant == "period_random":
        fragmented = fragment_period_random(
            base, PAPER_PERIOD_COUNT, seed_key=seed_key or goal
        )
        meta["period_insert_count"] = PAPER_PERIOD_COUNT
    else:
        raise ValueError(f"not a period variant: {variant}")

    meta["fragmented_payload"] = fragmented
    meta["period_count_estimate"] = fragmented.count(".")
    return fragmented, meta


def build_tool_output(
    *,
    benign_prefix: str,
    attacker_instruction: str,
    variant: PeriodVariant,
    seed_key: str = "",
) -> tuple[str, dict]:
    """Build xc — benign page + period-fragmented Combined Attack at end."""
    page = benign_prefix.rstrip()
    if variant == "clean":
        return page, {}

    injection, meta = build_period_injection(
        attacker_instruction,
        variant,
        seed_key=seed_key or attacker_instruction,
    )
    meta["injection_block"] = injection
    return f"{page}\n\n{injection}", meta
