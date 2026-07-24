"""加载 .env 并运行 Garak 安全扫描。"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "garak_config.yaml"

# 预设探针集合
PROBE_SETS = {
    "jailbreak": [
        "dan",
        "encoding",
        "goodside",
        "grandma",
        "leakreplay",
        "misleading",
        "promptinject",
    ],
    "ipi": [
        "latentinjection",
        "promptinject",
    ],
    "all": None,  # 全量扫描
}


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        print(f"错误: 未找到 {env_path}", file=sys.stderr)
        sys.exit(1)
    load_dotenv(env_path)


def build_cmd(probe_set: str, generations: int, model: str) -> list[str]:
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    cmd = [
        str(python),
        "-m",
        "garak",
        "--config",
        str(CONFIG),
        "--target_type",
        "openai.OpenAICompatible",
        "--target_name",
        model,
        "-g",
        str(generations),
    ]

    probes = PROBE_SETS.get(probe_set)
    if probes:
        for probe in probes:
            cmd.extend(["--probes", probe])

    return cmd


def main() -> int:
    parser = argparse.ArgumentParser(description="IAS 助研库 — Garak 安全扫描")
    parser.add_argument(
        "probe_set",
        choices=["jailbreak", "ipi", "all"],
        help="jailbreak=越狱测试, ipi=间接提示词注入, all=全量",
    )
    parser.add_argument("-m", "--model", default=None, help="覆盖 .env 中的 TARGET_MODEL")
    parser.add_argument("-g", "--generations", type=int, default=None, help="每探针生成次数")
    args = parser.parse_args()

    load_env()

    model = args.model or os.getenv("TARGET_MODEL", "qwen-plus")
    generations = args.generations or int(os.getenv("GARAK_GENERATIONS", "3"))

    api_key = os.getenv("OPENAICOMPATIBLE_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("错误: 未配置 API Key", file=sys.stderr)
        return 1

    env = os.environ.copy()
    env["OPENAICOMPATIBLE_API_KEY"] = api_key

    cmd = build_cmd(args.probe_set, generations, model)

    print(f"目标模型: {model}")
    print(f"探针集合: {args.probe_set}")
    print(f"生成次数: {generations}")
    print(f"命令: {' '.join(cmd)}\n")

    return subprocess.call(cmd, cwd=ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
