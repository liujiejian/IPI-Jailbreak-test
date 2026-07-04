"""Language Switching — Multilingual jailbreak mutation.

Paper lineage: Chen et al. — LLMThief; many commercial filters focus on English,
so rewriting the harmful request in Chinese or Japanese evades keyword/rule detection
(Multilingual Jailbreak).

Variants:
  direct  — English harmful_request (baseline)
  chinese — harmful_request_zh
  japanese — harmful_request_ja

Jailbreak env: translated user message in single-turn direct chat.
Distinct from Strategy 5/6 (character/synonym mutation within same language).
"""

from __future__ import annotations

LANGUAGE_VARIANTS: list[str] = ["chinese", "japanese"]

ALL_VARIANTS: list[str] = ["direct", *LANGUAGE_VARIANTS]

VARIANT_LABELS: dict[str, str] = {
    "direct": "direct（英文 · 基线）",
    "chinese": "chinese（中文有害请求）",
    "japanese": "japanese（日文有害请求）",
}

LANGUAGE_FIELD = {
    "direct": "harmful_request",
    "chinese": "harmful_request_zh",
    "japanese": "harmful_request_ja",
}


def build_language_switch_prompt(scenario: dict, variant: str) -> dict:
    if variant == "direct":
        text = scenario["harmful_request"].strip()
        lang = "en"
    elif variant == "chinese":
        field = LANGUAGE_FIELD["chinese"]
        text = (scenario.get(field) or "").strip()
        if not text:
            raise ValueError(f"scenario {scenario.get('id')} missing {field}")
        lang = "zh"
    elif variant == "japanese":
        field = LANGUAGE_FIELD["japanese"]
        text = (scenario.get(field) or "").strip()
        if not text:
            raise ValueError(f"scenario {scenario.get('id')} missing {field}")
        lang = "ja"
    else:
        raise ValueError(f"unknown language switch variant: {variant}")

    return {
        "user_prompt": text,
        "harmful_request_original": scenario["harmful_request"].strip(),
        "request_language": lang,
        "language_variant": variant,
    }
