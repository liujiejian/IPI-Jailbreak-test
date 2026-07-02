#!/usr/bin/env python
"""
Strategy 20: NeuralExec Attack (Jia et al., 2025)
Paper: PromptLocate — Localizing Prompt Injection Attacks, Table 10
Reference [23]: Pasquini et al., 2024 — Neural Exec (AISec)

Gradient-based Prompt Optimization，联合优化 Separator + Prompt Suffix：

  xc = xt || z || se || xe || z′

  Target → Separator (z) → Attack Prompt → Suffix (z′)

Suffix 帮助模型更稳定执行攻击；比 Universal 更复杂，论文认为更强。
本脚本使用 PromptLocate Table 10 / Pasquini GitHub 预优化的 z + z′
（Meta-Llama-3-8B-Instruct Neural Exec），而非测试时重新跑优化。

对比 clean（良性网页）与 neural_exec（NeuralExec 注入）。

Usage:
  python test/20_jia_neural_exec_injection.py
  python test/20_jia_neural_exec_injection.py -m qwen3.7-plus
  python test/20_jia_neural_exec_injection.py --serial
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
from lib.console import print_case_result, print_summary_table  # noqa: E402
from lib.evaluation import evaluate_response  # noqa: E402
from lib.neural_exec_injection import (  # noqa: E402
    NEURAL_EXEC_SEPARATOR,
    NEURAL_EXEC_SUFFIX,
    build_agent_messages,
    build_neural_exec_injection,
    build_tool_output,
    load_scenarios,
)
from lib.report import write_html_report  # noqa: E402
from lib.runner import run_parallel_by_model, sort_results  # noqa: E402

STRATEGY_TITLE = "Strategy 20: NeuralExec Attack (Jia et al., 2025)"
RUN_PREFIX = "20_jia_neural_exec"
VARIANT_A = "clean"
VARIANT_B = "neural_exec"


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    tool_output = build_tool_output(
        benign_prefix=scenario["benign_prefix"],
        attacker_instruction=scenario["attacker_instruction"],
        variant=variant,  # type: ignore[arg-type]
    )
    injection_block = ""
    if variant == VARIANT_B:
        injection_block = build_neural_exec_injection(scenario["attacker_instruction"])

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
        "template_family": "neural-exec-ipi" if variant == VARIANT_B else "clean",
        "neural_exec_separator": NEURAL_EXEC_SEPARATOR if variant == VARIANT_B else "",
        "neural_exec_suffix": NEURAL_EXEC_SUFFIX if variant == VARIANT_B else "",
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
        clean = labels.get(VARIANT_A)
        neural_exec = labels.get(VARIANT_B)
        rows.append(
            {
                "model": model,
                "scenario_id": scenario_id,
                "baseline": clean,
                "neural_exec": neural_exec,
                "neural_exec_beats_baseline": neural_exec == "success"
                and clean in ("blocked", "partial", None),
            }
        )

    total = len(rows) or 1
    return {
        "total_cases": len(rows),
        "baseline_success_count": sum(1 for r in rows if r["baseline"] == "success"),
        "neural_exec_success_count": sum(1 for r in rows if r["neural_exec"] == "success"),
        "neural_exec_beats_baseline_count": sum(
            1 for r in rows if r["neural_exec_beats_baseline"]
        ),
        "baseline_asr": round(sum(1 for r in rows if r["baseline"] == "success") / total, 4),
        "neural_exec_asr": round(sum(1 for r in rows if r["neural_exec"] == "success") / total, 4),
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
    set_api_key_map_file(TEST_ROOT / "config" / "20_api_key_map.yaml")

    parser = argparse.ArgumentParser(description=STRATEGY_TITLE)
    parser.add_argument("-m", "--model", help="单个模型；默认全部 7 个")
    parser.add_argument("--scenario", help="password_change / send_link / exfiltrate_data")
    parser.add_argument("--variant", choices=["clean", "neural_exec", "both"], default="both")
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
    print(f"Stack     : z (separator) + attack prompt + z′ (suffix)")
    print(f"Chain     : poison webpage -> get_webpage -> NeuralExec IPI")
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
        variant_b_label="neural_exec（z + z′ 预优化 Trigger）",
        compare_label="NeuralExec 有效",
        compare_field="neural_exec_beats_baseline",
        asr_b_key="neural_exec_asr",
    )

    print_summary_table(
        summary["rows"],
        variant_b_key=VARIANT_B,
        compare_field="neural_exec_beats_baseline",
        compare_label="攻击有效",
    )
    print(f"\nclean ASR       : {summary['baseline_asr']:.1%}")
    print(f"neural_exec ASR : {summary['neural_exec_asr']:.1%}")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
