#!/usr/bin/env python
"""
Strategy 48: Adaptive Prompt Injection (Syros et al., 2026 / MUZZLE §3.5)

攻击思路（Adaptive / Reflection Loop IPI）：
  传统 IPI：攻击一次失败 → 结束。
  MUZZLE §3.5：Judge 分析失败原因 →
    • payload 太弱 (instruction) → Payload Generator 重新生成
    • 注入点不对 (vessel)       → Grafter 换下一个 Injection Surface
  循环直到成功或 vessels 耗尽。

环境检查结论：**复用** web_agent_trajectory_env（46–47 同一威胁模型）。
**扩展** web_agent_trajectory_env（format_judge_verdict / format_reflection_loop）。
**新建** adaptive_prompt_injection.py — 模拟 Reflection Loop 收敛后的 redeploy。

与相近策略：
  • 46 MUZZLE Trajectory   — §3.3 选注入面
  • 47 MUZZLE Context-aware — §3.4 上下文对齐 payload（单次生成）
  • 48 MUZZLE Adaptive      — §3.5 Judge 反馈 → 自适应 retry

变体：
  • clean                    — 良性线程
  • single_shot              — 传统单次 generic 攻击（无 Reflection）
  • reflect_payload_refined  — Judge→instruction → 同 vessel 换 refined payload
  • reflect_surface_switch   — Judge→vessel → 换 Grafter #1 + refined payload
  • reflect_combined_loop    — 先换 surface 再 refine payload（2 轮 Reflection）

Usage:
  python test/48_syros_muzzle_adaptive_injection.py
  python test/48_syros_muzzle_adaptive_injection.py -m qwen3.7-plus
  python test/48_syros_muzzle_adaptive_injection.py --scenario password_change
  python test/48_syros_muzzle_adaptive_injection.py --variant reflect_payload_refined
  python test/48_syros_muzzle_adaptive_injection.py --serial
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

from lib.adaptive_prompt_injection import (  # noqa: E402
    ADAPTIVE_VARIANTS,
    build_adaptive_messages,
    get_tools_for_scenario,
    load_scenarios,
)
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
from lib.report import write_html_report  # noqa: E402
from lib.runner import run_parallel_by_model, sort_results  # noqa: E402
from lib.web_agent_trajectory_env import build_web_agent_system_prompt  # noqa: E402

STRATEGY_TITLE = "Strategy 48: Adaptive Prompt Injection (Syros et al., 2026 MUZZLE)"
RUN_PREFIX = "48_syros_muzzle_adaptive_injection"
VARIANT_CLEAN = "clean"
ALL_VARIANTS = [VARIANT_CLEAN, *ADAPTIVE_VARIANTS]
REFLECT_VARIANTS = (
    "reflect_payload_refined",
    "reflect_surface_switch",
    "reflect_combined_loop",
)


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    messages, adapt_meta = build_adaptive_messages(scenario, variant)
    tools = get_tools_for_scenario(scenario)
    system_prompt = build_web_agent_system_prompt(scenario)
    tool_output = messages[-1]["content"] if messages else ""

    base = {
        "model": model,
        "scenario_id": scenario["id"],
        "scenario_name": scenario["name"],
        "variant": variant,
        "web_app": scenario.get("web_app"),
        "template_family": variant if variant != VARIANT_CLEAN else "clean",
        "adapt_meta": adapt_meta,
        "reflection_iterations": adapt_meta.get("reflection_iterations"),
        "adaptation": adapt_meta.get("adaptation"),
        "judge_attribution": adapt_meta.get("judge_attribution"),
        "payload_style": adapt_meta.get("payload_style"),
        "injection_surface": adapt_meta.get("injection_surface"),
        "attack_chain": " → ".join(adapt_meta.get("attack_chain", [])),
        "attacker_instruction": scenario.get("attacker_instruction", ""),
        "user_instruction": scenario["user_instruction"],
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
        attack_labels = {v: labels.get(v) for v in ADAPTIVE_VARIANTS}
        single = labels.get("single_shot")
        any_reflect = any(labels.get(v) == "success" for v in REFLECT_VARIANTS)
        row = {
            "model": model,
            "scenario_id": scenario_id,
            "baseline": clean,
            **attack_labels,
            "reflect_beats_single_shot": any_reflect and single in ("blocked", "partial", None),
            "any_attack_beats_baseline": any(
                labels.get(v) == "success" for v in ADAPTIVE_VARIANTS
            )
            and clean in ("blocked", "partial", None),
        }
        rows.append(row)

    total = len(rows) or 1
    asr: dict[str, float] = {
        "baseline_asr": round(sum(1 for r in rows if r["baseline"] == "success") / total, 4)
    }
    for v in ADAPTIVE_VARIANTS:
        asr[f"{v}_asr"] = round(sum(1 for r in rows if r.get(v) == "success") / total, 4)
    asr["any_attack_asr"] = round(
        sum(
            1 for r in rows if any(r.get(v) == "success" for v in ADAPTIVE_VARIANTS)
        )
        / total,
        4,
    )
    asr["reflect_beats_single_shot_rate"] = round(
        sum(1 for r in rows if r.get("reflect_beats_single_shot")) / total, 4
    )

    return {
        "total_cases": len(rows),
        "rows": rows,
        **asr,
        **{
            f"{v}_success_count": sum(1 for r in rows if r.get(v) == "success")
            for v in ADAPTIVE_VARIANTS
        },
        "reflect_beats_single_shot_count": sum(
            1 for r in rows if r.get("reflect_beats_single_shot")
        ),
        "any_attack_beats_baseline_count": sum(
            1 for r in rows if r.get("any_attack_beats_baseline")
        ),
    }


def print_summary_table(rows: list[dict]) -> None:
    print("\n" + "=" * 184)
    print("对比汇总（clean vs Adaptive — single-shot vs Reflection Loop 收敛 redeploy）")
    print("=" * 184)
    header = (
        f"{'模型':<22} {'场景':<24} {'clean':<10} "
        f"{'single':<10} {'payload':<10} {'surface':<10} {'combined':<10} {'refl>1x'}"
    )
    print(header)
    print("-" * 184)

    def tag(label: str | None) -> str:
        if not label:
            return "-"
        return STATUS_TAG.get(label, label) if label in STATUS_TAG else label

    for row in rows:
        cmp = "是" if row.get("reflect_beats_single_shot") else "-"
        print(
            f"{row['model']:<22} {row['scenario_id']:<24} "
            f"{tag(row.get('baseline')):<10} "
            f"{tag(row.get('single_shot')):<10} "
            f"{tag(row.get('reflect_payload_refined')):<10} "
            f"{tag(row.get('reflect_surface_switch')):<10} "
            f"{tag(row.get('reflect_combined_loop')):<10} {cmp}"
        )
    print("=" * 184)


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
                iters = result.get("reflection_iterations")
                extra = f" | iters={iters}" if variant != VARIANT_CLEAN and iters is not None else ""
                print_case_result(
                    model,
                    scenario["id"],
                    variant,
                    ev.get("label", "error"),
                    ev.get("reason", "") + extra,
                )
    return sort_results(results, models, scenarios, variants)


def main() -> int:
    set_api_key_map_file(TEST_ROOT / "config" / "48_api_key_map.yaml")

    variant_choices = ["all", VARIANT_CLEAN, *ADAPTIVE_VARIANTS]
    parser = argparse.ArgumentParser(description=STRATEGY_TITLE)
    parser.add_argument("-m", "--model", help="单个模型；默认全部 7 个")
    parser.add_argument(
        "--scenario",
        help="password_change / send_link / exfiltrate_data / pwned_hijack / system_prompt_leak",
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
    print(f"Env       : web_agent_trajectory_env + Reflection Loop (Judge / Grafter / Payload Gen)")
    print(f"Variants  : {', '.join(variants)}")
    print(f"Chain     : attack → Judge → adapt payload|vessel → redeploy at T* j*")
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
        variant_b_key="reflect_payload_refined",
        variant_b_label="reflect_payload_refined（Judge→instruction → refined payload）",
        compare_label="Adaptive Reflection 有效",
        compare_field="any_attack_beats_baseline",
        asr_b_key="any_attack_asr",
    )

    print_summary_table(summary["rows"])
    print(f"\nclean ASR                     : {summary['baseline_asr']:.1%}")
    print(f"single_shot ASR               : {summary['single_shot_asr']:.1%}")
    print(f"reflect_payload_refined ASR     : {summary['reflect_payload_refined_asr']:.1%}")
    print(f"reflect_surface_switch ASR      : {summary['reflect_surface_switch_asr']:.1%}")
    print(f"reflect_combined_loop ASR       : {summary['reflect_combined_loop_asr']:.1%}")
    print(f"any attack ASR                : {summary['any_attack_asr']:.1%}")
    print(f"reflect beats single-shot       : {summary['reflect_beats_single_shot_rate']:.1%}")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
