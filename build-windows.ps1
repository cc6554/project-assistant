$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
# Rust may be installed under CARGO_HOME without its bin directory in PATH.
# Update only this build process; do not change the user's saved environment.
$cargoCommand = Get-Command cargo.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $cargoCommand) {
    $cargoRoots = @()
    if ($env:CARGO_HOME) { $cargoRoots += $env:CARGO_HOME }
    if ($env:USERPROFILE) { $cargoRoots += (Join-Path $env:USERPROFILE '.cargo') }
    foreach ($cargoRoot in $cargoRoots) {
        $cargoBin = Join-Path $cargoRoot 'bin'
        if (Test-Path -LiteralPath (Join-Path $cargoBin 'cargo.exe') -PathType Leaf) {
            $env:Path = $cargoBin + [IO.Path]::PathSeparator + $env:Path
            $cargoCommand = Get-Command cargo.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1
            break
        }
    }
}
if (-not $cargoCommand) {
    throw '找不到 cargo.exe。请安装 Rust，或将 CARGO_HOME 指向已有 Cargo 安装目录（包含 bin/cargo.exe）。'
}
$cargoExecutable = $cargoCommand.Source
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw '请先创建 .venv 并安装 requirements.txt 和 pyinstaller。'
}
& $python -m PyInstaller --noconfirm --distpath build/sidecar-dist --workpath build/pyinstaller src-tauri/job-radar-sidecar.spec
if ($LASTEXITCODE -ne 0) { throw 'Sidecar 构建失败' }
& npm.cmd ci
if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败' }
& npm.cmd run build
if ($LASTEXITCODE -ne 0) { throw '前端构建失败' }
& $cargoExecutable build --release --manifest-path src-tauri/Cargo.toml
if ($LASTEXITCODE -ne 0) { throw '桌面壳构建失败' }
& $python scripts/assemble_release.py --windows
if ($LASTEXITCODE -ne 0) { throw '分发目录组装失败' }
