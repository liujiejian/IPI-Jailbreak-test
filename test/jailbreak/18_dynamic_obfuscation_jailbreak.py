#!/usr/bin/env python
"""
Strategy 18: Dynamic Obfuscation Jailbreak (Liu et al. 2024 DRA Algorithm 2)

论文：Liu et al., 2024 — Making Them Ask and Answer (USENIX Security 24 / arXiv:2402.18104)
Algorithm 1–2 · updateParam(toxicRatio, benignRatio)

DRA 不是固定切词比例，而是根据模型反馈动态调整：
  • 越狱失败（GCG 拒答前缀）→ toxicRatio -= 0.1（toxic 词切更多）
  • 恢复失败（em 词重叠不足）→ benignRatio += 0.1（benign 词保留更多）

环境选择：**多轮 API 反馈循环**（`jblib/dynamic_obfuscation_env.py`）
单条用例最多 dynamic_max_attempts 次 API（默认 10；论文 T=20）。

与 S15 区分：
  • S15 — 固定 toxic/benign ratio 单次 word split
  • S18 — **动态 updateParam 循环**直至成功或耗尽尝试

变体（默认全部）：
  • direct                — 明文 harmful_request（基线）
  • static_control        — ratio=0.5 单次全量 DRA prompt（无动态 · 对照）
  • dynamic_obfuscation   — Puzzle + Recon + updateParam 循环
  • dynamic_split_only    — Split + Recon + updateParam（无 Puzzle）

Usage:
  python test/jailbreak/18_dynamic_obfuscation_jailbreak.py
  python test/jailbreak/18_dynamic_obfuscation_jailbreak.py -m qwen3.7-plus
  python test/jailbreak/18_dynamic_obfuscation_jailbreak.py --scenario lock_picking
  python test/jailbreak/18_dynamic_obfuscation_jailbreak.py --variant dynamic_obfuscation
  python test/jailbreak/18_dynamic_obfuscation_jailbreak.py --max-attempts 5 --serial
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
from jblib.dynamic_obfuscation import (  # noqa: E402
    ALL_VARIANTS,
    VARIANT_LABELS,
    build_static_control_prompt,
)
from jblib.dynamic_obfuscation_env import run_dynamic_obfuscation_loop  # noqa: E402
from jblib.multi_variant_report import write_multi_variant_report  # noqa: E402
from jblib.multi_variant_summary import summarize_variants  # noqa: E402
from jblib.multiturn_env import run_direct_turn  # noqa: E402
from jblib.scenarios import load_scenarios  # noqa: E402
from lib.api_keys import set_api_key_map_file, validate_model_keys  # noqa: E402
from lib.common import get_client, resolve_target_models  # noqa: E402
from lib.console import print_case_result  # noqa: E402
from lib.runner import run_parallel_by_model, sort_results  # noqa: E402

STRATEGY_TITLE = "Strategy 18: Dynamic Obfuscation Jailbreak (Liu et al. 2024 DRA)"
RUN_PREFIX = "18_dynamic_obfuscation"


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
            payload = {"harmful_request_original": scenario["harmful_request"].strip()}
        elif variant == "static_control":
            payload = build_static_control_prompt(scenario)
            outcome = run_direct_turn(
                client,
                model=model,
                scenario=scenario,
                system_prompt=system_prompt,
                label=label,
                user_content=payload["user_prompt"],
            )
            outcome["api_turn_count"] = 1
            outcome["dynamic_attempts"] = 1
            outcome["dynamic_success_step"] = 1 if outcome.get("evaluation", {}).get("label") == "success" else 0
            outcome["final_toxic_ratio"] = payload.get("toxic_ratio")
            outcome["final_benign_ratio"] = payload.get("benign_ratio")
        elif variant == "dynamic_obfuscation":
            outcome = run_dynamic_obfuscation_loop(
                client,
                model=model,
                scenario=scenario,
                system_prompt=system_prompt,
                label=label,
                include_puzzle=True,
            )
            payload = outcome
        elif variant == "dynamic_split_only":
            outcome = run_dynamic_obfuscation_loop(
                client,
                model=model,
                scenario=scenario,
                system_prompt=system_prompt,
                label=label,
                include_puzzle=False,
            )
            payload = outcome
        else:
            raise ValueError(f"unknown dynamic obfuscation variant: {variant}")

        user_prompt = outcome.get("user_prompt") or payload.get("user_prompt", "")
        return {
            **base,
            **payload,
            **outcome,
            "user_prompt": user_prompt,
            "tool_calls": [],
            "harmful_request_original": payload.get("harmful_request_original", base["harmful_request"]),
        }
    except Exception as exc:
        return {
            **base,
            "messages": [],
            "response": "",
            "user_prompt": "",
            "turn_log": [],
            "api_turn_count": 0,
            "dynamic_attempts": 0,
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
                attempts = result.get("dynamic_attempts", result.get("api_turn_count", 1))
                extra = f" attempts={attempts}" if variant.startswith("dynamic") or variant == "static_control" else ""
                print_case_result(
                    model,
                    scenario["id"],
                    variant,
                    ev.get("label", "error"),
                    (ev.get("reason", "") or "") + extra,
                )
    return sort_results(results, models, scenarios, variants)


def print_dynamic_summary(summary: dict) -> None:
    variants = summary.get("variants") or ALL_VARIANTS
    variant_asr = summary.get("variant_asr") or {}
    print("\n" + "=" * 78)
    print("Dynamic Obfuscation ASR 汇总（success）")
    print("=" * 78)
    for v in variants:
        tag = VARIANT_LABELS.get(v, v)
        asr = variant_asr.get(v, 0)
        beat = ""
        if v != "direct" and summary.get("beats_baseline", {}).get(v):
            beat = " ↑ vs direct"
        print(f"  {tag:<44} {asr:>6.1%}{beat}")
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
        help="默认 all = direct + static + 两种 dynamic",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=0,
        help="覆盖 scenario dynamic_max_attempts（0=用 YAML 默认 10）",
    )
    parser.add_argument("--serial", action="store_true", help="串行模式（单 Key，调试用）")
    args = parser.parse_args()

    set_api_key_map_file(JB_ROOT / "config" / "18_api_key_map.yaml")

    models = resolve_target_models(args.model)
    scenarios = load_scenarios("18_scenarios")
    if args.max_attempts > 0:
        for scenario in scenarios:
            scenario["dynamic_max_attempts"] = args.max_attempts
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
    print(f"Max tries : {scenarios[0].get('dynamic_max_attempts', 10)} per dynamic case")
    print(f"Mode      : {'7路并行 (每模型专用Key)' if parallel else '串行'}")
    print(f"Cases     : {total} (dynamic 变体每条最多多次 API)")
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

    print_dynamic_summary(summary)
    print(f"\nJSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
