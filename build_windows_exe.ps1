[CmdletBinding()]
param(
    [string]$Python = 'F:\Doubao\job-radar-coach\.venv\Scripts\python.exe',
    [string]$PackageName = 'AgentCoachDesktop',
    [switch]$InstallDesktopDeps,
    [switch]$SkipZip
)

$ErrorActionPreference = 'Stop'

$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot '.')).Path
$BuildRoot = Join-Path $ProjectDir 'build\windows-exe'
$WorkPath = Join-Path $BuildRoot 'pyinstaller-work'
$SpecPath = Join-Path $BuildRoot 'spec'
$PyInstallerConfig = Join-Path $BuildRoot 'cache'
$DistPath = Join-Path $ProjectDir 'dist'
$TempPath = Join-Path $BuildRoot 'tmp'
$OutputDir = Join-Path $DistPath $PackageName
$ZipPath = Join-Path $DistPath "$PackageName-windows.zip"
$DesktopDepsPath = Join-Path $BuildRoot 'desktop-deps'

if ($PackageName -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') {
    throw "PackageName must be a simple filename stem: $PackageName"
}

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Python interpreter not found: $Python"
}

# PyInstaller and Python may otherwise use the small C: temp/cache volume.
# These are process-local changes and do not modify the user's virtualenv.
New-Item -ItemType Directory -Force -Path $WorkPath, $SpecPath, $PyInstallerConfig, $DistPath, $TempPath, $DesktopDepsPath | Out-Null
$env:TEMP = $TempPath
$env:TMP = $TempPath
$env:PIP_CACHE_DIR = Join-Path $BuildRoot 'pip-cache'
$env:PYINSTALLER_CONFIG_DIR = $PyInstallerConfig
$env:PYTHONPATH = "$DesktopDepsPath;$($ProjectDir)\src"

if ($InstallDesktopDeps) {
    # Keep all new wheels in the project build directory.  The selected
    # interpreter is used only as a host for PyInstaller; its site-packages is
    # never modified.  Clear the machine's inherited PIP_NO_INDEX setting so a
    # previously configured offline mirror cannot silently block this explicit
    # opt-in installation.
    $OriginalNoIndex = $env:PIP_NO_INDEX
    $OriginalIndexUrl = $env:PIP_INDEX_URL
    $env:PIP_NO_INDEX = $null
    $env:PIP_INDEX_URL = 'https://pypi.org/simple'
    & $Python -m pip install --disable-pip-version-check --no-cache-dir --upgrade --target $DesktopDepsPath --index-url 'https://pypi.org/simple' 'pywebview>=6.2.1,<7' 'pythonnet>=3.1,<4'
    if ($LASTEXITCODE -ne 0) { throw "Desktop dependency installation failed with exit code $LASTEXITCODE" }
    $env:PIP_NO_INDEX = $OriginalNoIndex
    $env:PIP_INDEX_URL = $OriginalIndexUrl
}

& $Python -c 'import webview, clr; print("pywebview " + str(getattr(webview, "__version__", "unknown")) + "; pythonnet loaded")'
if ($LASTEXITCODE -ne 0) {
    throw "pywebview/pythonnet are missing from $DesktopDepsPath. Re-run with -InstallDesktopDeps after network access is approved."
}

& $Python -c 'import PyInstaller; print("PyInstaller " + PyInstaller.__version__)'
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller is unavailable in the selected interpreter.' }

if (Test-Path -LiteralPath $OutputDir) {
    # Rebuilds must never erase a user's work area or provider credentials.
    # Refuse the rebuild when this output folder has already been used.
    $ProtectedPaths = @(
        (Join-Path $OutputDir 'work'),
        (Join-Path $OutputDir '.agent-coach'),
        (Join-Path $OutputDir 'config\providers.yaml')
    )
    $ExistingProtected = @($ProtectedPaths | Where-Object { Test-Path -LiteralPath $_ })
    if ($ExistingProtected.Count -gt 0) {
        throw "Refusing to replace $OutputDir because it contains user data: $($ExistingProtected -join ', '). Move/copy that data first, then rebuild."
    }

    $ResolvedDist = (Resolve-Path -LiteralPath $DistPath).Path.TrimEnd('\')
    $ResolvedOutput = (Resolve-Path -LiteralPath $OutputDir).Path.TrimEnd('\')
    $DistPrefix = "$ResolvedDist\"
    if (-not $ResolvedOutput.StartsWith($DistPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to delete output outside dist directory: $ResolvedOutput"
    }
    Remove-Item -LiteralPath $ResolvedOutput -Recurse -Force
}

$Args = @(
    '-m', 'PyInstaller',
    '--noconfirm',
    '--clean',
    '--onedir',
    '--windowed',
    '--name', $PackageName,
    '--distpath', $DistPath,
    '--workpath', $WorkPath,
    '--specpath', $SpecPath,
    '--paths', (Join-Path $ProjectDir 'src'),
    '--add-data', ((Join-Path $ProjectDir 'src\agent_coach\web_static') + ';agent_coach/web_static'),
    '--add-data', ((Join-Path $ProjectDir 'src\agent_coach\skills') + ';agent_coach/skills'),
    '--add-data', ((Join-Path $ProjectDir 'src\agent_coach\resources') + ';agent_coach/resources'),
    '--hidden-import', 'agent_coach.job_radar',
    '--hidden-import', 'agent_coach.skills',
    '--hidden-import', 'agent_coach.legacy.core.llm.openai_compat',
    '--hidden-import', 'agent_coach.legacy.core.llm.anthropic_adapter',
    '--collect-submodules', 'agent_coach.legacy',
    '--hidden-import', 'webview',
    '--hidden-import', 'webview.platforms.edgechromium',
    '--hidden-import', 'webview.platforms.winforms',
    '--hidden-import', 'clr',
    '--collect-all', 'webview',
    '--collect-all', 'pythonnet',
    '--collect-all', 'clr_loader',
    (Join-Path $ProjectDir 'packaged_launcher.py')
)

Write-Host "Building $PackageName in $OutputDir ..."
& $Python @Args
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

$ExePath = Join-Path $OutputDir "$PackageName.exe"
if (-not (Test-Path -LiteralPath $ExePath -PathType Leaf)) {
    throw "Build completed without expected executable: $ExePath"
}

# Ship a small, key-free configuration template beside the executable.  The
# launcher only activates it when the user copies it to providers.yaml.
$ConfigDir = Join-Path $OutputDir 'config'
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
Copy-Item -LiteralPath (Join-Path $ProjectDir 'config\providers.example.yaml') -Destination (Join-Path $ConfigDir 'providers.example.yaml') -Force

if (-not $SkipZip) {
    if (Test-Path -LiteralPath $ZipPath) {
        Remove-Item -LiteralPath $ZipPath -Force
    }
    Compress-Archive -Path (Join-Path $OutputDir '*') -DestinationPath $ZipPath -CompressionLevel Optimal
}

$exeInfo = Get-Item -LiteralPath $ExePath
Write-Host "EXE: $($exeInfo.FullName) ($($exeInfo.Length) bytes)"
if (-not $SkipZip) {
    $zipInfo = Get-Item -LiteralPath $ZipPath
    Write-Host "ZIP: $($zipInfo.FullName) ($($zipInfo.Length) bytes)"
}
Write-Host 'Smoke command: .\AgentCoachDesktop.exe --headless --port 0'
