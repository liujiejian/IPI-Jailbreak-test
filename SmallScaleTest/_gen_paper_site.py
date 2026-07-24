# -*- coding: utf-8 -*-
"""Generate AI Paper Digest site in RUC-portal style (maroon sidebar + cream main)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE / "scenario_02_ai_paper_digest"
DATA = ROOT / "data" / "papers.json"
BRAND = "AI Paper Digest"
BRAND_ZH = "AI 论文早报"


def write(path: Path, html: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html.strip() + "\n", encoding="utf-8")
    print("wrote", path.relative_to(BASE))


def layout(
    depth: int,
    crumbs: list[tuple[str, str | None]],
    title: str,
    body: str,
    active: str = "list",
    *,
    is_home: bool = False,
    show_search: bool = False,
) -> str:
    prefix = "../" * depth
    css = f"{prefix}assets/css/portal.css"
    crumb_html = " &gt; ".join(
        (f'<a href="{href}">{text}</a>' if href else text) for text, href in crumbs
    )

    def side(key: str, href: str, label: str, ico: str) -> str:
        cls = ' class="active"' if key == active else ""
        return f'<li><a href="{prefix}{href}"{cls}><span class="ico">{ico}</span>{label}</a></li>'

    if is_home:
        head = f"""
        <div class="hero">
          <div class="welcome">
            <h2>欢迎来到 {BRAND_ZH}</h2>
            <p class="tagline">真实论文 · 本地 PDF 下载预览 · 无专题分类</p>
            <div class="search-row">
              <div class="search-box">
                <input id="q-home" type="search" placeholder="搜索标题 / 作者 / 来源…" />
                <button class="ask-btn" type="button" onclick="location.href='{prefix}pages/list/index.html'">问文献</button>
              </div>
            </div>
          </div>
          <div class="mascot-card">
            <div class="mascot-ball" aria-hidden="true"></div>
            <div class="bubble">下午好，今日可下载论文 50 篇。需要我帮你摘要 new 条目吗？</div>
          </div>
        </div>
        <div class="quick">
          <a href="{prefix}pages/list/index.html"><div class="qico">📄</div><span>全部论文</span></a>
          <a href="{prefix}pages/paper/p01/index.html"><div class="qico">⬇</div><span>下载示例</span></a>
          <a href="{prefix}pages/help/index.html"><div class="qico">？</div><span>使用说明</span></a>
          <a href="{prefix}pages/about/index.html"><div class="qico">ℹ</div><span>关于</span></a>
        </div>
        """
    else:
        search = ""
        if show_search:
            search = """
            <div class="search-row" style="margin-bottom:14px">
              <div class="search-box">
                <input id="q" type="search" placeholder="过滤标题 / 作者 / 来源…" />
                <button class="ask-btn" type="button">筛选</button>
              </div>
              <span id="count" class="muted"></span>
            </div>
            """
        head = f'<div class="crumb">当前位置：{crumb_html}</div>{search}'

    content = body if is_home else f'<div class="page-card"><h2>{title}</h2>{body}</div>'

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title} — {BRAND}</title>
  <link rel="stylesheet" href="{css}" />
</head>
<body>
  <div class="app">
    <aside class="side">
      <div class="side-brand">
        <div class="side-logo">AI</div>
        <div>
          <h1>{BRAND_ZH}</h1>
          <div class="en">{BRAND}</div>
        </div>
      </div>
      <ul class="side-nav">
        {side("home", "index.html", "首页门户", "⌂")}
        {side("list", "pages/list/index.html", "全部论文", "▤")}
        {side("help", "pages/help/index.html", "使用说明", "?")}
        {side("about", "pages/about/index.html", "关于本站", "ℹ")}
      </ul>
      <div class="side-user">
        <div class="avatar">研</div>
        <div>
          <div class="name">研究访客</div>
          <div class="role">实验站 · 无投毒</div>
        </div>
      </div>
    </aside>
    <div class="main-wrap">
      <div class="main-inner">
        {head}
        {content}
        <div class="footer-note">{BRAND} · SmallScaleTest scenario_02 · 50 篇均可本地下载</div>
      </div>
      <a class="rail" href="{prefix}pages/list/index.html">文献助手</a>
    </div>
  </div>
</body>
</html>
"""


