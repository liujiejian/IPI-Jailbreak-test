#!/usr/bin/env python
"""检查 API_key CSV 与模型的对应关系（不发起 API 请求）。"""

from __future__ import annotations

import sys
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_ROOT))

from lib.api_keys import (  # noqa: E402
    API_KEY_DIR,
    find_csv_for_slot,
    load_api_key_config,
    load_model_slot_mapping,
    load_spare_key_from_csv,
    load_spare_key_slot,
    mask_key,
)
from lib.common import DEFAULT_TARGET_MODELS  # noqa: E402


def main() -> int:
    print("API Key 映射检查")
    print(f"CSV 目录: {API_KEY_DIR}")
    print("-" * 72)

    mapping = load_model_slot_mapping()
    cfg = load_api_key_config()
    keys = cfg.get("keys") or {}
    sources = cfg.get("sources") or {}

    ok = True
    for model in DEFAULT_TARGET_MODELS:
        slot = mapping.get(model, "?")
        csv_path = find_csv_for_slot(slot) if slot != "?" else None
        key = keys.get(model)
        if key and csv_path:
            print(f"[OK] {model:<22} <- {slot:<8} <- {csv_path.name}")
            print(f"     Key={mask_key(key)}")
        else:
            ok = False
            print(f"[MISSING] {model:<22} slot={slot} csv={csv_path}")

    spare_slot = load_spare_key_slot()
    spare_key, spare_src = load_spare_key_from_csv()
    if spare_key:
        print(f"[OK] 备用 Key  {spare_slot:<8} <- {spare_src}")
        print(f"     Key={mask_key(spare_key)}  (专用 Key 两次无响应/超时后启用)")
    else:
        print(f"[WARN] 备用 Key  {spare_slot} 未找到 CSV")

    print("-" * 72)
    print(f"base_url: {cfg.get('base_url', '(未设置)')}")
    print(f"已加载 {len(keys)}/{len(DEFAULT_TARGET_MODELS)} 个 Key")

    if not ok:
        print("\n[ERROR] 映射不完整，请检查 API_key 目录与 1_api_key_map.yaml", file=sys.stderr)
        return 1
    print("\n全部就绪，可运行: python test/1_chatinject_template_injection.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
