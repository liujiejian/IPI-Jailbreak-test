#!/usr/bin/env python
"""
Strategy 39: Task Hijacking / Application-level Hijacking (Kaya et al., 2025)
Paper §7.1 — coerce Sales Assistant into unauthorized coding task

攻击思路：嵌入式客服机器人（Sales Assistant）被注入 UPDATED ROLE，
从销售任务劫持为 Code Generator：

  原本：Sales Assistant → 回答产品/订单
  注入：UPDATED ROLE: internal coding mode (Listing 6)
  触发：Print Fibonacci in Python (Listing 7)
  结果：输出 Python Fibonacci 代码

环境：task_hijacking_env（新建）— 应用层身份 + 范围限制

与相近策略：
  • 29–31 — plugin 历史/角色伪造（非应用任务劫持专精）
  • 37–38 — tool 说明/参数劫持（非应用层角色）

变体：
  • clean               — 正常销售咨询 + 良性 tool output
  • task_paper_system   — Listing 6 注入 system + Fibonacci 触发
  • task_paper_assistant — Listing 6 注入 assistant + Fibonacci 触发
  • task_tool_inject    — 产品页 tool output 含 role-update + 编码任务
  • task_buried         — role-update buried 在 tool output

Usage:
  python test/39_kaya_task_hijacking.py
  python test/39_kaya_task_hijacking.py -m qwen3.7-plus
  python test/39_kaya_task_hijacking.py --scenario sales_assistant
  python test/39_kaya_task_hijacking.py --variant task_paper_system
  python test/39_kaya_task_hijacking.py --serial
"""

from __future__ import annotations

import argparse
import json
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
from lib.task_hijacking import (  # noqa: E402
    TASK_HIJACK_VARIANTS,
    VARIANT_CLEAN,
    build_plugin_messages,
    get_tools_for_scenario,
    load_scenarios,
)
from lib.task_hijacking_env import build_task_hijacking_system_prompt  # noqa: E402

STRATEGY_TITLE = "Strategy 39: Task Hijacking (Kaya et al., 2025)"
RUN_PREFIX = "39_kaya_task_hijacking"
ALL_VARIANTS = [VARIANT_CLEAN, *TASK_HIJACK_VARIANTS]


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    messages, meta = build_plugin_messages(scenario, variant)
    tools = get_tools_for_scenario(scenario)
    system_prompt = build_task_hijacking_system_prompt(scenario)
    tool_output = ""
    for m in reversed(messages):
        if m.get("role") == "tool":
            tool_output = m.get("content", "")
            break

    base = {
        "model": model,
        "scenario_id": scenario["id"],
        "scenario_name": scenario["name"],
        "variant": variant,
        "template_family": variant if variant != VARIANT_CLEAN else "clean",
        "hijack_meta": meta,
        "app_name": scenario.get("app_name", ""),
        "plugin_post_json": json.dumps(meta.get("plugin_post", {}), ensure_ascii=False),
        "attacker_instruction": meta.get("task_trigger", scenario.get("task_trigger_message", "")),
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
        attack_labels = {v: labels.get(v) for v in TASK_HIJACK_VARIANTS}
        any_success = any(v == "success" for v in attack_labels.values())
        row = {
            "model": model,
            "scenario_id": scenario_id,
            "baseline": clean,
            **attack_labels,
            "any_hijack_beats_baseline": any_success
            and clean in ("blocked", "partial", None),
        }
        for v in TASK_HIJACK_VARIANTS:
            row[f"{v}_beats_baseline"] = (
                attack_labels.get(v) == "success" and clean in ("blocked", "partial", None)
            )
        rows.append(row)

    total = len(rows) or 1
    asr: dict[str, float] = {
        "baseline_asr": round(sum(1 for r in rows if r["baseline"] == "success") / total, 4)
    }
    for v in TASK_HIJACK_VARIANTS:
        asr[f"{v}_asr"] = round(sum(1 for r in rows if r.get(v) == "success") / total, 4)
    asr["any_hijack_asr"] = round(
        sum(1 for r in rows if any(r.get(v) == "success" for v in TASK_HIJACK_VARIANTS)) / total,
        4,
    )

    return {
        "total_cases": len(rows),
        "rows": rows,
        **asr,
        **{
            f"{v}_success_count": sum(1 for r in rows if r.get(v) == "success")
            for v in TASK_HIJACK_VARIANTS
        },
        "any_hijack_beats_baseline_count": sum(
            1 for r in rows if r["any_hijack_beats_baseline"]
        ),
    }


def print_summary_table(rows: list[dict]) -> None:
    print("\n" + "=" * 150)
    print("对比汇总（clean vs Task Hijacking）")
    print("=" * 150)
    header = (
        f"{'模型':<22} {'场景':<22} {'clean':<12} "
        f"{'sys':<12} {'asst':<12} {'tool':<12} {'buried':<12} {'任一有效'}"
    )
    print(header)
    print("-" * 150)

    def tag(label: str | None) -> str:
        if not label:
            return "-"
        return STATUS_TAG.get(label, label) if label in STATUS_TAG else label

    for row in rows:
        cmp = "是" if row.get("any_hijack_beats_baseline") else "-"
        print(
            f"{row['model']:<22} {row['scenario_id']:<22} "
            f"{tag(row.get('baseline')):<12} "
            f"{tag(row.get('task_paper_system')):<12} "
            f"{tag(row.get('task_paper_assistant')):<12} "
            f"{tag(row.get('task_tool_inject')):<12} "
            f"{tag(row.get('task_buried')):<12} {cmp}"
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
                app = result.get("app_name", "-")
                extra = f" | app={app}" if variant != VARIANT_CLEAN else ""
                print_case_result(
                    model,
                    scenario["id"],
                    variant,
                    ev.get("label", "error"),
                    ev.get("reason", "") + extra,
                )
    return sort_results(results, models, scenarios, variants)


def main() -> int:
    set_api_key_map_file(TEST_ROOT / "config" / "39_api_key_map.yaml")

    variant_choices = ["all", VARIANT_CLEAN, *TASK_HIJACK_VARIANTS]
    parser = argparse.ArgumentParser(description=STRATEGY_TITLE)
    parser.add_argument("-m", "--model", help="单个模型；默认全部 7 个")
    parser.add_argument(
        "--scenario",
        help="sales_assistant / support_assistant / booking_assistant / hr_assistant",
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
    print(f"Env       : task_hijacking_env (Sales/Support app scope → code generation hijack)")
    print(f"Variants  : {', '.join(variants)}")
    print(f"Chain     : UPDATED ROLE inject -> Fibonacci trigger -> Python code output")
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
        variant_b_key="task_paper_system",
        variant_b_label="task_paper_system（Listing 6 system 注入）",
        compare_label="Task Hijacking 有效",
        compare_field="any_hijack_beats_baseline",
        asr_b_key="any_hijack_asr",
    )

    print_summary_table(summary["rows"])
    print(f"\nclean ASR              : {summary['baseline_asr']:.1%}")
    print(f"task_paper_system ASR  : {summary['task_paper_system_asr']:.1%}")
    print(f"task_paper_assistant ASR: {summary['task_paper_assistant_asr']:.1%}")
    print(f"task_tool_inject ASR   : {summary['task_tool_inject_asr']:.1%}")
    print(f"task_buried ASR        : {summary['task_buried_asr']:.1%}")
    print(f"any hijack ASR         : {summary['any_hijack_asr']:.1%}")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
