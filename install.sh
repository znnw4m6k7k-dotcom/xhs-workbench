#!/bin/bash
# 把仓库里的工作台装到 ~/.codex/skills/xhs-autopilot（已装过的先备份旧文件）
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
X="$HOME/.codex/skills/xhs-autopilot"
mkdir -p "$X/panel" "$X/publisher" "$X/references"
if [ -f "$X/panel/server.py" ]; then
  B="$X/panel/backup-$(date +%Y%m%d-%H%M)-before-update"
  mkdir -p "$B"
  for f in server.py index.html studio.py test_server.py; do [ -f "$X/panel/$f" ] && cp "$X/panel/$f" "$B/"; done
  [ -f "$X/SKILL.md" ] && cp "$X/SKILL.md" "$B/"
  [ -f "$X/publisher/xhs_publish.py" ] && cp "$X/publisher/xhs_publish.py" "$B/"
  [ -f "$X/references/gaosaka-style.md" ] && cp "$X/references/gaosaka-style.md" "$B/"
  echo "旧文件备份在：$B"
fi
cp "$SRC"/panel/server.py "$SRC"/panel/index.html "$SRC"/panel/studio.py "$SRC"/panel/test_server.py "$SRC"/panel/fashion.example.json "$X/panel/"
# 店里自己的设置和启动脚本已经有就不动
for f in start.sh stop.sh; do [ -f "$X/panel/$f" ] || cp "$SRC/panel/$f" "$X/panel/"; done
cp "$SRC"/publisher/*.py "$SRC"/publisher/*.html "$X/publisher/"
cp "$SRC"/references/*.md "$X/references/"
cp "$SRC/SKILL.md" "$X/SKILL.md"
cp "$SRC/使用说明.md" "$X/使用说明.md"
cp "$SRC/references/gaosaka-style.md" "$X/references/gaosaka-style.md"
cd "$X/panel" && python3 -m unittest test_server -q
bash "$X/panel/stop.sh" >/dev/null 2>&1 || true
bash "$X/panel/start.sh"
echo "装好了。"
cat <<TIP

第一次用，按顺序做（详细见 $X/使用说明.md）：
  1. 发布 › 账号登录：每个号扫码登录
  2. 产品图 › 模特和参考图库：建模特资料卡（正面/左侧/右侧/全身），给每个号选固定模特
  3. 先拿 1 款衣服 × 1 个号走一遍，到「发布 › 待发布」点「只填不发试一次」检查
  提醒：到点发帖靠这台 Mac，要开机、别合盖睡眠、工作台开着。
TIP
[ -n "$B" ] && echo "回退：把 $B 里的文件拷回原位置，再运行 stop.sh / start.sh"
exit 0
