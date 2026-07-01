# IAS 助研库 — 大模型安全测试环境
# 激活虚拟环境
.\.venv\Scripts\Activate.ps1

# 加载 .env 环境变量
Get-Content .env | ForEach-Object {
    if ($_ -match '^\s*([^#=]+?)\s*=\s*(.+?)\s*$') {
        [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
    }
}

Write-Host "环境已激活。可用命令:" -ForegroundColor Green
Write-Host "  python scripts/list_models.py          # 列出可用模型"
Write-Host "  python scripts/run_scan.py jailbreak   # 越狱攻击测试"
Write-Host "  python scripts/run_scan.py ipi         # 间接提示词注入(IPI)测试"
Write-Host "  python scripts/run_scan.py all         # 全量扫描(耗时较长)"
