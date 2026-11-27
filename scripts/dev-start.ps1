$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'
$RunDir = Join-Path $Root '.dev-run'
$VenvDir = Join-Path $Backend 'venv'
$VenvPy = Join-Path $VenvDir 'Scripts\python.exe'
$HealthUrl = 'http://127.0.0.1:8000/api/v1/health'
$PidFile = Join-Path $RunDir 'backend.pid'
$LogFile = Join-Path $RunDir 'backend.log'
$ErrLogFile = Join-Path $RunDir 'backend.err.log'

New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

function Write-Info([string]$Message) {
    Write-Host "[dev-start] $Message" -ForegroundColor Cyan
}

if (-not (Test-Path $VenvPy)) {
    Write-Info '创建后端虚拟环境 backend\venv ...'
    python -m venv $VenvDir
}

Write-Info '安装后端依赖 ...'
& $VenvPy -m pip install -q -r (Join-Path $Backend 'requirements.txt') `
    -i https://mirrors.aliyun.com/pypi/simple/

$EnvFile = Join-Path $Backend '.env'
if (-not (Test-Path $EnvFile)) {
    Write-Info '未找到 backend\.env，写入 SQLite 兜底配置（改用本机 MySQL 请自行修改）...'
    $envContent = @(
        'APP_ENV=dev',
        'DEBUG=true',
        'DATABASE_URL=sqlite:///./dev.db',
        'JWT_SECRET_KEY=dev-only-change-me',
        'LLM_API_KEY=',
        'LLM_BASE_URL=https://api.deepseek.com/v1',
        'LLM_MODEL=deepseek-chat'
    ) -join "`r`n"
    [System.IO.File]::WriteAllText($EnvFile, $envContent, [System.Text.Encoding]::ASCII)
}

Write-Info '执行数据库迁移（alembic upgrade head）...'
Push-Location $Backend
try {
    & $VenvPy -m alembic upgrade head
}
finally {
    Pop-Location
}

Write-Info '初始化管理员账号（幂等；随机密码仅在首次创建时打印）...'
Push-Location $Backend
try {
    & $VenvPy scripts/bootstrap_admin.py
    if ($LASTEXITCODE -ne 0) {
        Write-Warning '管理员初始化失败（不影响服务启动），可稍后手动执行 backend\scripts\bootstrap_admin.py'
    }
}
catch {
    Write-Warning '管理员初始化失败（不影响服务启动），可稍后手动执行 backend\scripts\bootstrap_admin.py'
}
finally {
    Pop-Location
}

if (Test-Path $PidFile) {
    $oldPid = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
        Write-Info "停止上次残留的后端进程 $oldPid"
        Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $PidFile -ErrorAction SilentlyContinue
}

Write-Info '后台启动后端 uvicorn ...'
$backendProcess = Start-Process -FilePath $VenvPy `
    -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000' `
    -WorkingDirectory $Backend `
    -RedirectStandardOutput $LogFile `
    -RedirectStandardError $ErrLogFile `
    -PassThru -WindowStyle Hidden
$backendProcess.Id | Set-Content $PidFile

try {
    Write-Info "等待后端健康检查 $HealthUrl ..."
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        try {
            $response = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -eq 200) {
                $ready = $true
                break
            }
        }
        catch {
        }
        if (-not (Get-Process -Id $backendProcess.Id -ErrorAction SilentlyContinue)) {
            Get-Content $ErrLogFile -Tail 30 -ErrorAction SilentlyContinue | Write-Host
            Get-Content $LogFile -Tail 30 -ErrorAction SilentlyContinue | Write-Host
            throw '后端启动失败，请查看 .dev-run\backend.log'
        }
    }
    if (-not $ready) {
        Get-Content $ErrLogFile -Tail 30 -ErrorAction SilentlyContinue | Write-Host
        Get-Content $LogFile -Tail 30 -ErrorAction SilentlyContinue | Write-Host
        throw '后端 30 秒内未通过健康检查'
    }
    Write-Info "后端已就绪：$HealthUrl"

    Write-Info '安装前端依赖 ...'
    Push-Location $Frontend
    try {
        & npm install --no-audit --no-fund --registry=https://registry.npmmirror.com
    }
    finally {
        Pop-Location
    }

    Write-Info '启动前端 Vite 开发服务器：http://127.0.0.1:5173（Ctrl+C 同时停止前后端）'
    Push-Location $Frontend
    try {
        & npm run dev
    }
    finally {
        Pop-Location
    }
}
finally {
    if (Get-Process -Id $backendProcess.Id -ErrorAction SilentlyContinue) {
        Write-Info '停止后端进程 ...'
        Stop-Process -Id $backendProcess.Id -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $PidFile -ErrorAction SilentlyContinue
}