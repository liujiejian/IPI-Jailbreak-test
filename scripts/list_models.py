"""列出阿里云 MaaS 工作空间中可用的模型。"""

import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


def main() -> int:
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
    base_url = os.getenv("OPENAI_API_BASE") or os.getenv("DASHSCOPE_BASE_URL")

    if not api_key:
        print("错误：未在 .env 中找到 OPENAI_API_KEY 或 DASHSCOPE_API_KEY", file=sys.stderr)
        return 1
    if not base_url:
        print("错误：未在 .env 中找到 OPENAI_API_BASE", file=sys.stderr)
        return 1

    client = OpenAI(api_key=api_key, base_url=base_url)

    print(f"端点: {base_url}")
    print("-" * 50)

    try:
        models = client.models.list()
    except Exception as exc:
        print(f"连接失败: {exc}", file=sys.stderr)
        return 1

    model_ids = sorted(m.id for m in models.data)
    if not model_ids:
        print("未找到可用模型，请检查 MaaS 控制台是否已部署模型。")
        return 1

    print(f"共 {len(model_ids)} 个模型:\n")
    for mid in model_ids:
        marker = "  ← 当前 TARGET_MODEL" if mid == os.getenv("TARGET_MODEL") else ""
        print(f"  {mid}{marker}")

    print("\n提示: 将 .env 中的 TARGET_MODEL 修改为你要测试的模型名称。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
