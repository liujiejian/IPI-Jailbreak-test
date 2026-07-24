"""Parallel test runner — one model per worker, merge results."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from .api_keys import get_api_key_for_model, get_client_for_model, get_key_source_for_model, load_api_key_config, mask_key
from .console import print_case_result
from .heartbeat import set_heartbeat_print_lock


def sort_results(results: list[dict], models: list[str], scenarios: list[dict], variants: list[str]) -> list[dict]:
    scenario_ids = [s["id"] for s in scenarios]
    variant_rank = {v: i for i, v in enumerate(variants)}

    def sort_key(r: dict) -> tuple:
        model_i = models.index(r["model"]) if r["model"] in models else 999
        scen_i = scenario_ids.index(r["scenario_id"]) if r["scenario_id"] in scenario_ids else 999
        var_i = variant_rank.get(r["variant"], 999)
        return (model_i, scen_i, var_i)

    return sorted(results, key=sort_key)


def run_model_batch(
    model: str,
    scenarios: list[dict],
    variants: list[str],
    run_case_fn: Callable,
    print_lock: threading.Lock,
    counter: list[int],
    total: int,
) -> list[dict]:
    cfg = load_api_key_config()
    client = get_client_for_model(model, cfg)
    key_hint = mask_key(get_api_key_for_model(model, cfg) or "")
    src = get_key_source_for_model(model, cfg)
    results: list[dict] = []

    with print_lock:
        print(f"  >> 启动 [{model}] Key={key_hint} ({src})", flush=True)

    set_heartbeat_print_lock(print_lock)
    try:
        for scenario in scenarios:
            for variant in variants:
                with print_lock:
                    counter[0] += 1
                    n = counter[0]
                    label = f"{model} | {scenario['id']} | {variant}"
                    print(f"  [{n}/{total}] 等待 API: {label} ...", flush=True)
                result = run_case_fn(client, model, scenario, variant)
                results.append(result)
                ev = result.get("evaluation", {})
                with print_lock:
                    print_case_result(
                        model,
                        scenario["id"],
                        variant,
                        ev.get("label", "error"),
                        ev.get("reason", ""),
                    )

        with print_lock:
            print(f"  << 完成 [{model}] {len(results)} 条", flush=True)
        return results
    finally:
        set_heartbeat_print_lock(None)


def run_parallel_by_model(
    models: list[str],
    scenarios: list[dict],
    variants: list[str],
    run_case_fn: Callable,
    max_workers: int | None = None,
) -> list[dict]:
    print_lock = threading.Lock()
    counter = [0]
    total = len(models) * len(scenarios) * len(variants)
    workers = max_workers or len(models)
    all_results: list[dict] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                run_model_batch,
                model,
                scenarios,
                variants,
                run_case_fn,
                print_lock,
                counter,
                total,
            ): model
            for model in models
        }
        for fut in as_completed(futures):
            model = futures[fut]
            try:
                all_results.extend(fut.result())
            except Exception as exc:
                with print_lock:
                    print(f"  [ERROR] 模型 {model} 线程失败: {exc}", flush=True)
                raise

    return sort_results(all_results, models, scenarios, variants)
