"""Load per-model API keys from API_key/*.csv or 1_api_keys.yaml."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import yaml
from openai import OpenAI

from .common import DEFAULT_TARGET_MODELS, ROOT, TEST_ROOT, load_project_env

KEYS_FILE = TEST_ROOT / "config" / "1_api_keys.yaml"
KEYS_EXAMPLE = TEST_ROOT / "config" / "1_api_keys.yaml.example"
MAP_FILE = TEST_ROOT / "config" / "1_api_key_map.yaml"
API_KEY_DIR = ROOT / "API_key"
_active_map_file: Path | None = None


def set_api_key_map_file(path: Path) -> None:
    """Override model→CSV slot mapping (e.g. Strategy 2 uses 2_api_key_map.yaml)."""
    global _active_map_file
    _active_map_file = path


def parse_maas_key_csv(path: Path) -> dict[str, str]:
    """Parse Aliyun MaaS exported key-value CSV."""
    result: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or "," not in line:
            i += 1
            continue
        key, rest = line.split(",", 1)
        key = key.strip()
        if rest.startswith('"'):
            if rest.endswith('"') and len(rest) > 1:
                value = rest[1:-1]
                i += 1
            else:
                parts = [rest[1:]]
                i += 1
                while i < len(lines):
                    if lines[i].endswith('"'):
                        parts.append(lines[i][:-1])
                        i += 1
                        break
                    parts.append(lines[i])
                    i += 1
                value = "\n".join(parts)
        else:
            value = rest.strip()
            i += 1
        result[key] = value
    return result


def _slot_number(slot: str) -> str:
    m = re.search(r"apiKey(\d+)", slot, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"(\d+)", slot)
    return m.group(1) if m else slot


def find_csv_for_slot(slot: str) -> Path | None:
    if not API_KEY_DIR.is_dir():
        return None
    num = _slot_number(slot)
    matches = sorted(API_KEY_DIR.glob(f"*apiKey{num}*.csv"))
    return matches[0] if matches else None


def load_model_slot_mapping() -> dict[str, str]:
    map_file = _active_map_file or MAP_FILE
    if map_file.is_file():
        data = yaml.safe_load(map_file.read_text(encoding="utf-8")) or {}
        mapping = data.get("mapping") or {}
        if mapping:
            return {str(k): str(v) for k, v in mapping.items()}

    # fallback: DEFAULT_TARGET_MODELS order -> apiKey1..7
    return {model: f"apiKey{i}" for i, model in enumerate(DEFAULT_TARGET_MODELS, 1)}


def load_spare_key_slot() -> str:
    load_project_env()
    map_file = _active_map_file or MAP_FILE
    if map_file.is_file():
        data = yaml.safe_load(map_file.read_text(encoding="utf-8")) or {}
        slot = data.get("spare_key_slot")
        if slot:
            return str(slot)
    return os.getenv("API_SPARE_KEY_SLOT", "apiKey8")


def load_spare_key_from_csv() -> tuple[str | None, str]:
    csv_path = find_csv_for_slot(load_spare_key_slot())
    if not csv_path:
        return None, ""
    parsed = parse_maas_key_csv(csv_path)
    api_key = (parsed.get("apiKey") or "").strip()
    return api_key or None, csv_path.name


def load_keys_from_csv() -> dict:
    mapping = load_model_slot_mapping()
    keys: dict[str, str] = {}
    base_url = ""
    sources: dict[str, str] = {}

    for model, slot in mapping.items():
        csv_path = find_csv_for_slot(slot)
        if not csv_path:
            continue
        parsed = parse_maas_key_csv(csv_path)
        api_key = (parsed.get("apiKey") or "").strip()
        if api_key:
            keys[model] = api_key
            sources[model] = csv_path.name
        if not base_url:
            base_url = (parsed.get("openAiCompatible") or "").strip()

    return {"keys": keys, "base_url": base_url, "sources": sources}


def load_api_key_config() -> dict:
    cfg = load_keys_from_csv()

    if KEYS_FILE.is_file():
        manual = yaml.safe_load(KEYS_FILE.read_text(encoding="utf-8")) or {}
        manual_keys = manual.get("keys") or {}
        cfg["keys"].update({k: v for k, v in manual_keys.items() if v})
        if manual.get("base_url"):
            cfg["base_url"] = manual["base_url"]

    load_project_env()
    if not cfg.get("base_url"):
        cfg["base_url"] = os.getenv("OPENAI_API_BASE") or os.getenv("DASHSCOPE_BASE_URL") or ""

    return cfg


def get_base_url(config: dict | None = None) -> str:
    cfg = config if config is not None else load_api_key_config()
    return cfg.get("base_url") or ""


def get_api_key_for_model(model: str, config: dict | None = None) -> str | None:
    cfg = config if config is not None else load_api_key_config()
    key = (cfg.get("keys") or {}).get(model)
    return str(key).strip() if key else None


def get_key_source_for_model(model: str, config: dict | None = None) -> str:
    cfg = config if config is not None else load_api_key_config()
    return (cfg.get("sources") or {}).get(model, "1_api_keys.yaml or .env")


def get_client_for_model(model: str, config: dict | None = None) -> OpenAI:
    load_project_env()
    cfg = config if config is not None else load_api_key_config()
    base_url = get_base_url(cfg)
    if not base_url:
        print("[ERROR] 未找到 base_url（API_key CSV 或 .env 的 OPENAI_API_BASE）", file=sys.stderr)
        sys.exit(1)

    api_key = get_api_key_for_model(model, cfg)
    if not api_key:
        api_key = os.getenv("OPENAICOMPATIBLE_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        print(f"[ERROR] 模型 {model} 无 API Key", file=sys.stderr)
        sys.exit(1)

    from .common import openai_http_timeout

    return OpenAI(api_key=api_key, base_url=base_url, timeout=openai_http_timeout())


def get_spare_client(config: dict | None = None) -> OpenAI | None:
    """备用 Key（默认 apiKey8），专用 Key 两次无响应/超时后切换。"""
    load_project_env()
    cfg = config if config is not None else load_api_key_config()
    base_url = get_base_url(cfg)
    if not base_url:
        return None

    spare_key, _ = load_spare_key_from_csv()
    if not spare_key:
        return None

    from .common import openai_http_timeout

    return OpenAI(api_key=spare_key, base_url=base_url, timeout=openai_http_timeout())


def get_spare_key_source() -> str:
    _, name = load_spare_key_from_csv()
    return name or load_spare_key_slot()


def validate_model_keys(models: list[str], require_all: bool = True) -> list[str]:
    cfg = load_api_key_config()
    keys = cfg.get("keys") or {}
    missing = [m for m in models if not (keys.get(m) or "").strip()]
    if require_all and missing:
        print("[ERROR] 以下模型未找到专用 API Key:", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        print("\n请检查:", file=sys.stderr)
        print(f"  1) {API_KEY_DIR} 下是否有 apiKey1..7 的 CSV", file=sys.stderr)
        print(f"  2) {MAP_FILE.name} 中的模型映射是否正确", file=sys.stderr)
        sys.exit(1)
    return missing


def mask_key(key: str) -> str:
    if len(key) <= 12:
        return "***"
    return key[:8] + "..." + key[-4:]