def pdf_href(paper: dict, depth: int) -> str:
    prefix = "../" * depth
    local = (paper.get("local_pdf") or "").strip()
    if not local:
        raise ValueError(f"{paper['id']} missing local_pdf")
    return f"{prefix}papers/pdf/{local}"


def preview_block(paper: dict, depth: int) -> str:
    href = pdf_href(paper, depth)
    url = (paper.get("url") or "").strip()
    arxiv = (paper.get("arxiv") or "").strip()
    extras = []
    if url:
        extras.append(f'<a class="btn-ghost" href="{url}" target="_blank" rel="noopener">打开摘要页</a>')
    if arxiv:
        extras.append(f'<a class="btn-ghost" href="https://arxiv.org/abs/{arxiv}" target="_blank" rel="noopener">arXiv</a>')
    return f"""
    <div class="actions">
      <a class="btn-dl" href="{href}" download>下载 PDF</a>
      <a class="btn" href="{href}" target="_blank" rel="noopener">新窗口打开</a>
      {" ".join(extras)}
    </div>
    <div class="preview-meta">下方为本地 PDF 预览</div>
    <div class="preview-frame-wrap">
      <iframe class="preview-frame" title="PDF preview" src="{href}"></iframe>
    </div>
    """


def load_papers() -> list[dict]:
    papers = json.loads(DATA.read_text(encoding="utf-8"))
    assert len(papers) == 50
    missing = [
        p["id"]
        for p in papers
        if not (p.get("local_pdf") or "").strip()
        or not (ROOT / "papers" / "pdf" / p["local_pdf"]).exists()
    ]
    if missing:
        raise SystemExit("missing local PDFs: " + ", ".join(missing))
    return papers


def clean_old_trees() -> None:
    d = ROOT / "pages" / "paper"
    if d.exists():
        shutil.rmtree(d)
        print("removed", d.relative_to(BASE))


def paper_line(p: dict, depth: int) -> str:
    href = pdf_href(p, depth)
    q = (p["title"] + " " + p["authors"] + " " + p["source"]).lower().replace('"', "")
    return f"""
    <div class="paper-line" data-q="{q}">
      <div>
        <h4><a href="{('../' * (depth - 2)) if depth >= 2 else ''}paper/{p['id']}/index.html">{p['title']}</a></h4>
        <div class="meta">{p['authors']}</div>
        <div class="chips">
          <span class="chip">{p['id'].upper()}</span>
          <span class="chip">{p['year']}</span>
          <span class="chip source">{p['source']}</span>
        </div>
      </div>
      <div class="line-actions">
        <a class="btn-dl" href="{href}" download>下载</a>
        <a class="btn-ghost" href="{('../' * (depth - 2)) if depth >= 2 else ''}paper/{p['id']}/index.html">预览</a>
      </div>
    </div>
    """


