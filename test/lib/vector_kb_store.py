"""In-process Vector DB — simulates persistent RAG index across chat sessions.

Poison indexed offline remains retrievable in every future session with no chat
history carried over (Kaya et al., 2025 — Persistent Prompt Injection).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .rag_kb_env import describe_kb_index_entry


@dataclass
class VectorKBStore:
    """Minimal vector KB: chunk list persisted for repeated semantic search."""

    chunks: list[tuple[dict[str, str], str]] = field(default_factory=list)
    poison_indexed: bool = False
    indexed_at: str = "2025-01-14"

    def search(self, query: str) -> str:
        parts = [f'Knowledge base search results for query: "{query}"\n']
        for meta, text in self.chunks:
            parts.append(describe_kb_index_entry(text=text, **meta))
            parts.append("")
        return "\n".join(parts).rstrip()

    def document_count(self) -> int:
        return len(self.chunks)

    def snapshot(self) -> dict:
        return {
            "poison_indexed": self.poison_indexed,
            "indexed_documents": self.document_count(),
            "indexed_at": self.indexed_at,
        }
