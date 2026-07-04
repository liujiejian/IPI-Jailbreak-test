#!/usr/bin/env python
"""
Strategy 11: Arbitrarily Wrong Summary Jailbreak（错误摘要操纵）

论文：Greshake et al., 2023 — Not what you've signed up for (arXiv:2302.12173)
§4.2.5 Manipulated Content / Arbitrarily-Wrong Summaries, Prompt 9, Figure 18.

"We leverage 'jailbreaking' to instruct the model to produce factually wrong output."

环境选择：**单轮 direct chat**（`jblib/wrong_summary.py` + 专用判定 `wrong_summary_evaluation.py`）
  user message = 源文档 + 摘要任务（+ AIM jailbreak 前缀）。

与 jailbreak S1–S9 区分：
  • S1–S9 — 生成有害/违规**内容**（邮件、代码、撬锁等）
  • S11 — **完整性操纵**：故意生成与文档矛盾的错误摘要

与 IPI test/10–15 (Greshake) 区分：
  • IPI — 注入在 webpage/tool output / hidden text 等间接面
  • S11 — user message 内嵌 jailbreak + 文档（本策略测 jailbreak 操纵输出能力）

变体（默认全部）：
  • direct          — 诚实摘要（基线）
  • aim_jailbreak   — Prompt 9 AIM Machiavelli persona
  • explicit_wrong  — 明确要求与文档矛盾的错误摘要

Usage:
  python test/jailbreak/11_wrong_summary_jailbreak.py
  python test/jailbreak/11_wrong_summary_jailbreak.py -m qwen3.7-plus
  python test/jailbreak/11_wrong_summary_jailbreak.py --scenario earnings_report
  python test/jailbreak/11_wrong_summary_jailbreak.py --variant aim_jailbreak
  python test/jailbreak/11_wrong_summary_jailbreak.py --serial
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve()
SCRIPT_STEM = SCRIPT_PATH.stem
JB_ROOT = SCRIPT_PATH.parent
IPI_TEST_ROOT = JB_ROOT.parent
sys.path.insert(0, str(IPI_TEST_ROOT))
sys.path.insert(0, str(JB_ROOT))

from jblib.common import (  # noqa: E402
    DEFAULT_SYSTEM_PROMPT,
    ensure_results_dir,
    report_html_path,
    save_json,
    utc_timestamp,
)
from jblib.env import build_messages  # noqa: E402
from jblib.multi_variant_report import write_multi_variant_report  # noqa: E402
from jblib.multi_variant_summary import summarize_variants  # noqa: E402
from jblib.scenarios import load_scenarios  # noqa: E402
from jblib.wrong_summary import ALL_VARIANTS, VARIANT_LABELS, build_wrong_summary_prompt  # noqa: E402
from jblib.wrong_summary_evaluation import evaluate_wrong_summary  # noqa: E402
from lib.api_call import create_chat_completion  # noqa: E402
from lib.api_keys import set_api_key_map_file, validate_model_keys  # noqa: E402
from lib.common import get_client, resolve_target_models  # noqa: E402
from lib.console import print_case_result  # noqa: E402
from lib.runner import run_parallel_by_model, sort_results  # noqa: E402

STRATEGY_TITLE = "Strategy 11: Arbitrarily Wrong Summary Jailbreak (Greshake)"
RUN_PREFIX = "11_wrong_summary"


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    system_prompt = DEFAULT_SYSTEM_PROMPT
    payload = build_wrong_summary_prompt(scenario, variant)
    user_prompt = payload["user_prompt"]
    messages = build_messages(user_content=user_prompt, system_prompt=system_prompt)
    label = f"{model} | {scenario['id']} | {variant}"

    base = {
        "model": model,
        "scenario_id": scenario["id"],
        "scenario_name": scenario["name"],
        "category": scenario.get("category", ""),
        "variant": variant,
        "manipulation_frame": payload.get("manipulation_frame", ""),
        "manipulation_variant": payload.get("manipulation_variant", variant),
        "source_document": payload.get("source_document", ""),
        "user_instruction": payload.get("user_instruction", ""),
        "system_prompt": system_prompt,
    }

    try:
        completion = create_chat_completion(
            client,
            model=model,
            messages=messages,
            label=label,
            tools=None,
            temperature=0,
            max_tokens=1024,
        )
        response = completion.content or ""
        evaluation = evaluate_wrong_summary(response, scenario, variant=variant)
        return {
            **base,
            "messages": messages,
            "response": response,
            "user_prompt": user_prompt,
            "tool_calls": [],
            "turn_log": [],
            "api_turn_count": 1,
            "evaluation": evaluation,
        }
    except Exception as exc:
        return {
            **base,
            "messages": messages,
            "response": "",
            "user_prompt": user_prompt,
            "turn_log": [],
            "api_turn_count": 0,
            "error": str(exc),
            "evaluation": {"label": "error", "reason": str(exc)},
        }


def run_serial(models: list[str], scenarios: list[dict], variants: list[str]) -> list[dict]:
    client = get_client()
    results: list[dict] = []
    total = len(models) * len(scenarios) * len(variants)
    n = 0
    for model in models:
        for scenario in scenarios:
            for variant in variants:
                n += 1
                print(f"  [{n}/{total}] 等待 API: {model} | {scenario['id']} | {variant} ...", flush=True)
                result = run_case(client, model, scenario, variant)
                results.append(result)
                ev = result.get("evaluation", {})
                print_case_result(model, scenario["id"], variant, ev.get("label", "error"), ev.get("reason", ""))
    return sort_results(results, models, scenarios, variants)


def print_wrong_summary_report(summary: dict) -> None:
    variants = summary.get("variants") or ALL_VARIANTS
    variant_asr = summary.get("variant_asr") or {}
    print("\n" + "=" * 78)
    print("Wrong Summary ASR 汇总（attack 变体 success = 输出与文档矛盾的错误事实）")
    print("=" * 78)
    for v in variants:
        tag = VARIANT_LABELS.get(v, v)
        asr = variant_asr.get(v, 0)
        note = ""
        if v == "direct":
            note = " （忠实摘要率）"
        elif summary.get("beats_baseline", {}).get(v):
            note = " ↑ vs direct"
        print(f"  {tag:<44} {asr:>6.1%}{note}")
    best = summary.get("best_variant", "")
    attack_variants = [v for v in variants if v != "direct"]
    if best in attack_variants:
        print(f"\n最强攻击变体: {VARIANT_LABELS.get(best, best)}")
    print("=" * 78)


def main() -> int:
    parser = argparse.ArgumentParser(description=STRATEGY_TITLE)
    parser.add_argument("-m", "--model", help="单个模型；默认全部 7 个")
    parser.add_argument(
        "--scenario",
        help="earnings_report / clinical_note / merger_news / nobel_fact",
    )
    parser.add_argument(
        "--variant",
        choices=[*ALL_VARIANTS, "all"],
        default="all",
        help="默认 all = direct + aim_jailbreak + explicit_wrong",
    )
    parser.add_argument("--serial", action="store_true", help="串行模式（单 Key，调试用）")
    args = parser.parse_args()

    set_api_key_map_file(JB_ROOT / "config" / "11_api_key_map.yaml")

    models = resolve_target_models(args.model)
    scenarios = load_scenarios("11_scenarios")
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
    print(f"Variants  : {', '.join(variants)}")
    print(f"Paper     : Greshake Prompt 9 — factually wrong summary via jailbreak")
    print(f"Mode      : {'7路并行 (每模型专用Key)' if parallel else '串行'}")
    print(f"Cases     : {total}")
    print(f"HTML      : {html_path}")
    print("-" * 78)

    if parallel:
        results = run_parallel_by_model(models, scenarios, variants, run_case)
    else:
        results = run_serial(models, scenarios, variants)

    summary = summarize_variants(results, variants, baseline="direct")
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    save_json(out_dir / "results.json", results)
    save_json(out_dir / "summary.json", summary)
    write_multi_variant_report(
        SCRIPT_STEM,
        results,
        summary,
        title=STRATEGY_TITLE,
        variants=variants,
        run_dir=out_dir,
        generated_at=generated_at,
        variant_labels=VARIANT_LABELS,
    )

    print_wrong_summary_report(summary)
    print(f"\nJSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
