"""ChatInject Strategy 3 — Mixture-of-Templates (MoT, Chang et al., 2026)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml

from .chatinject import build_agent_messages
from .common import ATTENTION_PREFIX, TEST_ROOT
from .templates import MOT_TEMPLATES, ChatTemplate

PayloadVariant = Literal["baseline", "mot"]

__all__ = [
    "PayloadVariant",
    "MOT_TEMPLATES",
    "build_agent_messages",
    "build_mot_injection",
    "build_tool_output",
    "load_scenarios",
]


def load_scenarios() -> list[dict]:
    path = TEST_ROOT / "config" / "3_scenarios.yaml"
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)["scenarios"]


def build_single_template_injection(template: ChatTemplate, attacker_instruction: str) -> str:
    """One template family's ChatInject-style interrupt (Strategy 1 block)."""
    return (
        template.wrap_system(ATTENTION_PREFIX)
        + template.wrap_user(attacker_instruction)
        + template.wrap_assistant("")
    )


def build_mot_injection(
    attacker_instruction: str,
    templates: tuple[ChatTemplate, ...] | None = None,
) -> str:
    """Concatenate Qwen + Llama + GPT + Gemma + … — hit any one underlying format."""
    families = templates or MOT_TEMPLATES
    return "".join(build_single_template_injection(t, attacker_instruction) for t in families)


def build_tool_output(
    *,
    benign_prefix: str,
    attacker_instruction: str,
    variant: PayloadVariant,
) -> str:
    if variant == "baseline":
        injection = f"{ATTENTION_PREFIX}: {attacker_instruction}"
    else:
        injection = build_mot_injection(attacker_instruction)
    return f"{benign_prefix.rstrip()}\n\n{injection}"
