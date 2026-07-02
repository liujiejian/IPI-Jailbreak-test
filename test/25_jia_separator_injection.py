#!/usr/bin/env python
"""
Strategy 25: Separator Optimization Adaptive Attack (Jia et al., 2025)
Paper: PromptLocate §5.3 — evade Step II DataSentinel oracle
Reference [7]: Liu et al., 2025 — DataSentinel (GCG-optimized adaptive trigger)

优化 Separator z，而非手写 "Ignore previous instructions"：
  通过梯度（GCG）寻找最容易骗过 Detector 的 z，同时保持后端 LLM 执行注入任务。

  max_{xc} [ -E_{k'∈K} ℓ(k', g(s_d k' || xc)) - α·ℓ(y_e, f(s_t || xc)) ]
  xc = x_t || z || s_e || x_e

Separator 变体仅优化 z；s_e||x_e 沿用 Combined Attack 族（Table 3 实验设定）。

变体：
  • separator          — z_opt + attacker goal（论文 Separator 默认）
  • separator_combined — z_opt + Combined se||xe（Assistant/Completed + goal）

本脚本使用 DataSentinel 论文公布的 GCG 预优化 z，而非测试时重新跑梯度优化。

Usage:
  python test/25_jia_separator_injection.py
  python test/25_jia_separator_injection.py -m qwen3.7-plus
  python test/25_jia_separator_injection.py --variant separator_combined
  python test/25_jia_separator_injection.py --serial
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

from lib.agent_env import build_system_prompt  # noqa: E402
from lib.api_call import create_chat_completion  # noqa: E402
from lib.api_keys import set_api_key_map_file, validate_model_keys  # noqa: E402
from lib.chatinject import get_tools_for_scenario  # noqa: E402
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
from lib.separator_injection import (  # noqa: E402
    SEPARATOR_OPTIMIZED_Z,
    SEPARATOR_VARIANTS,
    build_agent_messages,
    build_separator_injection,
    build_tool_output,
    load_scenarios,
)

STRATEGY_TITLE = "Strategy 25: Separator Optimization Adaptive Attack (Jia et al., 2025)"
RUN_PREFIX = "25_jia_separator"
VARIANT_CLEAN = "clean"
ALL_VARIANTS = [VARIANT_CLEAN, *SEPARATOR_VARIANTS]


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    tool_output, sep_meta = build_tool_output(
        benign_prefix=scenario["benign_prefix"],
        attacker_instruction=scenario["attacker_instruction"],
        variant=variant,  # type: ignore[arg-type]
    )
    injection_block = sep_meta.get("injection_block", "")
    if variant != VARIANT_CLEAN and not injection_block:
        injection_block, sep_meta = build_separator_injection(
            scenario["attacker_instruction"],
            variant,
        )

    system_prompt = build_system_prompt(scenario)
    tools = get_tools_for_scenario(scenario)
    messages = build_agent_messages(
        user_instruction=scenario["user_instruction"],
        tool_output=tool_output,
        system_prompt=system_prompt,
        scenario=scenario,
        tool_name=scenario.get("tool_name", "get_webpage"),
    )

    base = {
        "model": model,
        "scenario_id": scenario["id"],
        "scenario_name": scenario["name"],
        "variant": variant,
        "template_family": variant if variant != VARIANT_CLEAN else "clean",
        "separator_z": SEPARATOR_OPTIMIZED_Z if variant != VARIANT_CLEAN else "",
        "separator_meta": sep_meta,
        "injection_block": injection_block,
        "webpage_url": scenario.get("webpage_url", ""),
        "attacker_instruction": scenario["attacker_instruction"],
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
        sep_labels = {v: labels.get(v) for v in SEPARATOR_VARIANTS}
        any_success = any(v == "success" for v in sep_labels.values())
        row = {
            "model": model,
            "scenario_id": scenario_id,
            "baseline": clean,
            **sep_labels,
            "any_separator_beats_baseline": any_success
            and clean in ("blocked", "partial", None),
        }
        for v in SEPARATOR_VARIANTS:
            row[f"{v}_beats_baseline"] = (
                sep_labels.get(v) == "success" and clean in ("blocked", "partial", None)
            )
        rows.append(row)

    total = len(rows) or 1
    asr: dict[str, float] = {
        "baseline_asr": round(sum(1 for r in rows if r["baseline"] == "success") / total, 4)
    }
    for v in SEPARATOR_VARIANTS:
        asr[f"{v}_asr"] = round(sum(1 for r in rows if r.get(v) == "success") / total, 4)
    asr["any_separator_asr"] = round(
        sum(1 for r in rows if any(r.get(v) == "success" for v in SEPARATOR_VARIANTS)) / total,
        4,
    )

    return {
        "total_cases": len(rows),
        "rows": rows,
        **asr,
        **{
            f"{v}_success_count": sum(1 for r in rows if r.get(v) == "success")
            for v in SEPARATOR_VARIANTS
        },
        "any_separator_beats_baseline_count": sum(
            1 for r in rows if r["any_separator_beats_baseline"]
        ),
    }


def print_separator_summary_table(rows: list[dict]) -> None:
    print("\n" + "=" * 105)
    print("对比汇总（clean vs Separator 梯度优化触发器）")
    print("=" * 105)
    header = (
        f"{'模型':<22} {'场景':<18} {'clean':<12} "
        f"{'separator':<12} {'combined':<12} {'任一有效'}"
    )
    print(header)
    print("-" * 105)

    def tag(label: str | None) -> str:
        if not label:
            return "-"
        return STATUS_TAG.get(label, label) if label in STATUS_TAG else label

    for row in rows:
        cmp = "是" if row.get("any_separator_beats_baseline") else "-"
        print(
            f"{row['model']:<22} {row['scenario_id']:<18} "
            f"{tag(row.get('baseline')):<12} "
            f"{tag(row.get('separator')):<12} "
            f"{tag(row.get('separator_combined')):<12} {cmp}"
        )
    print("=" * 105)


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
                print_case_result(
                    model,
                    scenario["id"],
                    variant,
                    ev.get("label", "error"),
                    ev.get("reason", ""),
                )
    return sort_results(results, models, scenarios, variants)


def main() -> int:
    set_api_key_map_file(TEST_ROOT / "config" / "25_api_key_map.yaml")

    variant_choices = ["all", VARIANT_CLEAN, *SEPARATOR_VARIANTS]
    parser = argparse.ArgumentParser(description=STRATEGY_TITLE)
    parser.add_argument("-m", "--model", help="单个模型；默认全部 7 个")
    parser.add_argument("--scenario", help="password_change / send_link / exfiltrate_data")
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
    print(f"Target    : Step II — evade DataSentinel oracle via optimized z")
    print(f"Trigger   : {SEPARATOR_OPTIMIZED_Z!r} (DataSentinel GCG)")
    print(f"Variants  : {', '.join(variants)}")
    print(f"Chain     : poison webpage -> get_webpage -> Separator IPI")
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
        variant_b_key="separator",
        variant_b_label="separator（GCG 优化 z）",
        compare_label="Separator 有效",
        compare_field="any_separator_beats_baseline",
        asr_b_key="any_separator_asr",
    )

    print_separator_summary_table(summary["rows"])
    print(f"\nclean ASR           : {summary['baseline_asr']:.1%}")
    print(f"separator ASR       : {summary['separator_asr']:.1%}")
    print(f"separator_combined ASR: {summary['separator_combined_asr']:.1%}")
    print(f"any separator ASR   : {summary['any_separator_asr']:.1%}")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
