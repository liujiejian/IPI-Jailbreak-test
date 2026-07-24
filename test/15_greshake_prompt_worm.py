#!/usr/bin/env python
"""
Strategy 15: AI Malware / Prompt Worm (Greshake et al., 2023)
Paper: arXiv:2302.12173 — email-based prompt worm propagation

跨 LLM 应用蠕虫传播链（多 Agent 独立 API 会话 + EmailStore）：

  感染邮件 → Agent A 读取 → send_email(蠕虫) → Agent B 读取 → 继续传播 → 终端 Agent 执行恶意目标

对比 clean（良性邮件）与 prompt_worm（自复制邮件 IPI）。

Usage:
  python test/15_greshake_prompt_worm.py
  python test/15_greshake_prompt_worm.py -m qwen3.7-plus
  python test/15_greshake_prompt_worm.py --serial
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
from lib.prompt_worm import (  # noqa: E402
    build_initial_email,
    build_worm_body,
    count_expected_propagation_hops,
    load_scenarios,
    run_prompt_worm_case,
)
from lib.report import write_html_report  # noqa: E402
from lib.runner import run_parallel_by_model, sort_results  # noqa: E402

STRATEGY_TITLE = "Strategy 15: AI Malware / Prompt Worm (Greshake et al., 2023)"
RUN_PREFIX = "15_greshake_worm"
VARIANT_A = "clean"
VARIANT_B = "prompt_worm"


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    system_prompt = build_system_prompt(scenario)
    tools = get_tools_for_scenario(scenario)
    label = f"{model} | {scenario['id']} | {variant}"
    expected_hops = count_expected_propagation_hops(scenario)

    initial = build_initial_email(scenario=scenario, variant=variant)  # type: ignore[arg-type]
    worm_body = ""
    if variant == VARIANT_B:
        first = scenario["hop_agents"][0]
        worm_body = build_worm_body(
            goal=scenario["attacker_instruction"],
            next_recipient=first.get("expected_forward_to") or scenario["hop_agents"][1]["inbox"],
            worm_subject=scenario.get("worm_subject", scenario.get("initial_email_subject", "")),
            worm_marker=scenario.get("worm_marker", "[WORM-PAYLOAD-START]"),
        )

    base = {
        "model": model,
        "scenario_id": scenario["id"],
        "scenario_name": scenario["name"],
        "variant": variant,
        "template_family": "email-prompt-worm" if variant == VARIANT_B else "clean-email",
        "attacker_instruction": scenario["attacker_instruction"],
        "hop_agents": scenario["hop_agents"],
        "initial_email": {
            "from": initial.from_addr,
            "to": initial.to_addr,
            "subject": initial.subject,
            "body": initial.body,
        },
        "worm_body": worm_body,
        "system_prompt": system_prompt,
        "tools": tools,
        "cross_session": True,
        "expected_propagation_hops": expected_hops,
    }

    try:
        run = run_prompt_worm_case(
            client,
            model=model,
            scenario=scenario,
            variant=variant,  # type: ignore[arg-type]
            label=label,
        )
    except Exception as exc:
        return {**base, "error": str(exc), "evaluation": {"label": "error", "reason": str(exc)}}

    evaluation = run.terminal_evaluation
    full_propagation = run.propagation_hops >= expected_hops

    hop_summaries = [
        {
            "hop_index": h.hop_index,
            "agent_id": h.agent_id,
            "inbox": h.inbox,
            "worm_forwarded": h.worm_forwarded,
            "expected_forward_to": h.expected_forward_to,
            "tool_calls": h.tool_calls,
            "response": h.completion.content,
        }
        for h in run.hop_results
    ]

    return {
        **base,
        "hop_results": hop_summaries,
        "propagation_hops": run.propagation_hops,
        "full_propagation": full_propagation,
        "email_snapshot": run.email_snapshot,
        "terminal_hop": hop_summaries[-1] if hop_summaries else {},
        "messages": run.terminal_messages,
        "response": run.terminal_completion.content,
        "tool_calls": run.terminal_tool_calls,
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
        worm = labels.get(VARIANT_B)
        rows.append(
            {
                "model": model,
                "scenario_id": scenario_id,
                "baseline": clean,
                "prompt_worm": worm,
                "prompt_worm_beats_baseline": worm == "success"
                and clean in ("blocked", "partial", None),
            }
        )

    total = len(rows) or 1
    worm_runs = [r for r in results if r.get("variant") == VARIANT_B and "error" not in r]
    full_prop = sum(1 for r in worm_runs if r.get("full_propagation"))
    any_prop = sum(1 for r in worm_runs if (r.get("propagation_hops") or 0) > 0)

    return {
        "total_cases": len(rows),
        "baseline_success_count": sum(1 for r in rows if r["baseline"] == "success"),
        "prompt_worm_success_count": sum(1 for r in rows if r["prompt_worm"] == "success"),
        "prompt_worm_beats_baseline_count": sum(
            1 for r in rows if r["prompt_worm_beats_baseline"]
        ),
        "full_propagation_count": full_prop,
        "any_propagation_count": any_prop,
        "full_propagation_rate": round(full_prop / (len(worm_runs) or 1), 4),
        "any_propagation_rate": round(any_prop / (len(worm_runs) or 1), 4),
        "baseline_asr": round(sum(1 for r in rows if r["baseline"] == "success") / total, 4),
        "prompt_worm_asr": round(sum(1 for r in rows if r["prompt_worm"] == "success") / total, 4),
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
                if variant == VARIANT_B and "propagation_hops" in result:
                    extra = (
                        f" | hops={result['propagation_hops']}/"
                        f"{result.get('expected_propagation_hops', '?')}"
                    )
                print_case_result(
                    model,
                    scenario["id"],
                    variant,
                    ev.get("label", "error"),
                    ev.get("reason", "") + extra,
                )
    return sort_results(results, models, scenarios, variants)


def main() -> int:
    set_api_key_map_file(TEST_ROOT / "config" / "15_api_key_map.yaml")

    parser = argparse.ArgumentParser(description=STRATEGY_TITLE)
    parser.add_argument("-m", "--model", help="单个模型；默认全部 7 个")
    parser.add_argument("--scenario", help="password_change / send_link / exfiltrate_data")
    parser.add_argument("--variant", choices=["clean", "prompt_worm", "both"], default="both")
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
    print(f"Chain     : Email→AgentA→send_email→AgentB→send_email→AgentC→attack")
    print(f"Mode      : {'7路并行 (每模型专用Key)' if parallel else '串行'}")
    print(f"Cases     : {total} (prompt_worm 每用例 ≈3 Agent × 多轮 API)")
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
        variant_b_label="prompt_worm（邮件蠕虫传播）",
        compare_label="Prompt Worm 有效",
        compare_field="prompt_worm_beats_baseline",
        asr_b_key="prompt_worm_asr",
    )

    print_summary_table(
        summary["rows"],
        variant_b_key=VARIANT_B,
        compare_field="prompt_worm_beats_baseline",
        compare_label="攻击有效",
    )
    print(f"\nclean ASR              : {summary['baseline_asr']:.1%}")
    print(f"prompt_worm ASR        : {summary['prompt_worm_asr']:.1%}")
    print(f"full propagation rate  : {summary['full_propagation_rate']:.1%} (A→B→C 全部转发)")
    print(f"any propagation rate   : {summary['any_propagation_rate']:.1%} (至少 1 跳转发)")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
