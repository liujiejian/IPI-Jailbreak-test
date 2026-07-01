#!/usr/bin/env python
"""
Strategy 3: Mixture-of-Templates (MoT)
Paper: Chang et al., 2026 — arXiv:2509.22830, ICLR 2026

攻击者未知 Agent 底层模型，在 Tool Output 中同时注入 Qwen + Llama + GPT + Gemma + … 模板。
对比 baseline（One-shot）与 mot（模板混合）。

Usage:
  python test/3_chatinject_mixture_templates.py
  python test/3_chatinject_mixture_templates.py -m qwen3.7-plus
  python test/3_chatinject_mixture_templates.py --serial
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
from lib.chatinject import build_agent_messages, get_tools_for_scenario  # noqa: E402
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
from lib.mot import MOT_TEMPLATES, build_tool_output, load_scenarios  # noqa: E402
from lib.report import write_html_report  # noqa: E402
from lib.runner import run_parallel_by_model, sort_results  # noqa: E402
from lib.templates import get_template_for_model  # noqa: E402

STRATEGY_TITLE = "Strategy 3: Mixture-of-Templates (MoT)"
RUN_PREFIX = "3_chatinject_mot"
VARIANT_A = "baseline"
VARIANT_B = "mot"


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    tool_output = build_tool_output(
        benign_prefix=scenario["benign_prefix"],
        attacker_instruction=scenario["attacker_instruction"],
        variant=variant,  # type: ignore[arg-type]
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

    # MoT: attacker does not know model — record oracle template only for reference
    oracle = get_template_for_model(model)
    mot_names = [t.name for t in MOT_TEMPLATES]

    base = {
        "model": model,
        "scenario_id": scenario["id"],
        "scenario_name": scenario["name"],
        "variant": variant,
        "template_family": "mixture-of-templates" if variant == "mot" else "baseline",
        "mot_templates": mot_names if variant == "mot" else [],
        "oracle_template": oracle.name,
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
        baseline = labels.get(VARIANT_A)
        mot = labels.get(VARIANT_B)
        rows.append(
            {
                "model": model,
                "scenario_id": scenario_id,
                "baseline": baseline,
                "mot": mot,
                "mot_beats_baseline": mot == "success"
                and baseline in ("blocked", "partial", None),
            }
        )

    total = len(rows) or 1
    return {
        "total_cases": len(rows),
        "baseline_success_count": sum(1 for r in rows if r["baseline"] == "success"),
        "mot_success_count": sum(1 for r in rows if r["mot"] == "success"),
        "mot_beats_baseline_count": sum(1 for r in rows if r["mot_beats_baseline"]),
        "baseline_asr": round(sum(1 for r in rows if r["baseline"] == "success") / total, 4),
        "mot_asr": round(sum(1 for r in rows if r["mot"] == "success") / total, 4),
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
                print_case_result(
                    model, scenario["id"], variant, ev.get("label", "error"), ev.get("reason", "")
                )
    return sort_results(results, models, scenarios, variants)


def main() -> int:
    set_api_key_map_file(TEST_ROOT / "config" / "3_api_key_map.yaml")

    parser = argparse.ArgumentParser(description=STRATEGY_TITLE)
    parser.add_argument("-m", "--model", help="单个模型；默认全部 7 个")
    parser.add_argument("--scenario", help="password_change / send_link / exfiltrate_data")
    parser.add_argument("--variant", choices=["baseline", "mot", "both"], default="both")
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
    mot_list = ", ".join(t.name for t in MOT_TEMPLATES)

    print(STRATEGY_TITLE)
    print(f"Models    : {', '.join(models)}")
    print(f"Scenarios : {', '.join(s['id'] for s in scenarios)}")
    print(f"MoT mix   : {mot_list}")
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
        variant_b_key=VARIANT_B,
        variant_b_label="mot（模板混合）",
        compare_label="MoT 更强",
        compare_field="mot_beats_baseline",
        asr_b_key="mot_asr",
    )

    print_summary_table(
        summary["rows"],
        variant_b_key=VARIANT_B,
        compare_field="mot_beats_baseline",
        compare_label="MoT更强",
    )
    print(f"\nbaseline ASR : {summary['baseline_asr']:.1%}")
    print(f"MoT ASR      : {summary['mot_asr']:.1%}")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
