#!/usr/bin/env python
"""
Strategy 22: Multiple Adaptive Attack / Multi-injection (Jia et al., 2025)
Paper: PromptLocate §5.3 — evade Step I localization

在干净数据 xt 内插入两个 Combined Attack 载荷（Prompt A + Prompt B）：
  xc = xt[:posA] || PromptA || xt[posA:posB] || PromptB || xt[posB:]

定位器必须找到两处注入（Multi-injection），比 Random-Loc 更难分离。

插入位置变体：
  • multiple_sentence  — 两处随机句子间隙
  • multiple_paragraph — 两处随机段落间隙
  • multiple_comment   — 两处随机评论间隙

对比 clean 与 3 种 Multiple 变体（默认跑全部）。

Usage:
  python test/22_jia_multiple_injection.py
  python test/22_jia_multiple_injection.py -m qwen3.7-plus
  python test/22_jia_multiple_injection.py --variant multiple_paragraph
  python test/22_jia_multiple_injection.py --serial
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
from lib.multiple_injection import (  # noqa: E402
    MULTIPLE_VARIANTS,
    build_agent_messages,
    build_prompt_a,
    build_prompt_b,
    build_tool_output,
    load_scenarios,
)
from lib.report import write_html_report  # noqa: E402
from lib.runner import run_parallel_by_model, sort_results  # noqa: E402

STRATEGY_TITLE = "Strategy 22: Multiple Adaptive Attack / Multi-injection (Jia et al., 2025)"
RUN_PREFIX = "22_jia_multiple"
VARIANT_CLEAN = "clean"
ALL_VARIANTS = [VARIANT_CLEAN, *MULTIPLE_VARIANTS]


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    seed_key = f"{model}|{scenario['id']}|{variant}"
    tool_output, placement_meta = build_tool_output(
        benign_prefix=scenario["benign_prefix"],
        attacker_instruction=scenario["attacker_instruction"],
        variant=variant,  # type: ignore[arg-type]
        seed_key=seed_key,
    )
    injection_block = placement_meta.get("injection_block", "")
    if variant != VARIANT_CLEAN and not injection_block:
        injection_block = (
            f"{build_prompt_a(scenario['attacker_instruction'])}\n\n"
            f"{build_prompt_b(scenario['attacker_instruction'])}"
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
        "placement": placement_meta,
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
        multi_labels = {v: labels.get(v) for v in MULTIPLE_VARIANTS}
        any_success = any(v == "success" for v in multi_labels.values())
        row = {
            "model": model,
            "scenario_id": scenario_id,
            "baseline": clean,
            **multi_labels,
            "any_multiple_beats_baseline": any_success
            and clean in ("blocked", "partial", None),
        }
        for v in MULTIPLE_VARIANTS:
            row[f"{v}_beats_baseline"] = (
                multi_labels.get(v) == "success" and clean in ("blocked", "partial", None)
            )
        rows.append(row)

    total = len(rows) or 1
    asr: dict[str, float] = {
        "baseline_asr": round(sum(1 for r in rows if r["baseline"] == "success") / total, 4)
    }
    for v in MULTIPLE_VARIANTS:
        asr[f"{v}_asr"] = round(sum(1 for r in rows if r.get(v) == "success") / total, 4)
    asr["any_multiple_asr"] = round(
        sum(1 for r in rows if any(r.get(v) == "success" for v in MULTIPLE_VARIANTS)) / total,
        4,
    )

    return {
        "total_cases": len(rows),
        "rows": rows,
        **asr,
        **{
            f"{v}_success_count": sum(1 for r in rows if r.get(v) == "success")
            for v in MULTIPLE_VARIANTS
        },
        "any_multiple_beats_baseline_count": sum(
            1 for r in rows if r["any_multiple_beats_baseline"]
        ),
    }


def print_multiple_summary_table(rows: list[dict]) -> None:
    print("\n" + "=" * 115)
    print("对比汇总（clean vs 三种 Multiple Multi-injection）")
    print("=" * 115)
    header = (
        f"{'模型':<22} {'场景':<18} {'clean':<12} "
        f"{'sentence':<12} {'paragraph':<12} {'comment':<12} {'任一有效'}"
    )
    print(header)
    print("-" * 115)

    def tag(label: str | None) -> str:
        if not label:
            return "-"
        return STATUS_TAG.get(label, label) if label in STATUS_TAG else label

    for row in rows:
        cmp = "是" if row.get("any_multiple_beats_baseline") else "-"
        print(
            f"{row['model']:<22} {row['scenario_id']:<18} "
            f"{tag(row.get('baseline')):<12} "
            f"{tag(row.get('multiple_sentence')):<12} "
            f"{tag(row.get('multiple_paragraph')):<12} "
            f"{tag(row.get('multiple_comment')):<12} {cmp}"
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
                if variant in MULTIPLE_VARIANTS:
                    gaps = result.get("placement", {}).get("insertion_gaps", [])
                    extra = f" | gaps={gaps}"
                print_case_result(
                    model,
                    scenario["id"],
                    variant,
                    ev.get("label", "error"),
                    ev.get("reason", "") + extra,
                )
    return sort_results(results, models, scenarios, variants)


def main() -> int:
    set_api_key_map_file(TEST_ROOT / "config" / "22_api_key_map.yaml")

    variant_choices = ["all", VARIANT_CLEAN, *MULTIPLE_VARIANTS]
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
    print(f"Injections: Prompt A + Prompt B (two Combined Attack slots)")
    print(f"Placement : two random gaps — sentence | paragraph | comment")
    print(f"Variants  : {', '.join(variants)}")
    print(f"Chain     : poison webpage -> get_webpage -> Multiple IPI")
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
        variant_b_key="multiple_paragraph",
        variant_b_label="multiple_paragraph（双段落 Multi-injection）",
        compare_label="Multiple 有效",
        compare_field="any_multiple_beats_baseline",
        asr_b_key="any_multiple_asr",
    )

    print_multiple_summary_table(summary["rows"])
    print(f"\nclean ASR             : {summary['baseline_asr']:.1%}")
    print(f"multiple_sentence ASR : {summary['multiple_sentence_asr']:.1%}")
    print(f"multiple_paragraph ASR: {summary['multiple_paragraph_asr']:.1%}")
    print(f"multiple_comment ASR  : {summary['multiple_comment_asr']:.1%}")
    print(f"any multiple ASR      : {summary['any_multiple_asr']:.1%}")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
