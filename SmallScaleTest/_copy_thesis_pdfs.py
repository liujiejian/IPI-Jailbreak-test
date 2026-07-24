# -*- coding: utf-8 -*-
"""Copy thesis PDFs into scenario_02 papers/pdf with stable slugs."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "thesis"
DST = Path(__file__).resolve().parent / "scenario_02_ai_paper_digest" / "papers" / "pdf"

TOKENS = [
    ("ChatInject", "chatinject"),
    ("LLMThief", "llmthief"),
    ("AgentDojo", "agentdojo"),
    ("Not what", "greshake-aise"),
    ("PromptLocate", "promptlocate"),
    ("When AI Meets", "kaya-web-plugins"),
    ("GraphRAG", "graphrag-under-fire"),
    ("Making Them", "dra-jailbreak"),
    ("Formalizing", "formalizing-pi"),
    ("URLcoat", "urlcoat"),
    ("MUZZLE", "muzzle"),
    ("SoK", "sok-guardrails"),
    ("EnchTable", "enchtable"),
    ("Towards Automating", "agent-permissions"),
    ("LLM-Fuzzer", "llm-fuzzer"),
    ("Adaptive Attacks", "adaptive-ipi-defenses"),
    ("Who Taught", "who-taught-the-lie"),
    ("AgentSentry", "agentsentry"),
    ("MetaBreak", "metabreak"),
]


def main() -> None:
    DST.mkdir(parents=True, exist_ok=True)
    for old in DST.glob("*.pdf"):
        old.unlink()

    mapping: list[tuple[str, str]] = []
    seen: set[str] = set()
    for i, p in enumerate(sorted(SRC.glob("*.pdf")), 1):
        name = p.name
        # skip undated Formalizing duplicate
        if "Formalizing" in name and "2025" not in name:
            print("SKIP", name)
            continue
        author_m = re.match(r"^([A-Za-z]+)", name)
        author = (author_m.group(1) if author_m else "paper").lower()
        year_m = re.search(r"(20\d{2})", name)
        year = year_m.group(1) if year_m else "0000"
        token = f"{i:02d}"
        for needle, slug_tok in TOKENS:
            if needle in name:
                token = slug_tok
                break
        slug = f"{author}-{year}-{token}"
        slug = re.sub(r"[^a-z0-9\-]+", "-", slug).strip("-")
        if slug in seen:
            slug = f"{slug}-{i}"
        seen.add(slug)
        out = DST / f"{slug}.pdf"
        shutil.copy2(p, out)
        mapping.append((slug, name))
        print(f"{slug}  ({out.stat().st_size // 1024} KB)")

    print("copied", len(mapping))
    map_path = DST.parent / "pdf_map.txt"
    map_path.write_text("\n".join(f"{a}\t{b}" for a, b in mapping), encoding="utf-8")


if __name__ == "__main__":
    main()
