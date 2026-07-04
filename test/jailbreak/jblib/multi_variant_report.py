"""HTML report for multi-variant jailbreak tests."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from .common import report_html_path
from .persuasion import VARIANT_LABELS

LABEL_ZH = {
    "success": "攻击成功",
    "blocked": "已拦截",
    "partial": "部分成功",
    "error": "错误",
}


def _badge(label: str | None) -> str:
    if not label:
        return '<span class="na">-</span>'
    cls = {"success": "ok", "blocked": "fail", "partial": "warn", "error": "err"}.get(label, "na")
    return f'<span class="badge {cls}">{escape(LABEL_ZH.get(label, label))}</span>'


def _pre(text: str) -> str:
    return f'<pre class="block">{escape(text or "")}</pre>'


def _format_response(r: dict) -> str:
    parts: list[str] = []
    if r.get("response"):
        parts.append(str(r["response"]))
    tool_calls = r.get("tool_calls") or []
    if tool_calls:
        parts.append("\n--- tool_calls ---")
        parts.append(json.dumps(tool_calls, ensure_ascii=False, indent=2))
    if not parts:
        return str(r.get("error", ""))
    return "\n".join(parts)


def _panel(r: dict | None, name: str) -> str:
    if not r:
        return f'<div class="panel empty"><h4>{escape(name)}</h4><p>未运行</p></div>'
    ev = r.get("evaluation", {})
    user_block = r.get("user_prompt") or r.get("user_instruction") or ""
    agent_extra = ""
    if r.get("tool_output"):
        agent_extra += (
            f"<details open><summary>Tool Output</summary>{_pre(r.get('tool_output', ''))}</details>"
        )
    if r.get("tools"):
        agent_extra += (
            f"<details><summary>可用 Tools</summary>"
            f"{_pre(json.dumps(r.get('tools', []), ensure_ascii=False, indent=2))}</details>"
        )
    return f"""<div class="panel">
  <h4>{escape(name)} {_badge(ev.get('label'))}</h4>
  <p class="reason">{escape(ev.get('reason', ''))}</p>
  <details open><summary>输入 · User</summary>{_pre(user_block)}</details>
  {agent_extra}
  <details><summary>完整 Messages</summary>{_pre(json.dumps(r.get('messages', []), ensure_ascii=False, indent=2))}</details>
  <details open><summary>模型回复</summary>{_pre(_format_response(r))}</details>
</div>"""


def format_multi_variant_report(
    results: list[dict],
    summary: dict,
    *,
    title: str,
    variants: list[str],
    run_dir: Path | None = None,
    generated_at: str = "",
    variant_labels: dict[str, str] | None = None,
) -> str:
    labels = variant_labels or VARIANT_LABELS
    variant_asr = summary.get("variant_asr") or {}

    header_cells = "".join(f"<th>{escape(labels.get(v, v))}</th>" for v in variants)
    summary_rows = []
    for row in summary.get("rows", []):
        cells = "".join(f"<td>{_badge(row.get(v))}</td>" for v in variants)
        summary_rows.append(
            "<tr>"
            f"<td>{escape(row['model'])}</td>"
            f"<td>{escape(row.get('scenario_id', ''))}</td>"
            f"{cells}"
            "</tr>"
        )

    asr_row = "".join(
        f"<td>{variant_asr.get(v, 0):.1%}</td>" for v in variants
    )

    grouped: dict[str, dict[str, dict]] = {}
    for r in results:
        key = f"{r.get('model')}|{r.get('scenario_id')}"
        grouped.setdefault(key, {})[r.get("variant", "")] = r

    detail_sections = []
    for key, variant_results in grouped.items():
        model, scenario_id = key.split("|", 1)
        sample = next(iter(variant_results.values()), {})
        panels = "".join(
            _panel(variant_results.get(v), labels.get(v, v)) for v in variants
        )
        detail_sections.append(
            f"""<section class="case">
  <h2>{escape(model)} · {escape(scenario_id)}</h2>
  <p><b>用户任务</b> {escape(sample.get('user_instruction') or sample.get('harmful_request', ''))}</p>
  <p><b>注入目标</b> {escape(sample.get('attacker_instruction', ''))}</p>
  {('<details><summary>源文档</summary>' + _pre(sample.get('source_document', '')) + '</details>') if sample.get('source_document') else ''}
  <div class="grid">{panels}</div>
</section>"""
        )

    best = summary.get("best_variant", "")
    meta_parts = [
        f"生成: {escape(generated_at)}" if generated_at else "",
        f"数据: {escape(run_dir.name)}" if run_dir else "",
        f"最佳变体: {escape(labels.get(best, best))}" if best else "",
    ]
    meta = " · ".join(p for p in meta_parts if p)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 24px; background: #f0f2f5; }}
  h1 {{ font-size: 22px; }}
  .meta {{ color: #555; font-size: 13px; margin-bottom: 20px; }}
  table {{ border-collapse: collapse; width: 100%; background: #fff; margin-bottom: 24px; font-size: 12px; }}
  th, td {{ border: 1px solid #e2e5ea; padding: 8px 10px; text-align: left; }}
  th {{ background: #e8ecf1; }}
  .badge {{ padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
  .ok {{ background: #d1fae5; color: #065f46; }}
  .fail {{ background: #fee2e2; color: #991b1b; }}
  .warn {{ background: #fef3c7; color: #92400e; }}
  .err {{ background: #fecaca; color: #7f1d1d; }}
  .case {{ background: #fff; border: 1px solid #e2e5ea; border-radius: 10px; padding: 16px; margin-bottom: 16px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }}
  .panel {{ background: #f8f9fb; border: 1px solid #e2e5ea; border-radius: 8px; padding: 10px; }}
  summary {{ cursor: pointer; font-weight: 600; font-size: 12px; }}
  .block {{ white-space: pre-wrap; word-break: break-word; background: #fff; border: 1px solid #e5e7eb; padding: 8px; border-radius: 6px; font-size: 11px; max-height: 320px; overflow: auto; }}
  .reason {{ color: #666; font-size: 11px; }}
</style>
</head>
<body>
<h1>{escape(title)}</h1>
<p class="meta">{meta}</p>
<h2>各变体结果矩阵</h2>
<table>
<tr><th>模型</th><th>场景</th>{header_cells}</tr>
{''.join(summary_rows)}
<tr style="font-weight:600;background:#f8fafc"><td colspan="2">ASR（success）</td>{asr_row}</tr>
</table>
<h2>详情</h2>
{''.join(detail_sections)}
</body>
</html>"""


def write_multi_variant_report(
    script_stem: str,
    results: list[dict],
    summary: dict,
    *,
    title: str,
    variants: list[str],
    run_dir: Path | None = None,
    generated_at: str = "",
    variant_labels: dict[str, str] | None = None,
) -> Path:
    path = report_html_path(script_stem)
    path.write_text(
        format_multi_variant_report(
            results,
            summary,
            title=title,
            variants=variants,
            run_dir=run_dir,
            generated_at=generated_at,
            variant_labels=variant_labels,
        ),
        encoding="utf-8",
    )
    return path
