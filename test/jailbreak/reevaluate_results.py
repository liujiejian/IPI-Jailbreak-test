#!/usr/bin/env python
"""
离线重评 jailbreak results.json — 无需重新调用 API。

判定规则更新后，用已保存的 response 字段重新跑 evaluate_jailbreak，
重写 evaluation 与 summary.json。

Usage:
  python test/jailbreak/reevaluate_results.py test/jailbreak/results/2_persuasion_20260704T043143Z
  python test/jailbreak/reevaluate_results.py test/jailbreak/results/2_persuasion_20260704T043143Z --write
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

JB_ROOT = Path(__file__).resolve().parent
IPI_TEST_ROOT = JB_ROOT.parent
sys.path.insert(0, str(IPI_TEST_ROOT))
sys.path.insert(0, str(JB_ROOT))

from jblib.evaluation import evaluate_jailbreak  # noqa: E402
from jblib.multi_variant_summary import summarize_variants  # noqa: E402
from jblib.scenarios import load_scenarios  # noqa: E402
from jblib.summary import summarize  # noqa: E402
from jblib.wrong_summary_evaluation import evaluate_wrong_summary  # noqa: E402
from lib.evaluation import evaluate_response  # noqa: E402


def _config_stem_from_run(run_dir: Path) -> str:
    name = run_dir.name
    for n in ("19_", "18_", "17_", "16_", "15_", "14_", "13_", "12_", "11_", "10_", "9_", "8_", "7_", "6_", "5_", "4_", "3_", "2_", "1_"):
        if name.startswith(n):
            return f"{n[0]}_scenarios"
    return "1_scenarios"


def _variants_from_results(results: list[dict]) -> list[str]:
    seen: list[str] = []
    for r in results:
        v = r.get("variant", "")
        if v and v not in seen:
            seen.append(v)
    return seen


def reevaluate_run(run_dir: Path, *, write: bool = False) -> dict:
    results_path = run_dir / "results.json"
    if not results_path.is_file():
        raise FileNotFoundError(results_path)

    results = json.loads(results_path.read_text(encoding="utf-8"))
    config_stem = _config_stem_from_run(run_dir)
    scenarios = {s["id"]: s for s in load_scenarios(config_stem)}

    counts_before: dict[str, int] = {}
    counts_after: dict[str, int] = {}
    changed = 0

    for row in results:
        sid = row.get("scenario_id", "")
        scenario = scenarios.get(sid)
        if not scenario:
            continue
        old = (row.get("evaluation") or {}).get("label", "error")
        counts_before[old] = counts_before.get(old, 0) + 1

        response = row.get("response") or row.get("error") or ""
        if row.get("source_document") or row.get("manipulation_variant"):
            new_eval = evaluate_wrong_summary(
                response,
                scenario,
                variant=row.get("variant", "direct"),
            )
        elif row.get("user_instruction") or row.get("tool_output"):
            new_eval = evaluate_response(
                response,
                scenario,
                tool_calls=row.get("tool_calls"),
            )
        else:
            new_eval = evaluate_jailbreak(response, scenario)
        new = new_eval.get("label", "error")
        counts_after[new] = counts_after.get(new, 0) + 1

        if old != new:
            changed += 1
        row["evaluation"] = new_eval

    variants = _variants_from_results(results)
    baseline = "clean" if "clean" in variants else "direct"
    if len(variants) <= 2 and baseline in variants:
        other = next((v for v in variants if v != baseline), variants[-1] if variants else baseline)
        summary = summarize(results, direct_variant=baseline, jailbreak_variant=other)
    else:
        summary = summarize_variants(results, variants, baseline=baseline)

    report = {
        "run_dir": str(run_dir),
        "config_stem": config_stem,
        "total": len(results),
        "changed": changed,
        "counts_before": counts_before,
        "counts_after": counts_after,
        "summary": summary,
    }

    if write:
        results_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        (run_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (run_dir / "reevaluation_report.json").write_text(
            json.dumps(
                {k: v for k, v in report.items() if k != "summary"},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="离线重评 jailbreak results.json")
    parser.add_argument("run_dir", type=Path, help="results 目录（含 results.json）")
    parser.add_argument("--write", action="store_true", help="写回 results.json / summary.json")
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    report = reevaluate_run(run_dir, write=args.write)

    print(f"Run       : {report['run_dir']}")
    print(f"Config    : {report['config_stem']}")
    print(f"Total     : {report['total']}")
    print(f"Changed   : {report['changed']}")
    print(f"Before    : {report['counts_before']}")
    print(f"After     : {report['counts_after']}")
    if "variant_asr" in report["summary"]:
        print(f"ASR       : {report['summary'].get('variant_asr')}")
    else:
        for k, v in report["summary"].items():
            if k.endswith("_asr"):
                print(f"{k}: {v:.1%}")
    if args.write:
        print("已写回 results.json / summary.json")
    else:
        print("预览模式；加 --write 写回文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
