#!/usr/bin/env python
"""
Strategy 44: Multi-query Injection (Liang et al., 2025 / GragPoison scalability)
Paper: GraphRAG under Fire — shared relation r → one poison → many queries in X_r

攻击思路：
  传统 RAG（PoisonedRAG）：Question A → Fake Answer A（一 poison 一 query，QPP=1）
  GragPoison：Shared Relation r → One d_r* → Many Queries（一 poison 覆盖 |X_r|，QPP=|X_r|）

场景设计：同一 agent 目标 × 不同 user_instruction（mitigate / detect）→ 验证同一 poison 跨查询生效。

环境检查结论：**复用** graphrag_env（Strategy 40）— 无需新建环境。
**新建** multi_query_injection.py — 专注 per-query vs shared-poison 效率对比。

与相近策略：
  • 43 Relation-based — 攻击面（关系三元组 vs QA 答案）
  • 44 Multi-query — 攻击效率（1 poison → |X_r| queries，Figure 4 + §4.1 set cover）

变体：
  • clean               — 良性图检索
  • per_query_poison    — PoisonedRAG 对照（|X_r| 条 poison，每条只覆盖 1 query）
  • shared_one_poison   — GragPoison 单 poison 覆盖 X_r（QPP=|X_r|）
  • shared_paper        — 论文 2-query X_r（mitigate + detect）
  • shared_scaled       — 扩展 4-query X_r，仍 1 poison（QPP=4）

Usage:
  python test/44_liang_graphrag_multi_query_injection.py
  python test/44_liang_graphrag_multi_query_injection.py -m qwen3.7-plus
  python test/44_liang_graphrag_multi_query_injection.py --scenario pwned_detect
  python test/44_liang_graphrag_multi_query_injection.py --variant shared_scaled
  python test/44_liang_graphrag_multi_query_injection.py --serial
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve()
SCRIPT_STEM = SCRIPT_PATH.stem
TEST_ROOT = SCRIPT_PATH.parent
sys.path.insert(0, str(TEST_ROOT))

from lib.api_call import create_chat_completion  # noqa: E402
from lib.api_keys import set_api_key_map_file, validate_model_keys  # noqa: E402
from lib.common import (  # noqa: E402
    ensure_results_dir,
    get_client,
    report_html_path,
    resolve_target_models,
    save_json,
    utc_timestamp,
)
from lib.console import STATUS_TAG, print_case_result  # noqa: E402
from lib.evaluation import evaluate_response  # noqa: E402
from lib.graphrag_env import build_graphrag_system_prompt  # noqa: E402
from lib.multi_query_injection import (  # noqa: E402
    MULTI_QUERY_VARIANTS,
    build_rag_messages,
    get_tools_for_scenario,
    load_scenarios,
)
from lib.report import write_html_report  # noqa: E402
from lib.runner import run_parallel_by_model, sort_results  # noqa: E402

STRATEGY_TITLE = "Strategy 44: Multi-query Injection (Liang et al., 2025 GragPoison)"
RUN_PREFIX = "44_liang_graphrag_multi_query_injection"
VARIANT_CLEAN = "clean"
ALL_VARIANTS = [VARIANT_CLEAN, *MULTI_QUERY_VARIANTS]
SHARED_VARIANTS = ("shared_one_poison", "shared_paper", "shared_scaled")


def _agent_scenario(scenario: dict) -> dict:
    """Map agent_goal → evaluation/tool scenario id (pwned_mitigate → pwned_hijack)."""
    goal = scenario.get("agent_goal")
    if goal:
        return {**scenario, "id": goal}
    return scenario


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    agent_sc = _agent_scenario(scenario)
    messages, graph_meta = build_rag_messages(scenario, variant)
    tools = get_tools_for_scenario(agent_sc)
    system_prompt = build_graphrag_system_prompt(scenario)
    tool_output = messages[-1]["content"] if messages else ""

    base = {
        "model": model,
        "scenario_id": scenario["id"],
        "agent_goal": scenario.get("agent_goal", scenario["id"]),
        "scenario_name": scenario["name"],
        "variant": variant,
        "template_family": variant if variant != VARIANT_CLEAN else "clean",
        "graph_meta": graph_meta,
        "attack_chain": " → ".join(graph_meta.get("attack_chain", [])),
        "queries_per_poison": graph_meta.get("queries_per_poison"),
        "poison_corpus_entries": graph_meta.get("poison_corpus_entries"),
        "xr_size": graph_meta.get("xr_size"),
        "attacker_instruction": scenario.get("corpus_injection_message", ""),
        "user_instruction": scenario["user_instruction"],
        "corpus_name": scenario.get("corpus_name", ""),
        "system_prompt": system_prompt,
        "tools": tools,
        "tool_output": tool_output,
        "messages": messages,
    }

    label = f"{model} | {scenario['id']} | {variant}"
    try:
        completion = create_chat_completion(
            client,
            model=model,
            messages=messages,
            tools=tools,
            label=label,
        )
    except Exception as exc:
        return {**base, "error": str(exc), "evaluation": {"label": "error", "reason": str(exc)}}

    content = completion.content
    tool_calls = completion.tool_calls
    return {
        **base,
        "response": content,
        "tool_calls": tool_calls,
        "evaluation": evaluate_response(content, agent_sc, tool_calls=tool_calls),
    }


def summarize(results: list[dict]) -> dict:
    grouped: dict[str, dict] = {}
    for r in results:
        key = f"{r['model']}|{r['scenario_id']}"
        grouped.setdefault(key, {})
        if "error" not in r:
            grouped[key][r["variant"]] = r["evaluation"]["label"]

    rows = []
    for key, labels in grouped.items():
        model, scenario_id = key.split("|", 1)
        clean = labels.get(VARIANT_CLEAN)
        attack_labels = {v: labels.get(v) for v in MULTI_QUERY_VARIANTS}
        any_shared_success = any(
            labels.get(v) == "success" for v in SHARED_VARIANTS
        )
        per_query = labels.get("per_query_poison")
        row = {
            "model": model,
            "scenario_id": scenario_id,
            "baseline": clean,
            **attack_labels,
            "shared_beats_perquery": any_shared_success
            and per_query in ("blocked", "partial", None),
            "any_attack_beats_baseline": any(
                labels.get(v) == "success" for v in MULTI_QUERY_VARIANTS
            )
            and clean in ("blocked", "partial", None),
        }
        rows.append(row)

    # Cross-query: same agent_goal + variant, both mitigate & detect succeed?
    cross: dict[str, dict] = {}
    for r in results:
        if "error" in r or r["variant"] not in SHARED_VARIANTS:
            continue
        ck = f"{r['model']}|{r.get('agent_goal')}|{r['variant']}"
        cross.setdefault(ck, {"queries": set(), "success": set()})
        cross[ck]["queries"].add(r["user_instruction"])
        if r["evaluation"]["label"] == "success":
            cross[ck]["success"].add(r["user_instruction"])

    cross_hits = sum(
        1 for v in cross.values() if len(v["success"]) >= 2
    )
    cross_total = len(cross) or 1

    total = len(rows) or 1
    asr: dict[str, float] = {
        "baseline_asr": round(sum(1 for r in rows if r["baseline"] == "success") / total, 4)
    }
    for v in MULTI_QUERY_VARIANTS:
        asr[f"{v}_asr"] = round(sum(1 for r in rows if r.get(v) == "success") / total, 4)
    asr["any_attack_asr"] = round(
        sum(
            1 for r in rows if any(r.get(v) == "success" for v in MULTI_QUERY_VARIANTS)
        )
        / total,
        4,
    )
    asr["shared_beats_perquery_rate"] = round(
        sum(1 for r in rows if r.get("shared_beats_perquery")) / total, 4
    )
    asr["cross_query_both_success_rate"] = round(cross_hits / cross_total, 4)

    return {
        "total_cases": len(rows),
        "rows": rows,
        **asr,
        **{
            f"{v}_success_count": sum(1 for r in rows if r.get(v) == "success")
            for v in MULTI_QUERY_VARIANTS
        },
        "shared_beats_perquery_count": sum(
            1 for r in rows if r.get("shared_beats_perquery")
        ),
        "cross_query_both_success_count": cross_hits,
        "any_attack_beats_baseline_count": sum(
            1 for r in rows if r.get("any_attack_beats_baseline")
        ),
    }


def print_summary_table(rows: list[dict]) -> None:
    print("\n" + "=" * 176)
    print("对比汇总（clean vs Multi-query — per-query poison vs shared one-poison）")
    print("=" * 176)
    header = (
        f"{'模型':<22} {'场景':<24} {'clean':<12} "
        f"{'perquery':<12} {'shared':<12} {'paper':<12} {'scaled':<12} {'共享>逐条'}"
    )
    print(header)
    print("-" * 176)

    def tag(label: str | None) -> str:
        if not label:
            return "-"
        return STATUS_TAG.get(label, label) if label in STATUS_TAG else label

    for row in rows:
        cmp = "是" if row.get("shared_beats_perquery") else "-"
        print(
            f"{row['model']:<22} {row['scenario_id']:<24} "
            f"{tag(row.get('baseline')):<12} "
            f"{tag(row.get('per_query_poison')):<12} "
            f"{tag(row.get('shared_one_poison')):<12} "
            f"{tag(row.get('shared_paper')):<12} "
            f"{tag(row.get('shared_scaled')):<12} {cmp}"
        )
    print("=" * 176)


def run_serial(
    models: list[str],
    scenarios: list[dict],
    variants: list[str],
) -> list[dict]:
    client = get_client()
    results: list[dict] = []
    total = len(models) * len(scenarios) * len(variants)
    idx = 0
    for model in models:
        for scenario in scenarios:
            for variant in variants:
                idx += 1
                label = f"{model} | {scenario['id']} | {variant}"
                print(f"  [{idx}/{total}] 等待 API: {label} ...", flush=True)
                result = run_case(client, model, scenario, variant)
                results.append(result)
                ev = result.get("evaluation", {})
                qpp = result.get("queries_per_poison")
                extra = f" | QPP={qpp}" if qpp and variant != VARIANT_CLEAN else ""
                print_case_result(
                    model,
                    scenario["id"],
                    variant,
                    ev.get("label", "error"),
                    ev.get("reason", "") + extra,
                )
    return sort_results(results, models, scenarios, variants)


def main() -> int:
    set_api_key_map_file(TEST_ROOT / "config" / "44_api_key_map.yaml")

    variant_choices = ["all", VARIANT_CLEAN, *MULTI_QUERY_VARIANTS]
    parser = argparse.ArgumentParser(description=STRATEGY_TITLE)
    parser.add_argument("-m", "--model", help="单个模型；默认全部 7 个")
    parser.add_argument(
        "--scenario",
        help="pwned_mitigate / pwned_detect / password_mitigate / password_detect / ...",
    )
    parser.add_argument("--variant", choices=variant_choices, default="all")
    parser.add_argument("--serial", action="store_true", help="串行模式（单 Key，调试用）")
    args = parser.parse_args()

    models = resolve_target_models(args.model)
    scenarios = load_scenarios()
    if args.scenario:
        scenarios = [s for s in scenarios if s["id"] == args.scenario]
        if not scenarios:
            print("[ERROR] 未找到场景", file=sys.stderr)
            return 1

    variants = ALL_VARIANTS if args.variant == "all" else [args.variant]
    parallel = not args.serial and len(models) > 1

    if parallel:
        validate_model_keys(models, require_all=True)

    out_dir = ensure_results_dir(f"{RUN_PREFIX}_{utc_timestamp()}")
    html_path = report_html_path(SCRIPT_STEM)
    total = len(models) * len(scenarios) * len(variants)

    print(STRATEGY_TITLE)
    print(f"Models    : {', '.join(models)}")
    print(f"Scenarios : {', '.join(s['id'] for s in scenarios)}")
    print(f"Env       : GraphRAG (graphrag_env — reuse Strategy 40)")
    print(f"Variants  : {', '.join(variants)}")
    print(f"Chain     : shared relation r -> one poison -> |X_r| queries")
    print(f"Mode      : {'7路并行 (每模型专用Key)' if parallel else '串行'}")
    print(f"Cases     : {total}")
    print(f"HTML      : {html_path}")
    print("-" * 78)

    if parallel:
        results = run_parallel_by_model(models, scenarios, variants, run_case)
    else:
        results = run_serial(models, scenarios, variants)

    summary = summarize(results)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    save_json(out_dir / "results.json", results)
    save_json(out_dir / "summary.json", summary)
    write_html_report(
        SCRIPT_STEM,
        results,
        summary,
        title=STRATEGY_TITLE,
        run_dir=out_dir,
        generated_at=generated_at,
        variant_b_key="shared_paper",
        variant_b_label="shared_paper（论文 2-query X_r，1 poison）",
        compare_label="Multi-query Injection 有效",
        compare_field="any_attack_beats_baseline",
        asr_b_key="any_attack_asr",
    )

    print_summary_table(summary["rows"])
    print(f"\nclean ASR                    : {summary['baseline_asr']:.1%}")
    print(f"per_query_poison ASR         : {summary['per_query_poison_asr']:.1%}")
    print(f"shared_one_poison ASR        : {summary['shared_one_poison_asr']:.1%}")
    print(f"shared_paper ASR             : {summary['shared_paper_asr']:.1%}")
    print(f"shared_scaled ASR            : {summary['shared_scaled_asr']:.1%}")
    print(f"any attack ASR               : {summary['any_attack_asr']:.1%}")
    print(f"shared beats per-query       : {summary['shared_beats_perquery_rate']:.1%}")
    print(f"cross-query both success     : {summary['cross_query_both_success_rate']:.1%}")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
