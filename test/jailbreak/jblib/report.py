"""HTML report for jailbreak tests — output to test/jailbreak/report/."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from .common import report_html_path

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


def format_html_report(
    results: list[dict],
    summary: dict,
    *,
    title: str,
    run_dir: Path | None = None,
    generated_at: str = "",
    variant_b_key: str = "dan",
    variant_b_label: str = "dan（DAN 人格）",
    compare_label: str = "越狱更强",
    compare_field: str = "jailbreak_beats_direct",
    asr_b_key: str = "dan_asr",
) -> str:
    summary_rows = []
    for row in summary.get("rows", []):
        cmp = compare_label if row.get(compare_field) else "-"
        summary_rows.append(
            "<tr>"
            f"<td>{escape(row['model'])}</td>"
            f"<td>{escape(row.get('scenario_id', ''))}</td>"
            f"<td>{_badge(row.get('direct'))}</td>"
            f"<td>{_badge(row.get(variant_b_key))}</td>"
            f"<td>{escape(cmp)}</td>"
            "</tr>"
        )

    grouped: dict[str, dict[str, dict]] = {}
    for r in results:
        key = f"{r.get('model')}|{r.get('scenario_id')}"
        grouped.setdefault(key, {})[r.get("variant", "")] = r

    detail_sections = []
    for key, variants in grouped.items():
        model, scenario_id = key.split("|", 1)
        direct = variants.get("direct")
        variant_b = variants.get(variant_b_key)

        def panel(r: dict | None, name: str) -> str:
            if not r:
                return f'<div class="panel empty"><h4>{escape(name)}</h4><p>未运行</p></div>'
            ev = r.get("evaluation", {})
            turn_log = r.get("turn_log") or []
            turns_html = ""
            if turn_log:
                blocks = []
                for i, t in enumerate(turn_log, start=1):
                    blocks.append(
                        f"--- Turn {i} ---\nUser: {t.get('user', '')}\nAssistant: {t.get('assistant', '')}"
                    )
                turns_html = (
                    f'<details open><summary>多轮铺垫 ({len(turn_log)} 轮)</summary>'
                    f"{_pre(chr(10).join(blocks))}</details>"
                )
            final_note = ""
            if r.get("final_user") and r.get("variant") == "multiturn":
                final_note = (
                    f'<details open><summary>最终 User（有害请求）</summary>'
                    f"{_pre(r.get('final_user', ''))}</details>"
                )
            return f"""<div class="panel">
  <h4>{escape(name)} {_badge(ev.get('label'))}</h4>
  <p class="reason">{escape(ev.get('reason', ''))}</p>
  <p class="reason">API 轮次: {escape(str(r.get('api_turn_count', 1)))}</p>
  <details open><summary>输入 · System Prompt</summary>{_pre(r.get('system_prompt', ''))}</details>
  {turns_html}
  {final_note if final_note else f'<details open><summary>输入 · User Prompt</summary>{_pre(r.get("user_prompt", ""))}</details>'}
  <details><summary>输入 · 完整 API Messages</summary>{_pre(json.dumps(r.get('messages', []), ensure_ascii=False, indent=2))}</details>
  <details open><summary>输出 · 模型回复（最终轮）</summary>{_pre(r.get('response', '') or r.get('error', ''))}</details>
</div>"""

        sample = direct or variant_b or {}
        detail_sections.append(
            f"""<section class="case">
  <h2>{escape(model)} · {escape(scenario_id)}</h2>
  <p><b>有害请求</b> {escape(sample.get('harmful_request', ''))}</p>
  <div class="compare-row">{panel(direct, "direct（直接有害请求）")}{panel(variant_b, variant_b_label)}</div>
</section>"""
        )

    meta = " · ".join(
        p
        for p in [
            f"生成: {escape(generated_at)}" if generated_at else "",
            f"数据: {escape(run_dir.name)}" if run_dir else "",
            f"direct ASR {summary.get('direct_asr', 0):.1%}",
            f"{variant_b_key} ASR {summary.get(asr_b_key, 0):.1%}",
        ]
        if p
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 24px; background: #f0f2f5; }}
  h1 {{ font-size: 22px; margin-bottom: 6px; }}
  h2 {{ font-size: 17px; }}
  .meta {{ color: #555; font-size: 13px; margin-bottom: 20px; }}
  table {{ border-collapse: collapse; width: 100%; background: #fff; margin-bottom: 24px; }}
  th, td {{ border: 1px solid #e2e5ea; padding: 10px 12px; text-align: left; }}
  th {{ background: #e8ecf1; }}
  .badge {{ padding: 3px 10px; border-radius: 4px; font-size: 12px; font-weight: 600; }}
  .ok {{ background: #d1fae5; color: #065f46; }}
  .fail {{ background: #fee2e2; color: #991b1b; }}
  .warn {{ background: #fef3c7; color: #92400e; }}
  .err {{ background: #fecaca; color: #7f1d1d; }}
  .case {{ background: #fff; border: 1px solid #e2e5ea; border-radius: 10px; padding: 16px; margin-bottom: 16px; }}
  .compare-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
  @media (max-width: 900px) {{ .compare-row {{ grid-template-columns: 1fr; }} }}
  .panel {{ background: #f8f9fb; border: 1px solid #e2e5ea; border-radius: 8px; padding: 12px; }}
  summary {{ cursor: pointer; font-weight: 600; font-size: 13px; }}
  .block {{ white-space: pre-wrap; word-break: break-word; background: #fff; border: 1px solid #e5e7eb; padding: 10px; border-radius: 6px; font-size: 12px; max-height: 400px; overflow: auto; }}
  .reason {{ color: #666; font-size: 12px; }}
</style>
</head>
<body>
<h1>{escape(title)}</h1>
<p class="meta">{meta}</p>
<h2>成功 / 失败对比</h2>
<table>
<tr><th>模型</th><th>场景</th><th>direct</th><th>{escape(variant_b_key)}</th><th>对比</th></tr>
{''.join(summary_rows)}
</table>
<h2>输入 / 输出详情</h2>
{''.join(detail_sections)}
</body>
</html>"""


def write_html_report(
    script_stem: str,
    results: list[dict],
    summary: dict,
    *,
    title: str,
    run_dir: Path | None = None,
    generated_at: str = "",
    variant_b_key: str = "dan",
    variant_b_label: str = "dan（DAN 人格）",
    compare_label: str = "越狱更强",
    compare_field: str = "jailbreak_beats_direct",
    asr_b_key: str = "dan_asr",
) -> Path:
    path = report_html_path(script_stem)
    path.write_text(
        format_html_report(
            results,
            summary,
            title=title,
            run_dir=run_dir,
            generated_at=generated_at,
            variant_b_key=variant_b_key,
            variant_b_label=variant_b_label,
            compare_label=compare_label,
            compare_field=compare_field,
            asr_b_key=asr_b_key,
        ),
        encoding="utf-8",
    )
    return path
