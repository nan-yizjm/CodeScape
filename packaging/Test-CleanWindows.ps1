param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [ValidateSet('Developer', 'CleanWin10', 'CleanWin11')][string]$EnvironmentKind = 'Developer'
)
$ErrorActionPreference = 'Stop'
$phase = '校验安装文件'
$ownedProcess = $null
$utf8 = New-Object System.Text.UTF8Encoding($false)
$exePath = (Resolve-Path -LiteralPath $Executable).Path
$bundleRoot = Split-Path -Parent $exePath
$bundlePrefix = $bundleRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
$tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$testRoot = [IO.Path]::GetFullPath((Join-Path $tempBase ('CodeScape-验收-' + [Guid]::NewGuid().ToString())))
if (-not $testRoot.StartsWith($tempBase, [StringComparison]::OrdinalIgnoreCase)) {
    throw '验收目录不在当前用户临时目录中。'
}
[void][IO.Directory]::CreateDirectory($testRoot)
$reportPath = Join-Path $testRoot 'Windows独立环境验收.json'
$report = [ordered]@{
    '日期UTC' = [DateTime]::UtcNow.ToString('o')
    '环境声明' = @{ Developer = '开发机'; CleanWin10 = '操作者声明为干净Windows10'; CleanWin11 = '操作者声明为干净Windows11' }[$EnvironmentKind]
    '环境边界' = '脚本不能证明系统是干净镜像；须同时记录镜像来源和人工界面验收。'
    '系统版本' = [Environment]::OSVersion.VersionString
    'PowerShell版本' = $PSVersionTable.PSVersion.ToString()
    '程序sha256' = (Get-FileHash -LiteralPath $exePath -Algorithm SHA256).Hash.ToLowerInvariant()
    '数据目录' = $testRoot
    '状态' = '进行中'
    '模型请求' = 0
    '执行样例源码' = $false
}
function Require([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}
function Start-OwnedProcess([string]$Arguments) {
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $exePath
    $info.Arguments = $Arguments
    $info.WorkingDirectory = $testRoot
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.EnvironmentVariables['CODESCAPE_DATA_DIR'] = $testRoot
    $info.EnvironmentVariables['PATH'] = Join-Path $env:SystemRoot 'System32'
    $info.EnvironmentVariables.Remove('PYTHONPATH')
    $info.EnvironmentVariables.Remove('PYTHONHOME')
    return [Diagnostics.Process]::Start($info)
}
function Api([string]$Method, [string]$Path, $Body = $null) {
    $request = [Net.HttpWebRequest]::Create($script:baseUrl + $Path)
    $request.Method = $Method
    $request.Proxy = $null
    $request.Timeout = 30000
    $request.CookieContainer = $script:cookies
    if ($script:csrf) { $request.Headers['x-csrf-token'] = $script:csrf }
    if ($null -ne $Body) {
        $bytes = $utf8.GetBytes(($Body | ConvertTo-Json -Depth 20 -Compress))
        $request.ContentType = 'application/json'
        $request.ContentLength = $bytes.Length
        $stream = $request.GetRequestStream()
        try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
    }
    try {
        $response = $request.GetResponse()
        $reader = New-Object IO.StreamReader($response.GetResponseStream(), $utf8)
        try { return ($reader.ReadToEnd() | ConvertFrom-Json).data }
        finally { $reader.Dispose(); $response.Dispose() }
    } catch { throw '本地API请求未成功；不输出认证信息或原始响应。' }
}
function Connect-OwnedProcess {
    $launchPath = Join-Path $testRoot 'run/launch.json'
    $script:cookies = New-Object Net.CookieContainer
    $script:csrf = $null
    for ($step = 0; $step -lt 600; $step++) {
        Require (-not $ownedProcess.HasExited) '独立程序启动失败。'
        if (Test-Path -LiteralPath $launchPath) {
            try {
                $launch = Get-Content -LiteralPath $launchPath -Raw -Encoding UTF8 | ConvertFrom-Json
                $address = [Uri]$launch.url
                Require ($address.Scheme -eq 'http' -and $address.Host -eq '127.0.0.1') '启动地址不是本机回环服务。'
                $script:baseUrl = $address.GetLeftPart([UriPartial]::Authority)
                $session = Api 'POST' '/api/v1/session/exchange' @{ nonce = $address.Fragment.Substring(7) }
                $script:csrf = $session.csrf_token
                return
            } catch { }
        }
        Start-Sleep -Milliseconds 100
    }
    throw '独立程序启动超时。'
}
function Stop-OwnedProcess {
    $stopper = Start-OwnedProcess '--prepare-update --no-browser'
    Require ($stopper.WaitForExit(90000)) '正常保存退出超时。'
    Require ($stopper.ExitCode -eq 0) '数据保存与退出未通过。'
    Require ($ownedProcess.WaitForExit(10000) -and $ownedProcess.ExitCode -eq 0) '独立服务没有正常退出。'
}
try {
    $manifest = Get-Content -LiteralPath (Join-Path $bundleRoot 'SHA256-files.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($entry in $manifest.PSObject.Properties) {
        $file = [IO.Path]::GetFullPath((Join-Path $bundleRoot $entry.Name))
        Require ($file.StartsWith($bundlePrefix, [StringComparison]::OrdinalIgnoreCase)) '制品清单路径超出程序目录。'
        Require ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -eq $entry.Value) '制品文件摘要不匹配。'
    }
    $build = Get-Content -LiteralPath (Join-Path $bundleRoot '_internal/build-info.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $report['构建号'] = $build.build_id
    $report['版本'] = $build.version
    $report['未签名预发布'] = -not $build.signed
    $source = Join-Path $testRoot '只读源码样例'
    [void][IO.Directory]::CreateDirectory($source)
    $files = @{
        'owned.py' = "raise RuntimeError('仅供静态读取，不得执行')`ndef target(): return 1`ndef caller(): return target()`n"
        'owned.ts' = "throw new Error('仅供静态读取，不得执行');`nexport function tsTarget(){return 1;}`nexport function tsCaller(){return tsTarget();}`n"
        'owned.c' = "int cTarget(void){return 1;}`nint cCaller(void){return cTarget();}`n"
        'owned.cpp' = "int cppTarget(){return 1;}`nint cppCaller(){return cppTarget();}`n"
    }
    foreach ($name in $files.Keys) { [IO.File]::WriteAllText((Join-Path $source $name), $files[$name], $utf8) }
    $phase = '独立启动与静态导入'
    $ownedProcess = Start-OwnedProcess '--no-browser'
    Connect-OwnedProcess
    $samples = @(Api 'GET' '/api/v1/projects')
    Require ($samples.Count -eq 1 -and $samples[0].demo) '预置样例标识或项目库初态不符。'
    $project = Api 'POST' '/api/v1/imports' @{ path = $source }
    $projectUrl = '/api/v1/projects/' + $project.id
    for ($step = 0; $step -lt 1200; $step++) {
        $project = Api 'GET' $projectUrl
        if ($project.status -ne 'indexing') { break }
        Start-Sleep -Milliseconds 100
    }
    Require ($project.status -eq 'indexed' -and $project.function_count -eq 8) '四语言静态索引未通过。'
    Require (-not $project.has_architecture -and -not $project.upload_consent.accepted) '未授权静态索引被错误标为AI架构。'
    $languages = @($project.analysis.calls | Where-Object { $_.resolution -eq 'confirmed' } | ForEach-Object {
        $targetId = $_.target_id
        $project.analysis.entities | Where-Object { $_.id -eq $targetId } | ForEach-Object { $_.language }
    } | Sort-Object -Unique)
    Require (($languages -join ',') -eq 'c,cpp,python,typescript') '四语言调用确认未通过。'
    foreach ($name in $files.Keys) {
        Require ([IO.File]::ReadAllText((Join-Path $source $name), $utf8) -eq $files[$name]) '样例源码被修改。'
    }
    $demoUrl = '/api/v1/projects/' + $samples[0].id
    $demo = Api 'GET' $demoUrl
    $entity = @($demo.analysis.entities | Where-Object { $_.visible })[0]
    $null = Api 'PUT' ($demoUrl + '/learning/' + $entity.id) @{ version_id = $demo.version_id; state = 'mastered'; expected_revision = 0 }
    $phase = '正常退出与重开读取'
    Stop-OwnedProcess
    $ownedProcess = Start-OwnedProcess '--no-browser'
    Connect-OwnedProcess
    $restored = Api 'GET' $demoUrl
    Require ($restored.progress.mastered -eq 1) '重开后学习标记未保留。'
    $saved = Api 'GET' $projectUrl
    $fileId = $saved.file_ids.'owned.py'
    $text = Api 'GET' ($projectUrl + '/files/' + $fileId + '?version_id=' + $saved.version_id)
    Require ($text.text -eq $files['owned.py']) '重开后源码快照读取未通过。'
    $usage = Api 'GET' '/api/v1/usage'
    Require (@($usage.records).Count -eq 0) '普通读取出现模型请求。'
    Stop-OwnedProcess
    $report['状态'] = '自动步骤通过，待人工界面与环境验收'
    $report['静态函数数'] = 8
    $report['确认调用语言'] = $languages
    $report['学习与源码重开读取'] = '通过'
    $report['开发工具PATH'] = '已从测试子进程移除；未改变主系统环境'
    $report['待人工验证'] = @('镜像来源与干净环境证明', '安装升级卸载', '真实系统断网重开', '真实休眠与主动恢复', '图形可读性和项目用途说明')
} catch {
    $report['状态'] = '未通过'
    $report['失败阶段'] = $phase
    Write-Host ('验收未通过，阶段：' + $phase)
} finally {
    if ($null -ne $ownedProcess -and -not $ownedProcess.HasExited) {
        try { Stop-OwnedProcess } catch { $ownedProcess.Kill(); [void]$ownedProcess.WaitForExit(10000) }
    }
    [IO.File]::WriteAllText($reportPath, ($report | ConvertTo-Json -Depth 20), $utf8)
    Write-Host ('验收记录已保存：' + $reportPath)
}
if ($report['状态'] -eq '未通过') { exit 1 }
