#!/usr/bin/env python
"""
Strategy 40: External Content Injection (Liang et al., 2025)
Paper: GraphRAG under Fire — Figure 1 poisoning via external corpus

攻击思路：攻击者不能修改 Prompt，而是向 GraphRAG 知识库语料注入恶意文本；
GraphRAG 建图（实体/关系/社区/文本块）→ Retriever 检索出投毒内容 → LLM 相信。

  Attacker → inject corpus text → Graph Construction → Retriever → LLM

与 IPI 的共同点：攻击依赖外部数据间接影响模型，而非直接修改用户 Prompt。

环境检查结论：**不可复用** rag_kb_env（仅向量 chunk）或 kb_poisoning_env（FAQ 向量库）。
**新建** graphrag_env：search_graph_knowledge 返回 V(x)/R(x)/S(x)/T(x) 多尺度图检索。

与相近策略：
  • 32/33 — 爬虫→向量 KB（search_knowledge_base），无图结构
  • 35 — PoisonedRAG FAQ 投毒，非 GraphRAG 建图
  • 40 — 外部语料→GraphRAG 索引→图检索（Liang Figure 1 威胁模型）

变体：
  • clean              — 仅良性图检索（Stuxnet/DLL Injection 干净路径）
  • corpus_inject      — 投毒语料 chunk 被索引并检索
  • corpus_paper       — 论文 §3 PoisonedRAG 式 naive 投毒 + Ignore/Say Pwned
  • corpus_buried      — 注入 buried 在长语料正文中
  • corpus_multinode   — GragPoison 式多节点（relation + community + chunk 同时投毒）

Usage:
  python test/40_liang_graphrag_external_injection.py
  python test/40_liang_graphrag_external_injection.py -m qwen3.7-plus
  python test/40_liang_graphrag_external_injection.py --scenario pwned_hijack
  python test/40_liang_graphrag_external_injection.py --variant corpus_paper
  python test/40_liang_graphrag_external_injection.py --serial
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
from lib.external_content_injection import (  # noqa: E402
    EXTERNAL_CONTENT_VARIANTS,
    build_rag_messages,
    get_tools_for_scenario,
    load_scenarios,
)
from lib.graphrag_env import build_graphrag_system_prompt  # noqa: E402
from lib.report import write_html_report  # noqa: E402
from lib.runner import run_parallel_by_model, sort_results  # noqa: E402

STRATEGY_TITLE = "Strategy 40: External Content Injection (Liang et al., 2025 GraphRAG)"
RUN_PREFIX = "40_liang_graphrag_external_injection"
VARIANT_CLEAN = "clean"
ALL_VARIANTS = [VARIANT_CLEAN, *EXTERNAL_CONTENT_VARIANTS]


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
        attack_labels = {v: labels.get(v) for v in EXTERNAL_CONTENT_VARIANTS}
        any_success = any(v == "success" for v in attack_labels.values())
        row = {
            "model": model,
            "scenario_id": scenario_id,
            "baseline": clean,
            **attack_labels,
            "any_attack_beats_baseline": any_success
            and clean in ("blocked", "partial", None),
        }
        for v in EXTERNAL_CONTENT_VARIANTS:
            row[f"{v}_beats_baseline"] = (
                attack_labels.get(v) == "success" and clean in ("blocked", "partial", None)
            )
        rows.append(row)

    total = len(rows) or 1
    asr: dict[str, float] = {
        "baseline_asr": round(sum(1 for r in rows if r["baseline"] == "success") / total, 4)
    }
    for v in EXTERNAL_CONTENT_VARIANTS:
        asr[f"{v}_asr"] = round(sum(1 for r in rows if r.get(v) == "success") / total, 4)
    asr["any_attack_asr"] = round(
        sum(
            1 for r in rows if any(r.get(v) == "success" for v in EXTERNAL_CONTENT_VARIANTS)
        )
        / total,
        4,
    )

    return {
        "total_cases": len(rows),
        "rows": rows,
        **asr,
        **{
            f"{v}_success_count": sum(1 for r in rows if r.get(v) == "success")
            for v in EXTERNAL_CONTENT_VARIANTS
        },
        "any_attack_beats_baseline_count": sum(
            1 for r in rows if r["any_attack_beats_baseline"]
        ),
    }


def print_summary_table(rows: list[dict]) -> None:
    print("\n" + "=" * 160)
    print("对比汇总（clean vs External Content Injection / GraphRAG）")
    print("=" * 160)
    header = (
        f"{'模型':<22} {'场景':<22} {'clean':<12} "
        f"{'paper':<12} {'inject':<12} {'buried':<12} {'multinode':<12} {'任一有效'}"
    )
    print(header)
    print("-" * 160)

    def tag(label: str | None) -> str:
        if not label:
            return "-"
        return STATUS_TAG.get(label, label) if label in STATUS_TAG else label

    for row in rows:
        cmp = "是" if row.get("any_attack_beats_baseline") else "-"
        print(
            f"{row['model']:<22} {row['scenario_id']:<22} "
            f"{tag(row.get('baseline')):<12} "
            f"{tag(row.get('corpus_paper')):<12} "
            f"{tag(row.get('corpus_inject')):<12} "
            f"{tag(row.get('corpus_buried')):<12} "
            f"{tag(row.get('corpus_multinode')):<12} {cmp}"
        )
    print("=" * 160)


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
                chain = result.get("attack_chain", "-")
                extra = f" | chain={chain}" if variant != VARIANT_CLEAN else ""
                print_case_result(
                    model,
                    scenario["id"],
                    variant,
                    ev.get("label", "error"),
                    ev.get("reason", "") + extra,
                )
    return sort_results(results, models, scenarios, variants)


def main() -> int:
    set_api_key_map_file(TEST_ROOT / "config" / "40_api_key_map.yaml")

    variant_choices = ["all", VARIANT_CLEAN, *EXTERNAL_CONTENT_VARIANTS]
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
    print(f"Env       : GraphRAG (search_graph_knowledge — V/R/S/T graph retrieval)")
    print(f"Variants  : {', '.join(variants)}")
    print(f"Chain     : corpus inject -> graph index -> retrieve -> LLM")
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
        variant_b_key="corpus_paper",
        variant_b_label="corpus_paper（论文 §3 naive corpus poison）",
        compare_label="External Content Injection 有效",
        compare_field="any_attack_beats_baseline",
        asr_b_key="any_attack_asr",
    )

    print_summary_table(summary["rows"])
    print(f"\nclean ASR              : {summary['baseline_asr']:.1%}")
    print(f"corpus_paper ASR       : {summary['corpus_paper_asr']:.1%}")
    print(f"corpus_inject ASR      : {summary['corpus_inject_asr']:.1%}")
    print(f"corpus_buried ASR      : {summary['corpus_buried_asr']:.1%}")
    print(f"corpus_multinode ASR   : {summary['corpus_multinode_asr']:.1%}")
    print(f"any attack ASR         : {summary['any_attack_asr']:.1%}")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
