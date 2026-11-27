[CmdletBinding()]
param(
    [string]$Python = 'F:\Doubao\job-radar-coach\.venv\Scripts\python.exe',
    [string]$PackageName = 'JobCoachDesktop',
    [string]$ExecutableName = '求职教练.exe',
    [switch]$InstallDesktopDeps,
    [switch]$SkipZip,
    [string]$SpeechModelDir,
    [string]$SpeechDepsPath
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

$SpeechOptionSpecified = $PSBoundParameters.ContainsKey('SpeechModelDir') -or $PSBoundParameters.ContainsKey('SpeechDepsPath')
$SpeechDefaultModelDir = Join-Path $ProjectDir 'build\offline-speech\model-base'
$SpeechDefaultDepsPath = Join-Path $ProjectDir 'build\offline-speech\pip-target'
$SpeechModelPath = if ($SpeechOptionSpecified) { $SpeechModelDir } else { $SpeechDefaultModelDir }
$SpeechDepsPathResolved = if ($SpeechOptionSpecified) { $SpeechDepsPath } else { $SpeechDefaultDepsPath }

function Get-SpeechBundleProblem {
    param(
        [Parameter(Mandatory = $true)][string]$ModelPath,
        [Parameter(Mandatory = $true)][string]$DepsPath
    )

    if (-not (Test-Path -LiteralPath $ModelPath -PathType Container)) {
        return "model directory not found: $ModelPath"
    }
    if (-not (Test-Path -LiteralPath $DepsPath -PathType Container)) {
        return "runtime directory not found: $DepsPath"
    }

    foreach ($modelFile in @('config.json', 'tokenizer.json')) {
        if (-not (Test-Path -LiteralPath (Join-Path $ModelPath $modelFile) -PathType Leaf)) {
            return "model file missing: $modelFile"
        }
    }
    if (-not ((Test-Path -LiteralPath (Join-Path $ModelPath 'model.bin') -PathType Leaf) -or
              (Test-Path -LiteralPath (Join-Path $ModelPath 'model.safetensors') -PathType Leaf))) {
        return 'model weights missing: model.bin or model.safetensors'
    }

    foreach ($packageDir in @('faster_whisper', 'ctranslate2', 'av', 'tokenizers', 'onnxruntime', 'numpy', 'huggingface_hub')) {
        if (-not (Test-Path -LiteralPath (Join-Path $DepsPath $packageDir) -PathType Container)) {
            return "runtime package missing: $packageDir"
        }
    }
    return $null
}

if ($SpeechOptionSpecified -and ([string]::IsNullOrWhiteSpace($SpeechModelPath) -or [string]::IsNullOrWhiteSpace($SpeechDepsPathResolved))) {
    throw 'SpeechModelDir and SpeechDepsPath must be supplied together when enabling offline speech.'
}

$SpeechEnabled = $false
if ($SpeechOptionSpecified) {
    $SpeechBundleProblem = Get-SpeechBundleProblem -ModelPath $SpeechModelPath -DepsPath $SpeechDepsPathResolved
    if ($null -ne $SpeechBundleProblem) {
        throw "Offline speech bundle validation failed: $SpeechBundleProblem"
    }
    $SpeechModelPath = (Resolve-Path -LiteralPath $SpeechModelPath).Path
    $SpeechDepsPathResolved = (Resolve-Path -LiteralPath $SpeechDepsPathResolved).Path
    $SpeechEnabled = $true
} elseif ((Test-Path -LiteralPath $SpeechModelPath -PathType Container) -and
          (Test-Path -LiteralPath $SpeechDepsPathResolved -PathType Container)) {
    $SpeechBundleProblem = Get-SpeechBundleProblem -ModelPath $SpeechModelPath -DepsPath $SpeechDepsPathResolved
    if ($null -eq $SpeechBundleProblem) {
        $SpeechModelPath = (Resolve-Path -LiteralPath $SpeechModelPath).Path
        $SpeechDepsPathResolved = (Resolve-Path -LiteralPath $SpeechDepsPathResolved).Path
        $SpeechEnabled = $true
    } else {
        Write-Warning "Offline speech bundle is incomplete; continuing with the legacy non-speech build: $SpeechBundleProblem"
    }
} else {
    Write-Host 'Offline speech bundle not found; continuing with the legacy non-speech build.'
}

if ($PackageName -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') {
    throw "PackageName must be a simple filename stem: $PackageName"
}
if ($ExecutableName -notmatch '^[^\\/:*?"<>|]+\.exe$') {
    throw "ExecutableName must be a safe .exe filename: $ExecutableName"
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
$PythonPathEntries = @($DesktopDepsPath, (Join-Path $ProjectDir 'src'))
if ($SpeechEnabled) {
    $PythonPathEntries = @($SpeechDepsPathResolved) + $PythonPathEntries
}
$env:PYTHONPATH = ($PythonPathEntries -join ';')

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
    '--exclude-module', 'torch'
)

if ($SpeechEnabled) {
    $Args += @(
        '--paths', $SpeechDepsPathResolved,
        '--add-data', ($SpeechModelPath + ';models/faster-whisper-base'),
        '--hidden-import', 'numpy',
        '--collect-all', 'faster_whisper',
        '--collect-all', 'ctranslate2',
        '--collect-all', 'av',
        '--collect-all', 'tokenizers',
        '--collect-all', 'onnxruntime',
        '--collect-all', 'huggingface_hub'
    )
    # The packaged Windows path has a native NLS fallback, but collect
    # OpenCC when the speech target contains it so the same source remains
    # fully script-normalizing on other platforms and in source probes.
    if (Test-Path -LiteralPath (Join-Path $SpeechDepsPathResolved 'opencc') -PathType Container) {
        $Args += @('--collect-all', 'opencc')
    }
}
$Args += (Join-Path $ProjectDir 'packaged_launcher.py')

Write-Host "Building $PackageName in $OutputDir ..."
& $Python @Args
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

$BuiltExePath = Join-Path $OutputDir "$PackageName.exe"
if (-not (Test-Path -LiteralPath $BuiltExePath -PathType Leaf)) {
    throw "Build completed without expected executable: $BuiltExePath"
}
$ExePath = Join-Path $OutputDir $ExecutableName
if ($BuiltExePath -ne $ExePath) {
    if (Test-Path -LiteralPath $ExePath) {
        throw "Refusing to replace existing executable: $ExePath"
    }
    Move-Item -LiteralPath $BuiltExePath -Destination $ExePath
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
Write-Host "Smoke command: .\$ExecutableName --headless --port 0"
