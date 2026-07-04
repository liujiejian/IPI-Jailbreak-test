# IPI-Jailbreak-test / IAS 助研库

基于 [Garak](https://github.com/NVIDIA/garak) 搭建的大模型红队测试环境，用于评估模型对**越狱攻击**和**间接提示词注入（IPI）**的抵抗能力。

## 快速开始

```powershell
# 1. 激活环境
.\activate.ps1

# 2. 复制环境变量模板
copy .env.example .env   # 填入 API Key，或将 CSV 放入 API_key/

# 3. 查看可用模型
python scripts/list_models.py

# 4. Garak 扫描
python scripts/run_scan.py jailbreak   # 越狱测试
python scripts/run_scan.py ipi         # IPI 测试

# 5. 论文策略测试
python test/1_chatinject_template_injection.py          # IPI（见 test/README.md）
python test/jailbreak/1_multiturn_persuasion_jailbreak.py       # 越狱（见 test/jailbreak/README.md）
```

## 探针说明（Garak）

| 命令 | 探针集合 | 测试内容 |
|------|----------|----------|
| `jailbreak` | dan, encoding, goodside, grandma, leakreplay, misleading, promptinject | DAN 越狱、编码绕过、角色扮演等 |
| `ipi` | latentinjection, promptinject | 简历/文档/邮件等上下文中隐藏的间接注入 |
| `all` | 全部探针 | 完整安全扫描（耗时数小时） |

## 配置

所有配置在 `.env` 文件中，API 密钥来自 `API_key/` 目录下的阿里云 MaaS CSV。

| 变量 | 说明 |
|------|------|
| `TARGET_MODELS` | 待测模型列表（7 个，见 `.cursor/rules/target-models.mdc`） |
| `GARAK_GENERATIONS` | 每个探针的测试次数（默认 3） |
| `OPENAI_API_BASE` | OpenAI 兼容端点 |

## 目录结构

```
IAS助研库/
├── .env.example            # 环境变量模板
├── .venv/                  # Python 虚拟环境
├── config/                 # Garak 配置
├── scripts/                # Garak 扫描脚本
├── test/                   # ChatInject 等论文策略测试
│   ├── 1_*.py              # Strategy 1: Template Injection
│   ├── 2_*.py              # Strategy 2: Multi-turn
│   └── 3_*.py              # Strategy 3: Mixture-of-Templates
├── thesis/                 # 参考论文 PDF
├── API_key/                # MaaS API Key CSV（勿提交 git）
└── requirements.txt
```

## 注意事项

- `.env` 和 `API_key/` 已加入 `.gitignore`，请勿提交密钥
- 全量扫描会产生大量 API 调用，请注意费用
- 建议先用 `-g 1` 做快速验证：`python scripts/run_scan.py ipi -g 1`

详细测试说明见 [test/README.md](test/README.md)。
