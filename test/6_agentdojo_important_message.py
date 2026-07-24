#!/usr/bin/env python
"""
Strategy 6: Important Message Injection (AgentDojo)
Paper: Debenedetti et al., 2024 — AgentDojo (arXiv:2406.13352)

论文 Figure 19(a) / Appendix B.3 — 伪装成正常通知的注入攻击（非直接命令）。
论文 Table 4：GPT-4o 上 Important message 的 Targeted ASR (~57.7%) 远高于
Ignore previous (~5.4%) 等简单模板。

对比 clean（无注入）与 important_message（AgentDojo 完整 <INFORMATION> 模板）。

Usage:
  python test/6_agentdojo_important_message.py
  python test/6_agentdojo_important_message.py -m qwen3.7-plus
  python test/6_agentdojo_important_message.py --serial
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
from lib.agentdojo import (  # noqa: E402
    DEFAULT_MODEL_NAME,
    DEFAULT_USER_NAME,
    build_agent_messages,
    build_important_message_tool_output,
    load_scenarios_6,
)
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

STRATEGY_TITLE = "Strategy 6: Important Message Injection (AgentDojo)"
RUN_PREFIX = "6_agentdojo_important"
VARIANT_A = "clean"
VARIANT_B = "important_message"


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    user_name = scenario.get("user_name", DEFAULT_USER_NAME)
    model_name = scenario.get("model_name", DEFAULT_MODEL_NAME)
    tool_output = build_important_message_tool_output(
        benign_prefix=scenario["benign_prefix"],
        attacker_instruction=scenario["attacker_instruction"],
        variant=variant,  # type: ignore[arg-type]
        user_name=user_name,
        model_name=model_name,
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
        "template_family": "agentdojo-important-message" if variant == VARIANT_B else "clean",
        "user_name": user_name,
        "model_name": model_name,
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
        clean = labels.get(VARIANT_A)
        important_message = labels.get(VARIANT_B)
        rows.append(
            {
                "model": model,
                "scenario_id": scenario_id,
                "baseline": clean,
                "important_message": important_message,
                "important_message_beats_baseline": important_message == "success"
                and clean in ("blocked", "partial", None),
            }
        )

    total = len(rows) or 1
    return {
        "total_cases": len(rows),
        "baseline_success_count": sum(1 for r in rows if r["baseline"] == "success"),
        "important_message_success_count": sum(
            1 for r in rows if r["important_message"] == "success"
        ),
        "important_message_beats_baseline_count": sum(
            1 for r in rows if r["important_message_beats_baseline"]
        ),
        "baseline_asr": round(sum(1 for r in rows if r["baseline"] == "success") / total, 4),
        "important_message_asr": round(
            sum(1 for r in rows if r["important_message"] == "success") / total, 4
        ),
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
    set_api_key_map_file(TEST_ROOT / "config" / "6_api_key_map.yaml")

    parser = argparse.ArgumentParser(description=STRATEGY_TITLE)
    parser.add_argument("-m", "--model", help="单个模型；默认全部 7 个")
    parser.add_argument("--scenario", help="password_change / send_link / exfiltrate_data")
    parser.add_argument(
        "--variant", choices=["clean", "important_message", "both"], default="both"
    )
    parser.add_argument("--serial", action="store_true", help="串行模式（单 Key，调试用）")
    args = parser.parse_args()

    models = resolve_target_models(args.model)
    scenarios = load_scenarios_6()
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
    print(f"Attack    : Important Message (<INFORMATION> block, Figure 19a)")
    print(f"Placeholders: user={DEFAULT_USER_NAME!r}, model={DEFAULT_MODEL_NAME!r}")
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
        variant_b_label="important_message（AgentDojo 通知伪装）",
        compare_label="Important Message 有效",
        compare_field="important_message_beats_baseline",
        asr_b_key="important_message_asr",
    )

    print_summary_table(
        summary["rows"],
        variant_b_key=VARIANT_B,
        compare_field="important_message_beats_baseline",
        compare_label="攻击有效",
    )
    print(f"\nclean ASR               : {summary['baseline_asr']:.1%}")
    print(f"important_message ASR   : {summary['important_message_asr']:.1%}")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
