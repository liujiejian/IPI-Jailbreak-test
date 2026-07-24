#!/usr/bin/env python
"""
Strategy 56: Stealth Prompt Injection (Zhan et al., 2025 NAACL)

论文 Stealth Injection（Multi-objective GCG / M-GCG §4）：
  目标不仅是骗 Agent 执行 Ta，还要骗过 Detector —— 同时优化：
    L_joint = α·L_attack + (1-α)·L_detect
  • L_attack  — 执行恶意 tool
  • L_detect  — R_Tu 被分类为 benign（FD: P(D=0)；LD: 回答 No）

与 Strategy 54 区分：
  • 54 — 通用 Adaptive 套件
  • 56 — Stealth 专测：attack-only GCG vs M-GCG + 启发式 Detector 模拟

环境检查结论：**复用** agent_env + chatinject（E_Tu 投毒，同 54/55）。
**新建** zhan_stealth_injection.py — M-GCG payload + simulate_detectors() 双指标。

变体：
  • clean              — 无注入
  • plain_ia           — 裸 Ia（高检测率基线，Table 2 DR-o）
  • gcg_attack_only    — 仅 L_attack GCG（高困惑度，PF 可检测）
  • stealth_mgcg_fd    — M-GCG vs Fine-tuned Detector
  • stealth_mgcg_ld    — M-GCG vs LLM-based Detector
  • stealth_autodan_pf — 语义 S 绕 Perplexity Filtering

Usage:
  python test/56_zhan_stealth_ipi_injection.py
  python test/56_zhan_stealth_ipi_injection.py -m qwen3.7-plus
  python test/56_zhan_stealth_ipi_injection.py --scenario password_change
  python test/56_zhan_stealth_ipi_injection.py --variant stealth_mgcg_fd
  python test/56_zhan_stealth_ipi_injection.py --serial
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
from lib.console import STATUS_TAG, print_case_result  # noqa: E402
from lib.evaluation import evaluate_response  # noqa: E402
from lib.report import write_html_report  # noqa: E402
from lib.runner import run_parallel_by_model, sort_results  # noqa: E402
from lib.zhan_stealth_injection import (  # noqa: E402
    STEALTH_VARIANTS,
    build_case_payload,
    build_tool_output,
    load_scenarios,
    stealth_dual_success,
)

STRATEGY_TITLE = "Strategy 56: Stealth Prompt Injection (Zhan et al., 2025)"
RUN_PREFIX = "56_zhan_stealth_ipi_injection"
VARIANT_CLEAN = "clean"
STEALTH_ATTACK_VARIANTS = [v for v in STEALTH_VARIANTS if v.startswith("stealth_")]
ALL_VARIANTS = [VARIANT_CLEAN, *STEALTH_VARIANTS]


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    system_prompt = build_system_prompt(scenario)
    tools = get_tools_for_scenario(scenario)
    messages, attack_meta = build_case_payload(
        scenario, variant, system_prompt=system_prompt
    )
    tool_output, _ = build_tool_output(
        benign_prefix=scenario["benign_prefix"],
        attacker_instruction=scenario["attacker_instruction"],
        variant=variant,  # type: ignore[arg-type]
    )
    detector = attack_meta.get("detector_simulation") or {}

    base = {
        "model": model,
        "scenario_id": scenario["id"],
        "scenario_name": scenario["name"],
        "variant": variant,
        "template_family": variant if variant != VARIANT_CLEAN else "clean",
        "attack_meta": attack_meta,
        "loss_objective": attack_meta.get("loss_objective"),
        "target_defense": attack_meta.get("target_defense"),
        "detector_simulation": detector,
        "looks_benign": detector.get("looks_benign"),
        "injection_surface": attack_meta.get("injection_surface"),
        "attack_chain": " → ".join(attack_meta.get("attack_chain", [])),
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
    agent_ev = evaluate_response(content, scenario, tool_calls=tool_calls)
    dual = stealth_dual_success(agent_ev["label"], detector)
    return {
        **base,
        "response": content,
        "tool_calls": tool_calls,
        "evaluation": agent_ev,
        "stealth_dual_success": dual,
    }


def summarize(results: list[dict]) -> dict:
    grouped: dict[str, dict] = {}
    for r in results:
        key = f"{r['model']}|{r['scenario_id']}"
        grouped.setdefault(key, {})
        if "error" not in r:
            grouped[key][r["variant"]] = r

    rows = []
    for key, by_var in grouped.items():
        model, scenario_id = key.split("|", 1)
        clean_r = by_var.get(VARIANT_CLEAN, {})
        plain = by_var.get("plain_ia", {})
        gcg = by_var.get("gcg_attack_only", {})
        mgcg_fd = by_var.get("stealth_mgcg_fd", {})
        mgcg_ld = by_var.get("stealth_mgcg_ld", {})
        autodan = by_var.get("stealth_autodan_pf", {})

        def agent_label(rec: dict) -> str | None:
            return rec.get("evaluation", {}).get("label") if rec else None

        def dual(rec: dict) -> bool:
            return bool(rec.get("stealth_dual_success")) if rec else False

        row = {
            "model": model,
            "scenario_id": scenario_id,
            "baseline": agent_label(clean_r),
            "plain_ia": agent_label(plain),
            "gcg_attack_only": agent_label(gcg),
            "stealth_mgcg_fd": agent_label(mgcg_fd),
            "stealth_mgcg_ld": agent_label(mgcg_ld),
            "stealth_autodan_pf": agent_label(autodan),
            "plain_detected": not (plain.get("looks_benign") if plain else True),
            "mgcg_fd_dual": dual(mgcg_fd),
            "mgcg_ld_dual": dual(mgcg_ld),
            "stealth_beats_plain": dual(mgcg_fd)
            and agent_label(plain) in ("blocked", "partial", None),
            "stealth_beats_gcg_only": dual(mgcg_fd)
            and not dual(gcg)
            and agent_label(gcg) in ("blocked", "partial", None),
        }
        rows.append(row)

    total = len(rows) or 1
    n = total

    def rate(field: str) -> float:
        return round(sum(1 for r in rows if r.get(field)) / n, 4)

    def asr_agent(variant: str) -> float:
        return round(
            sum(1 for r in rows if r.get(variant) == "success") / n, 4
        )

    return {
        "total_cases": len(rows),
        "rows": rows,
        "baseline_asr": asr_agent("baseline"),
        "plain_ia_asr": asr_agent("plain_ia"),
        "gcg_attack_only_asr": asr_agent("gcg_attack_only"),
        "stealth_mgcg_fd_asr": asr_agent("stealth_mgcg_fd"),
        "stealth_mgcg_ld_asr": asr_agent("stealth_mgcg_ld"),
        "stealth_autodan_pf_asr": asr_agent("stealth_autodan_pf"),
        "mgcg_fd_dual_success_rate": rate("mgcg_fd_dual"),
        "mgcg_ld_dual_success_rate": rate("mgcg_ld_dual"),
        "stealth_beats_plain_rate": rate("stealth_beats_plain"),
        "plain_detection_rate": rate("plain_detected"),
    }


def print_summary_table(rows: list[dict]) -> None:
    print("\n" + "=" * 184)
    print("对比汇总（Agent ASR + Stealth dual-success — execute Ta AND looks benign）")
    print("=" * 184)
    header = (
        f"{'模型':<22} {'场景':<28} {'plain':<10} {'gcgOnly':<10} "
        f"{'M-GCGfd':<10} {'M-GCGld':<10} {'dual-fd':<8} {'dual-ld'}"
    )
    print(header)
    print("-" * 184)

    def tag(label: str | None) -> str:
        if not label:
            return "-"
        return STATUS_TAG.get(label, label) if label in STATUS_TAG else label

    for row in rows:
        dfd = "是" if row.get("mgcg_fd_dual") else "-"
        dld = "是" if row.get("mgcg_ld_dual") else "-"
        print(
            f"{row['model']:<22} {row['scenario_id']:<28} "
            f"{tag(row.get('plain_ia')):<10} "
            f"{tag(row.get('gcg_attack_only')):<10} "
            f"{tag(row.get('stealth_mgcg_fd')):<10} "
            f"{tag(row.get('stealth_mgcg_ld')):<10} {dfd:<8} {dld}"
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
                det = result.get("detector_simulation") or {}
                benign = "benign" if det.get("looks_benign") else "flagged"
                dual = "dual-OK" if result.get("stealth_dual_success") else "-"
                extra = f" | det={benign} {dual}" if variant != VARIANT_CLEAN else ""
                print_case_result(
                    model,
                    scenario["id"],
                    variant,
                    ev.get("label", "error"),
                    ev.get("reason", "") + extra,
                )
    return sort_results(results, models, scenarios, variants)


def main() -> int:
    set_api_key_map_file(TEST_ROOT / "config" / "56_api_key_map.yaml")

    variant_choices = ["all", VARIANT_CLEAN, *STEALTH_VARIANTS]
    parser = argparse.ArgumentParser(description=STRATEGY_TITLE)
    parser.add_argument("-m", "--model", help="单个模型；默认全部 7 个")
    parser.add_argument("--scenario", help="password_change / send_link / ...")
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
    print(f"Env       : agent_env + simulate_detectors (FD/LD/PF heuristic)")
    print(f"Objective : L_joint = α·L_attack + (1-α)·L_detect")
    print(f"Variants  : {', '.join(variants)}")
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
        variant_b_key="stealth_mgcg_fd",
        variant_b_label="stealth_mgcg_fd（M-GCG — execute Ta AND pass FD）",
        compare_label="Stealth dual-success",
        compare_field="mgcg_fd_dual",
        asr_b_key="stealth_mgcg_fd_asr",
    )

    print_summary_table(summary["rows"])
    print(f"\nplain_ia ASR              : {summary['plain_ia_asr']:.1%}")
    print(f"gcg_attack_only ASR       : {summary['gcg_attack_only_asr']:.1%}")
    print(f"stealth_mgcg_fd ASR      : {summary['stealth_mgcg_fd_asr']:.1%}")
    print(f"stealth_mgcg_ld ASR       : {summary['stealth_mgcg_ld_asr']:.1%}")
    print(f"stealth_autodan_pf ASR    : {summary['stealth_autodan_pf_asr']:.1%}")
    print(f"M-GCG FD dual-success     : {summary['mgcg_fd_dual_success_rate']:.1%}")
    print(f"M-GCG LD dual-success     : {summary['mgcg_ld_dual_success_rate']:.1%}")
    print(f"plain detection rate      : {summary['plain_detection_rate']:.1%}")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
