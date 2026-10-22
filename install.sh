#!/bin/bash
# 把仓库里的工作台装到 ~/.codex/skills/xhs-autopilot（已装过的先备份旧文件）
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
X="$HOME/.codex/skills/xhs-autopilot"
mkdir -p "$X/panel" "$X/publisher"
if [ -f "$X/panel/server.py" ]; then
  B="$X/panel/backup-$(date +%Y%m%d-%H%M)-before-update"
  mkdir -p "$B"
  for f in server.py index.html studio.py test_server.py; do [ -f "$X/panel/$f" ] && cp "$X/panel/$f" "$B/"; done
  [ -f "$X/SKILL.md" ] && cp "$X/SKILL.md" "$B/"
  [ -f "$X/publisher/xhs_publish.py" ] && cp "$X/publisher/xhs_publish.py" "$B/"
  echo "旧文件备份在：$B"
fi
cp "$SRC"/panel/server.py "$SRC"/panel/index.html "$SRC"/panel/studio.py "$SRC"/panel/test_server.py "$SRC"/panel/fashion.example.json "$X/panel/"
# 店里自己的设置和启动脚本已经有就不动
for f in start.sh stop.sh; do [ -f "$X/panel/$f" ] || cp "$SRC/panel/$f" "$X/panel/"; done
cp "$SRC"/publisher/*.py "$SRC"/publisher/*.html "$X/publisher/"
cp "$SRC/SKILL.md" "$X/SKILL.md"
cd "$X/panel" && python3 -m unittest test_server -q
bash "$X/panel/stop.sh" >/dev/null 2>&1 || true
bash "$X/panel/start.sh"
echo "装好了。"
[ -n "$B" ] && echo "回退：把 $B 里的文件拷回原位置，再运行 stop.sh / start.sh"
exit 0
