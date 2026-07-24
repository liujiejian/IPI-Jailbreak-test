#!/usr/bin/env python
"""
Strategy 46: Trajectory-aware Prompt Injection (Syros et al., 2026 / MUZZLE)
Paper: MUZZLE — Adaptive Agentic Red-Teaming of Web Agents (§3.3 Reconnaissance)

攻击思路（Execution-aware IPI）：
  以前：攻击者人工猜注入点（评论区 / 标题 / 隐藏 HTML）
  MUZZLE：先跑 benign task → 记录 trajectory T^b → Grafter 排名 high-salience vessels
          → 在 Agent 一定会读到的位置注入（comment / reply / listing reply）

  navigate → click → extract_page_content → poison in T* context snapshot

环境检查结论：**不可复用** agent_env + chatinject（单次 get_webpage，无轨迹）。
**新建** web_agent_trajectory_env + trajectory_aware_injection.py。

与相近策略：
  • 1 ChatInject — 单次 tool output 投毒，人工指定 benign_prefix
  • 32 Kaya Website — 离线索引 RAG，非 web agent 轨迹
  • 46 MUZZLE — 多步轨迹 + Grafter 高显著性注入面

变体：
  • clean              — 良性 comment/reply 线程
  • manual_hidden      — 人工猜：hidden HTML（不在 T* 提取区）
  • manual_title       — 人工猜：issue title（step2 见过，step3 extract 未含）
  • salient_comment    — Grafter #1 Gitea issue comment（轨迹选定）
  • salient_reply      — Grafter #1 Postmill post reply
  • salient_listing    — Grafter #1 Classifieds listing reply

Usage:
  python test/46_syros_muzzle_trajectory_injection.py
  python test/46_syros_muzzle_trajectory_injection.py -m qwen3.7-plus
  python test/46_syros_muzzle_trajectory_injection.py --scenario password_change
  python test/46_syros_muzzle_trajectory_injection.py --variant salient_comment
  python test/46_syros_muzzle_trajectory_injection.py --serial
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
from lib.trajectory_aware_injection import (  # noqa: E402
    TRAJECTORY_VARIANTS,
    build_trajectory_messages,
    get_tools_for_scenario,
    load_scenarios,
)
from lib.web_agent_trajectory_env import build_web_agent_system_prompt  # noqa: E402

STRATEGY_TITLE = "Strategy 46: Trajectory-aware Prompt Injection (Syros et al., 2026 MUZZLE)"
RUN_PREFIX = "46_syros_muzzle_trajectory_injection"
VARIANT_CLEAN = "clean"
ALL_VARIANTS = [VARIANT_CLEAN, *TRAJECTORY_VARIANTS]
SALIENT_VARIANTS = ("salient_comment", "salient_reply", "salient_listing")
MANUAL_VARIANTS = ("manual_hidden", "manual_title")


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    messages, traj_meta = build_trajectory_messages(scenario, variant)
    tools = get_tools_for_scenario(scenario)
    system_prompt = build_web_agent_system_prompt(scenario)
    tool_output = messages[-1]["content"] if messages else ""

    base = {
        "model": model,
        "scenario_id": scenario["id"],
        "scenario_name": scenario["name"],
        "variant": variant,
        "web_app": scenario.get("web_app"),
        "template_family": variant if variant != VARIANT_CLEAN else "clean",
        "traj_meta": traj_meta,
        "injection_surface": traj_meta.get("injection_surface"),
        "in_context": traj_meta.get("in_context"),
        "trajectory_salient": traj_meta.get("trajectory_salient"),
        "attack_chain": " → ".join(traj_meta.get("attack_chain", [])),
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
        attack_labels = {v: labels.get(v) for v in TRAJECTORY_VARIANTS}
        any_salient = any(labels.get(v) == "success" for v in SALIENT_VARIANTS)
        manual = labels.get("manual_hidden") or labels.get("manual_title")
        row = {
            "model": model,
            "scenario_id": scenario_id,
            "baseline": clean,
            **attack_labels,
            "salient_beats_manual": any_salient and manual in ("blocked", "partial", None),
            "any_attack_beats_baseline": any(
                labels.get(v) == "success" for v in TRAJECTORY_VARIANTS
            )
            and clean in ("blocked", "partial", None),
        }
        rows.append(row)

    total = len(rows) or 1
    asr: dict[str, float] = {
        "baseline_asr": round(sum(1 for r in rows if r["baseline"] == "success") / total, 4)
    }
    for v in TRAJECTORY_VARIANTS:
        asr[f"{v}_asr"] = round(sum(1 for r in rows if r.get(v) == "success") / total, 4)
    asr["any_attack_asr"] = round(
        sum(
            1 for r in rows if any(r.get(v) == "success" for v in TRAJECTORY_VARIANTS)
        )
        / total,
        4,
    )
    asr["salient_beats_manual_rate"] = round(
        sum(1 for r in rows if r.get("salient_beats_manual")) / total, 4
    )

    return {
        "total_cases": len(rows),
        "rows": rows,
        **asr,
        **{
            f"{v}_success_count": sum(1 for r in rows if r.get(v) == "success")
            for v in TRAJECTORY_VARIANTS
        },
        "salient_beats_manual_count": sum(1 for r in rows if r.get("salient_beats_manual")),
        "any_attack_beats_baseline_count": sum(
            1 for r in rows if r.get("any_attack_beats_baseline")
        ),
    }


def print_summary_table(rows: list[dict]) -> None:
    print("\n" + "=" * 184)
    print("对比汇总（clean vs Trajectory-aware — manual guess vs Grafter salient surface）")
    print("=" * 184)
    header = (
        f"{'模型':<22} {'场景':<24} {'clean':<10} "
        f"{'hidden':<10} {'title':<10} {'comment':<10} {'reply':<10} {'listing':<10} {'显著>人工'}"
    )
    print(header)
    print("-" * 184)

    def tag(label: str | None) -> str:
        if not label:
            return "-"
        return STATUS_TAG.get(label, label) if label in STATUS_TAG else label

    for row in rows:
        cmp = "是" if row.get("salient_beats_manual") else "-"
        print(
            f"{row['model']:<22} {row['scenario_id']:<24} "
            f"{tag(row.get('baseline')):<10} "
            f"{tag(row.get('manual_hidden')):<10} "
            f"{tag(row.get('manual_title')):<10} "
            f"{tag(row.get('salient_comment')):<10} "
            f"{tag(row.get('salient_reply')):<10} "
            f"{tag(row.get('salient_listing')):<10} {cmp}"
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
                surface = result.get("injection_surface") or "-"
                extra = f" | surface={surface}" if variant != VARIANT_CLEAN else ""
                print_case_result(
                    model,
                    scenario["id"],
                    variant,
                    ev.get("label", "error"),
                    ev.get("reason", "") + extra,
                )
    return sort_results(results, models, scenarios, variants)


def main() -> int:
    set_api_key_map_file(TEST_ROOT / "config" / "46_api_key_map.yaml")

    variant_choices = ["all", VARIANT_CLEAN, *TRAJECTORY_VARIANTS]
    parser = argparse.ArgumentParser(description=STRATEGY_TITLE)
    parser.add_argument("-m", "--model", help="单个模型；默认全部 7 个")
    parser.add_argument(
        "--scenario",
        help="password_change / send_link / exfiltrate_data / pwned_hijack / system_prompt_leak",
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
    print(f"Env       : web_agent_trajectory_env (navigate/click/extract + Grafter vessels)")
    print(f"Variants  : {', '.join(variants)}")
    print(f"Chain     : recon T^b -> Grafter rank -> inject at salient surface -> T* context")
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
        variant_b_key="salient_comment",
        variant_b_label="salient_comment（Grafter #1 issue comment，轨迹选定）",
        compare_label="Trajectory-aware Injection 有效",
        compare_field="any_attack_beats_baseline",
        asr_b_key="any_attack_asr",
    )

    print_summary_table(summary["rows"])
    print(f"\nclean ASR                 : {summary['baseline_asr']:.1%}")
    print(f"manual_hidden ASR         : {summary['manual_hidden_asr']:.1%}")
    print(f"manual_title ASR          : {summary['manual_title_asr']:.1%}")
    print(f"salient_comment ASR       : {summary['salient_comment_asr']:.1%}")
    print(f"salient_reply ASR         : {summary['salient_reply_asr']:.1%}")
    print(f"salient_listing ASR       : {summary['salient_listing_asr']:.1%}")
    print(f"any attack ASR            : {summary['any_attack_asr']:.1%}")
    print(f"salient beats manual        : {summary['salient_beats_manual_rate']:.1%}")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
