#!/usr/bin/env python
"""
Strategy 43: Relation-based Injection (Liang et al., 2025 / GragPoison)
Paper: GraphRAG under Fire — §4.1–4.3 core contribution

攻击思路：GragPoison 核心 — 不是攻击 Question→Answer，而是攻击图关系：
  Stuxnet --uses--> DLL Injection  →  替换为  Stuxnet --uses--> Process Hollowing
一条被污染的关系 r* 可同时影响所有依赖原关系 r 的查询 X_r（Figure 4）。

  传统 PoisonedRAG：Question → Wrong Answer（GraphRAG 上效果差）
  GragPoison：      Entity → Relation → Entity（知识层注入，非传统 IPI）

环境检查结论：**复用** graphrag_env（Strategy 40）— GraphRAG 图检索已含 V/R/S/T。
**新建** relation_based_injection.py — 专注 relation-level vs answer-level 对比。

与相近策略：
  • 40 — External Content（语料投毒入口）
  • 41 — Narrative（文本包装）
  • 42 — Semantic（替换实体的语义类别）
  • 43 — Relation-based（GragPoison 核心：攻击图关系三元组 + 共享关系多查询）

变体：
  • clean             — 良性 Stuxnet --uses--> DLL Injection 多跳路径
  • answer_poison     — PoisonedRAG 对照（Question→Wrong Answer）
  • relation_inject   — GragPoison 关系注入 r*（Figure 4b）
  • relation_shared   — 共享关系 X_r 多查询覆盖（Figure 4 + §4.1）
  • relation_enhanced — r* + d_r+ 关系增强（Figure 4c + §4.3）

Usage:
  python test/43_liang_graphrag_relation_injection.py
  python test/43_liang_graphrag_relation_injection.py -m qwen3.7-plus
  python test/43_liang_graphrag_relation_injection.py --scenario pwned_hijack
  python test/43_liang_graphrag_relation_injection.py --variant relation_shared
  python test/43_liang_graphrag_relation_injection.py --serial
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
from lib.relation_based_injection import (  # noqa: E402
    RELATION_VARIANTS,
    build_rag_messages,
    get_tools_for_scenario,
    load_scenarios,
)
from lib.report import write_html_report  # noqa: E402
from lib.runner import run_parallel_by_model, sort_results  # noqa: E402

STRATEGY_TITLE = "Strategy 43: Relation-based Injection (Liang et al., 2025 GragPoison)"
RUN_PREFIX = "43_liang_graphrag_relation_injection"
VARIANT_CLEAN = "clean"
ALL_VARIANTS = [VARIANT_CLEAN, *RELATION_VARIANTS]
GRAGPOISON_VARIANTS = ("relation_inject", "relation_shared", "relation_enhanced")


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    messages, graph_meta = build_rag_messages(scenario, variant)
    tools = get_tools_for_scenario(scenario)
    system_prompt = build_graphrag_system_prompt(scenario)
    tool_output = messages[-1]["content"] if messages else ""

    base = {
        "model": model,
        "scenario_id": scenario["id"],
        "scenario_name": scenario["name"],
        "variant": variant,
        "template_family": variant if variant != VARIANT_CLEAN else "clean",
        "graph_meta": graph_meta,
        "attack_chain": " → ".join(graph_meta.get("attack_chain", [])),
        "attack_surface": graph_meta.get("attack_surface"),
        "attacker_instruction": scenario.get("corpus_injection_message", ""),
        "user_instruction": scenario["user_instruction"],
        "injected_relation": graph_meta.get("injected_relation"),
        "shared_queries_count": graph_meta.get("shared_queries_count"),
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
        "evaluation": evaluate_response(content, scenario, tool_calls=tool_calls),
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
        attack_labels = {v: labels.get(v) for v in RELATION_VARIANTS}
        any_grag_success = any(
            labels.get(v) == "success" for v in GRAGPOISON_VARIANTS
        )
        answer = labels.get("answer_poison")
        row = {
            "model": model,
            "scenario_id": scenario_id,
            "baseline": clean,
            **attack_labels,
            "relation_beats_answer": any_grag_success
            and answer in ("blocked", "partial", None),
            "any_attack_beats_baseline": any(
                labels.get(v) == "success" for v in RELATION_VARIANTS
            )
            and clean in ("blocked", "partial", None),
        }
        rows.append(row)

    total = len(rows) or 1
    asr: dict[str, float] = {
        "baseline_asr": round(sum(1 for r in rows if r["baseline"] == "success") / total, 4)
    }
    for v in RELATION_VARIANTS:
        asr[f"{v}_asr"] = round(sum(1 for r in rows if r.get(v) == "success") / total, 4)
    asr["any_attack_asr"] = round(
        sum(
            1 for r in rows if any(r.get(v) == "success" for v in RELATION_VARIANTS)
        )
        / total,
        4,
    )
    asr["relation_beats_answer_rate"] = round(
        sum(1 for r in rows if r.get("relation_beats_answer")) / total, 4
    )

    return {
        "total_cases": len(rows),
        "rows": rows,
        **asr,
        **{
            f"{v}_success_count": sum(1 for r in rows if r.get(v) == "success")
            for v in RELATION_VARIANTS
        },
        "relation_beats_answer_count": sum(
            1 for r in rows if r.get("relation_beats_answer")
        ),
        "any_attack_beats_baseline_count": sum(
            1 for r in rows if r.get("any_attack_beats_baseline")
        ),
    }


def print_summary_table(rows: list[dict]) -> None:
    print("\n" + "=" * 168)
    print("对比汇总（clean vs Relation-based — answer-level vs relation-level GragPoison）")
    print("=" * 168)
    header = (
        f"{'模型':<22} {'场景':<22} {'clean':<12} "
        f"{'answer':<12} {'inject':<12} {'shared':<12} {'enhanced':<12} {'关系>答案'}"
    )
    print(header)
    print("-" * 168)

    def tag(label: str | None) -> str:
        if not label:
            return "-"
        return STATUS_TAG.get(label, label) if label in STATUS_TAG else label

    for row in rows:
        cmp = "是" if row.get("relation_beats_answer") else "-"
        print(
            f"{row['model']:<22} {row['scenario_id']:<22} "
            f"{tag(row.get('baseline')):<12} "
            f"{tag(row.get('answer_poison')):<12} "
            f"{tag(row.get('relation_inject')):<12} "
            f"{tag(row.get('relation_shared')):<12} "
            f"{tag(row.get('relation_enhanced')):<12} {cmp}"
        )
    print("=" * 168)


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
                surface = result.get("attack_surface") or "-"
                extra = f" | surface={surface}" if variant != VARIANT_CLEAN else ""
                print_case_result(
                    model,
                    scenario["id"],
                    variant,
                    ev.get("label", "error"),
                    ev.get("reason", "") + extra,
                )
    return sort_results(results, models, scenarios, variants)


def main() -> int:
    set_api_key_map_file(TEST_ROOT / "config" / "43_api_key_map.yaml")

    variant_choices = ["all", VARIANT_CLEAN, *RELATION_VARIANTS]
    parser = argparse.ArgumentParser(description=STRATEGY_TITLE)
    parser.add_argument("-m", "--model", help="单个模型；默认全部 7 个")
    parser.add_argument(
        "--scenario",
        help="pwned_hijack / password_change / send_link / exfiltrate_data / system_prompt_leak",
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
    print(f"Chain     : relation r* inject -> graph index -> shared X_r retrieve -> LLM")
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
        variant_b_key="relation_shared",
        variant_b_label="relation_shared（GragPoison 共享关系 X_r，Figure 4）",
        compare_label="Relation-based Injection 有效",
        compare_field="any_attack_beats_baseline",
        asr_b_key="any_attack_asr",
    )

    print_summary_table(summary["rows"])
    print(f"\nclean ASR                 : {summary['baseline_asr']:.1%}")
    print(f"answer_poison ASR         : {summary['answer_poison_asr']:.1%}")
    print(f"relation_inject ASR       : {summary['relation_inject_asr']:.1%}")
    print(f"relation_shared ASR       : {summary['relation_shared_asr']:.1%}")
    print(f"relation_enhanced ASR     : {summary['relation_enhanced_asr']:.1%}")
    print(f"any attack ASR            : {summary['any_attack_asr']:.1%}")
    print(f"relation beats answer     : {summary['relation_beats_answer_rate']:.1%}")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
