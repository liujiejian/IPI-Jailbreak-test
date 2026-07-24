# 在 SmallScaleTest 目录启动本地静态站（两个场景同一端口）
# 用法：.\start-localhost.ps1
$port = 8765
Set-Location $PSScriptRoot
Write-Host "Serving SmallScaleTest at http://127.0.0.1:$port/"
Write-Host "SkyBrief home:  http://127.0.0.1:$port/scenario_01_weather_broadcast/"
Write-Host "AI Digest home: http://127.0.0.1:$port/scenario_02_ai_paper_digest/"
Write-Host "Today weather:  http://127.0.0.1:$port/scenario_01_weather_broadcast/pages/forecast/today/"
Write-Host "Paper list:     http://127.0.0.1:$port/scenario_02_ai_paper_digest/pages/list/"
python -m http.server $port
