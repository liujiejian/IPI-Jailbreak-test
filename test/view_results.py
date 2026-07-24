#!/usr/bin/env python
"""从 results/ 重新生成 report/ 下的 HTML，或在浏览器打开。"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_ROOT))

from lib.common import RESULTS_DIR, report_html_path  # noqa: E402
from lib.report import find_latest_run, load_run, write_html_report  # noqa: E402

DEFAULT_STEM = "1_chatinject_template_injection"
DEFAULT_PREFIX = "1_chatinject"
DEFAULT_TITLE = "Strategy 1: Chat Template Injection"


def main() -> int:
    parser = argparse.ArgumentParser(description="查看/重新生成 HTML 报告")
    parser.add_argument("run", nargs="?", help="results 子目录名；省略则用最新一次")
    parser.add_argument("--open", action="store_true", help="浏览器打开报告")
    parser.add_argument("--script", default=DEFAULT_STEM, help="脚本 stem，决定 HTML 文件名")
    parser.add_argument("--prefix", default=DEFAULT_PREFIX, help="results 目录前缀")
    parser.add_argument("--title", default=DEFAULT_TITLE, help="报告标题")
    args = parser.parse_args()

    if args.run:
        run_dir = Path(args.run)
        if not run_dir.is_absolute():
            run_dir = RESULTS_DIR / args.run
    else:
        run_dir = find_latest_run(RESULTS_DIR, args.prefix)

    if not run_dir or not run_dir.is_dir():
        print("[ERROR] 未找到测试结果。请先运行测试脚本。", file=sys.stderr)
        return 1

    results, summary = load_run(run_dir)
    html_path = write_html_report(
        args.script,
        results,
        summary,
        title=args.title,
        run_dir=run_dir,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    )

    print(f"HTML -> {html_path}")
    print(f"数据 -> {run_dir}")

    if args.open:
        webbrowser.open(html_path.resolve().as_uri())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
