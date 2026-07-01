# IAS 助研库 — 大模型安全测试

基于 [Garak](https://github.com/NVIDIA/garak) 搭建的大模型红队测试环境，用于评估模型对**越狱攻击**和**间接提示词注入（IPI）**的抵抗能力。

## 快速开始

```powershell
# 1. 激活环境
.\activate.ps1

# 2. 查看可用模型
python scripts/list_models.py

# 3. 运行测试（修改 .env 中的 TARGET_MODEL 指定目标模型）
python scripts/run_scan.py jailbreak   # 越狱测试
python scripts/run_scan.py ipi         # IPI 测试
python scripts/run_scan.py all         # 全量扫描
```

## 探针说明

| 命令 | 探针集合 | 测试内容 |
|------|----------|----------|
| `jailbreak` | dan, encoding, goodside, grandma, leakreplay, misleading, promptinject | DAN 越狱、编码绕过、角色扮演等 |
| `ipi` | latentinjection, promptinject | 简历/文档/邮件等上下文中隐藏的间接注入 |
| `all` | 全部探针 | 完整安全扫描（耗时数小时） |

## 配置

所有配置在 `.env` 文件中，API 密钥来自 `API_key/` 目录下的阿里云 MaaS 信息。

| 变量 | 说明 |
|------|------|
| `TARGET_MODEL` | 待测模型名称（默认 `qwen-plus`） |
| `GARAK_GENERATIONS` | 每个探针的测试次数（默认 3） |
| `OPENAI_API_BASE` | OpenAI 兼容端点 |

## 目录结构

```
IAS助研库/
├── .env                    # 环境变量（含 API Key）
├── .venv/                  # Python 虚拟环境
├── config/
│   └── garak_config.yaml   # Garak 生成器配置
├── scripts/
│   ├── list_models.py      # 列出 MaaS 可用模型
│   └── run_scan.py         # 运行安全扫描
├── API_key/                # 原始 API 凭证（勿提交 git）
├── activate.ps1            # 一键激活脚本
└── requirements.txt
```

## 注意事项

- `.env` 和 `API_key/` 已加入 `.gitignore`，请勿提交密钥
- 全量扫描会产生大量 API 调用，请注意费用
- 建议先用 `-g 1` 做快速验证：`python scripts/run_scan.py ipi -g 1`
