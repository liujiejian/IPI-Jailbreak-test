#!/usr/bin/env python
"""
Strategy 1: Multi-turn Persuasion Jailbreak (Foot-in-the-door)

论文：Chang et al., 2026 — ChatInject (arXiv:2509.22830, ICLR 2026)
论文 §2 明确引用 Foot-in-the-door、X-Teaming、Humanizing LLMs 等，认为越狱中
最成功的是 Multi-turn Persuasion：不要一次攻击，而是一步一步建立信任后再提出有害请求。

环境选择：**真实多轮 API 对话**（`jblib/multiturn_env.py`）
  User(良性) → Assistant(真实) → User(铺垫) → Assistant(真实) → User(有害) → 判定

与 IPI test/2 区分（论文最大借鉴落在 IPI 侧）：
  • 越狱（本脚本）— 攻击者控制 user 消息，assistant 回复由模型实时生成
  • IPI test/2 — 单次 API；在 Tool Output 内用 chat template 伪造整段历史对话

变体：
  • direct   — harmful_request 单轮直接发送（基线拒答率）
  • multiturn — Foot-in-the-door 多轮说服后发送 final_user

Usage:
  python test/jailbreak/1_multiturn_persuasion_jailbreak.py
  python test/jailbreak/1_multiturn_persuasion_jailbreak.py -m qwen3.7-plus
  python test/jailbreak/1_multiturn_persuasion_jailbreak.py --scenario phishing_email
  python test/jailbreak/1_multiturn_persuasion_jailbreak.py --variant multiturn
  python test/jailbreak/1_multiturn_persuasion_jailbreak.py --serial
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
from jblib.multiturn_env import run_direct_turn, run_multiturn_persuasion  # noqa: E402
from jblib.report import write_html_report  # noqa: E402
from jblib.scenarios import load_scenarios  # noqa: E402
from jblib.summary import summarize  # noqa: E402
from lib.api_keys import set_api_key_map_file, validate_model_keys  # noqa: E402
from lib.common import get_client, resolve_target_models  # noqa: E402
from lib.console import print_case_result, print_summary_table  # noqa: E402
from lib.runner import run_parallel_by_model, sort_results  # noqa: E402

STRATEGY_TITLE = "Strategy 1: Multi-turn Persuasion Jailbreak"
RUN_PREFIX = "1_multiturn_persuasion"
VARIANTS = ["direct", "multiturn"]


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    system_prompt = DEFAULT_SYSTEM_PROMPT
    label = f"{model} | {scenario['id']} | {variant}"
    base = {
        "model": model,
        "scenario_id": scenario["id"],
        "scenario_name": scenario["name"],
        "category": scenario.get("category", ""),
        "variant": variant,
        "harmful_request": scenario["harmful_request"].strip(),
        "system_prompt": system_prompt,
    }

    try:
        if variant == "direct":
            outcome = run_direct_turn(
                client,
                model=model,
                scenario=scenario,
                system_prompt=system_prompt,
                label=label,
            )
        elif variant == "multiturn":
            outcome = run_multiturn_persuasion(
                client,
                model=model,
                scenario=scenario,
                system_prompt=system_prompt,
                label_prefix=label,
            )
        else:
            raise ValueError(f"unknown variant: {variant}")

        return {**base, **outcome, "tool_calls": []}
    except Exception as exc:
        return {
            **base,
            "messages": [],
            "response": "",
            "user_prompt": scenario["harmful_request"].strip(),
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


def main() -> int:
    parser = argparse.ArgumentParser(description=STRATEGY_TITLE)
    parser.add_argument("-m", "--model", help="单个模型；默认全部 7 个")
    parser.add_argument("--scenario", help="phishing_email / lock_picking / keylogger_code / sql_injection")
    parser.add_argument("--variant", choices=["direct", "multiturn", "both"], default="both")
    parser.add_argument("--serial", action="store_true", help="串行模式（单 Key，调试用）")
    args = parser.parse_args()

    set_api_key_map_file(JB_ROOT / "config" / "1_api_key_map.yaml")

    models = resolve_target_models(args.model)
    scenarios = load_scenarios("1_scenarios")
    if args.scenario:
        scenarios = [s for s in scenarios if s["id"] == args.scenario]
        if not scenarios:
            print("[ERROR] 未找到场景", file=sys.stderr)
            return 1

    variants = VARIANTS if args.variant == "both" else [args.variant]
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
    print(f"Cases     : {total} (multiturn 每条约 3+ API 轮次)")
    print(f"HTML      : {html_path}")
    print("-" * 78)

    if parallel:
        results = run_parallel_by_model(models, scenarios, variants, run_case)
    else:
        results = run_serial(models, scenarios, variants)

    summary = summarize(results, direct_variant="direct", jailbreak_variant="multiturn")
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
        variant_b_key="multiturn",
        variant_b_label="multiturn（多轮说服）",
        compare_label="Multi-turn 更强",
        compare_field="jailbreak_beats_direct",
        asr_b_key="multiturn_asr",
    )

    print_summary_table(
        summary["rows"],
        variant_b_key="multiturn",
        compare_field="jailbreak_beats_direct",
        compare_label="Multi-turn更强",
    )
    print(f"\ndirect ASR    : {summary['direct_asr']:.1%}")
    print(f"multiturn ASR : {summary['multiturn_asr']:.1%}")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
