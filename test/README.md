# test/ — 论文策略测试

## API Key（7 个 CSV，自动读取）

Key 放在项目根目录 **`API_key/`** 下，文件名形如：

```
默认业务空间-apiKey1-5930959.csv
默认业务空间-apiKey2-5932559.csv
...
默认业务空间-apiKey7-5932724.csv
```

**模型对应关系**（在 `config/1_api_key_map.yaml` 中配置）：

| CSV 槽位 | 模型 |
|----------|------|
| apiKey1 | qwen3.7-plus |
| apiKey2 | qwen3.7-max |
| apiKey3 | qwen3.6-plus |
| apiKey4 | qwen3.6-flash |
| apiKey5 | qwen3.5-plus |
| apiKey6 | deepseek-v4-pro |
| apiKey7 | deepseek-v4-flash |

无需手动复制 Key 到 yaml，脚本会**自动从 CSV 解析**。

### 检查映射是否正确

```powershell
python test/verify_api_keys.py
```

应显示 7 行 `[OK]`。

### 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `API_TIMEOUT` | 120 | 单次 API 超时（秒），超时后切断请求 |
| `API_MAX_ATTEMPTS` | 2 | 每次 Key 失败后最多尝试次数（含首次） |
| `API_SPARE_KEY_SLOT` | apiKey8 | 备用 Key 槽位（专用 Key 两次无响应/超时后启用） |
| `HEARTBEAT_INTERVAL` | 15 | 等待 API 时心跳间隔（秒） |

等待期间终端会打印 `[心跳 45s] 仍在等待 API: ...`。  
若专用 Key 连续两次无响应/超时，会切换 apiKey8 备用 Key 继续测试。

### 运行全量测试（7 路并行）

```powershell
python test/1_chatinject_template_injection.py
```

报告：`test/report/1_chatinject_template_injection测试.html`

---

## 策略 1 — Chat Template Injection

见 `1_chatinject_template_injection.py`，规范见 `.cursor/rules/test-scripts.mdc`。

```powershell
python test/1_chatinject_template_injection.py
```

报告：`test/report/1_chatinject_template_injection测试.html`

---

## 策略 2 — Multi-turn Prompt Injection

论文第二个创新：**Template-Based Multi-turn Persuasion**。将 Tool Output 伪造成已发生的多轮 User/Assistant 对话，恶意指令隐藏其中。

| Variant | 说明 |
|---------|------|
| `baseline` | One-shot 纯文本注入（`IMPORTANT!!! Ignore...`） |
| `multiturn` | 模板标签伪造多轮对话（User→Assistant→User→Assistant） |

```powershell
python test/2_chatinject_multiturn_injection.py
python test/2_chatinject_multiturn_injection.py -m qwen3.7-plus
python test/2_chatinject_multiturn_injection.py --scenario password_change
```

报告：`test/report/2_chatinject_multiturn_injection测试.html`

场景配置：`config/2_scenarios.yaml`（含每场景的 `multiturn_dialogue`）

---

## 策略 3 — Mixture-of-Templates (MoT)

论文第三个创新：攻击者**不知道** Agent 底层模型，在 Tool Output 中**同时注入**多种 chat template（Qwen + Llama + GPT/ChatML + Gemma + DeepSeek + Generic），任一命中即可提高 ASR。

| Variant | 说明 |
|---------|------|
| `baseline` | One-shot 纯文本注入 |
| `mot` | 6 种模板连续拼接（`lib/templates.py` → `MOT_TEMPLATES`） |

```powershell
python test/3_chatinject_mixture_templates.py
python test/3_chatinject_mixture_templates.py -m qwen3.7-plus
python test/3_chatinject_mixture_templates.py --scenario password_change
```

报告：`test/report/3_chatinject_mixture_templates测试.html`

与策略 1 的区别：策略 1 的 `chatinject` 使用**针对被测模型的 oracle 模板**；策略 3 的 `mot` **不依赖模型信息**，模拟真实攻击者。

---

可选：若需覆盖 CSV，可手动创建 `config/1_api_keys.yaml`（优先级高于 CSV）。
