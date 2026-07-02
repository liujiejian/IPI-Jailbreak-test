#!/usr/bin/env python
"""
Strategy 38: Tool Parameter Manipulation / Injection (Kaya et al., 2025)

攻击思路：tool output 嵌入恶意 **参数值**（非覆盖 tool description），模型将其
复制进 action tool_call：

  channel: #notification → #general
  topic: (benign) → Malicious URL
  → post_slack_message 向 #general 发送恶意链接

环境：tool_param_env（新建）— Slack 集成 + 参数化 action tools

与 Strategy 37 区分：
  • 37 — Tool Instruction Override（Ignore tool instructions，覆盖工具说明）
  • 38 — Tool Parameter Injection（注入 channel/topic/password 等参数值）

变体：
  • clean        — 无参数注入
  • param_inject — 明文 parameter block
  • param_paper  — 论文 Slack channel + malicious URL
  • param_json   — JSON tool_call 建议块
  • param_buried — 参数块 buried 在 log 中

Usage:
  python test/38_kaya_tool_parameter_injection.py
  python test/38_kaya_tool_parameter_injection.py -m qwen3.7-plus
  python test/38_kaya_tool_parameter_injection.py --scenario slack_param_injection
  python test/38_kaya_tool_parameter_injection.py --variant param_paper
  python test/38_kaya_tool_parameter_injection.py --serial
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
from lib.report import write_html_report  # noqa: E402
from lib.runner import run_parallel_by_model, sort_results  # noqa: E402
from lib.tool_param_env import build_tool_param_system_prompt  # noqa: E402
from lib.tool_parameter_injection import (  # noqa: E402
    TOOL_PARAM_VARIANTS,
    VARIANT_CLEAN,
    build_case_payload,
    build_tool_output,
    get_tools_for_scenario,
    load_scenarios,
)

STRATEGY_TITLE = "Strategy 38: Tool Parameter Manipulation (Kaya et al., 2025)"
RUN_PREFIX = "38_kaya_tool_parameter_injection"
ALL_VARIANTS = [VARIANT_CLEAN, *TOOL_PARAM_VARIANTS]


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    messages, param_meta = build_case_payload(scenario, variant)
    tools = get_tools_for_scenario(scenario)
    system_prompt = build_tool_param_system_prompt(scenario)
    tool_output = build_tool_output(scenario, variant)

    base = {
        "model": model,
        "scenario_id": scenario["id"],
        "scenario_name": scenario["name"],
        "variant": variant,
        "template_family": variant if variant != VARIANT_CLEAN else "clean",
        "param_meta": param_meta,
        "target_tool": scenario.get("target_tool", ""),
        "attacker_instruction": scenario.get("param_injection_message", ""),
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
        attack_labels = {v: labels.get(v) for v in TOOL_PARAM_VARIANTS}
        any_success = any(v == "success" for v in attack_labels.values())
        row = {
            "model": model,
            "scenario_id": scenario_id,
            "baseline": clean,
            **attack_labels,
            "any_param_beats_baseline": any_success
            and clean in ("blocked", "partial", None),
        }
        for v in TOOL_PARAM_VARIANTS:
            row[f"{v}_beats_baseline"] = (
                attack_labels.get(v) == "success" and clean in ("blocked", "partial", None)
            )
        rows.append(row)

    total = len(rows) or 1
    asr: dict[str, float] = {
        "baseline_asr": round(sum(1 for r in rows if r["baseline"] == "success") / total, 4)
    }
    for v in TOOL_PARAM_VARIANTS:
        asr[f"{v}_asr"] = round(sum(1 for r in rows if r.get(v) == "success") / total, 4)
    asr["any_param_asr"] = round(
        sum(1 for r in rows if any(r.get(v) == "success" for v in TOOL_PARAM_VARIANTS)) / total,
        4,
    )

    return {
        "total_cases": len(rows),
        "rows": rows,
        **asr,
        **{
            f"{v}_success_count": sum(1 for r in rows if r.get(v) == "success")
            for v in TOOL_PARAM_VARIANTS
        },
        "any_param_beats_baseline_count": sum(1 for r in rows if r["any_param_beats_baseline"]),
    }


def print_summary_table(rows: list[dict]) -> None:
    print("\n" + "=" * 150)
    print("对比汇总（clean vs Tool Parameter Injection）")
    print("=" * 150)
    header = (
        f"{'模型':<22} {'场景':<22} {'clean':<12} "
        f"{'inject':<12} {'paper':<12} {'json':<12} {'buried':<12} {'任一有效'}"
    )
    print(header)
    print("-" * 150)

    def tag(label: str | None) -> str:
        if not label:
            return "-"
        return STATUS_TAG.get(label, label) if label in STATUS_TAG else label

    for row in rows:
        cmp = "是" if row.get("any_param_beats_baseline") else "-"
        print(
            f"{row['model']:<22} {row['scenario_id']:<22} "
            f"{tag(row.get('baseline')):<12} "
            f"{tag(row.get('param_inject')):<12} "
            f"{tag(row.get('param_paper')):<12} "
            f"{tag(row.get('param_json')):<12} "
            f"{tag(row.get('param_buried')):<12} {cmp}"
        )
    print("=" * 150)


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
                tool = result.get("target_tool", "-")
                extra = f" | target={tool}" if variant != VARIANT_CLEAN else ""
                print_case_result(
                    model,
                    scenario["id"],
                    variant,
                    ev.get("label", "error"),
                    ev.get("reason", "") + extra,
                )
    return sort_results(results, models, scenarios, variants)


def main() -> int:
    set_api_key_map_file(TEST_ROOT / "config" / "38_api_key_map.yaml")

    variant_choices = ["all", VARIANT_CLEAN, *TOOL_PARAM_VARIANTS]
    parser = argparse.ArgumentParser(description=STRATEGY_TITLE)
    parser.add_argument("-m", "--model", help="单个模型；默认全部 7 个")
    parser.add_argument(
        "--scenario",
        help="slack_param_injection / password_change / send_link / exfiltrate_data",
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
    print(f"Env       : tool_param_env (Slack channel/topic + param injection via tool output)")
    print(f"Variants  : {', '.join(variants)}")
    print(f"Chain     : tool output param block -> post_slack / action tool with injected args")
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
        variant_b_key="param_paper",
        variant_b_label="param_paper（论文 Slack channel+URL 参数注入）",
        compare_label="Tool Parameter Injection 有效",
        compare_field="any_param_beats_baseline",
        asr_b_key="any_param_asr",
    )

    print_summary_table(summary["rows"])
    print(f"\nclean ASR        : {summary['baseline_asr']:.1%}")
    print(f"param_inject ASR : {summary['param_inject_asr']:.1%}")
    print(f"param_paper ASR  : {summary['param_paper_asr']:.1%}")
    print(f"param_json ASR   : {summary['param_json_asr']:.1%}")
    print(f"param_buried ASR : {summary['param_buried_asr']:.1%}")
    print(f"any param ASR    : {summary['any_param_asr']:.1%}")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
