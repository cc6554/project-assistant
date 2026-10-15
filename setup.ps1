param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskEnvPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskEnvPython)) {
    & $Python -m venv (Join-Path $taskRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create the local Python environment.' }
}
& $taskEnvPython -m pip install --no-cache-dir -e "$taskRoot[dev]"
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
Write-Host "Ready. Run: & '$taskEnvPython' '$taskRoot\run.py' --workspace '$taskRoot\work'"
