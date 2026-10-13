#!/usr/bin/env bash
# 校招雷达 · Linux 构建脚本（Ubuntu 22.04+；需在目标机器验收）
# 用法：bash build-linux.sh
#
# 前置系统依赖（Ubuntu/Debian）：
#   sudo apt install -y libwebkit2gtk-4.1-dev build-essential curl wget file \
#     libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev \
#     python3-venv python3-pip nodejs npm
# Rust 工具链：curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
set -euo pipefail
cd "$(dirname "$0")"

echo "==> 1/5 Python venv + 依赖"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
pip install pyinstaller

echo "==> 2/5 打包 sidecar（PyInstaller onedir）"
pyinstaller --noconfirm --distpath build/sidecar-dist --workpath build/pyinstaller src-tauri/job-radar-sidecar.spec

echo "==> 3/5 前端构建"
npm ci
npm run build

echo "==> 4/5 编译 Tauri 壳（约 2 分钟）"
cd src-tauri
cargo build --release
cd ..

echo "==> 5/5 组装分发目录"
REL="$(python scripts/assemble_release.py)"

echo ""
echo "==> 完成。运行：$REL/job-radar-coach"
echo "    首次运行会启动 sidecar（127.0.0.1:17689）并打开应用窗口。"
