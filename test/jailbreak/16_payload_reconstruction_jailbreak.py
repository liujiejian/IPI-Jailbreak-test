#!/usr/bin/env python
"""
Strategy 16: Payload Reconstruction Jailbreak — Safety Bias (Liu et al. 2024 DRA)

论文：Liu et al., 2024 — Making Them Ask and Answer (USENIX Security 24 / arXiv:2402.18104)
§3 Safety Bias · §4.2 Payload Reconstruction · §4.3 Context Manipulation

核心创新：模型对 User 中的危险问题防御强，对 **Assistant completion** 中的
危险文本防御弱。DRA 不让用户直接写有害指令，而是引导模型：
  1. 从 disguised content 恢复 Q1
  2. 在 **自己的输出** 中先说出 Q1，再逐步回答
→ 危险 Prompt 成为 Self-generated Prompt。

环境选择：**单轮 direct chat**（`jblib/payload_reconstruction.py` + `run_direct_turn`）
Disguise 层复用 S14 puzzle + S15 word-split word guide；本策略聚焦 Reconstruction 模板。

与 S14/S15 区分：
  • S14 — Word Puzzle disguise ablation
  • S15 — Word Split disguise ablation
  • S16 — **Payload Reconstruction / Self-generated Prompt Attack**（论文核心）

变体（默认全部）：
  • direct           — User 明文有害请求（Safety Bias 对照基线）
  • recon_minimal    — Puzzle + 最小 recover-then-answer
  • recon_standard   — Puzzle + Word Guide + §4.2 Reconstruction
  • recon_context    — §4.2 + §4.3 Context Manipulation（DRA 完整核心链）

Usage:
  python test/jailbreak/16_payload_reconstruction_jailbreak.py
  python test/jailbreak/16_payload_reconstruction_jailbreak.py -m qwen3.7-plus
  python test/jailbreak/16_payload_reconstruction_jailbreak.py --scenario lock_picking
  python test/jailbreak/16_payload_reconstruction_jailbreak.py --variant recon_standard
  python test/jailbreak/16_payload_reconstruction_jailbreak.py --serial
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
from jblib.payload_reconstruction import (  # noqa: E402
    ALL_VARIANTS,
    VARIANT_LABELS,
    build_payload_reconstruction_prompt,
)
from jblib.scenarios import load_scenarios  # noqa: E402
from lib.api_keys import set_api_key_map_file, validate_model_keys  # noqa: E402
from lib.common import get_client, resolve_target_models  # noqa: E402
from lib.console import print_case_result  # noqa: E402
from lib.runner import run_parallel_by_model, sort_results  # noqa: E402

STRATEGY_TITLE = "Strategy 16: Payload Reconstruction Jailbreak — Safety Bias (Liu et al. 2024 DRA)"
RUN_PREFIX = "16_payload_reconstruction"


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    system_prompt = DEFAULT_SYSTEM_PROMPT
    payload = build_payload_reconstruction_prompt(scenario, variant)
    user_prompt = payload["user_prompt"]
    label = f"{model} | {scenario['id']} | {variant}"

    base = {
        "model": model,
        "scenario_id": scenario["id"],
        "scenario_name": scenario["name"],
        "category": scenario.get("category", ""),
        "variant": variant,
        "reconstruction_variant": payload.get("reconstruction_variant", variant),
        "safety_bias_attack": payload.get("safety_bias_attack", False),
        "word_puzzle": payload.get("word_puzzle", ""),
        "split_text": payload.get("split_text", ""),
        "word_guide": payload.get("word_guide", ""),
        "word_fragments": payload.get("word_fragments", []),
        "recon_tools": payload.get("recon_tools", ""),
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


def print_reconstruction_summary(summary: dict) -> None:
    variants = summary.get("variants") or ALL_VARIANTS
    variant_asr = summary.get("variant_asr") or {}
    print("\n" + "=" * 78)
    print("Payload Reconstruction ASR 汇总（success）")
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
        help="默认 all = direct + 三种 reconstruction",
    )
    parser.add_argument("--serial", action="store_true", help="串行模式（单 Key，调试用）")
    args = parser.parse_args()

    set_api_key_map_file(JB_ROOT / "config" / "16_api_key_map.yaml")

    models = resolve_target_models(args.model)
    scenarios = load_scenarios("16_scenarios")
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

    print_reconstruction_summary(summary)
    print(f"\nJSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
