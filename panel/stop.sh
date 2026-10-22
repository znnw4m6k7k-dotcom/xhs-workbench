#!/bin/bash
# 停止工作台
DIR="$(cd "$(dirname "$0")" && pwd)"
if pkill -f "$DIR/server.py"; then
  for i in $(seq 1 20); do pgrep -f "$DIR/server.py" >/dev/null || break; sleep 0.3; done
  echo "工作台已停止"
else
  echo "工作台没在运行"
fi
