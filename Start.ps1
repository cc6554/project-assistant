param([switch]$PrepareOnly)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-StreamSha256 {
    param([IO.Stream]$Stream)

    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        [BitConverter]::ToString($algorithm.ComputeHash($Stream)).Replace('-', '')
    }
    finally {
        $algorithm.Dispose()
    }
}

function Get-ApplicationExecutable {
    param([string]$Directory)

    $requiredFiles = @(
        '简历项目助手.exe'
        '_internal\models\faster-whisper-base\model.bin'
        '_internal\faster_whisper\assets\silero_vad_v6.onnx'
    )
    foreach ($file in $requiredFiles) {
        if (-not [IO.File]::Exists((Join-Path $Directory $file))) {
            throw "应用目录缺少文件：$file。请将发布包重新解压到一个新目录后启动。"
        }
    }
    Join-Path $Directory $requiredFiles[0]
}

function Restore-Application {
    param([string]$Destination)

    $manifestPath = Join-Path $PSScriptRoot 'checksums.json'
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $stagingPath = Join-Path $PSScriptRoot ('.prepare-' + [Guid]::NewGuid().ToString('N').Substring(0, 12))
    $archivePath = Join-Path $stagingPath 'app.zip'
    $null = New-Item -ItemType Directory -Path $stagingPath

    try {
        Write-Host '正在校验和合并发布分卷…'
        $archive = [IO.File]::Open($archivePath, 'CreateNew', 'Write', 'None')
        try {
            foreach ($part in $manifest.parts) {
                $partPath = Join-Path $PSScriptRoot $part.file
                if (-not [IO.File]::Exists($partPath)) {
                    throw "找不到分卷 $($part.file)。请完整解压下载的发布包。"
                }
                $source = [IO.File]::Open($partPath, 'Open', 'Read', 'Read')
                try {
                    if ($source.Length -ne $part.bytes -or
                        (Get-StreamSha256 -Stream $source) -ne $part.sha256) {
                        throw "分卷 $($part.file) 校验失败。请重新下载并完整解压发布包。"
                    }
                    $source.Position = 0
                    $source.CopyTo($archive)
                }
                finally {
                    $source.Dispose()
                }
            }
            if ($archive.Length -ne $manifest.archive.bytes) {
                throw '合并后的发布包大小不一致。请重新下载并完整解压发布包。'
            }
        }
        finally {
            $archive.Dispose()
        }

        $combinedArchive = [IO.File]::OpenRead($archivePath)
        try {
            if ((Get-StreamSha256 -Stream $combinedArchive) -ne $manifest.archive.sha256) {
                throw '合并后的发布包校验失败。请重新下载并完整解压发布包。'
            }
        }
        finally {
            $combinedArchive.Dispose()
        }

        Write-Host '正在解压应用和离线语音模型…'
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [IO.Compression.ZipFile]::ExtractToDirectory($archivePath, $stagingPath)
        $restoredPath = Join-Path $stagingPath 'ResumeProjectAssistantDesktopV13'
        $null = Get-ApplicationExecutable -Directory $restoredPath
        try {
            [IO.Directory]::Move($restoredPath, $Destination)
        }
        catch [IO.IOException] {
            if (-not [IO.Directory]::Exists($Destination)) {
                throw
            }
            # A simultaneous launch may have already published the application.
            $null = Get-ApplicationExecutable -Directory $Destination
        }
    }
    finally {
        try {
            [IO.File]::Delete($archivePath)
            [IO.Directory]::Delete($stagingPath)
        }
        catch {
            Write-Host "临时目录已保留：$stagingPath"
        }
    }
}

$appPath = Join-Path $PSScriptRoot 'app'
try {
    if (-not (Test-Path -LiteralPath $appPath)) {
        Restore-Application -Destination $appPath
    }
    $executable = Get-ApplicationExecutable -Directory $appPath
}
catch {
    Write-Host "无法准备应用。$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

if ($PrepareOnly) {
    Write-Host "应用已就绪：$executable"
    exit 0
}

try {
    $application = Start-Process -FilePath $executable -WorkingDirectory $appPath -PassThru
    if ($application.WaitForExit(5000) -and $application.ExitCode -ne 0) {
        throw "应用退出代码：$($application.ExitCode)。"
    }
}
catch {
    Write-Host "启动失败。$($_.Exception.Message) 请确认系统已安装 Microsoft Edge WebView2 Runtime。" -ForegroundColor Red
    exit 1
}
