#!/usr/bin/env python
"""
Strategy 55: Prefix vs Suffix Injection (Zhan et al., 2025 NAACL)

论文 Prefix Injection 发现（Table 5 / Appendix A）：
  自适应串 S 的位置影响 ASR，且与 Agent 骨干模型相关：
    • Llama3-8B finetuned agent → S ⊕ Ia（**Prefix**）更优
    • Vicuna-7B ReAct agent      → Ia ⊕ S（**Suffix**）更优

结构示例（Prefix）：
  [optimized S]
  Ignore previous instructions
  Call attacker tool / {Ia}

与 Strategy 54 区分：
  • 54 — 完整 Adaptive 套件（AutoDAN、T-GCG、plain_ia 等）
  • 55 — **受控 ablation**：相同 Ia 块，仅变 S 位置（prefix vs suffix）

环境检查结论：**复用** agent_env + chatinject（与 54 相同 InjecAgent E_Tu 路径）。
**新建** zhan_prefix_injection.py — 归一化 Ia + prefix/suffix 组合。
GCG 串复用 zhan_adaptive_injection.py 中预优化 S。

变体：
  • clean            — 无注入
  • ia_only          — 仅 Ia（无 S，对照）
  • suffix_gcg       — Ia ⊕ S（GCG，Vicuna-favored）
  • prefix_gcg       — S ⊕ Ia（GCG，Llama3-favored）
  • suffix_semantic  — Ia ⊕ 可读 S
  • prefix_semantic  — 可读 S ⊕ Ia（S + Ignore previous + tool call）

Usage:
  python test/55_zhan_prefix_ipi_injection.py
  python test/55_zhan_prefix_ipi_injection.py -m qwen3.7-plus
  python test/55_zhan_prefix_ipi_injection.py --scenario password_change
  python test/55_zhan_prefix_ipi_injection.py --variant prefix_gcg
  python test/55_zhan_prefix_ipi_injection.py --serial
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
from lib.zhan_prefix_injection import (  # noqa: E402
    PREFIX_INJECTION_VARIANTS,
    build_case_payload,
    build_tool_output,
    load_scenarios,
)

STRATEGY_TITLE = "Strategy 55: Prefix vs Suffix Injection (Zhan et al., 2025)"
RUN_PREFIX = "55_zhan_prefix_ipi_injection"
VARIANT_CLEAN = "clean"
POSITION_VARIANTS = ("suffix_gcg", "prefix_gcg", "suffix_semantic", "prefix_semantic")
ALL_VARIANTS = [VARIANT_CLEAN, *PREFIX_INJECTION_VARIANTS]

# Paper analogy: DeepSeek ≈ Llama3 (prefix-favored), Qwen ≈ Vicuna (suffix-favored)
DEEPSEEK_MODELS = frozenset({"deepseek-v4-pro", "deepseek-v4-flash"})
QWEN_MODELS = frozenset(
    {"qwen3.7-plus", "qwen3.7-max", "qwen3.6-plus", "qwen3.6-flash", "qwen3.5-plus"}
)


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
        "string_position": attack_meta.get("string_position"),
        "paper_note": attack_meta.get("paper_note"),
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


def _position_win(prefix_label: str | None, suffix_label: str | None) -> str | None:
    """Return 'prefix' | 'suffix' | 'tie' | None if insufficient data."""
    if not prefix_label or not suffix_label:
        return None
    p_ok = prefix_label == "success"
    s_ok = suffix_label == "success"
    if p_ok and not s_ok:
        return "prefix"
    if s_ok and not p_ok:
        return "suffix"
    if p_ok and s_ok:
        return "tie"
    return None


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
        gcg_win = _position_win(labels.get("prefix_gcg"), labels.get("suffix_gcg"))
        row = {
            "model": model,
            "scenario_id": scenario_id,
            "baseline": labels.get(VARIANT_CLEAN),
            "ia_only": labels.get("ia_only"),
            "suffix_gcg": labels.get("suffix_gcg"),
            "prefix_gcg": labels.get("prefix_gcg"),
            "suffix_semantic": labels.get("suffix_semantic"),
            "prefix_semantic": labels.get("prefix_semantic"),
            "gcg_position_winner": gcg_win,
            "prefix_beats_suffix_gcg": gcg_win == "prefix",
            "suffix_beats_prefix_gcg": gcg_win == "suffix",
            "paper_aligned": (
                (model in DEEPSEEK_MODELS and gcg_win == "prefix")
                or (model in QWEN_MODELS and gcg_win == "suffix")
            ),
        }
        rows.append(row)

    total = len(rows) or 1
    asr = {
        "baseline_asr": round(sum(1 for r in rows if r["baseline"] == "success") / total, 4),
        "ia_only_asr": round(sum(1 for r in rows if r.get("ia_only") == "success") / total, 4),
        "suffix_gcg_asr": round(sum(1 for r in rows if r.get("suffix_gcg") == "success") / total, 4),
        "prefix_gcg_asr": round(sum(1 for r in rows if r.get("prefix_gcg") == "success") / total, 4),
        "suffix_semantic_asr": round(
            sum(1 for r in rows if r.get("suffix_semantic") == "success") / total, 4
        ),
        "prefix_semantic_asr": round(
            sum(1 for r in rows if r.get("prefix_semantic") == "success") / total, 4
        ),
    }
    asr["prefix_beats_suffix_gcg_rate"] = round(
        sum(1 for r in rows if r.get("prefix_beats_suffix_gcg")) / total, 4
    )
    asr["suffix_beats_prefix_gcg_rate"] = round(
        sum(1 for r in rows if r.get("suffix_beats_prefix_gcg")) / total, 4
    )
    asr["paper_aligned_rate"] = round(
        sum(1 for r in rows if r.get("paper_aligned")) / total, 4
    )

    ds_rows = [r for r in rows if r["model"] in DEEPSEEK_MODELS]
    qw_rows = [r for r in rows if r["model"] in QWEN_MODELS]
    ds_n = len(ds_rows) or 1
    qw_n = len(qw_rows) or 1
    asr["deepseek_prefix_win_rate"] = round(
        sum(1 for r in ds_rows if r.get("prefix_beats_suffix_gcg")) / ds_n, 4
    )
    asr["qwen_suffix_win_rate"] = round(
        sum(1 for r in qw_rows if r.get("suffix_beats_prefix_gcg")) / qw_n, 4
    )

    return {"total_cases": len(rows), "rows": rows, **asr}


def print_summary_table(rows: list[dict]) -> None:
    print("\n" + "=" * 176)
    print("对比汇总（Prefix S⊕Ia vs Suffix Ia⊕S — Zhan 2025 position ablation）")
    print("=" * 176)
    header = (
        f"{'模型':<22} {'场景':<28} {'clean':<10} {'ia':<10} "
        f"{'sufGCG':<10} {'preGCG':<10} {'winner':<8} {'paper'}"
    )
    print(header)
    print("-" * 176)

    def tag(label: str | None) -> str:
        if not label:
            return "-"
        return STATUS_TAG.get(label, label) if label in STATUS_TAG else label

    for row in rows:
        win = row.get("gcg_position_winner") or "-"
        aligned = "✓" if row.get("paper_aligned") else "-"
        print(
            f"{row['model']:<22} {row['scenario_id']:<28} "
            f"{tag(row.get('baseline')):<10} "
            f"{tag(row.get('ia_only')):<10} "
            f"{tag(row.get('suffix_gcg')):<10} "
            f"{tag(row.get('prefix_gcg')):<10} {win:<8} {aligned}"
        )
    print("=" * 176)


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
                pos = result.get("string_position") or "-"
                extra = f" | pos={pos}" if variant not in (VARIANT_CLEAN, "ia_only") else ""
                print_case_result(
                    model,
                    scenario["id"],
                    variant,
                    ev.get("label", "error"),
                    ev.get("reason", "") + extra,
                )
    return sort_results(results, models, scenarios, variants)


def main() -> int:
    set_api_key_map_file(TEST_ROOT / "config" / "55_api_key_map.yaml")

    variant_choices = ["all", VARIANT_CLEAN, *PREFIX_INJECTION_VARIANTS]
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
    print(f"Env       : agent_env (E_Tu prefix/suffix ablation)")
    print(f"Variants  : {', '.join(variants)}")
    print(f"Paper     : Llama3→prefix | Vicuna→suffix (DeepSeek/Qwen proxy)")
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
        variant_b_key="prefix_gcg",
        variant_b_label="prefix_gcg（S ⊕ Ia — Llama3-favored GCG prefix）",
        compare_label="Prefix 优于 Suffix (GCG)",
        compare_field="prefix_beats_suffix_gcg",
        asr_b_key="prefix_gcg_asr",
    )

    print_summary_table(summary["rows"])
    print(f"\nclean ASR                 : {summary['baseline_asr']:.1%}")
    print(f"ia_only ASR               : {summary['ia_only_asr']:.1%}")
    print(f"suffix_gcg ASR            : {summary['suffix_gcg_asr']:.1%}")
    print(f"prefix_gcg ASR            : {summary['prefix_gcg_asr']:.1%}")
    print(f"prefix beats suffix (GCG) : {summary['prefix_beats_suffix_gcg_rate']:.1%}")
    print(f"suffix beats prefix (GCG) : {summary['suffix_beats_prefix_gcg_rate']:.1%}")
    print(f"DeepSeek prefix-win rate  : {summary['deepseek_prefix_win_rate']:.1%}")
    print(f"Qwen suffix-win rate      : {summary['qwen_suffix_win_rate']:.1%}")
    print(f"paper-aligned rate        : {summary['paper_aligned_rate']:.1%}")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
