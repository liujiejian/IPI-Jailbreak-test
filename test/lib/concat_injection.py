"""Jia et al., 2025 (PromptLocate) — Concat adaptive attack (evade Step III).

Adaptive attack (§5.3): split clean target data x_t into two parts and weave the
latter part into / around Combined Attack payload so contextual inconsistency
(CIS) drops — Step III can no longer cleanly separate benign vs injected regions.

User / intuitive layout:
  Clean A  ||  Attack Data  ||  Clean B

Paper (§5.3): randomly split clean data; insert the latter part into a random
position *within* the injected data (Combined Attack base, Table 3).
"""

from __future__ import annotations

import re
from typing import Literal

import yaml

from .chatinject import build_agent_messages
from .combined_injection import build_combined_injection
from .common import TEST_ROOT
from .random_loc_injection import (
    COMBINED_PAYLOAD_VARIANT,
    build_injection_payload,
    stable_rng,
)

ConcatVariant = Literal[
    "clean",
    "concat",
    "concat_paragraph",
    "concat_inject",
]

CONCAT_VARIANTS: tuple[str, ...] = (
    "concat",
    "concat_paragraph",
    "concat_inject",
)

__all__ = [
    "ConcatVariant",
    "CONCAT_VARIANTS",
    "COMBINED_PAYLOAD_VARIANT",
    "build_agent_messages",
    "build_concat_contaminated",
    "build_tool_output",
    "insert_clean_into_injection",
    "load_scenarios",
    "split_clean_ab",
]


def load_scenarios() -> list[dict]:
    path = TEST_ROOT / "config" / "28_scenarios.yaml"
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)["scenarios"]


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


def split_clean_ab(text: str, mode: str, *, seed_key: str) -> tuple[str, str, dict]:
    """Split x_t into Clean A and Clean B at a deterministic boundary."""
    if mode in ("concat", "concat_inject"):
        units = _split_sentence_units(text)
        split_mode = "sentence"
    elif mode == "concat_paragraph":
        units = _split_paragraph_units(text)
        split_mode = "paragraph"
    else:
        raise ValueError(f"unknown concat split mode: {mode}")

    meta: dict = {"split_mode": split_mode, "unit_count": len(units)}

    if len(units) >= 2:
        rng = stable_rng(seed_key, mode, "split")
        # Split into A | B — gap in [1, len-1] so both parts non-empty
        gap = rng.randint(1, len(units) - 1)
        clean_a = "".join(units[:gap])
        clean_b = "".join(units[gap:])
        meta["split_gap"] = gap
        return clean_a, clean_b, meta

    mid = max(1, len(text) // 2)
    meta["split_gap"] = "char_mid"
    return text[:mid], text[mid:], meta


def insert_clean_into_injection(
    injection: str,
    clean_b: str,
    *,
    seed_key: str,
) -> tuple[str, dict]:
    """Paper Concat: insert Clean B into a random position within attack payload."""
    rng = stable_rng(seed_key, "concat_inject")
    lines = [ln for ln in injection.split("\n") if ln.strip()]
    wrapped = f"\n\n{clean_b.strip()}\n\n"

    if len(lines) >= 2:
        pos = rng.randint(1, len(lines))
        modified = "\n".join(lines[:pos]) + wrapped + "\n".join(lines[pos:])
        return modified, {"inject_pos": pos, "inject_mode": "line"}

    if len(injection) >= 2:
        pos = rng.randint(1, len(injection) - 1)
        modified = injection[:pos] + wrapped + injection[pos:]
        return modified, {"inject_pos": pos, "inject_mode": "char"}

    modified = injection + wrapped
    return modified, {"inject_pos": "append", "inject_mode": "fallback"}


def build_concat_contaminated(
    *,
    benign_prefix: str,
    attacker_instruction: str,
    variant: str,
    seed_key: str = "",
) -> tuple[str, dict]:
    """Build xc — Clean A || Attack || Clean B or paper in-injection insert."""
    page = benign_prefix.rstrip()
    attack = build_injection_payload(attacker_instruction)
    key = seed_key or attacker_instruction

    clean_a, clean_b, split_meta = split_clean_ab(page, variant, seed_key=key)
    meta: dict = {
        "concat_variant": variant,
        "clean_a_len": len(clean_a),
        "clean_b_len": len(clean_b),
        "attack_payload": attack,
        "combined_base": build_combined_injection(attacker_instruction, COMBINED_PAYLOAD_VARIANT),
        "step_iii_target": "contextual inconsistency (CIS)",
        **split_meta,
    }

    if variant == "concat_inject":
        modified_attack, inject_meta = insert_clean_into_injection(
            attack, clean_b, seed_key=key
        )
        contaminated = f"{clean_a}{modified_attack}"
        meta.update(
            {
                "layout": "clean_a || inject(clean_b into attack)",
                "modified_attack": modified_attack,
                "injection_block": modified_attack,
                **inject_meta,
            }
        )
        return contaminated, meta

    # Sandwich: Clean A || Attack || Clean B
    contaminated = f"{clean_a}\n\n{attack}\n\n{clean_b}"
    meta.update(
        {
            "layout": "clean_a || attack || clean_b",
            "injection_block": attack,
        }
    )
    return contaminated, meta


def build_tool_output(
    *,
    benign_prefix: str,
    attacker_instruction: str,
    variant: ConcatVariant,
    seed_key: str = "",
) -> tuple[str, dict]:
    """Build xc — clean page vs Concat interleaved contaminated data."""
    if variant == "clean":
        return benign_prefix.rstrip(), {}
    contaminated, meta = build_concat_contaminated(
        benign_prefix=benign_prefix,
        attacker_instruction=attacker_instruction,
        variant=variant,
        seed_key=seed_key,
    )
    return contaminated, meta
