#!/bin/bash
# 把仓库里的工作台装到 ~/.codex/skills/xhs-autopilot（已装过的先备份旧文件）。具体步骤在 install.py，Windows 用 install.ps1
set -e
exec python3 "$(cd "$(dirname "$0")" && pwd)/install.py" "$@"
