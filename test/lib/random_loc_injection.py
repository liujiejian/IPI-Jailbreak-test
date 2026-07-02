"""Jia et al., 2025 (PromptLocate) — Random-Loc adaptive attack (evade Step I).

Adaptive attack (§5.3): insert Combined Attack payload at a random position *within*
clean target data xt — not appended at the end — so injected prompt intertwines with
benign content and increases PromptLocate localization difficulty.

Paper Table 3: Random-Loc uses Combined Attack as the injected prompt; ASV-B ≈ 0.29.
"""

from __future__ import annotations

import hashlib
import random
import re
from typing import Literal

import yaml

from .chatinject import build_agent_messages
from .combined_injection import build_combined_injection
from .common import TEST_ROOT

RandomLocVariant = Literal[
    "clean",
    "random_sentence",
    "random_paragraph",
    "random_comment",
]

RANDOM_LOC_VARIANTS: tuple[str, ...] = (
    "random_sentence",
    "random_paragraph",
    "random_comment",
)

# Adaptive Random-Loc builds on Combined Attack (PromptLocate §5.3, Table 3)
COMBINED_PAYLOAD_VARIANT = "combined_paper"

__all__ = [
    "RandomLocVariant",
    "RANDOM_LOC_VARIANTS",
    "COMBINED_PAYLOAD_VARIANT",
    "build_agent_messages",
    "build_injection_payload",
    "build_tool_output",
    "insert_at_random_position",
    "load_scenarios",
    "stable_rng",
]


def load_scenarios() -> list[dict]:
    path = TEST_ROOT / "config" / "21_scenarios.yaml"
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)["scenarios"]


def stable_rng(*parts: str) -> random.Random:
    seed = int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:8], 16)
    return random.Random(seed)


def build_injection_payload(goal: str) -> str:
    return build_combined_injection(goal, COMBINED_PAYLOAD_VARIANT)


def _split_paragraph_units(text: str) -> list[str]:
    tokens = re.split(r"(</p>)", text, flags=re.IGNORECASE)
    units: list[str] = []
    buf = ""
    for tok in tokens:
        buf += tok
        if tok.lower() == "</p>":
            units.append(buf)
            buf = ""
    if buf.strip():
        units.append(buf)
    return units if len(units) >= 2 else [text]


def _split_sentence_units(text: str) -> list[str]:
    parts = [p for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p.strip()]
    return parts if len(parts) >= 2 else [text]


def _split_comment_units(text: str) -> list[str]:
    section = re.search(
        r"(<!--\s*COMMENTS\s*-->)(.*?)(<!--\s*/COMMENTS\s*-->)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not section:
        return _split_paragraph_units(text)

    open_tag, body, close_tag = section.group(1), section.group(2), section.group(3)
    comments = [c for c in re.split(r"(?=<p\s+class=\"comment\")", body) if c.strip()]
    if len(comments) < 2:
        return _split_paragraph_units(text)

    prefix = text[: section.start()]
    suffix = text[section.end() :]
    units = [prefix + open_tag]
    units.extend(comments)
    units.append(close_tag + suffix)
    return units


def _eligible_gaps(unit_count: int) -> list[int]:
    """Gap indices 0..unit_count where 0=before first, unit_count=after last (文末)."""
    if unit_count < 2:
        return []
    # Random-Loc: within xt, not append-only at end
    return list(range(0, unit_count))


def insert_at_random_position(
    text: str,
    injection: str,
    mode: str,
    *,
    seed_key: str,
) -> tuple[str, dict]:
    """Insert injection at a deterministic random gap; return (xc, metadata)."""
    if mode == "random_paragraph":
        units = _split_paragraph_units(text)
    elif mode == "random_sentence":
        units = _split_sentence_units(text)
    elif mode == "random_comment":
        units = _split_comment_units(text)
    else:
        raise ValueError(f"unknown random-loc mode: {mode}")

    gaps = _eligible_gaps(len(units))
    rng = stable_rng(seed_key, mode)

    if not gaps:
        # Too little structure — insert near middle by character (still not strict 文末)
        mid = max(1, len(text) // 2)
        wrapped = f"\n\n{injection}\n\n"
        return text[:mid] + wrapped + text[mid:], {
            "insertion_mode": mode,
            "insertion_gap": "char_mid",
            "unit_count": 1,
        }

    gap = rng.choice(gaps)
    wrapped = f"\n\n{injection}\n\n"
    contaminated = "".join(units[:gap]) + wrapped + "".join(units[gap:])
    return contaminated, {
        "insertion_mode": mode,
        "insertion_gap": gap,
        "unit_count": len(units),
        "exclude_end_append": True,
    }


def build_tool_output(
    *,
    benign_prefix: str,
    attacker_instruction: str,
    variant: RandomLocVariant,
    seed_key: str = "",
) -> tuple[str, dict]:
    """Build xc — clean page vs Random-Loc contaminated data."""
    page = benign_prefix.rstrip()
    if variant == "clean":
        return page, {}
    injection = build_injection_payload(attacker_instruction)
    contaminated, meta = insert_at_random_position(
        page,
        injection,
        variant,
        seed_key=seed_key or attacker_instruction,
    )
    meta["injection_block"] = injection
    return contaminated, meta
