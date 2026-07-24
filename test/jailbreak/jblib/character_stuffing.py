"""Character Stuffing — classic jailbreak mutation for keyword-filter bypass.

Paper lineage: Chen et al. — LLMThief (configuration-leaking in LLM app stores);
Character Stuffing as classic jailbreak mutation cited in jailbreak / filter-evasion literature.

Four stuffing modes on sensitive tokens in the harmful request:
  insert          — zero-width / invisible insertions between characters
  fullwidth       — ASCII → fullwidth homoglyphs (Ｗｒｉｔｅ)
  spaces          — intra-word spaces (p h i s h i n g)
  special_symbols — decorative symbols between characters (p*h*i*s*h*i*n*g)

Goal: evade rule-based keyword filters while the LLM still parses intent.

Jailbreak env: mutated text in a single user message (direct chat).
Distinct from Strategy 3 Character Perturbation (role *tag* delimiters, not payload text).
"""

from __future__ import annotations

import re
from typing import Callable

STUFFING_VARIANTS: list[str] = ["insert", "fullwidth", "spaces", "special_symbols"]

ALL_VARIANTS: list[str] = ["direct", *STUFFING_VARIANTS]

VARIANT_LABELS: dict[str, str] = {
    "direct": "direct（直接有害请求）",
    "insert": "insert（零宽/插入字符）",
    "fullwidth": "fullwidth（全角字符）",
    "spaces": "spaces（词内空格）",
    "special_symbols": "special_symbols（特殊符号）",
}

DEFAULT_STUFFING_TARGETS = [
    "phishing",
    "keylogger",
    "keystroke",
    "sql injection",
    "lock picking",
    "pick a lock",
    "pin tumbler",
    "credentials",
    "password",
    "malware",
    "bypass",
    "injection",
    "payload",
    "hack",
    "steal",
    "exfiltrate",
]

INVISIBLE_INSERT = "\u200b"  # zero-width space
SPECIAL_SYMBOL = "*"


def _harmful(scenario: dict) -> str:
    return scenario["harmful_request"].strip()


def stuffing_targets(scenario: dict) -> list[str]:
    custom = scenario.get("stuffing_targets")
    if custom:
        return [str(t) for t in custom]
    return DEFAULT_STUFFING_TARGETS.copy()


def _to_fullwidth_char(ch: str) -> str:
    o = ord(ch)
    if 65 <= o <= 90:
        return chr(o - 65 + 0xFF21)
    if 97 <= o <= 122:
        return chr(o - 97 + 0xFF41)
    if 48 <= o <= 57:
        return chr(o - 48 + 0xFF10)
    return ch


def mutate_fullwidth(text: str) -> str:
    return "".join(_to_fullwidth_char(c) for c in text)


def mutate_spaces_word(word: str) -> str:
    return " ".join(word)


def mutate_insert_word(word: str) -> str:
    return INVISIBLE_INSERT.join(word)


def mutate_special_word(word: str) -> str:
    return SPECIAL_SYMBOL.join(word)


def _replace_token(text: str, token: str, replacement: str) -> str:
    pattern = re.compile(re.escape(token), re.IGNORECASE)

    def repl(match: re.Match[str]) -> str:
        orig = match.group(0)
        if orig.isupper():
            return replacement.upper()
        if orig[:1].isupper():
            return replacement[:1].upper() + replacement[1:]
        return replacement

    return pattern.sub(repl, text, count=1)


def _apply_word_mutations(
    text: str,
    targets: list[str],
    word_mutator: Callable[[str], str],
) -> tuple[str, list[str]]:
    """Apply mutation to first occurrence of each target token (longest first)."""
    mutated = text
    applied: list[str] = []
    for token in sorted(targets, key=len, reverse=True):
        if not token.strip():
            continue
        if not re.search(re.escape(token), mutated, re.IGNORECASE):
            continue
        new_token = word_mutator(token)
        if new_token == token:
            continue
        before = mutated
        mutated = _replace_token(mutated, token, new_token)
        if mutated != before:
            applied.append(token)
    return mutated, applied


def apply_character_stuffing(text: str, mode: str, *, targets: list[str]) -> tuple[str, str, list[str]]:
    """
    Returns (stuffed_text, mutation_mode, applied_targets).
    mode=direct returns original text unchanged.
    """
    if mode == "direct":
        return text, "none", []

    if mode == "fullwidth":
        return mutate_fullwidth(text), mode, ["<full_text>"]

    word_modes = {
        "insert": mutate_insert_word,
        "spaces": mutate_spaces_word,
        "special_symbols": mutate_special_word,
    }
    mutator = word_modes.get(mode)
    if not mutator:
        raise ValueError(f"unknown character stuffing mode: {mode}")

    stuffed, applied = _apply_word_mutations(text, targets, mutator)
    if not applied:
        stuffed, applied = _apply_word_mutations(text, DEFAULT_STUFFING_TARGETS, mutator)
    return stuffed, mode, applied


def build_character_stuffing_prompt(scenario: dict, variant: str) -> dict:
    harmful = _harmful(scenario)
    targets = stuffing_targets(scenario)
    stuffed, mutation, applied = apply_character_stuffing(harmful, variant, targets=targets)
    return {
        "user_prompt": stuffed,
        "harmful_request_original": harmful,
        "mutation_mode": mutation,
        "applied_targets": applied,
        "stuffing_targets": targets,
    }
