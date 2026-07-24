#!/usr/bin/env python
"""
Strategy 2: Persuasion Jailbreak (四种说服攻击)

论文：Chang et al., 2026 — ChatInject (arXiv:2509.22830, ICLR 2026)
论文专门实验四种 Persuasion 单轮说服包装，发现 Time Pressure 与 Social Punishment 效果最好。

环境选择：**单轮 direct chat**（`jblib/multiturn_env.run_direct_turn` + `jblib/persuasion.py`）
  在 harmful_request 外包装说服话术，一次 API 调用。

与 Strategy 1 区分：
  • Strategy 1 — 真实多轮 Foot-in-the-door（多 API 轮次建立信任）
  • Strategy 2 — 单轮四种说服技巧（时间压力 / 社会惩罚 / 权威 / 协商）

与 IPI 区分：有害请求在 user message，不经 tool output。

变体（默认全部运行）：
  • direct            — 有害请求原样（基线）
  • time_pressure     — ① 时间压力（马上完成，否则账户冻结）
  • social_punishment — ② 社会惩罚（管理员要求，否则影响团队）
  • authority         — ③ 权威授权（系统管理员授权）
  • negotiation       — ④ 协商（先完成正常任务，再执行第二项）

Usage:
  python test/jailbreak/2_persuasion_jailbreak.py
  python test/jailbreak/2_persuasion_jailbreak.py -m qwen3.7-plus
  python test/jailbreak/2_persuasion_jailbreak.py --scenario phishing_email
  python test/jailbreak/2_persuasion_jailbreak.py --variant time_pressure
  python test/jailbreak/2_persuasion_jailbreak.py --serial
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
from jblib.multi_variant_report import write_multi_variant_report  # noqa: E402
from jblib.multi_variant_summary import summarize_variants  # noqa: E402
from jblib.multiturn_env import run_direct_turn  # noqa: E402
from jblib.persuasion import ALL_VARIANTS, PERSUASION_VARIANTS, VARIANT_LABELS, build_persuasion_prompt  # noqa: E402
from jblib.scenarios import load_scenarios  # noqa: E402
from lib.api_keys import set_api_key_map_file, validate_model_keys  # noqa: E402
from lib.common import get_client, resolve_target_models  # noqa: E402
from lib.console import print_case_result  # noqa: E402
from lib.runner import run_parallel_by_model, sort_results  # noqa: E402

STRATEGY_TITLE = "Strategy 2: Persuasion Jailbreak"
RUN_PREFIX = "2_persuasion"


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    system_prompt = DEFAULT_SYSTEM_PROMPT
    user_prompt = build_persuasion_prompt(scenario, variant)
    label = f"{model} | {scenario['id']} | {variant}"
    base = {
        "model": model,
        "scenario_id": scenario["id"],
        "scenario_name": scenario["name"],
        "category": scenario.get("category", ""),
        "variant": variant,
        "persuasion_type": variant,
        "harmful_request": scenario["harmful_request"].strip(),
        "system_prompt": system_prompt,
    }

    try:
        outcome = run_direct_turn(
            client,
            model=model,
            scenario=scenario,
            system_prompt=system_prompt,
            label=label,
            user_content=user_prompt,
        )
        return {**base, **outcome, "tool_calls": []}
    except Exception as exc:
        return {
            **base,
            "messages": [],
            "response": "",
            "user_prompt": user_prompt,
            "turn_log": [],
            "api_turn_count": 0,
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
                print_case_result(model, scenario["id"], variant, ev.get("label", "error"), ev.get("reason", ""))
    return sort_results(results, models, scenarios, variants)


def print_persuasion_summary(summary: dict) -> None:
    variants = summary.get("variants") or ALL_VARIANTS
    variant_asr = summary.get("variant_asr") or {}
    print("\n" + "=" * 78)
    print("Persuasion ASR 汇总（success）")
    print("=" * 78)
    for v in variants:
        tag = VARIANT_LABELS.get(v, v)
        asr = variant_asr.get(v, 0)
        beat = ""
        if v != "direct" and summary.get("beats_baseline", {}).get(v):
            beat = " ↑ vs direct"
        print(f"  {tag:<40} {asr:>6.1%}{beat}")
    best = summary.get("best_variant", "")
    if best and best != "direct":
        print(f"\n最佳变体: {VARIANT_LABELS.get(best, best)}")
    print("=" * 78)


def main() -> int:
    parser = argparse.ArgumentParser(description=STRATEGY_TITLE)
    parser.add_argument("-m", "--model", help="单个模型；默认全部 7 个")
    parser.add_argument("--scenario", help="phishing_email / lock_picking / keylogger_code / sql_injection")
    parser.add_argument(
        "--variant",
        choices=[*ALL_VARIANTS, "all"],
        default="all",
        help="默认 all = 5 种变体全部运行",
    )
    parser.add_argument("--serial", action="store_true", help="串行模式（单 Key，调试用）")
    args = parser.parse_args()

    set_api_key_map_file(JB_ROOT / "config" / "2_api_key_map.yaml")

    models = resolve_target_models(args.model)
    scenarios = load_scenarios("2_scenarios")
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

    summary = summarize_variants(results, variants, baseline="direct")
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

    print_persuasion_summary(summary)
    print(f"\nJSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
