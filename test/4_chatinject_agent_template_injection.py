#!/usr/bin/env python
"""
Strategy 4: Agent Template Injection (Reasoning + Tool-calling Hooks)
Paper: Chang et al., 2026 — arXiv:2509.22830, ICLR 2026

在 ChatInject（Strategy 1 oracle 模板）基础上，追加 Agent 专有 token：
  - Reasoning Hook：redacted_thinking + "Sure!" — 诱导模型认为已开始推理
  - Tool-calling Hook：tool_call 脚手架 — 诱导模型认为已进入工具调用阶段

Usage:
  python test/4_chatinject_agent_template_injection.py
  python test/4_chatinject_agent_template_injection.py -m qwen3.7-plus
  python test/4_chatinject_agent_template_injection.py --serial
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
from lib.agent_hooks import build_agent_messages, build_tool_output, load_scenarios  # noqa: E402
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
from lib.console import print_case_result, print_summary_table  # noqa: E402
from lib.evaluation import evaluate_response  # noqa: E402
from lib.report import write_html_report  # noqa: E402
from lib.runner import run_parallel_by_model, sort_results  # noqa: E402
from lib.templates import get_agent_hooks_for_model, get_template_for_model  # noqa: E402

STRATEGY_TITLE = "Strategy 4: Agent Template Injection (Reasoning + Tool-calling Hooks)"
RUN_PREFIX = "4_chatinject_agent"
VARIANT_A = "baseline"
VARIANT_B = "agent_hooks"


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    template = get_template_for_model(model)
    hooks = get_agent_hooks_for_model(model)
    action_tool = scenario.get("action_tool", "update_password")
    tool_output = build_tool_output(
        benign_prefix=scenario["benign_prefix"],
        attacker_instruction=scenario["attacker_instruction"],
        variant=variant,  # type: ignore[arg-type]
        template=template,
        action_tool=action_tool,
        hooks=hooks,
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
        "template_family": template.name,
        "agent_hooks_family": hooks.name if variant == VARIANT_B else "",
        "action_tool": action_tool,
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
        agent_hooks = labels.get(VARIANT_B)
        rows.append(
            {
                "model": model,
                "scenario_id": scenario_id,
                "baseline": baseline,
                "agent_hooks": agent_hooks,
                "agent_hooks_beats_baseline": agent_hooks == "success"
                and baseline in ("blocked", "partial", None),
            }
        )

    total = len(rows) or 1
    return {
        "total_cases": len(rows),
        "baseline_success_count": sum(1 for r in rows if r["baseline"] == "success"),
        "agent_hooks_success_count": sum(1 for r in rows if r["agent_hooks"] == "success"),
        "agent_hooks_beats_baseline_count": sum(
            1 for r in rows if r["agent_hooks_beats_baseline"]
        ),
        "baseline_asr": round(sum(1 for r in rows if r["baseline"] == "success") / total, 4),
        "agent_hooks_asr": round(sum(1 for r in rows if r["agent_hooks"] == "success") / total, 4),
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
    set_api_key_map_file(TEST_ROOT / "config" / "4_api_key_map.yaml")

    parser = argparse.ArgumentParser(description=STRATEGY_TITLE)
    parser.add_argument("-m", "--model", help="单个模型；默认全部 7 个")
    parser.add_argument("--scenario", help="password_change / send_link / exfiltrate_data")
    parser.add_argument("--variant", choices=["baseline", "agent_hooks", "both"], default="both")
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
    print(f"Hooks     : Reasoning (redacted_thinking) + Tool-calling (tool call)")
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
        variant_b_label="agent_hooks（Reasoning + Tool-calling）",
        compare_label="Agent Hooks 更强",
        compare_field="agent_hooks_beats_baseline",
        asr_b_key="agent_hooks_asr",
    )

    print_summary_table(
        summary["rows"],
        variant_b_key=VARIANT_B,
        compare_field="agent_hooks_beats_baseline",
        compare_label="Hooks更强",
    )
    print(f"\nbaseline ASR     : {summary['baseline_asr']:.1%}")
    print(f"agent_hooks ASR  : {summary['agent_hooks_asr']:.1%}")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
