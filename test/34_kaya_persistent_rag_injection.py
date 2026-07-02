#!/usr/bin/env python
"""
Strategy 34: Persistent Prompt Injection (Kaya et al., 2025)
Paper: When AI Meets the Web — RAG poison persists in Vector DB

攻击思路：普通 IPI 随对话结束消失；RAG 投毒写入 Vector DB 后永久存在。
任意后续会话只要 Retrieve 就会再次 Trigger：

  Scraper → Embedding → Vector DB  (offline, persistent)
  Session N (无历史): Retrieve → Prompt → Trigger

环境：扩展 rag_kb_env + 新建 VectorKBStore + persistent_rag_injection
（多会话 API，仅 Vector DB 桥接 — 非 MemoryStore、非单次 RAG）

与相近策略：
  • 32/33 — 单次会话 RAG，未证明跨会话持久
  • 14 (Greshake) — MemoryStore 持久化，非 Vector DB
  • 34 — Vector DB 持久化，2–3 次独立 API 会话

变体：
  • clean      — Vector DB 仅良性评论；3 会话均检索干净内容
  • persistent — Vector DB 含投毒评论；3 会话均检索同一 poison chunk

Usage:
  python test/34_kaya_persistent_rag_injection.py
  python test/34_kaya_persistent_rag_injection.py -m qwen3.7-plus
  python test/34_kaya_persistent_rag_injection.py --scenario pwned_hijack
  python test/34_kaya_persistent_rag_injection.py --variant persistent
  python test/34_kaya_persistent_rag_injection.py --serial
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

from lib.api_keys import set_api_key_map_file, validate_model_keys  # noqa: E402
from lib.common import (  # noqa: E402
    ensure_results_dir,
    get_client,
    report_html_path,
    resolve_target_models,
    save_json,
    utc_timestamp,
)
from lib.console import print_case_result, print_summary_table  # noqa: E402
from lib.evaluation import evaluate_response  # noqa: E402
from lib.persistent_rag_injection import (  # noqa: E402
    get_tools_for_scenario,
    load_scenarios,
    run_persistent_rag_case,
)
from lib.rag_kb_env import build_rag_system_prompt  # noqa: E402
from lib.report import write_html_report  # noqa: E402
from lib.runner import run_parallel_by_model, sort_results  # noqa: E402

STRATEGY_TITLE = "Strategy 34: Persistent Prompt Injection (Kaya et al., 2025)"
RUN_PREFIX = "34_kaya_persistent_rag_injection"
VARIANT_CLEAN = "clean"
VARIANT_PERSISTENT = "persistent"


def _eval_sessions(run, scenario: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for s in run.sessions:
        out[s.session_id] = evaluate_response(
            s.content or "",
            scenario,
            tool_calls=s.tool_calls,
        )
    return out


def run_case(client, model: str, scenario: dict, variant: str) -> dict:
    system_prompt = build_rag_system_prompt(scenario)
    tools = get_tools_for_scenario(scenario)
    label = f"{model} | {scenario['id']} | {variant}"

    base = {
        "model": model,
        "scenario_id": scenario["id"],
        "scenario_name": scenario["name"],
        "variant": variant,
        "template_family": "vector-db-persistent" if variant == VARIANT_PERSISTENT else "clean-vector-kb",
        "attacker_instruction": scenario.get("review_injection_message", ""),
        "system_prompt": system_prompt,
        "tools": tools,
        "cross_session": True,
        "persistence_store": "vector_kb",
        "attack_chain": "scraper → embedding → vector_db → retrieve → trigger (repeats)",
    }

    try:
        run = run_persistent_rag_case(
            client,
            model=model,
            scenario=scenario,
            variant=variant,  # type: ignore[arg-type]
            label=label,
        )
    except Exception as exc:
        return {**base, "error": str(exc), "evaluation": {"label": "error", "reason": str(exc)}}

    session_evals = _eval_sessions(run, scenario)
    # Primary rubric: session2 — new conversation, no chat history, KB still poisons
    primary = session_evals.get("session2", {"label": "error", "reason": "missing session2"})

    last = run.sessions[-1]
    return {
        **base,
        "kb_snapshot": run.kb_snapshot,
        "poison_indexed": run.poison_indexed,
        "session_count": len(run.sessions),
        "session_evaluations": session_evals,
        "session1_user_instruction": scenario["session1_user_instruction"],
        "session2_user_instruction": scenario["session2_user_instruction"],
        "session3_user_instruction": scenario.get("session3_user_instruction", ""),
        "session1_messages": run.sessions[0].messages if run.sessions else [],
        "session1_response": run.sessions[0].content if run.sessions else "",
        "session1_tool_calls": run.sessions[0].tool_calls if run.sessions else [],
        "session1_tool_output": run.sessions[0].retrieval if run.sessions else "",
        "session2_messages": run.sessions[1].messages if len(run.sessions) > 1 else [],
        "session2_response": run.sessions[1].content if len(run.sessions) > 1 else "",
        "session2_tool_calls": run.sessions[1].tool_calls if len(run.sessions) > 1 else [],
        "session2_tool_output": run.sessions[1].retrieval if len(run.sessions) > 1 else "",
        "session3_messages": last.messages if len(run.sessions) > 2 else [],
        "session3_response": last.content if len(run.sessions) > 2 else "",
        "session3_tool_calls": last.tool_calls if len(run.sessions) > 2 else [],
        "session3_tool_output": last.retrieval if len(run.sessions) > 2 else "",
        "messages": run.sessions[1].messages if len(run.sessions) > 1 else [],
        "tool_output": run.sessions[1].retrieval if len(run.sessions) > 1 else "",
        "response": run.sessions[1].content if len(run.sessions) > 1 else "",
        "tool_calls": run.sessions[1].tool_calls if len(run.sessions) > 1 else [],
        "evaluation": primary,
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
        persistent = labels.get(VARIANT_PERSISTENT)
        rows.append(
            {
                "model": model,
                "scenario_id": scenario_id,
                "baseline": clean,
                "persistent": persistent,
                "persistent_beats_baseline": persistent == "success"
                and clean in ("blocked", "partial", None),
            }
        )

    total = len(rows) or 1
    indexed = sum(1 for r in results if r.get("poison_indexed"))
    persistent_runs = sum(1 for r in results if r.get("variant") == VARIANT_PERSISTENT and "error" not in r)

    session3_success = 0
    for r in results:
        if r.get("variant") != VARIANT_PERSISTENT or "error" in r:
            continue
        ev = (r.get("session_evaluations") or {}).get("session3", {})
        if ev.get("label") == "success":
            session3_success += 1

    return {
        "total_cases": len(rows),
        "baseline_success_count": sum(1 for r in rows if r["baseline"] == "success"),
        "persistent_success_count": sum(1 for r in rows if r["persistent"] == "success"),
        "persistent_beats_baseline_count": sum(
            1 for r in rows if r["persistent_beats_baseline"]
        ),
        "vector_poison_indexed_count": indexed,
        "vector_poison_index_rate": round(indexed / (persistent_runs or 1), 4),
        "session3_persistent_success_count": session3_success,
        "baseline_asr": round(sum(1 for r in rows if r["baseline"] == "success") / total, 4),
        "persistent_asr": round(sum(1 for r in rows if r["persistent"] == "success") / total, 4),
        "rows": rows,
    }


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
                extra = ""
                if variant == VARIANT_PERSISTENT:
                    s3 = (result.get("session_evaluations") or {}).get("session3", {})
                    extra = (
                        f" | poison_indexed={result.get('poison_indexed')}"
                        f" | session3={s3.get('label', '-')}"
                    )
                print_case_result(
                    model,
                    scenario["id"],
                    variant,
                    ev.get("label", "error"),
                    ev.get("reason", "") + extra,
                )
    return sort_results(results, models, scenarios, variants)


def main() -> int:
    set_api_key_map_file(TEST_ROOT / "config" / "34_api_key_map.yaml")

    parser = argparse.ArgumentParser(description=STRATEGY_TITLE)
    parser.add_argument("-m", "--model", help="单个模型；默认全部 7 个")
    parser.add_argument(
        "--scenario",
        help="pwned_hijack / password_change / send_link / exfiltrate_data",
    )
    parser.add_argument("--variant", choices=["clean", "persistent", "both"], default="both")
    parser.add_argument("--serial", action="store_true", help="串行模式（单 Key，调试用）")
    args = parser.parse_args()

    models = resolve_target_models(args.model)
    scenarios = load_scenarios()
    if args.scenario:
        scenarios = [s for s in scenarios if s["id"] == args.scenario]
        if not scenarios:
            print("[ERROR] 未找到场景", file=sys.stderr)
            return 1

    variants = [VARIANT_CLEAN, VARIANT_PERSISTENT] if args.variant == "both" else [args.variant]
    parallel = not args.serial and len(models) > 1

    if parallel:
        validate_model_keys(models, require_all=True)

    out_dir = ensure_results_dir(f"{RUN_PREFIX}_{utc_timestamp()}")
    html_path = report_html_path(SCRIPT_STEM)
    total = len(models) * len(scenarios) * len(variants)
    sessions_per = 3

    print(STRATEGY_TITLE)
    print(f"Models    : {', '.join(models)}")
    print(f"Scenarios : {', '.join(s['id'] for s in scenarios)}")
    print(f"Env       : VectorKBStore + rag_kb_env (persistent index, no chat history bridge)")
    print(f"Chain     : Vector DB → Session1/2/3(API×N, 独立会话, 同一 poison retrieve)")
    print(f"Mode      : {'7路并行 (每模型专用Key)' if parallel else '串行'}")
    print(f"Cases     : {total} (每用例 ≈{sessions_per}×API 轮次; 主判定=session2)")
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
        variant_b_key=VARIANT_PERSISTENT,
        variant_b_label="persistent（Vector DB 跨会话 Retrieve→Trigger）",
        compare_label="Persistent RAG 有效",
        compare_field="persistent_beats_baseline",
        asr_b_key="persistent_asr",
    )

    print_summary_table(
        summary["rows"],
        variant_b_key=VARIANT_PERSISTENT,
        compare_field="persistent_beats_baseline",
        compare_label="攻击有效",
    )
    print(f"\nclean ASR              : {summary['baseline_asr']:.1%}")
    print(f"persistent ASR         : {summary['persistent_asr']:.1%} (session2 跨会话主判定)")
    print(f"session3 persistent OK : {summary['session3_persistent_success_count']} cases")
    print(f"vector poison index    : {summary['vector_poison_index_rate']:.1%}")
    print(f"JSON  -> {out_dir}")
    print(f"HTML  -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
