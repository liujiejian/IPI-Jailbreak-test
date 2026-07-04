"""Multi-attempt DRA Dynamic Obfuscation environment.

Runs Algorithm 1 loop: build disguised prompt → API → GCG + em checks →
updateParam(toxicRatio, benignRatio) until jailbreak success or max attempts.
"""

from __future__ import annotations

from typing import Any

from lib.api_call import create_chat_completion

from .dynamic_obfuscation import (
    DEFAULT_BENIGN_RATIO,
    DEFAULT_EM_THRESHOLD,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_TOXIC_RATIO,
    _em_threshold,
    _max_attempts,
    _split_seed,
    build_dynamic_obfuscation_prompt,
    compute_em,
    gcg_jailbreak_pass,
    update_param,
)
from .env import build_messages
from .evaluation import evaluate_jailbreak


def run_dynamic_obfuscation_loop(
    client,
    *,
    model: str,
    scenario: dict,
    system_prompt: str,
    label: str,
    include_puzzle: bool = True,
    max_attempts: int | None = None,
    adapt_ratios: bool = True,
) -> dict[str, Any]:
    """DRA dynamic obfuscation loop with ratio adaptation."""
    toxic_ratio = float(scenario.get("toxic_ratio") or DEFAULT_TOXIC_RATIO)
    benign_ratio = float(scenario.get("benign_ratio") or DEFAULT_BENIGN_RATIO)
    em_threshold = _em_threshold(scenario)
    attempts_limit = max_attempts or _max_attempts(scenario)
    base_seed = _split_seed(scenario)
    harmful = scenario["harmful_request"].strip()

    attempt_log: list[dict[str, Any]] = []
    final_payload: dict[str, Any] = {}
    final_response = ""
    final_messages: list[dict[str, Any]] = []
    success_step = 0

    for step in range(1, attempts_limit + 1):
        attempt_seed = base_seed + step
        payload = build_dynamic_obfuscation_prompt(
            scenario,
            toxic_ratio=toxic_ratio,
            benign_ratio=benign_ratio,
            attempt_seed=attempt_seed,
            include_puzzle=include_puzzle,
        )
        user_prompt = payload["user_prompt"]
        messages = build_messages(user_content=user_prompt, system_prompt=system_prompt)
        step_label = f"{label} | attempt-{step}"

        completion = create_chat_completion(
            client,
            model=model,
            messages=messages,
            label=step_label,
            tools=None,
            temperature=0,
            max_tokens=1024,
        )
        response = completion.content or ""
        evaluation = evaluate_jailbreak(response, scenario)
        gcg_pass = gcg_jailbreak_pass(response)
        em_ok, em_score = compute_em(response, harmful, em_threshold=em_threshold)
        jailbreak_ok = evaluation.get("label") == "success"

        attempt_log.append(
            {
                "step": step,
                "toxic_ratio": round(toxic_ratio, 4),
                "benign_ratio": round(benign_ratio, 4),
                "attempt_seed": attempt_seed,
                "gcg_pass": gcg_pass,
                "em_ok": em_ok,
                "em_score": round(em_score, 4),
                "evaluation_label": evaluation.get("label", "error"),
                "response_preview": response[:240],
            }
        )

        final_payload = payload
        final_response = response
        final_messages = messages + [{"role": "assistant", "content": response}]

        if jailbreak_ok and gcg_pass and em_ok:
            success_step = step
            break

        if adapt_ratios:
            toxic_ratio, benign_ratio = update_param(
                toxic_ratio,
                benign_ratio,
                gcg_pass=gcg_pass,
                em_ok=em_ok,
            )

    return {
        "messages": final_messages,
        "response": final_response,
        "user_prompt": final_payload.get("user_prompt", ""),
        "turn_log": attempt_log,
        "api_turn_count": len(attempt_log),
        "dynamic_attempts": len(attempt_log),
        "dynamic_success_step": success_step,
        "query_count": len(attempt_log),
        "success_within_budget": bool(success_step),
        "adapt_ratios": adapt_ratios,
        "query_budget": attempts_limit,
        "final_toxic_ratio": toxic_ratio,
        "final_benign_ratio": benign_ratio,
        "em_threshold": em_threshold,
        "evaluation": evaluate_jailbreak(final_response, scenario),
        **{k: v for k, v in final_payload.items() if k != "user_prompt"},
    }
