# docs — 研究与调研材料（非测试运行时依赖）

本目录与 `test/`（可执行策略脚本）分离，避免根目录堆积 Word/生成脚本。

```
docs/
├── agent-survey/          # Agent 框架调研（原 0719/）
│   ├── zh/                # 中文归纳 docx
│   ├── en/                # 英文归纳 docx
│   ├── scripts/           # _gen_* 生成脚本（输出到 zh/ 或 en/）
│   ├── agent-docs-en.zip  # 英文打包
│   └── archive/           # 杂项残留
├── scenarios/             # 课题场景描述（天气 / AI 论文早报等）
│   ├── scripts/           # 场景文档生成脚本
│   └── Agent_Security_*.docx
└── README.md              # 本文件
```

重新生成 Agent 调研文档示例：

```powershell
.\activate.ps1
python docs/agent-survey/scripts/_gen_openhands_doc.py
python docs/agent-survey/scripts/_gen_openhands_doc_en.py
```
