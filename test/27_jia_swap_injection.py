#!/usr/bin/env python
"""
Strategy 27: Swap Adaptive Attack (Jia et al., 2025)
Paper: PromptLocate §5.3 — evade Step III localization

把 Combined Attack 中 Instruction 与 Data 的顺序对调，违反
「注入数据跟在注入指令之后」的结构假设：

  正常:  z || s_e || x_e   (Instruction → Data)
  Swap:  z || x_e || s_e   (Data → Instruction)

Step III 依赖该假设做 CIS 定位；Swap 使定位器失效。

变体（Combined Attack 基底）：
  • swap          — z || x_e || s_e（论文 Swap 默认）
  • swap_minimal  — x_e || s_e（最简 Data→Instruction）
  • swap_leading  — x_e || z || s_e（Data 置首）

对比 clean 与 3 种 Swap 变体（默认跑全部）。

Usage:
  python test/27_jia_swap_injection.py
  python test/27_jia_swap_injection.py -m qwen3.7-plus
  python test/27_jia_swap_injection.py --variant swap_minimal
  python test/27_jia_swap_injection.py --serial
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
from lib.swap_injection import (  # noqa: E402
    SWAP_VARIANTS,
    build_agent_messages,
    build_swap_injection,
    build_tool_output,
    load_scenarios,
)

STRATEGY_TITLE = "Strategy 27: Swap Adaptive Attack (Jia et al., 2025)"
RUN_PREFIX = "27_jia_swap"
VARIANT_CLEAN = "clean"
ALL_VARIANTS = [VARIANT_CLEAN, *SWAP_VARIANTS]


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    tool_output, swap_meta = build_tool_output(
        benign_prefix=scenario["benign_prefix"],
        attacker_instruction=scenario["attacker_instruction"],
        variant=variant,  # type: ignore[arg-type]
    )
    injection_block = swap_meta.get("injection_block", "")
    if variant != VARIANT_CLEAN and not injection_block:
        injection_block, swap_meta = build_swap_injection(
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
        "swap_meta": swap_meta,
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
        swap_labels = {v: labels.get(v) for v in SWAP_VARIANTS}
        any_success = any(v == "success" for v in swap_labels.values())
        row = {
            "model": model,
            "scenario_id": scenario_id,
            "baseline": clean,
            **swap_labels,
            "any_swap_beats_baseline": any_success
            and clean in ("blocked", "partial", None),
        }
        for v in SWAP_VARIANTS:
            row[f"{v}_beats_baseline"] = (
                swap_labels.get(v) == "success" and clean in ("blocked", "partial", None)
            )
        rows.append(row)

    total = len(rows) or 1
    asr: dict[str, float] = {
        "baseline_asr": round(sum(1 for r in rows if r["baseline"] == "success") / total, 4)
    }
    for v in SWAP_VARIANTS:
        asr[f"{v}_asr"] = round(sum(1 for r in rows if r.get(v) == "success") / total, 4)
    asr["any_swap_asr"] = round(
        sum(1 for r in rows if any(r.get(v) == "success" for v in SWAP_VARIANTS)) / total,
        4,
    )

    return {
        "total_cases": len(rows),
        "rows": rows,
        **asr,
        **{f"{v}_success_count": sum(1 for r in rows if r.get(v) == "success") for v in SWAP_VARIANTS},
        "any_swap_beats_baseline_count": sum(1 for r in rows if r["any_swap_beats_baseline"]),
    }


def print_swap_summary_table(rows: list[dict]) -> None:
    print("\n" + "=" * 115)
    print("对比汇总（clean vs 三种 Swap 指令/数据对调）")
    print("=" * 115)
    header = (
        f"{'模型':<22} {'场景':<18} {'clean':<12} "
        f"{'swap':<12} {'minimal':<12} {'leading':<12} {'任一有效'}"
    )
    print(header)
    print("-" * 115)

    def tag(label: str | None) -> str:
        if not label:
            return "-"
        return STATUS_TAG.get(label, label) if label in STATUS_TAG else label

    for row in rows:
        cmp = "是" if row.get("any_swap_beats_baseline") else "-"
        print(
            f"{row['model']:<22} {row['scenario_id']:<18} "
            f"{tag(row.get('baseline')):<12} "
            f"{tag(row.get('swap')):<12} "
            f"{tag(row.get('swap_minimal')):<12} "
            f"{tag(row.get('swap_leading')):<12} {cmp}"
        )
    print("=" * 115)


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
                extra = ""
                if variant in SWAP_VARIANTS:
                    order = result.get("swap_meta", {}).get("component_order", "?")
                    extra = f" | order={order}"
                print_case_result(
                    model,
                    scenario["id"],
                    variant,
                    ev.get("label", "error"),
                    ev.get("reason", "") + extra,
                )
    return sort_results(results, models, scenarios, variants)


def main() -> int:
    set_api_key_map_file(TEST_ROOT / "config" / "27_api_key_map.yaml")

    variant_choices = ["all", VARIANT_CLEAN, *SWAP_VARIANTS]
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
    print(f"Target    : Step III — violate instruction→data order assumption")
    print(f"Payload   : Combined Attack with s_e || x_e reversed")
    print(f"Variants  : {', '.join(variants)}")
    print(f"Chain     : poison webpage -> get_webpage -> Swap IPI")
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
        variant_b_key="swap",
        variant_b_label="swap（z||xe||se 论文默认）",
        compare_label="Swap 有效",
        compare_field="any_swap_beats_baseline",
        asr_b_key="any_swap_asr",
    )

    print_swap_summary_table(summary["rows"])
    print(f"\nclean ASR       : {summary['baseline_asr']:.1%}")
    print(f"swap ASR        : {summary['swap_asr']:.1%}")
    print(f"swap_minimal ASR: {summary['swap_minimal_asr']:.1%}")
    print(f"swap_leading ASR: {summary['swap_leading_asr']:.1%}")
    print(f"any swap ASR    : {summary['any_swap_asr']:.1%}")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
