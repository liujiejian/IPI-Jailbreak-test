#!/usr/bin/env python
"""
Strategy 26: Sep.+Ins. Joint Optimization Adaptive Attack (Jia et al., 2025)
Paper: PromptLocate §5.3 — evade Step II DataSentinel oracle (joint z||se)
Reference [7]: Liu et al., 2025 — DataSentinel (GCG joint adaptive trigger)

在 Strategy 25 Separator 基础上，联合优化 Separator z + Injected Instruction s_e：

  max_{xc} [ -E_{k'∈K} ℓ(k', g(s_d k' || xc)) - α·ℓ(y_e, f(s_t || xc)) ]
  xc = x_t || z || s_e || x_e

Sep.+Ins. 同时优化 z 与 s_e（Joint Optimization），目标：
  • 绕过 Detector（DataSentinel oracle）
  • 保持攻击成功（后端 LLM 执行 x_e）

变体：
  • sep_ins         — DataSentinel Table 7 完整 joint z||se + x_e
  • sep_ins_compact — 缩写 joint z||se + x_e

本脚本使用 DataSentinel 论文 Table 7 公布的 GCG 预优化 joint block，
而非测试时重新跑联合梯度优化。

Usage:
  python test/26_jia_sep_ins_injection.py
  python test/26_jia_sep_ins_injection.py -m qwen3.7-plus
  python test/26_jia_sep_ins_injection.py --variant sep_ins
  python test/26_jia_sep_ins_injection.py --serial
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
from lib.sep_ins_injection import (  # noqa: E402
    SEP_INS_JOINT_BLOCK,
    SEP_INS_VARIANTS,
    build_agent_messages,
    build_sep_ins_injection,
    build_tool_output,
    load_scenarios,
)

STRATEGY_TITLE = "Strategy 26: Sep.+Ins. Joint Optimization Adaptive Attack (Jia et al., 2025)"
RUN_PREFIX = "26_jia_sep_ins"
VARIANT_CLEAN = "clean"
ALL_VARIANTS = [VARIANT_CLEAN, *SEP_INS_VARIANTS]


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    tool_output, meta = build_tool_output(
        benign_prefix=scenario["benign_prefix"],
        attacker_instruction=scenario["attacker_instruction"],
        variant=variant,  # type: ignore[arg-type]
    )
    injection_block = meta.get("injection_block", "")
    if variant != VARIANT_CLEAN and not injection_block:
        injection_block, meta = build_sep_ins_injection(
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
        "sep_ins_meta": meta,
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
        ins_labels = {v: labels.get(v) for v in SEP_INS_VARIANTS}
        any_success = any(v == "success" for v in ins_labels.values())
        row = {
            "model": model,
            "scenario_id": scenario_id,
            "baseline": clean,
            **ins_labels,
            "any_sep_ins_beats_baseline": any_success
            and clean in ("blocked", "partial", None),
        }
        for v in SEP_INS_VARIANTS:
            row[f"{v}_beats_baseline"] = (
                ins_labels.get(v) == "success" and clean in ("blocked", "partial", None)
            )
        rows.append(row)

    total = len(rows) or 1
    asr: dict[str, float] = {
        "baseline_asr": round(sum(1 for r in rows if r["baseline"] == "success") / total, 4)
    }
    for v in SEP_INS_VARIANTS:
        asr[f"{v}_asr"] = round(sum(1 for r in rows if r.get(v) == "success") / total, 4)
    asr["any_sep_ins_asr"] = round(
        sum(1 for r in rows if any(r.get(v) == "success" for v in SEP_INS_VARIANTS)) / total,
        4,
    )

    return {
        "total_cases": len(rows),
        "rows": rows,
        **asr,
        **{
            f"{v}_success_count": sum(1 for r in rows if r.get(v) == "success")
            for v in SEP_INS_VARIANTS
        },
        "any_sep_ins_beats_baseline_count": sum(
            1 for r in rows if r["any_sep_ins_beats_baseline"]
        ),
    }


def print_sep_ins_summary_table(rows: list[dict]) -> None:
    print("\n" + "=" * 105)
    print("对比汇总（clean vs Sep.+Ins. 联合优化 z||se）")
    print("=" * 105)
    header = (
        f"{'模型':<22} {'场景':<18} {'clean':<12} "
        f"{'sep_ins':<12} {'compact':<12} {'任一有效'}"
    )
    print(header)
    print("-" * 105)

    def tag(label: str | None) -> str:
        if not label:
            return "-"
        return STATUS_TAG.get(label, label) if label in STATUS_TAG else label

    for row in rows:
        cmp = "是" if row.get("any_sep_ins_beats_baseline") else "-"
        print(
            f"{row['model']:<22} {row['scenario_id']:<18} "
            f"{tag(row.get('baseline')):<12} "
            f"{tag(row.get('sep_ins')):<12} "
            f"{tag(row.get('sep_ins_compact')):<12} {cmp}"
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
    set_api_key_map_file(TEST_ROOT / "config" / "26_api_key_map.yaml")

    variant_choices = ["all", VARIANT_CLEAN, *SEP_INS_VARIANTS]
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
    print(f"Target    : Step II — joint optimize z||se to evade DataSentinel oracle")
    print(f"Joint z||se: {SEP_INS_JOINT_BLOCK[:72]}...")
    print(f"Variants  : {', '.join(variants)}")
    print(f"Chain     : poison webpage -> get_webpage -> Sep.+Ins. IPI")
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
        variant_b_key="sep_ins",
        variant_b_label="sep_ins（联合优化 z||se）",
        compare_label="Sep.+Ins. 有效",
        compare_field="any_sep_ins_beats_baseline",
        asr_b_key="any_sep_ins_asr",
    )

    print_sep_ins_summary_table(summary["rows"])
    print(f"\nclean ASR        : {summary['baseline_asr']:.1%}")
    print(f"sep_ins ASR      : {summary['sep_ins_asr']:.1%}")
    print(f"sep_ins_compact ASR: {summary['sep_ins_compact_asr']:.1%}")
    print(f"any sep_ins ASR  : {summary['any_sep_ins_asr']:.1%}")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
