# PyInstaller 打包 Tauri sidecar（Python FastAPI 服务 → job-radar-sidecar.exe）
# 用法：pyinstaller src-tauri/job-radar-sidecar.spec
# 产物：dist/job-radar-sidecar/（onedir 目录：exe + _internal/）
# onedir 模式启动只有 1 个进程（onefile 会有外层 bootloader 壳 + 实际子进程两个进程）

a = Analysis(
    ["../scripts/run_sidecar.py"],
    pathex=["../src"],
    binaries=[],
    datas=[
        ("../config/providers.example.yaml", "config"),
    ],
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.protocols.websockets.wsproto_impl",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "multipart.multipart",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # onedir：二进制留给 COLLECT
    name="job-radar-sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # 保留控制台便于排错；正式版可改 False
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="job-radar-sidecar",
)