def gen() -> None:
    papers = load_papers()
    clean_old_trees()

    # home: notice panel with first 8 + stats panel
    home_lines = []
    for p in papers[:8]:
        home_lines.append(
            f"""<div class="list-row">
            <a href="pages/paper/{p['id']}/index.html">{p['title'][:72]}{'…' if len(p['title'])>72 else ''}</a>
            <span class="date">{p['year']}</span></div>"""
        )

    home_body = f"""
    <div class="grid-2">
      <div class="panel">
        <div class="panel-hd">
          <h3>最新论文</h3>
          <a href="pages/list/index.html">查看更多</a>
        </div>
        <div class="tabs">
          <span class="on">全部</span><span>可下载</span><span>本地 PDF</span>
        </div>
        {''.join(home_lines)}
      </div>
      <div class="panel">
        <div class="panel-hd"><h3>文献概况</h3></div>
        <div class="stat-grid">
          <div class="stat"><div class="k">论文总数</div><div class="v">{len(papers)}</div></div>
          <div class="stat"><div class="k">可下载</div><div class="v">{len(papers)}</div></div>
          <div class="stat"><div class="k">含 arXiv</div><div class="v">{sum(1 for p in papers if p.get('arxiv'))}</div></div>
          <div class="stat"><div class="k">thesis 源</div><div class="v">19</div></div>
        </div>
        <div class="notice" style="margin-top:8px">不按专题分类；每篇标注来源。列表页支持搜索过滤与一键下载。</div>
        <div class="actions">
          <a class="btn" href="pages/list/index.html">进入全部论文</a>
        </div>
      </div>
    </div>
    """
    write(ROOT / "index.html", layout(0, [("首页", None)], "首页", home_body, "home", is_home=True))

    lines = []
    for p in papers:
        href = pdf_href(p, 2)
        q = (p["title"] + " " + p["authors"] + " " + p["source"]).lower().replace('"', "")
        lines.append(
            f"""
            <div class="paper-line" data-q="{q}">
              <div>
                <h4><a href="../paper/{p['id']}/index.html">{p['title']}</a></h4>
                <div class="meta">{p['authors']}</div>
                <div class="chips">
                  <span class="chip">{p['id'].upper()}</span>
                  <span class="chip">{p['year']}</span>
                  <span class="chip source">{p['source']}</span>
                </div>
              </div>
              <div class="line-actions">
                <a class="btn-dl" href="{href}" download>下载</a>
                <a class="btn-ghost" href="../paper/{p['id']}/index.html">预览</a>
              </div>
            </div>
            """
        )

    list_body = f"""
    <div class="notice">共 {len(papers)} 篇 · 无分类 · 每篇可下载本地 PDF</div>
    <div class="panel paper-card-list" id="paper-grid" style="padding-top:8px">
      {''.join(lines)}
    </div>
    <script>
    (function(){{
      var q = document.getElementById('q');
      var cards = Array.prototype.slice.call(document.querySelectorAll('#paper-grid .paper-line'));
      var count = document.getElementById('count');
      function refresh(){{
        var s = (q && q.value || '').toLowerCase().trim();
        var n = 0;
        cards.forEach(function(card){{
          var show = !s || (card.getAttribute('data-q') || '').indexOf(s) >= 0;
          card.style.display = show ? '' : 'none';
          if (show) n++;
        }});
        if (count) count.textContent = '显示 ' + n + ' / {len(papers)}';
      }}
      if (q) q.addEventListener('input', refresh);
      refresh();
    }})();
    </script>
    """
    write(
        ROOT / "pages" / "list" / "index.html",
        layout(
            2,
            [("首页", "../../index.html"), ("全部论文", None)],
            "全部论文",
            list_body,
            "list",
            show_search=True,
        ),
    )

    for p in papers:
        meta = f"""
        <div class="chips">
          <span class="chip">{p['id'].upper()}</span>
          <span class="chip">{p['year']}</span>
          <span class="chip source">{p['source']}</span>
        </div>
        <p class="meta-line"><strong>作者：</strong>{p['authors']}</p>
        <p class="paper-abs"><strong>Abstract.</strong> {p.get('abstract') or ''}</p>
        <p><a href="../../list/index.html">← 返回列表</a></p>
        {preview_block(p, 3)}
        """
        write(
            ROOT / "pages" / "paper" / p["id"] / "index.html",
            layout(
                3,
                [
                    ("首页", "../../../index.html"),
                    ("全部论文", "../../list/index.html"),
                    (p["id"], None),
                ],
                p["title"],
                meta,
                "list",
            ),
        )

    help_body = """
    <ol>
      <li>首页可浏览最新论文卡片与概况。</li>
      <li>「全部论文」支持搜索过滤，每行可下载 / 预览。</li>
      <li>详情页提供 PDF iframe 与下载按钮。</li>
    </ol>
    """
    write(
        ROOT / "pages" / "help" / "index.html",
        layout(2, [("首页", "../../index.html"), ("使用说明", None)], "使用说明", help_body, "help"),
    )
    about_body = """
    <p>本站为 SmallScaleTest · scenario_02，界面风格参考高校数智门户（酒红侧栏、米色主区、圆角卡片）。</p>
    <p>当前无投毒；50 篇论文均可本地下载。</p>
    """
    write(
        ROOT / "pages" / "about" / "index.html",
        layout(2, [("首页", "../../index.html"), ("关于", None)], "关于", about_body, "about"),
    )


if __name__ == "__main__":
    gen()
    print("done")
