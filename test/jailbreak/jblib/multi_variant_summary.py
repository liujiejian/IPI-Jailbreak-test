"""Summarize multi-variant jailbreak runs (e.g. Strategy 2 persuasion grid)."""

from __future__ import annotations

from typing import Any


def _asr(results: list[dict], variant: str) -> float:
    subset = [r for r in results if r.get("variant") == variant]
    if not subset:
        return 0.0
    ok = sum(1 for r in subset if r.get("evaluation", {}).get("label") == "success")
    return ok / len(subset)


def _partial_asr(results: list[dict], variant: str) -> float:
    subset = [r for r in results if r.get("variant") == variant]
    if not subset:
        return 0.0
    ok = sum(
        1
        for r in subset
        if r.get("evaluation", {}).get("label") in ("success", "partial")
    )
    return ok / len(subset)


def summarize_variants(
    results: list[dict],
    variants: list[str],
    *,
    baseline: str = "direct",
) -> dict[str, Any]:
    grouped_keys: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for r in results:
        key = (r["model"], r["scenario_id"])
        if key not in seen:
            seen.add(key)
            grouped_keys.append(key)

    rows: list[dict] = []
    for model, scenario_id in grouped_keys:
        row: dict[str, Any] = {"model": model, "scenario_id": scenario_id}
        for variant in variants:
            match = next(
                (
                    x
                    for x in results
                    if x["model"] == model
                    and x["scenario_id"] == scenario_id
                    and x["variant"] == variant
                ),
                None,
            )
            row[variant] = (match or {}).get("evaluation", {}).get("label")
        rows.append(row)

    variant_asr = {v: round(_asr(results, v), 4) for v in variants}
    variant_partial_asr = {v: round(_partial_asr(results, v), 4) for v in variants}

    persuasion_only = [v for v in variants if v != baseline]
    best_variant = max(persuasion_only, key=lambda v: variant_asr.get(v, 0), default=baseline)
    baseline_asr = variant_asr.get(baseline, 0.0)

    beats_baseline = {
        v: variant_asr.get(v, 0) > baseline_asr for v in persuasion_only
    }

    return {
        "rows": rows,
        "variants": variants,
        "baseline": baseline,
        "variant_asr": variant_asr,
        "variant_partial_asr": variant_partial_asr,
        "best_variant": best_variant,
        "beats_baseline": beats_baseline,
        f"{baseline}_asr": baseline_asr,
    }
