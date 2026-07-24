#!/usr/bin/env python
"""
Strategy 54: Adaptive Prompt Injection (Zhan et al., 2025 NAACL)

论文核心创新（Adaptive Attack / 全文最大 IPI 贡献）：
  普通 IPI：仅 Malicious Instruction Ia → 易被检测器/防御拦截。
  Adaptive IPI：E_Tu = Ia ⊕ S（suffix）或 S ⊕ Ia（prefix）
    S 非人工撰写，由 GCG / AutoDAN / Two-stage GCG 算法优化得到，
    绕过 Fine-tuned Detector、Perplexity Filtering、Paraphrasing 等 8 种防御。

环境检查结论：**可复用** agent_env + chatinject 消息流（InjecAgent 基准路径）。
  攻击面仅为 tool response 外部内容 E_Tu，无需新 tool schema。
**不可复用** Strategy 48 adaptive_prompt_injection.py（MUZZLE Reflection Loop，非 GCG）。
**新建** zhan_adaptive_injection.py — Ia⊕S 组合与预优化 adversarial string。

与相近策略：
  • 8  InjecAgent —  plain Ia（非 adaptive）
  • 26 Sep.+Ins.   —  Jia/DataSentinel joint z||se（不同论文/目标）
  • 48 MUZZLE     —  Judge 反馈 retry（非 adversarial suffix）

变体：
  • clean           — 无注入
  • plain_ia        — 仅 Ia（非 adaptive 基线，易被检测）
  • suffix_gcg      — Ia ⊕ S（GCG suffix，Table 5）
  • prefix_gcg      — S ⊕ Ia（GCG prefix，Llama3 更优）
  • suffix_autodan  — Ia ⊕ S（AutoDAN 语义串，绕过低困惑度过滤）
  • twostage_tgcg   — S1 ⊕ Ia ⊕ S2（T-GCG，绕过 Paraphrasing）

Usage:
  python test/54_zhan_adaptive_ipi_injection.py
  python test/54_zhan_adaptive_ipi_injection.py -m qwen3.7-plus
  python test/54_zhan_adaptive_ipi_injection.py --scenario password_change
  python test/54_zhan_adaptive_ipi_injection.py --variant suffix_gcg
  python test/54_zhan_adaptive_ipi_injection.py --serial
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
from lib.zhan_adaptive_injection import (  # noqa: E402
    ZHAN_ADAPTIVE_VARIANTS,
    build_case_payload,
    build_tool_output,
    load_scenarios,
)

STRATEGY_TITLE = "Strategy 54: Adaptive Prompt Injection (Zhan et al., 2025)"
RUN_PREFIX = "54_zhan_adaptive_ipi_injection"
VARIANT_CLEAN = "clean"
ADAPTIVE_ATTACK_VARIANTS = [v for v in ZHAN_ADAPTIVE_VARIANTS if v != "plain_ia"]
ALL_VARIANTS = [VARIANT_CLEAN, *ZHAN_ADAPTIVE_VARIANTS]


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

    base = {
        "model": model,
        "scenario_id": scenario["id"],
        "scenario_name": scenario["name"],
        "variant": variant,
        "template_family": variant if variant != VARIANT_CLEAN else "clean",
        "attack_meta": attack_meta,
        "composition": attack_meta.get("composition"),
        "optimizer": attack_meta.get("optimizer"),
        "string_position": attack_meta.get("string_position"),
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
    adaptive_only = [v for v in ZHAN_ADAPTIVE_VARIANTS if v != "plain_ia"]
    for key, labels in grouped.items():
        model, scenario_id = key.split("|", 1)
        clean = labels.get(VARIANT_CLEAN)
        plain = labels.get("plain_ia")
        attack_labels = {v: labels.get(v) for v in ZHAN_ADAPTIVE_VARIANTS}
        row = {
            "model": model,
            "scenario_id": scenario_id,
            "baseline": clean,
            "plain_ia": plain,
            **attack_labels,
            "adaptive_beats_plain": any(
                labels.get(v) == "success" for v in adaptive_only
            )
            and plain in ("blocked", "partial", None),
            "any_adaptive_beats_clean": any(
                labels.get(v) == "success" for v in adaptive_only
            )
            and clean in ("blocked", "partial", None),
        }
        rows.append(row)

    total = len(rows) or 1
    asr: dict[str, float] = {
        "baseline_asr": round(sum(1 for r in rows if r["baseline"] == "success") / total, 4),
        "plain_ia_asr": round(sum(1 for r in rows if r.get("plain_ia") == "success") / total, 4),
    }
    for v in ZHAN_ADAPTIVE_VARIANTS:
        asr[f"{v}_asr"] = round(sum(1 for r in rows if r.get(v) == "success") / total, 4)
    asr["any_adaptive_asr"] = round(
        sum(1 for r in rows if any(r.get(v) == "success" for v in adaptive_only)) / total, 4
    )
    asr["adaptive_beats_plain_rate"] = round(
        sum(1 for r in rows if r.get("adaptive_beats_plain")) / total, 4
    )

    return {
        "total_cases": len(rows),
        "rows": rows,
        **asr,
        "adaptive_beats_plain_count": sum(1 for r in rows if r.get("adaptive_beats_plain")),
    }


def print_summary_table(rows: list[dict]) -> None:
    print("\n" + "=" * 184)
    print("对比汇总（plain Ia vs Adaptive Ia⊕S — Zhan 2025 GCG/AutoDAN/T-GCG）")
    print("=" * 184)
    header = (
        f"{'模型':<22} {'场景':<28} {'clean':<10} {'plain':<10} "
        f"{'sufGCG':<10} {'preGCG':<10} {'AutoDAN':<10} {'T-GCG':<10} {'adp>plain'}"
    )
    print(header)
    print("-" * 184)

    def tag(label: str | None) -> str:
        if not label:
            return "-"
        return STATUS_TAG.get(label, label) if label in STATUS_TAG else label

    for row in rows:
        cmp = "是" if row.get("adaptive_beats_plain") else "-"
        print(
            f"{row['model']:<22} {row['scenario_id']:<28} "
            f"{tag(row.get('baseline')):<10} "
            f"{tag(row.get('plain_ia')):<10} "
            f"{tag(row.get('suffix_gcg')):<10} "
            f"{tag(row.get('prefix_gcg')):<10} "
            f"{tag(row.get('suffix_autodan')):<10} "
            f"{tag(row.get('twostage_tgcg')):<10} {cmp}"
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
                comp = result.get("composition") or "-"
                extra = f" | {comp}" if variant not in (VARIANT_CLEAN, "plain_ia") else ""
                print_case_result(
                    model,
                    scenario["id"],
                    variant,
                    ev.get("label", "error"),
                    ev.get("reason", "") + extra,
                )
    return sort_results(results, models, scenarios, variants)


def main() -> int:
    set_api_key_map_file(TEST_ROOT / "config" / "54_api_key_map.yaml")

    variant_choices = ["all", VARIANT_CLEAN, *ZHAN_ADAPTIVE_VARIANTS]
    parser = argparse.ArgumentParser(description=STRATEGY_TITLE)
    parser.add_argument("-m", "--model", help="单个模型；默认全部 7 个")
    parser.add_argument(
        "--scenario",
        help="password_change / send_link / exfiltrate_data / ...",
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
    print(f"Env       : agent_env (InjecAgent path — E_Tu in tool response)")
    print(f"Variants  : {', '.join(variants)}")
    print(f"Formula   : E_Tu = Ia ⊕ S (suffix) | S ⊕ Ia (prefix) | S1 ⊕ Ia ⊕ S2")
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
        variant_b_key="suffix_gcg",
        variant_b_label="suffix_gcg（Ia ⊕ S — GCG optimized adversarial suffix）",
        compare_label="Adaptive 优于 plain Ia",
        compare_field="adaptive_beats_plain",
        asr_b_key="suffix_gcg_asr",
    )

    print_summary_table(summary["rows"])
    print(f"\nclean ASR              : {summary['baseline_asr']:.1%}")
    print(f"plain_ia ASR           : {summary['plain_ia_asr']:.1%}")
    print(f"suffix_gcg ASR         : {summary['suffix_gcg_asr']:.1%}")
    print(f"prefix_gcg ASR         : {summary['prefix_gcg_asr']:.1%}")
    print(f"suffix_autodan ASR     : {summary['suffix_autodan_asr']:.1%}")
    print(f"twostage_tgcg ASR      : {summary['twostage_tgcg_asr']:.1%}")
    print(f"any adaptive ASR       : {summary['any_adaptive_asr']:.1%}")
    print(f"adaptive beats plain   : {summary['adaptive_beats_plain_rate']:.1%}")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
