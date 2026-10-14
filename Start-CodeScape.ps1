param([int]$Port = 0, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw '缺少 Python 环境。请先按 README.md 执行 uv sync。'
}
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'apps\web\dist\index.html'))) {
    throw '缺少前端构建。请先执行 npm ci 和 npm run build。'
}
if ($Port -ne 0 -and ($Port -lt 1024 -or $Port -gt 65535)) { throw '端口范围为 1024 至 65535；0 表示自动选择。' }
$arguments = @('-m', 'codescape', '--port', "$Port")
if ($NoBrowser) { $arguments += '--no-browser' }
& $pythonPath @arguments
exit $LASTEXITCODE
