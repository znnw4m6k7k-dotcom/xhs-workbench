#!/bin/bash
# 后台启动工作台；已在运行就直接给出地址（地址写在 ~/.xhs-studio/url.txt）
DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/.xhs-studio"
nohup python3 "$DIR/server.py" --open >> "$HOME/.xhs-studio/panel.log" 2>&1 &
for i in $(seq 1 20); do
  sleep 0.5
  if [ -f "$HOME/.xhs-studio/url.txt" ] && curl -s -o /dev/null "$(cat "$HOME/.xhs-studio/url.txt")"; then
    echo "工作台地址：$(cat "$HOME/.xhs-studio/url.txt")"; exit 0
  fi
done
echo "没启动起来，看日志：$HOME/.xhs-studio/panel.log"; exit 1
