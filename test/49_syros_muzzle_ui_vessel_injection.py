#!/usr/bin/env python
"""
Strategy 49: UI-based Prompt Injection (Syros et al., 2026 / MUZZLE §3.3)

攻击思路（UI Prompt Injection / Candidate Vessel Ranking）：
  传统 IPI 目标：Document / 纯文本语料。
  MUZZLE 目标：网页 UI 元素 — issue comment、forum reply、listing title、issue description…
  Grafter 按 visibility + exploitability + surface_size 排序，选最容易成功的 UI vessel。

环境检查结论：**复用** web_agent_trajectory_env（web agent DOM 感知链）。
**扩展** format_grafter_ui_ranking（三维度 UI 排序展示）。
**新建** ui_vessel_injection.py — 按 Grafter rank #1/#2/#3 UI 元素注入。

与相近策略：
  • 46 MUZZLE Trajectory — 轨迹 + 人工猜 vs Grafter #1（跨 app salient_* 变体）
  • 49 MUZZLE UI Vessel   — 同一 app 内 Grafter 三档 UI 元素排序 ablation

变体：
  • clean          — 良性 UI 线程
  • vessel_rank1   — Grafter #1 UI（comment/reply — 高 visibility，in T*）
  • vessel_rank2   — Grafter #2 UI（title — 高 exploitability，常 miss T*）
  • vessel_rank3   — Grafter #3 UI（description/body/title — 低 score 或 miss T*）

Usage:
  python test/49_syros_muzzle_ui_vessel_injection.py
  python test/49_syros_muzzle_ui_vessel_injection.py -m qwen3.7-plus
  python test/49_syros_muzzle_ui_vessel_injection.py --scenario password_change
  python test/49_syros_muzzle_ui_vessel_injection.py --variant vessel_rank1
  python test/49_syros_muzzle_ui_vessel_injection.py --serial
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
from lib.ui_vessel_injection import (  # noqa: E402
    UI_VESSEL_VARIANTS,
    build_ui_vessel_messages,
    get_tools_for_scenario,
    load_scenarios,
)
from lib.web_agent_trajectory_env import build_web_agent_system_prompt  # noqa: E402

STRATEGY_TITLE = "Strategy 49: UI-based Prompt Injection (Syros et al., 2026 MUZZLE)"
RUN_PREFIX = "49_syros_muzzle_ui_vessel_injection"
VARIANT_CLEAN = "clean"
ALL_VARIANTS = [VARIANT_CLEAN, *UI_VESSEL_VARIANTS]
RANK_VARIANTS = UI_VESSEL_VARIANTS


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    messages, ui_meta = build_ui_vessel_messages(scenario, variant)
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
        "ui_meta": ui_meta,
        "ui_element": ui_meta.get("ui_element"),
        "grafter_rank": ui_meta.get("grafter_rank"),
        "grafter_score": ui_meta.get("grafter_score"),
        "in_context": ui_meta.get("in_context"),
        "injection_surface": ui_meta.get("injection_surface"),
        "attack_chain": " → ".join(ui_meta.get("attack_chain", [])),
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
        attack_labels = {v: labels.get(v) for v in UI_VESSEL_VARIANTS}
        rank1 = labels.get("vessel_rank1")
        rank2 = labels.get("vessel_rank2")
        rank3 = labels.get("vessel_rank3")
        row = {
            "model": model,
            "scenario_id": scenario_id,
            "baseline": clean,
            **attack_labels,
            "rank1_beats_rank2_3": rank1 == "success"
            and rank2 in ("blocked", "partial", None)
            and rank3 in ("blocked", "partial", None),
            "any_attack_beats_baseline": any(
                labels.get(v) == "success" for v in UI_VESSEL_VARIANTS
            )
            and clean in ("blocked", "partial", None),
        }
        rows.append(row)

    total = len(rows) or 1
    asr: dict[str, float] = {
        "baseline_asr": round(sum(1 for r in rows if r["baseline"] == "success") / total, 4)
    }
    for v in UI_VESSEL_VARIANTS:
        asr[f"{v}_asr"] = round(sum(1 for r in rows if r.get(v) == "success") / total, 4)
    asr["any_attack_asr"] = round(
        sum(
            1 for r in rows if any(r.get(v) == "success" for v in UI_VESSEL_VARIANTS)
        )
        / total,
        4,
    )
    asr["rank1_beats_lower_ranks_rate"] = round(
        sum(1 for r in rows if r.get("rank1_beats_rank2_3")) / total, 4
    )

    return {
        "total_cases": len(rows),
        "rows": rows,
        **asr,
        **{
            f"{v}_success_count": sum(1 for r in rows if r.get(v) == "success")
            for v in UI_VESSEL_VARIANTS
        },
        "rank1_beats_lower_ranks_count": sum(
            1 for r in rows if r.get("rank1_beats_rank2_3")
        ),
        "any_attack_beats_baseline_count": sum(
            1 for r in rows if r.get("any_attack_beats_baseline")
        ),
    }


def print_summary_table(rows: list[dict]) -> None:
    print("\n" + "=" * 164)
    print("对比汇总（clean vs UI Vessel Ranking — Grafter #1/#2/#3 UI elements）")
    print("=" * 164)
    header = (
        f"{'模型':<22} {'场景':<24} {'clean':<10} "
        f"{'rank1':<10} {'rank2':<10} {'rank3':<10} {'#1>#2,#3'}"
    )
    print(header)
    print("-" * 164)

    def tag(label: str | None) -> str:
        if not label:
            return "-"
        return STATUS_TAG.get(label, label) if label in STATUS_TAG else label

    for row in rows:
        cmp = "是" if row.get("rank1_beats_rank2_3") else "-"
        print(
            f"{row['model']:<22} {row['scenario_id']:<24} "
            f"{tag(row.get('baseline')):<10} "
            f"{tag(row.get('vessel_rank1')):<10} "
            f"{tag(row.get('vessel_rank2')):<10} "
            f"{tag(row.get('vessel_rank3')):<10} {cmp}"
        )
    print("=" * 164)


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
                ui_el = result.get("ui_element") or "-"
                extra = f" | ui={ui_el}" if variant != VARIANT_CLEAN else ""
                print_case_result(
                    model,
                    scenario["id"],
                    variant,
                    ev.get("label", "error"),
                    ev.get("reason", "") + extra,
                )
    return sort_results(results, models, scenarios, variants)


def main() -> int:
    set_api_key_map_file(TEST_ROOT / "config" / "49_api_key_map.yaml")

    variant_choices = ["all", VARIANT_CLEAN, *UI_VESSEL_VARIANTS]
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
    print(f"Env       : web_agent_trajectory_env + Grafter UI Candidate Vessel Ranking")
    print(f"Variants  : {', '.join(variants)}")
    print(f"Chain     : T^b -> Grafter rank UI elements -> inject -> extract at j*")
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
        variant_b_key="vessel_rank1",
        variant_b_label="vessel_rank1（Grafter #1 UI — comment/reply，in T*）",
        compare_label="UI Vessel Ranking 有效",
        compare_field="any_attack_beats_baseline",
        asr_b_key="any_attack_asr",
    )

    print_summary_table(summary["rows"])
    print(f"\nclean ASR                 : {summary['baseline_asr']:.1%}")
    print(f"vessel_rank1 ASR          : {summary['vessel_rank1_asr']:.1%}")
    print(f"vessel_rank2 ASR          : {summary['vessel_rank2_asr']:.1%}")
    print(f"vessel_rank3 ASR          : {summary['vessel_rank3_asr']:.1%}")
    print(f"any attack ASR            : {summary['any_attack_asr']:.1%}")
    print(f"rank1 beats rank2+3       : {summary['rank1_beats_lower_ranks_rate']:.1%}")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
