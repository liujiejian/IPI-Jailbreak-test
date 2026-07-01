"""Chat API calls with heartbeat, timeout cut-off, retry, and spare-key fallback."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

from .agent_env import serialize_tool_calls
from .api_keys import (
    get_spare_client,
    get_spare_key_source,
    load_spare_key_from_csv,
    load_spare_key_slot,
    mask_key,
)
from .heartbeat import get_heartbeat_print_lock, run_with_heartbeat


class EmptyResponseError(RuntimeError):
    """API returned 200 but no text and no tool calls."""


class AttemptsExhaustedError(Exception):
    def __init__(self, exc: BaseException, failures: list[BaseException]):
        self.failures = failures
        super().__init__(str(exc))


@dataclass
class AgentCompletion:
    content: str = ""
    tool_calls: list[dict[str, str]] = field(default_factory=list)


def api_timeout() -> float:
    return float(os.getenv("API_TIMEOUT", "120"))


def api_max_attempts() -> int:
    return int(os.getenv("API_MAX_ATTEMPTS", "2"))


def is_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    msg = str(exc).lower()
    if "timed out" in msg or "timeout" in msg:
        return True
    return "timeout" in type(exc).__name__.lower()


def is_no_response_failure(exc: BaseException) -> bool:
    if isinstance(exc, EmptyResponseError):
        return True
    return is_timeout_error(exc)


def _emit(line: str) -> None:
    lock = get_heartbeat_print_lock()
    if lock:
        with lock:
            print(line, flush=True)
    else:
        print(line, flush=True)


def _retry_reason(exc: BaseException | None) -> str:
    if exc is None:
        return "空响应"
    if is_timeout_error(exc):
        return f"超时切断 {int(api_timeout())}s"
    return type(exc).__name__


def _make_do_call(
    client: OpenAI,
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    temperature: float,
    max_tokens: int,
) -> Callable[[], AgentCompletion]:
    def do_call() -> AgentCompletion:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        resp = client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        return AgentCompletion(
            content=msg.content or "",
            tool_calls=serialize_tool_calls(msg.tool_calls),
        )

    return do_call


def _has_response(result: AgentCompletion) -> bool:
    return bool(result.content.strip()) or bool(result.tool_calls)


def _attempts_with_client(
    client: OpenAI,
    *,
    label: str,
    do_call: Callable[[], AgentCompletion],
    attempts: int,
) -> AgentCompletion:
    last_exc: BaseException | None = None
    failures: list[BaseException] = []

    for attempt in range(1, attempts + 1):
        try:
            if attempt > 1:
                _emit(f"  ... [重试 {attempt}/{attempts}] {label}")

            result = run_with_heartbeat(do_call, label=label, deadline=api_timeout())
            if _has_response(result):
                return result

            last_exc = EmptyResponseError("empty response")
            failures.append(last_exc)
            if attempt < attempts:
                _emit(
                    f"  ... [{_retry_reason(last_exc)}] 第 {attempt}/{attempts} 次失败，"
                    f"重新请求: {label}"
                )
                continue
            raise AttemptsExhaustedError(last_exc, failures)
        except BaseException as exc:
            last_exc = exc
            failures.append(exc)
            if attempt < attempts:
                _emit(
                    f"  ... [{_retry_reason(exc)}] 第 {attempt}/{attempts} 次失败 "
                    f"({exc}) — 重新请求: {label}"
                )
                continue
            raise AttemptsExhaustedError(exc, failures) from exc

    assert last_exc is not None
    raise AttemptsExhaustedError(last_exc, failures)


def _run_with_fallback_keys(
    primary_client: OpenAI,
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    label: str,
    temperature: float,
    max_tokens: int,
) -> AgentCompletion:
    attempts = api_max_attempts()
    do_call = _make_do_call(
        primary_client,
        model=model,
        messages=messages,
        tools=tools,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    try:
        return _attempts_with_client(primary_client, label=label, do_call=do_call, attempts=attempts)
    except AttemptsExhaustedError as exhausted:
        failures = exhausted.failures
        if not failures or not all(is_no_response_failure(f) for f in failures):
            raise failures[-1] from exhausted

        spare = get_spare_client()
        if spare is None:
            raise failures[-1] from exhausted

        slot = load_spare_key_slot()
        src = get_spare_key_source()
        spare_key, _ = load_spare_key_from_csv()
        spare_hint = mask_key(spare_key or "")
        _emit(
            f"  ... [切换 {slot}] 专用 Key {attempts} 次无响应/超时，"
            f"改用备用 Key={spare_hint} ({src}): {label}"
        )

        spare_do_call = _make_do_call(
            spare,
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        try:
            return _attempts_with_client(spare, label=label, do_call=spare_do_call, attempts=attempts)
        except AttemptsExhaustedError as spare_exhausted:
            raise spare_exhausted.failures[-1] from spare_exhausted


def create_chat_completion(
    client: OpenAI,
    *,
    model: str,
    messages: list[dict[str, Any]],
    label: str,
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0,
    max_tokens: int = 1024,
) -> AgentCompletion:
    """Agent turn with tools; retry and spare-key fallback on failure."""
    return _run_with_fallback_keys(
        client,
        model=model,
        messages=messages,
        tools=tools,
        label=label,
        temperature=temperature,
        max_tokens=max_tokens,
    )
