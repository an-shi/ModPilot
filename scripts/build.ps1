param(
    [switch]$SkipTests,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {
    # Older hosts may not allow changing the output encoding.
}
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    $python = Join-Path $root ".venv\Scripts\python.exe"
    if (-not (Test-Path $python)) {
        throw "未找到 .venv。请先运行: py -3.12 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e .[dev]"
    }

    if (-not $SkipTests) {
        Write-Host "==> 运行测试"
        & $python -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw "测试失败，构建中止。" }
    }

    Write-Host "==> 生成图标"
    & $python scripts\make_icon.py
    if ($LASTEXITCODE -ne 0) { throw "图标生成失败。" }

    Write-Host "==> PyInstaller 构建"
    & $python -m PyInstaller --clean --noconfirm modpilot.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败。" }

    $exe = Join-Path $root "dist\ModPilot\ModPilot.exe"
    if (-not (Test-Path $exe)) { throw "构建产物不存在: $exe" }
    Write-Host "==> 可执行程序: $exe"

    if (-not $SkipInstaller) {
        $isccCandidates = @(
            "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
            "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
            "C:\Program Files\Inno Setup 6\ISCC.exe"
        )
        $iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($iscc) {
            Write-Host "==> 构建安装包"
            & $iscc "installer\modpilot.iss"
            if ($LASTEXITCODE -ne 0) { throw "Inno Setup 构建失败。" }
            Write-Host "==> 安装包位于 installer\Output"
        } else {
            Write-Warning "未找到 Inno Setup 6 (ISCC.exe)，已跳过安装包。安装后可运行 scripts\build.ps1 生成。"
        }
    }
} finally {
    Pop-Location
}
