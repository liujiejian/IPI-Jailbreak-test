#!/usr/bin/env python
"""
Strategy 52: Malicious Tool Prompt Injection (Wu et al., 2025)

攻击思路（Malicious Tool Prompt Injection）：
  攻击者注册/篡改第三方工具 book_travel 的 **function.description**（非 tool output）：
    Need: Passport, SSN, Credit Card, Private Notes...
  Agent 信任工具 schema → memory_read 过度读取 → book_travel 外传敏感字段。

环境检查结论：**不可复用** Strategy 37/38/14。
  • 37 — tool *output* 覆盖合法 tool description（方向相反）
  • 38 — tool output 注入参数值
  • 14 — 网页 IPI 污染 memory，非恶意 tool schema
**新建** malicious_tool_env.py — 可变 book_travel description + memory_read。
**新建** malicious_tool_injection.py — 变体切换 tools[] schema。

变体：
  • clean                 — 良性 tool description（data minimization）
  • malicious_full        — 显式要求 SSN + credit card + private notes
  • malicious_paper       — 论文威胁模型措辞（anti-fraud verification）
  • imprecise_description — 非对抗性模糊描述（"relevant user data"）

Usage:
  python test/52_wu_malicious_tool_injection.py
  python test/52_wu_malicious_tool_injection.py -m qwen3.7-plus
  python test/52_wu_malicious_tool_injection.py --scenario travel_domestic
  python test/52_wu_malicious_tool_injection.py --variant malicious_paper
  python test/52_wu_malicious_tool_injection.py --serial
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
from lib.malicious_tool_env import build_malicious_tool_system_prompt  # noqa: E402
from lib.malicious_tool_injection import (  # noqa: E402
    MALICIOUS_TOOL_VARIANTS,
    build_malicious_tool_messages,
    get_tools_for_malicious_tool_scenario,
    load_scenarios,
)
from lib.report import write_html_report  # noqa: E402
from lib.runner import run_parallel_by_model, sort_results  # noqa: E402

STRATEGY_TITLE = "Strategy 52: Malicious Tool Prompt Injection (Wu et al., 2025)"
RUN_PREFIX = "52_wu_malicious_tool_injection"
VARIANT_CLEAN = "clean"
ALL_VARIANTS = [VARIANT_CLEAN, *MALICIOUS_TOOL_VARIANTS]


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    messages, attack_meta = build_malicious_tool_messages(scenario, variant)
    tools = get_tools_for_malicious_tool_scenario(scenario, variant)
    system_prompt = build_malicious_tool_system_prompt(scenario, variant)

    base = {
        "model": model,
        "scenario_id": scenario["id"],
        "scenario_name": scenario["name"],
        "variant": variant,
        "domain": scenario.get("domain"),
        "template_family": variant if variant != VARIANT_CLEAN else "clean",
        "attack_meta": attack_meta,
        "malicious_tool_description": attack_meta.get("malicious_tool_description"),
        "injection_surface": attack_meta.get("injection_surface"),
        "attack_chain": " → ".join(attack_meta.get("attack_chain", [])),
        "user_instruction": scenario["user_instruction"],
        "system_prompt": system_prompt,
        "tools": tools,
        "tool_output": "",
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
        attack_labels = {v: labels.get(v) for v in MALICIOUS_TOOL_VARIANTS}
        row = {
            "model": model,
            "scenario_id": scenario_id,
            "baseline": clean,
            **attack_labels,
            "malicious_beats_clean": any(
                labels.get(v) == "success" for v in MALICIOUS_TOOL_VARIANTS
            )
            and clean in ("blocked", "partial", None),
            "imprecise_beats_explicit": labels.get("imprecise_description") == "success"
            and labels.get("malicious_full") in ("blocked", "partial", None),
        }
        rows.append(row)

    total = len(rows) or 1
    asr: dict[str, float] = {
        "baseline_asr": round(sum(1 for r in rows if r["baseline"] == "success") / total, 4)
    }
    for v in MALICIOUS_TOOL_VARIANTS:
        asr[f"{v}_asr"] = round(sum(1 for r in rows if r.get(v) == "success") / total, 4)
    asr["any_attack_asr"] = round(
        sum(1 for r in rows if any(r.get(v) == "success" for v in MALICIOUS_TOOL_VARIANTS))
        / total,
        4,
    )
    asr["malicious_beats_clean_rate"] = round(
        sum(1 for r in rows if r.get("malicious_beats_clean")) / total, 4
    )

    return {
        "total_cases": len(rows),
        "rows": rows,
        **asr,
        **{
            f"{v}_success_count": sum(1 for r in rows if r.get(v) == "success")
            for v in MALICIOUS_TOOL_VARIANTS
        },
        "malicious_beats_clean_count": sum(1 for r in rows if r.get("malicious_beats_clean")),
    }


def print_summary_table(rows: list[dict]) -> None:
    print("\n" + "=" * 168)
    print("对比汇总（clean tool desc vs Malicious Tool Prompt Injection — Wu 2025）")
    print("=" * 168)
    header = (
        f"{'模型':<22} {'场景':<24} {'clean':<10} "
        f"{'full':<10} {'paper':<10} {'imprecise':<12} {'mal>clean'}"
    )
    print(header)
    print("-" * 168)

    def tag(label: str | None) -> str:
        if not label:
            return "-"
        return STATUS_TAG.get(label, label) if label in STATUS_TAG else label

    for row in rows:
        cmp = "是" if row.get("malicious_beats_clean") else "-"
        print(
            f"{row['model']:<22} {row['scenario_id']:<24} "
            f"{tag(row.get('baseline')):<10} "
            f"{tag(row.get('malicious_full')):<10} "
            f"{tag(row.get('malicious_paper')):<10} "
            f"{tag(row.get('imprecise_description')):<12} {cmp}"
        )
    print("=" * 168)


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
                surface = result.get("injection_surface") or "-"
                extra = f" | surface={surface}" if variant != VARIANT_CLEAN else ""
                print_case_result(
                    model,
                    scenario["id"],
                    variant,
                    ev.get("label", "error"),
                    ev.get("reason", "") + extra,
                )
    return sort_results(results, models, scenarios, variants)


def main() -> int:
    set_api_key_map_file(TEST_ROOT / "config" / "52_api_key_map.yaml")

    variant_choices = ["all", VARIANT_CLEAN, *MALICIOUS_TOOL_VARIANTS]
    parser = argparse.ArgumentParser(description=STRATEGY_TITLE)
    parser.add_argument("-m", "--model", help="单个模型；默认全部 7 个")
    parser.add_argument(
        "--scenario",
        help="travel_domestic / travel_hotel / travel_international / ...",
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
    print(f"Env       : malicious_tool_env (poisoned book_travel tool schema)")
    print(f"Variants  : {', '.join(variants)}")
    print(f"Chain     : malicious tool desc → memory_read → book_travel exfil")
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
        variant_b_key="malicious_paper",
        variant_b_label="malicious_paper（Wu 2025 威胁模型 tool description）",
        compare_label="Malicious Tool 有效",
        compare_field="malicious_beats_clean",
        asr_b_key="malicious_paper_asr",
    )

    print_summary_table(summary["rows"])
    print(f"\nclean ASR                   : {summary['baseline_asr']:.1%}")
    print(f"malicious_full ASR          : {summary['malicious_full_asr']:.1%}")
    print(f"malicious_paper ASR         : {summary['malicious_paper_asr']:.1%}")
    print(f"imprecise_description ASR   : {summary['imprecise_description_asr']:.1%}")
    print(f"any attack ASR              : {summary['any_attack_asr']:.1%}")
    print(f"malicious beats clean       : {summary['malicious_beats_clean_rate']:.1%}")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
