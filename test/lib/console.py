"""Terminal status output (ASCII-safe for Windows)."""

from __future__ import annotations

STATUS_TAG = {
    "success": "[SUCCESS]",
    "blocked": "[BLOCKED]",
    "partial": "[PARTIAL]",
    "error": "[ERROR]  ",
}

STATUS_ZH = {
    "success": "攻击成功",
    "blocked": "已拦截",
    "partial": "部分成功",
    "error": "错误",
}


def format_status(label: str, reason: str = "") -> str:
    tag = STATUS_TAG.get(label, "[???????]")
    zh = STATUS_ZH.get(label, label)
    if reason:
        return f"{tag} {zh} ({reason})"
    return f"{tag} {zh}"


def print_case_result(model: str, scenario_id: str, variant: str, label: str, reason: str = "") -> None:
    status = format_status(label, reason)
    print(f"  {model:<22} | {scenario_id:<18} | {variant:<10} -> {status}")


def print_summary_table(
    rows: list[dict],
    *,
    variant_b_key: str = "chatinject",
    compare_field: str = "chatinject_beats_baseline",
    compare_label: str = "ChatInject更强",
) -> None:
    print("\n" + "=" * 78)
    print("对比汇总")
    print("=" * 78)
    print(f"{'模型':<22} {'场景':<18} {'baseline':<12} {variant_b_key:<12} {'对比'}")
    print("-" * 78)
    for row in rows:
        b = row.get("baseline") or "-"
        c = row.get(variant_b_key) or "-"
        b_tag = STATUS_TAG.get(b, b) if b in STATUS_TAG else b
        c_tag = STATUS_TAG.get(c, c) if c in STATUS_TAG else c
        cmp = compare_label if row.get(compare_field) else "-"
        print(f"{row['model']:<22} {row['scenario_id']:<18} {b_tag:<12} {c_tag:<12} {cmp}")
    print("=" * 78)
