#!/usr/bin/env python
"""
Strategy 15: Word Split Jailbreak — Token Fragmentation (Liu et al. 2024 DRA)

论文：Liu et al., 2024 — Making Them Ask and Answer (USENIX Security 24 / arXiv:2402.18104)
§4.1 Word-level Character Split · Algorithm 2 · Figure 3

DRA 第二步 Disguise：逐词截断有害指令（Front-end / Rear-end / Mixed），例：
  "How to rob a bank vault" → "Ho to ro a nk vau lt"
模型仍可从片段推断原意，但安全检测更难识别完整 toxic 词。

环境选择：**单轮 direct chat**（`jblib/word_split.py` + `run_direct_turn`）
截断比例默认 toxic_ratio=0.5 · benign_ratio=0.5；按 scenario id 固定 seed。

与 S14 区分：
  • S14 — Word Puzzle（字母藏头 / 括号标记）
  • S15 — Word-level Split（词级 Token Fragmentation / 截断）

与 S5 区分：S5 为字符填充/变异，S15 为词级截断拆分。

变体（默认全部）：
  • direct                       — 明文 harmful_request（基线）
  • word_split_front             — Front-end 截断（Figure 3）
  • word_split_rear              — Rear-end 截断
  • word_split_mixed             — DRA Algorithm 2 混合
  • word_split_reconstruction    — Mixed + Word Guide Reconstruction

Usage:
  python test/jailbreak/15_word_split_jailbreak.py
  python test/jailbreak/15_word_split_jailbreak.py -m qwen3.7-plus
  python test/jailbreak/15_word_split_jailbreak.py --scenario lock_picking
  python test/jailbreak/15_word_split_jailbreak.py --variant word_split_front
  python test/jailbreak/15_word_split_jailbreak.py --serial
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
from jblib.multi_variant_report import write_multi_variant_report  # noqa: E402
from jblib.multi_variant_summary import summarize_variants  # noqa: E402
from jblib.multiturn_env import run_direct_turn  # noqa: E402
from jblib.scenarios import load_scenarios  # noqa: E402
from jblib.word_split import ALL_VARIANTS, VARIANT_LABELS, build_word_split_prompt  # noqa: E402
from lib.api_keys import set_api_key_map_file, validate_model_keys  # noqa: E402
from lib.common import get_client, resolve_target_models  # noqa: E402
from lib.console import print_case_result  # noqa: E402
from lib.runner import run_parallel_by_model, sort_results  # noqa: E402

STRATEGY_TITLE = "Strategy 15: Word Split Jailbreak — Token Fragmentation (Liu et al. 2024 DRA)"
RUN_PREFIX = "15_word_split"


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    system_prompt = DEFAULT_SYSTEM_PROMPT
    payload = build_word_split_prompt(scenario, variant)
    user_prompt = payload["user_prompt"]
    label = f"{model} | {scenario['id']} | {variant}"

    base = {
        "model": model,
        "scenario_id": scenario["id"],
        "scenario_name": scenario["name"],
        "category": scenario.get("category", ""),
        "variant": variant,
        "word_split_variant": payload.get("word_split_variant", variant),
        "split_text": payload.get("split_text", ""),
        "word_fragments": payload.get("word_fragments", []),
        "word_guide": payload.get("word_guide", ""),
        "split_seed": payload.get("split_seed", 0),
        "toxic_ratio": payload.get("toxic_ratio", 0.5),
        "benign_ratio": payload.get("benign_ratio", 0.5),
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


def print_word_split_summary(summary: dict) -> None:
    variants = summary.get("variants") or ALL_VARIANTS
    variant_asr = summary.get("variant_asr") or {}
    print("\n" + "=" * 78)
    print("Word Split ASR 汇总（success）")
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
        help="默认 all = direct + 四种 word split",
    )
    parser.add_argument("--serial", action="store_true", help="串行模式（单 Key，调试用）")
    args = parser.parse_args()

    set_api_key_map_file(JB_ROOT / "config" / "15_api_key_map.yaml")

    models = resolve_target_models(args.model)
    scenarios = load_scenarios("15_scenarios")
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

    print_word_split_summary(summary)
    print(f"\nJSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
