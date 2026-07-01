"""In-process persistent memory — simulates long-term storage across chat sessions."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MemoryStore:
    """Key-value store that survives session boundaries within one test case."""

    _data: dict[str, str] = field(default_factory=dict)

    def write(self, key: str, value: str) -> None:
        self._data[key] = value

    def read(self, key: str) -> str | None:
        return self._data.get(key)

    def seed(self, key: str, value: str) -> None:
        if key not in self._data:
            self._data[key] = value

    def contains(self, key: str) -> bool:
        return key in self._data

    def snapshot(self) -> dict[str, str]:
        return dict(self._data)
