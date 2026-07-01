"""Heartbeat while waiting on slow API calls — avoids silent hangs."""

from __future__ import annotations

import os
import threading
import time
from typing import Callable, TypeVar

T = TypeVar("T")

_heartbeat_print_lock = threading.local()


def set_heartbeat_print_lock(lock: threading.Lock | None) -> None:
    _heartbeat_print_lock.lock = lock


def get_heartbeat_print_lock() -> threading.Lock | None:
    return getattr(_heartbeat_print_lock, "lock", None)


def heartbeat_interval() -> float:
    return float(os.getenv("HEARTBEAT_INTERVAL", "15"))


def run_with_heartbeat(
    fn: Callable[[], T],
    *,
    label: str,
    print_lock: threading.Lock | None = None,
    interval: float | None = None,
    deadline: float | None = None,
) -> T:
    """Run blocking work in a thread; heartbeat while waiting; hard cut-off at deadline."""
    wait = interval if interval is not None else heartbeat_interval()
    result: list[T | None] = [None]
    exc: list[BaseException | None] = [None]
    done = threading.Event()

    def worker() -> None:
        try:
            result[0] = fn()
        except BaseException as e:
            exc[0] = e
        finally:
            done.set()

    worker_thread = threading.Thread(target=worker, daemon=True)
    start = time.monotonic()
    worker_thread.start()

    lock = print_lock or get_heartbeat_print_lock()

    while True:
        elapsed = time.monotonic() - start
        if deadline is not None and elapsed >= deadline:
            raise TimeoutError(
                f"Request timed out after {int(elapsed)}s (deadline {int(deadline)}s): {label}"
            )

        tick = wait if deadline is None else min(wait, max(0.0, deadline - elapsed))
        if done.wait(tick):
            worker_thread.join(timeout=0.1)
            if exc[0] is not None:
                raise exc[0]
            assert result[0] is not None
            return result[0]

        elapsed = int(time.monotonic() - start)
        line = f"  ... [心跳 {elapsed}s] 仍在等待 API: {label}"
        if lock:
            with lock:
                print(line, flush=True)
        else:
            print(line, flush=True)
