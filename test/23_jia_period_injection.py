#!/usr/bin/env python
"""
Strategy 23: Period Adaptive Attack (Jia et al., 2025)
Paper: PromptLocate §5.3 — evade Step I sentence / semantic segmentation

在 Combined Attack 注入载荷中人为插入大量句号，使 Semantic Segmentation 失效：
  Ignore.
  Previous.
  Instructions.
  Print.
  ...

  xc = xt || PeriodFragmented(CombinedAttack)

论文实验默认在 injected prompt 中随机插入 5 个句号；专门针对 Sentence Split。

变体：
  • period_random — 随机 5 处句号（论文默认）
  • period_word   — 每词一行 + 句号（用户示例）
  • period_dense    — 所有空格替换为 ". "（密集句号）

对比 clean 与 3 种 Period 变体（默认跑全部）。

Usage:
  python test/23_jia_period_injection.py
  python test/23_jia_period_injection.py -m qwen3.7-plus
  python test/23_jia_period_injection.py --variant period_word
  python test/23_jia_period_injection.py --serial
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
from lib.period_injection import (  # noqa: E402
    PERIOD_VARIANTS,
    build_agent_messages,
    build_period_injection,
    build_tool_output,
    load_scenarios,
)
from lib.report import write_html_report  # noqa: E402
from lib.runner import run_parallel_by_model, sort_results  # noqa: E402

STRATEGY_TITLE = "Strategy 23: Period Adaptive Attack (Jia et al., 2025)"
RUN_PREFIX = "23_jia_period"
VARIANT_CLEAN = "clean"
ALL_VARIANTS = [VARIANT_CLEAN, *PERIOD_VARIANTS]


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    seed_key = f"{model}|{scenario['id']}|{variant}"
    tool_output, period_meta = build_tool_output(
        benign_prefix=scenario["benign_prefix"],
        attacker_instruction=scenario["attacker_instruction"],
        variant=variant,  # type: ignore[arg-type]
        seed_key=seed_key,
    )
    injection_block = period_meta.get("injection_block", "")
    if variant != VARIANT_CLEAN and not injection_block:
        injection_block, period_meta = build_period_injection(
            scenario["attacker_instruction"],
            variant,
            seed_key=seed_key,
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
        "period_meta": period_meta,
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
        period_labels = {v: labels.get(v) for v in PERIOD_VARIANTS}
        any_success = any(v == "success" for v in period_labels.values())
        row = {
            "model": model,
            "scenario_id": scenario_id,
            "baseline": clean,
            **period_labels,
            "any_period_beats_baseline": any_success
            and clean in ("blocked", "partial", None),
        }
        for v in PERIOD_VARIANTS:
            row[f"{v}_beats_baseline"] = (
                period_labels.get(v) == "success" and clean in ("blocked", "partial", None)
            )
        rows.append(row)

    total = len(rows) or 1
    asr: dict[str, float] = {
        "baseline_asr": round(sum(1 for r in rows if r["baseline"] == "success") / total, 4)
    }
    for v in PERIOD_VARIANTS:
        asr[f"{v}_asr"] = round(sum(1 for r in rows if r.get(v) == "success") / total, 4)
    asr["any_period_asr"] = round(
        sum(1 for r in rows if any(r.get(v) == "success" for v in PERIOD_VARIANTS)) / total,
        4,
    )

    return {
        "total_cases": len(rows),
        "rows": rows,
        **asr,
        **{f"{v}_success_count": sum(1 for r in rows if r.get(v) == "success") for v in PERIOD_VARIANTS},
        "any_period_beats_baseline_count": sum(1 for r in rows if r["any_period_beats_baseline"]),
    }


def print_period_summary_table(rows: list[dict]) -> None:
    print("\n" + "=" * 115)
    print("对比汇总（clean vs 三种 Period 句号碎片化）")
    print("=" * 115)
    header = (
        f"{'模型':<22} {'场景':<18} {'clean':<12} "
        f"{'random':<12} {'word':<12} {'dense':<12} {'任一有效'}"
    )
    print(header)
    print("-" * 115)

    def tag(label: str | None) -> str:
        if not label:
            return "-"
        return STATUS_TAG.get(label, label) if label in STATUS_TAG else label

    for row in rows:
        cmp = "是" if row.get("any_period_beats_baseline") else "-"
        print(
            f"{row['model']:<22} {row['scenario_id']:<18} "
            f"{tag(row.get('baseline')):<12} "
            f"{tag(row.get('period_random')):<12} "
            f"{tag(row.get('period_word')):<12} "
            f"{tag(row.get('period_dense')):<12} {cmp}"
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
                if variant in PERIOD_VARIANTS:
                    n = result.get("period_meta", {}).get("period_count_estimate", "?")
                    extra = f" | periods≈{n}"
                print_case_result(
                    model,
                    scenario["id"],
                    variant,
                    ev.get("label", "error"),
                    ev.get("reason", "") + extra,
                )
    return sort_results(results, models, scenarios, variants)


def main() -> int:
    set_api_key_map_file(TEST_ROOT / "config" / "23_api_key_map.yaml")

    variant_choices = ["all", VARIANT_CLEAN, *PERIOD_VARIANTS]
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
    print(f"Target    : Step I Sentence Split / Semantic Segmentation")
    print(f"Payload   : Combined Attack + period fragmentation")
    print(f"Variants  : {', '.join(variants)}")
    print(f"Chain     : poison webpage -> get_webpage -> Period IPI")
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
        variant_b_key="period_word",
        variant_b_label="period_word（逐词句号）",
        compare_label="Period 有效",
        compare_field="any_period_beats_baseline",
        asr_b_key="any_period_asr",
    )

    print_period_summary_table(summary["rows"])
    print(f"\nclean ASR         : {summary['baseline_asr']:.1%}")
    print(f"period_random ASR : {summary['period_random_asr']:.1%}")
    print(f"period_word ASR   : {summary['period_word_asr']:.1%}")
    print(f"period_dense ASR  : {summary['period_dense_asr']:.1%}")
    print(f"any period ASR    : {summary['any_period_asr']:.1%}")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
