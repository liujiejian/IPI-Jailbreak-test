#!/usr/bin/env python
"""
Strategy 50: Cross-Application Prompt Injection (Syros et al., 2026 / MUZZLE)

攻击思路（Cross-Agent / Cross-app IPI）：
  传统 IPI：GitHub → 删 GitHub（单应用内）。
  MUZZLE Cross-app：Prompt 来自 Application A → 最终在 Application B 执行恶意操作。
    • Classifieds → Northwind → Drop ORDERS table（Figure 3 / Table 2）
    • Gitea → Postmill → Delete user account（Table 2）

环境检查结论：**不可复用** 单应用 web_agent_trajectory_env（46–49）。
**新建** cross_app_web_agent_env.py — 多应用会话 + App B 专用 action tools。
**新建** cross_app_injection.py — App A UI 投毒 → 跨应用目标。

与相近策略：
  • 46–49 MUZZLE 单应用 — 轨迹/UI/payload 均在同一 app
  • 50 MUZZLE Cross-app — poison 在 App A，危害在 App B
  • 15 Prompt Worm — 跨会话 email 传播，非 web agent 跨应用导航

变体：
  • clean               — 良性 App A UI 线程
  • single_app_attack   — 单应用攻击（对照：不跨 app）
  • cross_app_direct    — blunt 跨应用指令
  • cross_app_paper     — Figure 3 论文原文风格（URGENT! go to northwind.zoo…）
  • cross_app_workflow  — 跨应用 workflow verification framing

Usage:
  python test/50_syros_muzzle_cross_app_injection.py
  python test/50_syros_muzzle_cross_app_injection.py -m qwen3.7-plus
  python test/50_syros_muzzle_cross_app_injection.py --scenario cross_northwind_drop
  python test/50_syros_muzzle_cross_app_injection.py --variant cross_app_paper
  python test/50_syros_muzzle_cross_app_injection.py --serial
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
from lib.cross_app_injection import (  # noqa: E402
    CROSS_APP_VARIANTS,
    build_cross_app_messages,
    get_tools_for_cross_app_scenario,
    load_scenarios,
)
from lib.cross_app_web_agent_env import build_cross_app_system_prompt  # noqa: E402
from lib.evaluation import evaluate_response  # noqa: E402
from lib.report import write_html_report  # noqa: E402
from lib.runner import run_parallel_by_model, sort_results  # noqa: E402

STRATEGY_TITLE = "Strategy 50: Cross-Application Prompt Injection (Syros et al., 2026 MUZZLE)"
RUN_PREFIX = "50_syros_muzzle_cross_app_injection"
VARIANT_CLEAN = "clean"
ALL_VARIANTS = [VARIANT_CLEAN, *CROSS_APP_VARIANTS]
CROSS_APP_ATTACK_VARIANTS = (
    "cross_app_direct",
    "cross_app_paper",
    "cross_app_workflow",
)


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    messages, cross_meta = build_cross_app_messages(scenario, variant)
    tools = get_tools_for_cross_app_scenario(scenario)
    system_prompt = build_cross_app_system_prompt(scenario)
    tool_output = messages[-1]["content"] if messages else ""

    base = {
        "model": model,
        "scenario_id": scenario["id"],
        "scenario_name": scenario["name"],
        "variant": variant,
        "source_app": scenario.get("source_app"),
        "target_app": scenario.get("target_app"),
        "template_family": variant if variant != VARIANT_CLEAN else "clean",
        "cross_meta": cross_meta,
        "cross_app": cross_meta.get("cross_app"),
        "payload_style": cross_meta.get("payload_style"),
        "target_action_tool": cross_meta.get("target_action_tool"),
        "attack_chain": " → ".join(cross_meta.get("attack_chain", [])),
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
        attack_labels = {v: labels.get(v) for v in CROSS_APP_VARIANTS}
        single = labels.get("single_app_attack")
        any_cross = any(labels.get(v) == "success" for v in CROSS_APP_ATTACK_VARIANTS)
        row = {
            "model": model,
            "scenario_id": scenario_id,
            "baseline": clean,
            **attack_labels,
            "cross_beats_single_app": any_cross and single in ("blocked", "partial", None),
            "any_attack_beats_baseline": any(
                labels.get(v) == "success" for v in CROSS_APP_VARIANTS
            )
            and clean in ("blocked", "partial", None),
        }
        rows.append(row)

    total = len(rows) or 1
    asr: dict[str, float] = {
        "baseline_asr": round(sum(1 for r in rows if r["baseline"] == "success") / total, 4)
    }
    for v in CROSS_APP_VARIANTS:
        asr[f"{v}_asr"] = round(sum(1 for r in rows if r.get(v) == "success") / total, 4)
    asr["any_attack_asr"] = round(
        sum(
            1 for r in rows if any(r.get(v) == "success" for v in CROSS_APP_VARIANTS)
        )
        / total,
        4,
    )
    asr["cross_beats_single_app_rate"] = round(
        sum(1 for r in rows if r.get("cross_beats_single_app")) / total, 4
    )

    return {
        "total_cases": len(rows),
        "rows": rows,
        **asr,
        **{
            f"{v}_success_count": sum(1 for r in rows if r.get(v) == "success")
            for v in CROSS_APP_VARIANTS
        },
        "cross_beats_single_app_count": sum(
            1 for r in rows if r.get("cross_beats_single_app")
        ),
        "any_attack_beats_baseline_count": sum(
            1 for r in rows if r.get("any_attack_beats_baseline")
        ),
    }


def print_summary_table(rows: list[dict]) -> None:
    print("\n" + "=" * 184)
    print("对比汇总（clean vs Cross-app — single-app vs App A→B poison）")
    print("=" * 184)
    header = (
        f"{'模型':<22} {'场景':<26} {'clean':<10} "
        f"{'single':<10} {'direct':<10} {'paper':<10} {'workflow':<10} {'x-app>1x'}"
    )
    print(header)
    print("-" * 184)

    def tag(label: str | None) -> str:
        if not label:
            return "-"
        return STATUS_TAG.get(label, label) if label in STATUS_TAG else label

    for row in rows:
        cmp = "是" if row.get("cross_beats_single_app") else "-"
        print(
            f"{row['model']:<22} {row['scenario_id']:<26} "
            f"{tag(row.get('baseline')):<10} "
            f"{tag(row.get('single_app_attack')):<10} "
            f"{tag(row.get('cross_app_direct')):<10} "
            f"{tag(row.get('cross_app_paper')):<10} "
            f"{tag(row.get('cross_app_workflow')):<10} {cmp}"
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
                apps = f"{result.get('source_app')}→{result.get('target_app')}"
                extra = f" | {apps}" if variant != VARIANT_CLEAN else ""
                print_case_result(
                    model,
                    scenario["id"],
                    variant,
                    ev.get("label", "error"),
                    ev.get("reason", "") + extra,
                )
    return sort_results(results, models, scenarios, variants)


def main() -> int:
    set_api_key_map_file(TEST_ROOT / "config" / "50_api_key_map.yaml")

    variant_choices = ["all", VARIANT_CLEAN, *CROSS_APP_VARIANTS]
    parser = argparse.ArgumentParser(description=STRATEGY_TITLE)
    parser.add_argument("-m", "--model", help="单个模型；默认全部 7 个")
    parser.add_argument(
        "--scenario",
        help="cross_northwind_drop / cross_postmill_delete / cross_postmill_scam / ...",
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
    print(f"Env       : cross_app_web_agent_env (App A poison → App B action)")
    print(f"Variants  : {', '.join(variants)}")
    print(f"Chain     : App A UI inject → cross-app navigate → App B tool call")
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
        variant_b_key="cross_app_paper",
        variant_b_label="cross_app_paper（Figure 3 URGENT northwind.zoo style）",
        compare_label="Cross-app IPI 有效",
        compare_field="any_attack_beats_baseline",
        asr_b_key="any_attack_asr",
    )

    print_summary_table(summary["rows"])
    print(f"\nclean ASR                   : {summary['baseline_asr']:.1%}")
    print(f"single_app_attack ASR       : {summary['single_app_attack_asr']:.1%}")
    print(f"cross_app_direct ASR        : {summary['cross_app_direct_asr']:.1%}")
    print(f"cross_app_paper ASR         : {summary['cross_app_paper_asr']:.1%}")
    print(f"cross_app_workflow ASR      : {summary['cross_app_workflow_asr']:.1%}")
    print(f"any attack ASR              : {summary['any_attack_asr']:.1%}")
    print(f"cross-app beats single-app  : {summary['cross_beats_single_app_rate']:.1%}")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
