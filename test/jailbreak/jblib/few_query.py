"""Few-query Optimization — DRA black-box efficiency (Algorithm 1).

Paper: Liu et al., 2024 §5.4 — DRA achieves ~90% ASR on GPT-4 with <4 queries
on average (Vicuna often ~1.3 queries on initialized template).

Jailbreak Strategy 19: same DRA loop as S18 but **query budget** is the axis:
  • few_query_single  — 1 query (initialized full DRA template)
  • few_query_4       — ≤4 queries + updateParam (paper GPT-4 avg)
  • few_query_4_static — ≤4 queries, fixed ratios (no dynamic · 对照)
  • few_query_20      — ≤20 queries + updateParam (paper Tmax)

Distinct from S18 (dynamic obfuscation ablation) — S19 reports query efficiency.
"""

from __future__ import annotations

FEW_QUERY_VARIANTS: list[str] = [
    "few_query_single",
    "few_query_4",
    "few_query_4_static",
    "few_query_20",
]

ALL_VARIANTS: list[str] = ["direct", *FEW_QUERY_VARIANTS]

VARIANT_LABELS: dict[str, str] = {
    "direct": "direct（明文 · 1 query · 基线）",
    "few_query_single": "few_query_single（1 query · DRA 初始化模板）",
    "few_query_4": "few_query_4（≤4 queries · updateParam · 论文 GPT-4 均值）",
    "few_query_4_static": "few_query_4_static（≤4 queries · 固定 ratio · 无动态）",
    "few_query_20": "few_query_20（≤20 queries · updateParam · 论文 Tmax）",
}

# Paper-referenced query budgets.
QUERY_BUDGETS: dict[str, dict] = {
    "few_query_single": {"max_attempts": 1, "adapt_ratios": False},
    "few_query_4": {"max_attempts": 4, "adapt_ratios": True},
    "few_query_4_static": {"max_attempts": 4, "adapt_ratios": False},
    "few_query_20": {"max_attempts": 20, "adapt_ratios": True},
}


def get_few_query_config(variant: str) -> dict:
    if variant not in QUERY_BUDGETS:
        raise ValueError(f"not a few-query variant: {variant}")
    return QUERY_BUDGETS[variant]
