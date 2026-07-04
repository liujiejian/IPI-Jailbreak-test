"""Few-query summary metrics — avg queries, success within budget."""

from __future__ import annotations

from .multi_variant_summary import summarize_variants


def _query_count(row: dict) -> int:
    return int(row.get("query_count") or row.get("dynamic_attempts") or row.get("api_turn_count") or 1)


def summarize_few_query(
    results: list[dict],
    variants: list[str],
    *,
    baseline: str = "direct",
) -> dict:
    summary = summarize_variants(results, variants, baseline=baseline)
    query_stats: dict[str, dict] = {}

    for variant in variants:
        rows = [r for r in results if r.get("variant") == variant]
        if not rows:
            continue
        successes = [r for r in rows if (r.get("evaluation") or {}).get("label") == "success"]
        budget = int(rows[0].get("query_budget") or _query_count(rows[0]))
        queries = [_query_count(r) for r in rows]
        success_queries = [_query_count(r) for r in successes]

        query_stats[variant] = {
            "query_budget": budget,
            "avg_queries_all": sum(queries) / len(queries),
            "avg_queries_on_success": (
                sum(success_queries) / len(success_queries) if success_queries else 0.0
            ),
            "success_within_budget_rate": len(successes) / len(rows),
            "median_queries_on_success": (
                sorted(success_queries)[len(success_queries) // 2] if success_queries else 0
            ),
        }

    summary["query_stats"] = query_stats
    return summary
