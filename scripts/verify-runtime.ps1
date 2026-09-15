<#
只读核对 AI 知识中台的 Compose 运行态、数据库迁移和当前 OpenAPI。
脚本通过 cmd.exe 调用 Docker，规避部分 Windows PowerShell 将 docker 解析为文件关联的环境问题。
#>

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$expectedAlembicHead = '0013_idempotency_records'
Push-Location $projectRoot
try {
    # 先确认 Compose 服务仍在运行；该命令只读取状态，不修改容器或数据卷。
    Write-Host '--- Compose 状态 ---'
    & cmd.exe /d /c 'docker compose ps'
    if ($LASTEXITCODE -ne 0) { throw '无法读取 Docker Compose 状态' }

    # 健康与就绪分别证明进程存活和基础依赖可用，避免只看容器 Up 状态。
    foreach ($endpoint in @('health', 'ready')) {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8000/$endpoint"
        Write-Host "$endpoint HTTP $($response.StatusCode): $($response.Content)"
        if ($response.StatusCode -ne 200) { throw "$endpoint 检查失败" }
    }

    # API 启动时会执行迁移；读取容器内 head，确认数据库结构和镜像代码处于同一代。
    Write-Host '--- Alembic 当前版本 ---'
    # Alembic 将正常 INFO 日志写入 stderr；PowerShell 会把原生命令的 stderr 包装成错误记录，
    # 即使退出码为零也会显示红字。直接捕获子进程的两个流，再仅以退出码判定真正失败。
    $processInfo = New-Object System.Diagnostics.ProcessStartInfo
    $processInfo.FileName = 'cmd.exe'
    $processInfo.Arguments = '/d /c "docker compose exec -T api python -m alembic current"'
    $processInfo.UseShellExecute = $false
    $processInfo.CreateNoWindow = $true
    $processInfo.RedirectStandardOutput = $true
    $processInfo.RedirectStandardError = $true
    $migrationProcess = New-Object System.Diagnostics.Process
    $migrationProcess.StartInfo = $processInfo
    if (-not $migrationProcess.Start()) { throw '无法启动容器内 Alembic 版本检查' }
    $standardOutputTask = $migrationProcess.StandardOutput.ReadToEndAsync()
    $standardErrorTask = $migrationProcess.StandardError.ReadToEndAsync()
    $migrationProcess.WaitForExit()
    $migrationExitCode = $migrationProcess.ExitCode
    $migrationOutput = @(
        $standardOutputTask.GetAwaiter().GetResult(),
        $standardErrorTask.GetAwaiter().GetResult()
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    $migrationOutput = ($migrationOutput -join [Environment]::NewLine).Trim()
    $migrationProcess.Dispose()
    Write-Host $migrationOutput.Trim()
    if ($migrationExitCode -ne 0) { throw '无法读取容器内 Alembic 版本' }
    if ($migrationOutput -notmatch [regex]::Escape($expectedAlembicHead)) {
        throw "运行镜像迁移版本不是 $expectedAlembicHead，请先重新构建 API 镜像"
    }
    Write-Host "迁移版本已确认：$expectedAlembicHead"

    $spec = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/openapi.json'
    $requiredPaths = @(
        '/control/v1/applications/{application_id}/environments/{environment_id}/operations/ingestion-attempts',
        '/control/v1/applications/{application_id}/environments/{environment_id}/documents/{document_id}/versions/{revision_id}/rollback'
    )
    foreach ($path in $requiredPaths) {
        if (-not $spec.paths.PSObject.Properties.Name.Contains($path)) {
            throw "运行时 OpenAPI 缺少路径：$path"
        }
        Write-Host "OpenAPI 已加载：$path"
    }

    # 幂等键必须出现在所有异步知识写入接口上，避免镜像只更新了部分路由。
    $idempotencyPaths = @(
        '/control/v1/applications/{application_id}/environments/{environment_id}/collections/{collection_id}/documents/upload',
        '/control/v1/applications/{application_id}/environments/{environment_id}/collections/{collection_id}/documents/text',
        '/control/v1/applications/{application_id}/environments/{environment_id}/collections/{collection_id}/documents/remote',
        '/control/v1/applications/{application_id}/environments/{environment_id}/ingestion-runs/{run_id}/retry',
        '/control/v1/applications/{application_id}/environments/{environment_id}/documents/{document_id}/versions/text',
        '/control/v1/applications/{application_id}/environments/{environment_id}/documents/{document_id}/versions/upload',
        '/control/v1/applications/{application_id}/environments/{environment_id}/documents/{document_id}/refresh'
    )
    foreach ($path in $idempotencyPaths) {
        if (-not $spec.paths.PSObject.Properties.Name.Contains($path)) {
            throw "运行时 OpenAPI 缺少幂等接口：$path"
        }
        $operations = $spec.paths.$path.PSObject.Properties.Value
        $hasHeader = $false
        foreach ($operation in $operations) {
            if ($null -ne $operation.parameters -and ($operation.parameters | Where-Object { $_.name -eq 'Idempotency-Key' -and $_.in -eq 'header' })) {
                $hasHeader = $true
                break
            }
        }
        if (-not $hasHeader) { throw "运行时 OpenAPI 缺少 Idempotency-Key：$path" }
        Write-Host "幂等接口已加载：$path"
    }

    $console = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:5173/'
    Write-Host "console HTTP $($console.StatusCode)"
    if ($console.StatusCode -ne 200) { throw '控制台首页检查失败' }
    Write-Host '运行时验收通过。'
}
finally {
    Pop-Location
}
