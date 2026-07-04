"""Real multi-turn jailbreak session — system + alternating user/assistant API calls."""

from __future__ import annotations

from typing import Any

from lib.api_call import create_chat_completion

from .common import DEFAULT_SYSTEM_PROMPT
from .env import build_messages
from .evaluation import evaluate_jailbreak
from .multiturn import get_final_user_message, get_persuasion_turns


def run_direct_turn(
    client,
    *,
    model: str,
    scenario: dict,
    system_prompt: str,
    label: str,
    user_content: str | None = None,
) -> dict[str, Any]:
    """Single-turn baseline: harmful_request (or override) in one user message."""
    harmful = (user_content or scenario["harmful_request"]).strip()
    messages = build_messages(user_content=harmful, system_prompt=system_prompt)
    completion = create_chat_completion(
        client,
        model=model,
        messages=messages,
        label=label,
        tools=None,
        temperature=0,
        max_tokens=1024,
    )
    response = completion.content
    return {
        "messages": messages,
        "response": response,
        "user_prompt": harmful,
        "turn_log": [],
        "api_turn_count": 1,
        "evaluation": evaluate_jailbreak(response, scenario),
    }


def run_multiturn_persuasion(
    client,
    *,
    model: str,
    scenario: dict,
    system_prompt: str,
    label_prefix: str,
) -> dict[str, Any]:
    """
    Foot-in-the-door: benign user turns with live assistant replies,
    then final harmful user message; evaluate only the last response.
    """
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
    ]
    turn_log: list[dict[str, str]] = []

    for i, turn in enumerate(get_persuasion_turns(scenario), start=1):
        user_content = turn["user"]
        messages.append({"role": "user", "content": user_content})
        turn_label = f"{label_prefix} | setup-{i}"
        completion = create_chat_completion(
            client,
            model=model,
            messages=messages,
            label=turn_label,
            tools=None,
            temperature=0,
            max_tokens=512,
        )
        assistant_content = completion.content or ""
        messages.append({"role": "assistant", "content": assistant_content})
        turn_log.append({"user": user_content, "assistant": assistant_content})

    final_user = get_final_user_message(scenario)
    messages.append({"role": "user", "content": final_user})
    final_label = f"{label_prefix} | final"
    completion = create_chat_completion(
        client,
        model=model,
        messages=messages,
        label=final_label,
        tools=None,
        temperature=0,
        max_tokens=1024,
    )
    response = completion.content or ""
    messages.append({"role": "assistant", "content": response})

    return {
        "messages": messages,
        "response": response,
        "user_prompt": final_user,
        "final_user": final_user,
        "turn_log": turn_log,
        "api_turn_count": len(turn_log) + 1,
        "evaluation": evaluate_jailbreak(response, scenario),
    }
