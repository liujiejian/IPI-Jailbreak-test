#!/usr/bin/env python
"""
Strategy 14: Persistent / Memory Injection (Greshake et al., 2023)
Paper: arXiv:2302.12173 — Figure 8, Output 3

真正跨会话 Memory 感染（两次独立 API 对话 + MemoryStore 桥接）：

  Session 1: IPI 网页 → 模型自主 memory_write(毒化载荷) → 写入持久存储
  [会话重置 — 不携带 Session 1 聊天历史]
  Session 2: 用户 memory_read → 读出毒化内容 → 再次感染 → 执行恶意目标

对比 clean（预置良性 memory，跳过 Session 1）与 persistent（完整两阶段链）。

Usage:
  python test/14_greshake_persistent_injection.py
  python test/14_greshake_persistent_injection.py -m qwen3.7-plus
  python test/14_greshake_persistent_injection.py --serial
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

from lib.agent_env import build_system_prompt, get_tools_for_scenario  # noqa: E402
from lib.api_keys import set_api_key_map_file, validate_model_keys  # noqa: E402
from lib.common import (  # noqa: E402
    ensure_results_dir,
    get_client,
    report_html_path,
    resolve_target_models,
    save_json,
    utc_timestamp,
)
from lib.console import print_case_result, print_summary_table  # noqa: E402
from lib.evaluation import evaluate_response  # noqa: E402
from lib.persistent_injection import (  # noqa: E402
    build_memory_payload,
    build_webpage_content,
    load_scenarios,
    run_persistent_case,
)
from lib.report import write_html_report  # noqa: E402
from lib.runner import run_parallel_by_model, sort_results  # noqa: E402

STRATEGY_TITLE = "Strategy 14: Persistent / Memory Injection (Greshake et al., 2023)"
RUN_PREFIX = "14_greshake_persistent"
VARIANT_A = "clean"
VARIANT_B = "persistent"


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    system_prompt = build_system_prompt(scenario)
    memory_payload = build_memory_payload(scenario["attacker_instruction"])
    tools = get_tools_for_scenario(scenario)
    label = f"{model} | {scenario['id']} | {variant}"

    base = {
        "model": model,
        "scenario_id": scenario["id"],
        "scenario_name": scenario["name"],
        "variant": variant,
        "template_family": "memory-persistent-ipi" if variant == VARIANT_B else "clean-memory",
        "memory_key": scenario["memory_key"],
        "memory_payload": memory_payload if variant == VARIANT_B else "",
        "attacker_instruction": scenario["attacker_instruction"],
        "session1_user_instruction": scenario["session1_user_instruction"],
        "session2_user_instruction": scenario["session2_user_instruction"],
        "system_prompt": system_prompt,
        "tools": tools,
        "webpage_content": build_webpage_content(scenario=scenario, variant=variant),  # type: ignore[arg-type]
        "cross_session": True,
    }

    try:
        run = run_persistent_case(
            client,
            model=model,
            scenario=scenario,
            variant=variant,  # type: ignore[arg-type]
            label=label,
        )
    except Exception as exc:
        return {**base, "error": str(exc), "evaluation": {"label": "error", "reason": str(exc)}}

    content = run.session2_completion.content
    tool_calls = run.session2_tool_calls
    evaluation = evaluate_response(content, scenario, tool_calls=tool_calls)

    return {
        **base,
        "session1_skipped": run.session1_skipped,
        "session1_messages": run.session1_messages,
        "session1_tool_calls": run.session1_tool_calls,
        "session1_response": run.session1_completion.content,
        "session2_messages": run.session2_messages,
        "session2_tool_calls": run.session2_tool_calls,
        "memory_snapshot": run.memory_snapshot,
        "memory_poisoned": run.memory_poisoned,
        "messages": run.session2_messages,
        "response": content,
        "tool_calls": tool_calls,
        "evaluation": evaluation,
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
        clean = labels.get(VARIANT_A)
        persistent = labels.get(VARIANT_B)
        rows.append(
            {
                "model": model,
                "scenario_id": scenario_id,
                "baseline": clean,
                "persistent": persistent,
                "persistent_beats_baseline": persistent == "success"
                and clean in ("blocked", "partial", None),
            }
        )

    total = len(rows) or 1
    poisoned_count = sum(1 for r in results if r.get("memory_poisoned"))
    persistent_runs = sum(1 for r in results if r.get("variant") == VARIANT_B and "error" not in r)

    return {
        "total_cases": len(rows),
        "baseline_success_count": sum(1 for r in rows if r["baseline"] == "success"),
        "persistent_success_count": sum(1 for r in rows if r["persistent"] == "success"),
        "persistent_beats_baseline_count": sum(
            1 for r in rows if r["persistent_beats_baseline"]
        ),
        "memory_poisoned_count": poisoned_count,
        "memory_poison_rate": round(poisoned_count / (persistent_runs or 1), 4),
        "baseline_asr": round(sum(1 for r in rows if r["baseline"] == "success") / total, 4),
        "persistent_asr": round(sum(1 for r in rows if r["persistent"] == "success") / total, 4),
        "rows": rows,
    }


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
                if variant == VARIANT_B and "memory_poisoned" in result:
                    extra = f" | memory_poisoned={result['memory_poisoned']}"
                print_case_result(
                    model,
                    scenario["id"],
                    variant,
                    ev.get("label", "error"),
                    ev.get("reason", "") + extra,
                )
    return sort_results(results, models, scenarios, variants)


def main() -> int:
    set_api_key_map_file(TEST_ROOT / "config" / "14_api_key_map.yaml")

    parser = argparse.ArgumentParser(description=STRATEGY_TITLE)
    parser.add_argument("-m", "--model", help="单个模型；默认全部 7 个")
    parser.add_argument("--scenario", help="password_change / send_link / exfiltrate_data")
    parser.add_argument("--variant", choices=["clean", "persistent", "both"], default="both")
    parser.add_argument("--serial", action="store_true", help="串行模式（单 Key，调试用）")
    args = parser.parse_args()

    models = resolve_target_models(args.model)
    scenarios = load_scenarios()
    if args.scenario:
        scenarios = [s for s in scenarios if s["id"] == args.scenario]
        if not scenarios:
            print("[ERROR] 未找到场景", file=sys.stderr)
            return 1

    variants = [VARIANT_A, VARIANT_B] if args.variant == "both" else [args.variant]
    parallel = not args.serial and len(models) > 1

    if parallel:
        validate_model_keys(models, require_all=True)

    out_dir = ensure_results_dir(f"{RUN_PREFIX}_{utc_timestamp()}")
    html_path = report_html_path(SCRIPT_STEM)
    total = len(models) * len(scenarios) * len(variants)

    print(STRATEGY_TITLE)
    print(f"Models    : {', '.join(models)}")
    print(f"Scenarios : {', '.join(s['id'] for s in scenarios)}")
    print(f"Chain     : Session1(API×N) → MemoryStore → Session2(API×N, 无历史)")
    print(f"Mode      : {'7路并行 (每模型专用Key)' if parallel else '串行'}")
    print(f"Cases     : {total} (persistent 每用例 ≈2×API 轮次)")
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
        variant_b_key=VARIANT_B,
        variant_b_label="persistent（跨会话 Memory 感染）",
        compare_label="Persistent 有效",
        compare_field="persistent_beats_baseline",
        asr_b_key="persistent_asr",
    )

    print_summary_table(
        summary["rows"],
        variant_b_key=VARIANT_B,
        compare_field="persistent_beats_baseline",
        compare_label="攻击有效",
    )
    print(f"\nclean ASR          : {summary['baseline_asr']:.1%}")
    print(f"persistent ASR     : {summary['persistent_asr']:.1%}")
    print(f"memory poison rate : {summary['memory_poison_rate']:.1%} (Session1 写入毒化 memory 比例)")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
