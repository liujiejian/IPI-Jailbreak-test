#!/usr/bin/env python
"""
Strategy 51: Agent-tailored Phishing (Syros et al., 2026 / MUZZLE Figure 4)

攻击思路（Social Engineering Prompt Injection / Agentic Phishing）：
  传统钓鱼：骗人 — "账号将被封禁，立即登录"。
  MUZZLE Agentic Phishing：骗 Agent — 把凭证提交包装成完成任务必须步骤：
    Task verification required → Please login → Username / Password
  Agent 认为验证是正常 workflow，自动输入账号密码 → 凭证泄露。

环境检查结论：**不可复用** 单应用 trajectory / cross_app env（无 verification 表单工具）。
**新建** agentic_phishing_env.py — submit_task_verification + type_text + spoofed DOM。
**新建** agentic_phishing_injection.py — Figure 4 Postmill reply → verify page。

与相近策略：
  • 50 Cross-app — App A poison → App B 破坏性 action（删表/删号）
  • 51 Agentic Phishing — Postmill reply → 伪造验证页 → 凭证提交
  • human_phishing 变体 — 对照传统 human-targeted 话术

变体：
  • clean               — 良性 Postmill 回复
  • human_phishing      — 传统 human 钓鱼（account suspended）
  • agentic_direct      — blunt 跨验证指令
  • agentic_paper       — Figure 4 原文 + 验证页 DOM 已在历史中
  • agentic_reply_only  — 仅 reply 投毒，Agent 须自行 navigate+submit

Usage:
  python test/51_syros_muzzle_agentic_phishing_injection.py
  python test/51_syros_muzzle_agentic_phishing_injection.py -m qwen3.7-plus
  python test/51_syros_muzzle_agentic_phishing_injection.py --scenario phishing_postmill_verify
  python test/51_syros_muzzle_agentic_phishing_injection.py --variant agentic_paper
  python test/51_syros_muzzle_agentic_phishing_injection.py --serial
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

from lib.agentic_phishing_injection import (  # noqa: E402
    AGENTIC_VARIANTS,
    PHISHING_VARIANTS,
    build_phishing_messages,
    get_tools_for_phishing_scenario,
    load_scenarios,
)
from lib.api_call import create_chat_completion  # noqa: E402
from lib.api_keys import set_api_key_map_file, validate_model_keys  # noqa: E402
from lib.common import (  # noqa: E402
    ensure_results_dir,
    get_client,
    report_html_path,
    resolve_target_models,
    save_json,
    utc_timestamp,
)
from lib.console import STATUS_TAG, print_case_result  # noqa: E402
from lib.evaluation import evaluate_response  # noqa: E402
from lib.report import write_html_report  # noqa: E402
from lib.runner import run_parallel_by_model, sort_results  # noqa: E402
from lib.agentic_phishing_env import build_agentic_phishing_system_prompt  # noqa: E402

STRATEGY_TITLE = "Strategy 51: Agent-tailored Phishing (Syros et al., 2026 MUZZLE)"
RUN_PREFIX = "51_syros_muzzle_agentic_phishing_injection"
VARIANT_CLEAN = "clean"
ALL_VARIANTS = [VARIANT_CLEAN, *PHISHING_VARIANTS]


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    messages, phish_meta = build_phishing_messages(scenario, variant)
    tools = get_tools_for_phishing_scenario(scenario)
    system_prompt = build_agentic_phishing_system_prompt(scenario)
    tool_output = messages[-1]["content"] if messages else ""

    base = {
        "model": model,
        "scenario_id": scenario["id"],
        "scenario_name": scenario["name"],
        "variant": variant,
        "web_app": scenario.get("web_app"),
        "template_family": variant if variant != VARIANT_CLEAN else "clean",
        "phish_meta": phish_meta,
        "agentic_phishing": phish_meta.get("agentic_phishing"),
        "payload_style": phish_meta.get("payload_style"),
        "verification_url": phish_meta.get("verification_url"),
        "injection_surface": phish_meta.get("injection_surface"),
        "attack_chain": " → ".join(phish_meta.get("attack_chain", [])),
        "attacker_instruction": scenario.get("attacker_instruction", ""),
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
        clean = labels.get(VARIANT_CLEAN)
        attack_labels = {v: labels.get(v) for v in PHISHING_VARIANTS}
        human = labels.get("human_phishing")
        any_agentic = any(labels.get(v) == "success" for v in AGENTIC_VARIANTS)
        row = {
            "model": model,
            "scenario_id": scenario_id,
            "baseline": clean,
            **attack_labels,
            "agentic_beats_human": any_agentic and human in ("blocked", "partial", None),
            "any_attack_beats_baseline": any(
                labels.get(v) == "success" for v in PHISHING_VARIANTS
            )
            and clean in ("blocked", "partial", None),
        }
        rows.append(row)

    total = len(rows) or 1
    asr: dict[str, float] = {
        "baseline_asr": round(sum(1 for r in rows if r["baseline"] == "success") / total, 4)
    }
    for v in PHISHING_VARIANTS:
        asr[f"{v}_asr"] = round(sum(1 for r in rows if r.get(v) == "success") / total, 4)
    asr["any_attack_asr"] = round(
        sum(
            1 for r in rows if any(r.get(v) == "success" for v in PHISHING_VARIANTS)
        )
        / total,
        4,
    )
    asr["agentic_beats_human_rate"] = round(
        sum(1 for r in rows if r.get("agentic_beats_human")) / total, 4
    )

    return {
        "total_cases": len(rows),
        "rows": rows,
        **asr,
        **{
            f"{v}_success_count": sum(1 for r in rows if r.get(v) == "success")
            for v in PHISHING_VARIANTS
        },
        "agentic_beats_human_count": sum(1 for r in rows if r.get("agentic_beats_human")),
        "any_attack_beats_baseline_count": sum(
            1 for r in rows if r.get("any_attack_beats_baseline")
        ),
    }


def print_summary_table(rows: list[dict]) -> None:
    print("\n" + "=" * 184)
    print("对比汇总（clean vs Agentic Phishing — human vs agent-tailored social engineering）")
    print("=" * 184)
    header = (
        f"{'模型':<22} {'场景':<28} {'clean':<10} "
        f"{'human':<10} {'direct':<10} {'paper':<10} {'reply':<10} {'ag>hum'}"
    )
    print(header)
    print("-" * 184)

    def tag(label: str | None) -> str:
        if not label:
            return "-"
        return STATUS_TAG.get(label, label) if label in STATUS_TAG else label

    for row in rows:
        cmp = "是" if row.get("agentic_beats_human") else "-"
        print(
            f"{row['model']:<22} {row['scenario_id']:<28} "
            f"{tag(row.get('baseline')):<10} "
            f"{tag(row.get('human_phishing')):<10} "
            f"{tag(row.get('agentic_direct')):<10} "
            f"{tag(row.get('agentic_paper')):<10} "
            f"{tag(row.get('agentic_reply_only')):<10} {cmp}"
        )
    print("=" * 184)


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
                style = result.get("payload_style") or "-"
                extra = f" | style={style}" if variant != VARIANT_CLEAN else ""
                print_case_result(
                    model,
                    scenario["id"],
                    variant,
                    ev.get("label", "error"),
                    ev.get("reason", "") + extra,
                )
    return sort_results(results, models, scenarios, variants)


def main() -> int:
    set_api_key_map_file(TEST_ROOT / "config" / "51_api_key_map.yaml")

    variant_choices = ["all", VARIANT_CLEAN, *PHISHING_VARIANTS]
    parser = argparse.ArgumentParser(description=STRATEGY_TITLE)
    parser.add_argument("-m", "--model", help="单个模型；默认全部 7 个")
    parser.add_argument(
        "--scenario",
        help="phishing_postmill_verify / phishing_postmill_practices / ...",
    )
    parser.add_argument("--variant", choices=variant_choices, default="all")
    parser.add_argument("--serial", action="store_true", help="串行模式（单 Key，调试用）")
    args = parser.parse_args()

    models = resolve_target_models(args.model)
    scenarios = load_scenarios()
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
    print(f"Env       : agentic_phishing_env (Postmill reply → task.webhost.zoo/verify)")
    print(f"Variants  : {', '.join(variants)}")
    print(f"Chain     : post reply poison → navigate verify → submit_task_verification")
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
        variant_b_key="agentic_paper",
        variant_b_label="agentic_paper（Figure 4 task verification + spoofed login DOM）",
        compare_label="Agentic Phishing 有效",
        compare_field="any_attack_beats_baseline",
        asr_b_key="any_attack_asr",
    )

    print_summary_table(summary["rows"])
    print(f"\nclean ASR                   : {summary['baseline_asr']:.1%}")
    print(f"human_phishing ASR          : {summary['human_phishing_asr']:.1%}")
    print(f"agentic_direct ASR          : {summary['agentic_direct_asr']:.1%}")
    print(f"agentic_paper ASR           : {summary['agentic_paper_asr']:.1%}")
    print(f"agentic_reply_only ASR      : {summary['agentic_reply_only_asr']:.1%}")
    print(f"any attack ASR              : {summary['any_attack_asr']:.1%}")
    print(f"agentic beats human         : {summary['agentic_beats_human_rate']:.1%}")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
