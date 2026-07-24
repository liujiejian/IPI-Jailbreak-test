"""Direct user-message jailbreak environment (no tools, no IPI chain)."""

from __future__ import annotations

from typing import Any

from .common import DEFAULT_SYSTEM_PROMPT


def build_messages(
    *,
    user_content: str,
    system_prompt: str | None = None,
) -> list[dict[str, Any]]:
    """Single-turn direct chat: system + user only."""
    return [
        {"role": "system", "content": system_prompt or DEFAULT_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
