#!/usr/bin/env python
"""
Strategy 19: Few-query Jailbreak — Black-box Query Efficiency (Liu et al. 2024 DRA)

论文：Liu et al., 2024 — Making Them Ask and Answer (USENIX Security 24 / arXiv:2402.18104)
Algorithm 1 · §5.4 Attack Efficiency

DRA 最大特点之一：**Few-query Black-box Jailbreak**
  • GPT-4 平均 ≈2–4 次 Query 即可成功（论文 §5.4 Table 7）
  • Vicuna 初始化模板平均 ≈1.3 次
  • 对比 GCG/PAIR 等需数十至上千次 Query

环境选择：**多轮 API + early stop**（复用 `dynamic_obfuscation_env.py`）
本策略以 **query budget** 为轴，而非 S18 的 ratio 动态机制 ablation。

与 S18 区分：
  • S18 — updateParam 动态混淆（默认 max 10）
  • S19 — **查询次数预算**（1 / 4 / 20）+ 平均 Query 统计

变体（默认全部）：
  • direct               — 明文 1 query（基线）
  • few_query_single     — 1 query · 全量 DRA 初始化模板
  • few_query_4          — ≤4 queries · updateParam（论文 GPT-4 均值）
  • few_query_4_static   — ≤4 queries · 固定 ratio（无动态 · 对照）
  • few_query_20         — ≤20 queries · updateParam（论文 Tmax）

Usage:
  python test/jailbreak/19_few_query_jailbreak.py
  python test/jailbreak/19_few_query_jailbreak.py -m qwen3.7-plus
  python test/jailbreak/19_few_query_jailbreak.py --scenario lock_picking
  python test/jailbreak/19_few_query_jailbreak.py --variant few_query_4
  python test/jailbreak/19_few_query_jailbreak.py --serial
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve()
SCRIPT_STEM = SCRIPT_PATH.stem
JB_ROOT = SCRIPT_PATH.parent
IPI_TEST_ROOT = JB_ROOT.parent
sys.path.insert(0, str(IPI_TEST_ROOT))
sys.path.insert(0, str(JB_ROOT))

from jblib.common import (  # noqa: E402
    DEFAULT_SYSTEM_PROMPT,
    ensure_results_dir,
    report_html_path,
    save_json,
    utc_timestamp,
)
from jblib.dynamic_obfuscation_env import run_dynamic_obfuscation_loop  # noqa: E402
from jblib.few_query import ALL_VARIANTS, VARIANT_LABELS, get_few_query_config  # noqa: E402
from jblib.few_query_summary import summarize_few_query  # noqa: E402
from jblib.multi_variant_report import write_multi_variant_report  # noqa: E402
from jblib.multiturn_env import run_direct_turn  # noqa: E402
from jblib.scenarios import load_scenarios  # noqa: E402
from lib.api_keys import set_api_key_map_file, validate_model_keys  # noqa: E402
from lib.common import get_client, resolve_target_models  # noqa: E402
from lib.console import print_case_result  # noqa: E402
from lib.runner import run_parallel_by_model, sort_results  # noqa: E402

STRATEGY_TITLE = "Strategy 19: Few-query Jailbreak — Black-box Efficiency (Liu et al. 2024 DRA)"
RUN_PREFIX = "19_few_query"


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    system_prompt = DEFAULT_SYSTEM_PROMPT
    label = f"{model} | {scenario['id']} | {variant}"

    base = {
        "model": model,
        "scenario_id": scenario["id"],
        "scenario_name": scenario["name"],
        "category": scenario.get("category", ""),
        "variant": variant,
        "harmful_request": scenario["harmful_request"].strip(),
        "system_prompt": system_prompt,
    }

    try:
        if variant == "direct":
            outcome = run_direct_turn(
                client,
                model=model,
                scenario=scenario,
                system_prompt=system_prompt,
                label=label,
            )
            outcome["query_count"] = 1
            outcome["query_budget"] = 1
            outcome["success_within_budget"] = outcome.get("evaluation", {}).get("label") == "success"
            outcome["adapt_ratios"] = False
            return {**base, **outcome, "user_prompt": outcome.get("user_prompt", ""), "tool_calls": []}

        cfg = get_few_query_config(variant)
        outcome = run_dynamic_obfuscation_loop(
            client,
            model=model,
            scenario=scenario,
            system_prompt=system_prompt,
            label=label,
            include_puzzle=True,
            max_attempts=cfg["max_attempts"],
            adapt_ratios=cfg["adapt_ratios"],
        )
        return {
            **base,
            **outcome,
            "user_prompt": outcome.get("user_prompt", ""),
            "tool_calls": [],
            "harmful_request_original": outcome.get("harmful_request_original", base["harmful_request"]),
        }
    except Exception as exc:
        return {
            **base,
            "messages": [],
            "response": "",
            "user_prompt": "",
            "turn_log": [],
            "api_turn_count": 0,
            "query_count": 0,
            "error": str(exc),
            "evaluation": {"label": "error", "reason": str(exc)},
        }


def run_serial(models: list[str], scenarios: list[dict], variants: list[str]) -> list[dict]:
    client = get_client()
    results: list[dict] = []
    total = len(models) * len(scenarios) * len(variants)
    n = 0
    for model in models:
        for scenario in scenarios:
            for variant in variants:
                n += 1
                print(f"  [{n}/{total}] 等待 API: {model} | {scenario['id']} | {variant} ...", flush=True)
                result = run_case(client, model, scenario, variant)
                results.append(result)
                ev = result.get("evaluation", {})
                q = result.get("query_count", 1)
                print_case_result(
                    model,
                    scenario["id"],
                    variant,
                    ev.get("label", "error"),
                    f"{ev.get('reason', '')} queries={q}",
                )
    return sort_results(results, models, scenarios, variants)


def print_few_query_summary(summary: dict) -> None:
    variants = summary.get("variants") or ALL_VARIANTS
    variant_asr = summary.get("variant_asr") or {}
    query_stats = summary.get("query_stats") or {}
    print("\n" + "=" * 78)
    print("Few-query ASR 汇总（success）")
    print("=" * 78)
    for v in variants:
        tag = VARIANT_LABELS.get(v, v)
        asr = variant_asr.get(v, 0)
        qs = query_stats.get(v, {})
        avg = qs.get("avg_queries_on_success", 0)
        avg_all = qs.get("avg_queries_all", 0)
        extra = ""
        if avg > 0:
            extra = f"  avgQ(success)={avg:.1f}"
        elif avg_all > 0 and v != "direct":
            extra = f"  avgQ={avg_all:.1f}"
        beat = ""
        if v != "direct" and summary.get("beats_baseline", {}).get(v):
            beat = " ↑ vs direct"
        print(f"  {tag:<44} {asr:>6.1%}{beat}{extra}")
    print("=" * 78)


def main() -> int:
    parser = argparse.ArgumentParser(description=STRATEGY_TITLE)
    parser.add_argument("-m", "--model", help="单个模型；默认全部 7 个")
    parser.add_argument("--scenario", help="phishing_email / lock_picking / keylogger_code / sql_injection")
    parser.add_argument(
        "--variant",
        choices=[*ALL_VARIANTS, "all"],
        default="all",
        help="默认 all = direct + 四种 few-query 预算",
    )
    parser.add_argument("--serial", action="store_true", help="串行模式（单 Key，调试用）")
    args = parser.parse_args()

    set_api_key_map_file(JB_ROOT / "config" / "19_api_key_map.yaml")

    models = resolve_target_models(args.model)
    scenarios = load_scenarios("19_scenarios")
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
    print(f"Variants  : {', '.join(variants)}")
    print(f"Mode      : {'7路并行 (每模型专用Key)' if parallel else '串行'}")
    print(f"Cases     : {total}")
    print(f"HTML      : {html_path}")
    print("-" * 78)

    if parallel:
        results = run_parallel_by_model(models, scenarios, variants, run_case)
    else:
        results = run_serial(models, scenarios, variants)

    summary = summarize_few_query(results, variants, baseline="direct")
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    save_json(out_dir / "results.json", results)
    save_json(out_dir / "summary.json", summary)
    write_multi_variant_report(
        SCRIPT_STEM,
        results,
        summary,
        title=STRATEGY_TITLE,
        variants=variants,
        run_dir=out_dir,
        generated_at=generated_at,
        variant_labels=VARIANT_LABELS,
    )

    print_few_query_summary(summary)
    print(f"\nJSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
