# SmallScaleTest — 两类原始场景（门户布局 · 日常内容 · 暂无投毒）

本地静态站，供 OpenClaw `web_fetch` 小规模实验。页面结构参考门户站；内容为日常天气与 AI 论文早报。论文站 **50 篇均可本地下载 PDF**。

## 启动

```powershell
cd SmallScaleTest
.\start-localhost.ps1
```

| 场景 | 首页 | 主入口 |
|------|------|--------|
| 1 天象天气 | .../scenario_01_weather_broadcast/ | .../pages/forecast/today/ |
| 2 AI Paper Digest | .../scenario_02_ai_paper_digest/ | .../pages/list/ |

重新生成：

```powershell
python SmallScaleTest/_gen_sites.py
python SmallScaleTest/_copy_thesis_pdfs.py
python SmallScaleTest/_fetch_arxiv_pdfs.py
python SmallScaleTest/_gen_paper_site.py
```
