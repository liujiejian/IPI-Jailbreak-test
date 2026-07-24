# -*- coding: utf-8 -*-
"""Download arXiv PDFs for papers missing local files; update papers.json local_pdf."""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "scenario_02_ai_paper_digest"
DATA = ROOT / "data" / "papers.json"
PDF_DIR = ROOT / "papers" / "pdf"

UA = "Mozilla/5.0 (compatible; SmallScaleTest/1.0; research mirror)"


def download(arxiv: str, dest: Path, tries: int = 4) -> None:
    url = f"https://arxiv.org/pdf/{arxiv}.pdf"
    last_err: Exception | None = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=300) as resp:
                chunks = []
                while True:
                    block = resp.read(256 * 1024)
                    if not block:
                        break
                    chunks.append(block)
                data = b"".join(chunks)
            if len(data) < 5000 or not data[:5].startswith(b"%PDF"):
                raise RuntimeError(f"bad pdf for {arxiv}: size={len(data)}")
            tmp = dest.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(dest)
            return
        except Exception as e:
            last_err = e
            print(f"  retry {i+1}/{tries}: {e}")
            time.sleep(2 + i * 2)
    raise RuntimeError(f"failed {arxiv}: {last_err}")


def main() -> None:
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    papers = json.loads(DATA.read_text(encoding="utf-8"))
    for p in papers:
        lp = (p.get("local_pdf") or "").strip()
        if lp and (PDF_DIR / lp).exists() and (PDF_DIR / lp).stat().st_size > 5000:
            continue
        arxiv = (p.get("arxiv") or "").strip()
        if not arxiv:
            # keep existing thesis local_pdf requirement
            if lp and (PDF_DIR / lp).exists():
                continue
            raise SystemExit(f"{p['id']} has no local_pdf and no arxiv")
        fname = f"{p['id']}-arxiv-{arxiv.replace('/', '_')}.pdf"
        dest = PDF_DIR / fname
        if dest.exists() and dest.stat().st_size > 5000:
            print("exists", fname)
            p["local_pdf"] = fname
            DATA.write_text(json.dumps(papers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            continue
        # remove broken partials
        part = dest.with_suffix(".part")
        if part.exists():
            part.unlink()
        print("fetch", p["id"], arxiv, "...")
        download(arxiv, dest)
        print("  ok", dest.stat().st_size // 1024, "KB")
        p["local_pdf"] = fname
        DATA.write_text(json.dumps(papers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        time.sleep(0.3)

    # also ensure thesis entries keep their local_pdf if already set
    DATA.write_text(json.dumps(papers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    missing = [
        p["id"]
        for p in papers
        if not (p.get("local_pdf") or "").strip()
        or not (PDF_DIR / p["local_pdf"]).exists()
    ]
    print("updated", DATA)
    print("all local?", not missing, "missing", missing)
    if missing:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
