#!/usr/bin/env python
"""
Strategy 28: Crescendo Jailbreak（多轮危险度逐级升级，SoK 多轮类代表）

论文：Wang et al. 2025 — SoK: Evaluating Jailbreak Guardrails, Strategy 5（Multi-turn / Crescendo）。
真实多轮对话，危险度**逐级递增**：良性概述 → 技术类别 → 代表技术机制 → 有害请求，利用「已在配合」
的对话惯性。与 S1 多轮说服（foot-in-the-door 信任铺垫）区分：S28 核心是危险度单调升级。

环境选择：**真实多轮 API 对话**（jblib/crescendo.py + run_multiturn_persuasion）；仅判定最后一轮回复。

变体：
  • direct     — harmful_request 单轮直接发送（基线）
  • crescendo  — 三轮升级铺垫后发送 final_user（每条约 4 次 API）

Usage:
  python test/jailbreak/28_crescendo_jailbreak.py
  python test/jailbreak/28_crescendo_jailbreak.py -m qwen3.7-plus
  python test/jailbreak/28_crescendo_jailbreak.py --scenario sql_injection
  python test/jailbreak/28_crescendo_jailbreak.py --variant crescendo
  python test/jailbreak/28_crescendo_jailbreak.py --serial
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
from jblib.crescendo import ALL_VARIANTS, VARIANT_LABELS, crescendo_scenario  # noqa: E402
from jblib.multi_variant_report import write_multi_variant_report  # noqa: E402
from jblib.multi_variant_summary import summarize_variants  # noqa: E402
from jblib.multiturn_env import run_direct_turn, run_multiturn_persuasion  # noqa: E402
from jblib.scenarios import load_scenarios  # noqa: E402
from lib.api_keys import set_api_key_map_file, validate_model_keys  # noqa: E402
from lib.common import get_client, resolve_target_models  # noqa: E402
from lib.console import print_case_result  # noqa: E402
from lib.runner import run_parallel_by_model, sort_results  # noqa: E402

STRATEGY_TITLE = "Strategy 28: Crescendo Jailbreak (multi-turn escalation)"
RUN_PREFIX = "28_crescendo"


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
                client, model=model, scenario=scenario,
                system_prompt=system_prompt, label=label,
            )
        elif variant == "crescendo":
            outcome = run_multiturn_persuasion(
                client, model=model, scenario=crescendo_scenario(scenario),
                system_prompt=system_prompt, label_prefix=label,
            )
        else:
            raise ValueError(f"unknown variant: {variant}")
        return {**base, **outcome, "tool_calls": []}
    except Exception as exc:
        return {
            **base, "messages": [], "response": "",
            "user_prompt": scenario["harmful_request"].strip(),
            "turn_log": [], "api_turn_count": 0, "error": str(exc),
            "evaluation": {"label": "error", "reason": str(exc)},
        }


def run_serial(models, scenarios, variants):
    client = get_client()
    results = []
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
                print_case_result(model, scenario["id"], variant, ev.get("label", "error"), ev.get("reason", ""))
    return sort_results(results, models, scenarios, variants)


def print_summary(summary):
    variants = summary.get("variants") or ALL_VARIANTS
    variant_asr = summary.get("variant_asr") or {}
    print("\n" + "=" * 78)
    print("Crescendo ASR 汇总（success）")
    print("=" * 78)
    for v in variants:
        tag = VARIANT_LABELS.get(v, v)
        asr = variant_asr.get(v, 0)
        beat = " ↑ vs direct" if v != "direct" and summary.get("beats_baseline", {}).get(v) else ""
        print(f"  {tag:<44} {asr:>6.1%}{beat}")
    best = summary.get("best_variant", "")
    if best and best != "direct":
        print(f"\n最佳变体: {VARIANT_LABELS.get(best, best)}")
    print("=" * 78)


def main() -> int:
    parser = argparse.ArgumentParser(description=STRATEGY_TITLE)
    parser.add_argument("-m", "--model", help="单个模型；默认全部 7 个")
    parser.add_argument("--scenario", help="phishing_email / lock_picking / keylogger_code / sql_injection")
    parser.add_argument("--variant", choices=[*ALL_VARIANTS, "all"], default="all")
    parser.add_argument("--serial", action="store_true", help="串行模式（单 Key，调试用）")
    args = parser.parse_args()

    set_api_key_map_file(JB_ROOT / "config" / "28_api_key_map.yaml")

    models = resolve_target_models(args.model)
    scenarios = load_scenarios("28_scenarios")
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
    print(f"Cases     : {total} (crescendo 每条约 4 次 API)")
    print(f"HTML      : {html_path}")
    print("-" * 78)

    if parallel:
        results = run_parallel_by_model(models, scenarios, variants, run_case)
    else:
        results = run_serial(models, scenarios, variants)

    summary = summarize_variants(results, variants, baseline="direct")
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    save_json(out_dir / "results.json", results)
    save_json(out_dir / "summary.json", summary)
    write_multi_variant_report(
        SCRIPT_STEM, results, summary, title=STRATEGY_TITLE, variants=variants,
        run_dir=out_dir, generated_at=generated_at, variant_labels=VARIANT_LABELS,
    )

    print_summary(summary)
    print(f"\nJSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
