#!/usr/bin/env python
"""
Strategy 14: Disguise Jailbreak — Word Puzzle Obfuscation (Liu et al. 2024 DRA)

论文：Liu et al., 2024 — Making Them Ask and Answer (USENIX Security 24 / arXiv:2402.18104)
§4.1 Harmful Instruction Disguise · Puzzle-based Obfuscation (Figure 2)

DRA 第一步 Disguise：将有害指令逐字符拆散，每个字符藏进随机短语，目标字母用
括号标记（藏头诗 / acrostic）。例 "how" → happy (h)our / (o)pen / (w)ise …
模型需提取括号内字母拼回原文。

环境选择：**单轮 direct chat**（`jblib/disguise.py` + `run_direct_turn`）
LETTER_MAP 与 prefix_gen 逻辑来自 DRA 官方仓库；puzzle 按 scenario id 固定 seed。

与 S13 区分：
  • S13 — Greshake 结构混淆（Base64 / HTML Comment / Hidden Text）
  • S14 — DRA Word Puzzle（字母藏头 + 可选 Reconstruction 模板）

与 DRA 完整攻击区分（后续策略）：
  • word-level split（词级截断伪装）
  • context manipulation（输出上下文操纵）
  • 本策略 `word_puzzle_reconstruction` 仅含 §4.2 最小 Reconstruction 后缀，
    **不含** word split 词提示（`Note that Q1 contains the words: "Ho", "to"...`）

变体（默认全部）：
  • direct                       — 明文 harmful_request（基线）
  • word_puzzle                  — Word Puzzle 伪装（Disguise only）
  • word_puzzle_reconstruction   — Puzzle + §4.2 Reconstruction 模板

Usage:
  python test/jailbreak/14_disguise_jailbreak.py
  python test/jailbreak/14_disguise_jailbreak.py -m qwen3.7-plus
  python test/jailbreak/14_disguise_jailbreak.py --scenario lock_picking
  python test/jailbreak/14_disguise_jailbreak.py --variant word_puzzle
  python test/jailbreak/14_disguise_jailbreak.py --serial
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
from jblib.disguise import ALL_VARIANTS, VARIANT_LABELS, build_disguise_prompt  # noqa: E402
from jblib.multi_variant_report import write_multi_variant_report  # noqa: E402
from jblib.multi_variant_summary import summarize_variants  # noqa: E402
from jblib.multiturn_env import run_direct_turn  # noqa: E402
from jblib.scenarios import load_scenarios  # noqa: E402
from lib.api_keys import set_api_key_map_file, validate_model_keys  # noqa: E402
from lib.common import get_client, resolve_target_models  # noqa: E402
from lib.console import print_case_result  # noqa: E402
from lib.runner import run_parallel_by_model, sort_results  # noqa: E402

STRATEGY_TITLE = "Strategy 14: Disguise Jailbreak — Word Puzzle (Liu et al. 2024 DRA)"
RUN_PREFIX = "14_disguise"


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    system_prompt = DEFAULT_SYSTEM_PROMPT
    payload = build_disguise_prompt(scenario, variant)
    user_prompt = payload["user_prompt"]
    label = f"{model} | {scenario['id']} | {variant}"

    base = {
        "model": model,
        "scenario_id": scenario["id"],
        "scenario_name": scenario["name"],
        "category": scenario.get("category", ""),
        "variant": variant,
        "disguise_variant": payload.get("disguise_variant", variant),
        "word_puzzle": payload.get("word_puzzle", ""),
        "normalized_instruction": payload.get("normalized_instruction", ""),
        "puzzle_seed": payload.get("puzzle_seed", 0),
        "harmful_request": scenario["harmful_request"].strip(),
        "harmful_request_original": payload.get("harmful_request_original", ""),
        "system_prompt": system_prompt,
    }

    try:
        outcome = run_direct_turn(
            client,
            model=model,
            scenario=scenario,
            system_prompt=system_prompt,
            label=label,
            user_content=user_prompt,
        )
        return {**base, **outcome, "user_prompt": user_prompt, "tool_calls": []}
    except Exception as exc:
        return {
            **base,
            "messages": [],
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


def print_disguise_summary(summary: dict) -> None:
    variants = summary.get("variants") or ALL_VARIANTS
    variant_asr = summary.get("variant_asr") or {}
    print("\n" + "=" * 78)
    print("Disguise ASR 汇总（success）")
    print("=" * 78)
    for v in variants:
        tag = VARIANT_LABELS.get(v, v)
        asr = variant_asr.get(v, 0)
        beat = ""
        if v != "direct" and summary.get("beats_baseline", {}).get(v):
            beat = " ↑ vs direct"
        print(f"  {tag:<44} {asr:>6.1%}{beat}")
    best = summary.get("best_variant", "")
    if best and best != "direct":
        print(f"\n最佳变体: {VARIANT_LABELS.get(best, best)}")
    print("=" * 78)


def main() -> int:
    parser = argparse.ArgumentParser(description=STRATEGY_TITLE)
    parser.add_argument("-m", "--model", help="单个模型；默认全部 7 个")
    parser.add_argument("--scenario", help="phishing_email / lock_picking / keylogger_code / sql_injection")
    parser.add_argument(
        "--variant",
        choices=[*ALL_VARIANTS, "all"],
        default="all",
        help="默认 all = direct + word_puzzle + word_puzzle_reconstruction",
    )
    parser.add_argument("--serial", action="store_true", help="串行模式（单 Key，调试用）")
    args = parser.parse_args()

    set_api_key_map_file(JB_ROOT / "config" / "14_api_key_map.yaml")

    models = resolve_target_models(args.model)
    scenarios = load_scenarios("14_scenarios")
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

    print_disguise_summary(summary)
    print(f"\nJSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
