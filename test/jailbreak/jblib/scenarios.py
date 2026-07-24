"""Load jailbreak scenario YAML from test/jailbreak/config/."""

from __future__ import annotations

from pathlib import Path

import yaml

from .common import JB_CONFIG_DIR

_EVAL_CACHE: dict | None = None


def _load_eval_fields() -> dict[str, dict]:
    global _EVAL_CACHE
    if _EVAL_CACHE is not None:
        return _EVAL_CACHE
    path = JB_CONFIG_DIR / "_eval_fields.yaml"
    if not path.is_file():
        _EVAL_CACHE = {}
        return _EVAL_CACHE
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    _EVAL_CACHE = {str(k): dict(v) for k, v in (data.get("eval_by_id") or {}).items()}
    return _EVAL_CACHE


def load_scenarios(config_stem: str = "1_scenarios") -> list[dict]:
    path = JB_CONFIG_DIR / f"{config_stem}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"场景配置不存在: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    scenarios = data.get("scenarios") or []
    if not scenarios:
        raise ValueError(f"场景列表为空: {path}")

    eval_by_id = _load_eval_fields()
    merged: list[dict] = []
    for scenario in scenarios:
        item = dict(scenario)
        extra = eval_by_id.get(item.get("id", ""))
        if extra:
            item.update(extra)
        merged.append(item)
    return merged
