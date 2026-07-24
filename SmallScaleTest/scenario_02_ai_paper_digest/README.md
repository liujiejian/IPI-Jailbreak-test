# 场景 2：AI Paper Digest（原始 · 无投毒 · 50 篇均可下载）

不做专题分类。每篇标注来源；**全部 PDF 本地托管**，列表与详情均可下载/预览。

## 入口

- 列表：http://127.0.0.1:8765/scenario_02_ai_paper_digest/pages/list/
- 单篇：.../pages/paper/p01/ （含「下载 PDF」与 iframe 预览）

## 同步 PDF / 重生页面

```powershell
python SmallScaleTest/_copy_thesis_pdfs.py
python SmallScaleTest/_fetch_arxiv_pdfs.py
python SmallScaleTest/_gen_paper_site.py
```
