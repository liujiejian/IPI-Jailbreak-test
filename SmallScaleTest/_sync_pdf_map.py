# -*- coding: utf-8 -*-
import json
from pathlib import Path

pdf = Path(__file__).resolve().parent / "scenario_02_ai_paper_digest" / "papers" / "pdf"
DATA = Path(__file__).resolve().parent / "scenario_02_ai_paper_digest" / "data" / "papers.json"
papers = json.loads(DATA.read_text(encoding="utf-8"))
for p in papers:
    ax = (p.get("arxiv") or "").strip()
    if ax:
        fname = f"{p['id']}-arxiv-{ax.replace('/', '_')}.pdf"
        if (pdf / fname).exists() and (pdf / fname).stat().st_size > 5000:
            p["local_pdf"] = fname
    lp = (p.get("local_pdf") or "").strip()
    ok = bool(lp) and (pdf / lp).exists()
    print(p["id"], "OK" if ok else "MISS", lp or "-")
DATA.write_text(json.dumps(papers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("synced")
