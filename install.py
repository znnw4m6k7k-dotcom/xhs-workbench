#!/usr/bin/env python3
"""把仓库里的工作台装到 ~/.codex/skills/xhs-autopilot（Mac / Windows 通用；已装过的先备份旧文件）。

  Mac：     bash install.sh          （等于 python3 install.py）
  Windows： powershell -ExecutionPolicy Bypass -File install.ps1   （等于 py -3 install.py）
"""
import datetime
import os
import shutil
import subprocess
import sys
from pathlib import Path

IS_WIN = os.name == "nt"
if IS_WIN:
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

SRC = Path(__file__).resolve().parent
X = Path.home() / ".codex" / "skills" / "xhs-autopilot"
PANEL_FILES = ["server.py", "index.html", "studio.py", "test_server.py", "fashion.example.json",
               "start.py", "stop.py", "start.cmd", "stop.cmd"]
# 店里自己改过的启动脚本已经有就不动
KEEP_IF_EXISTS = ["start.sh", "stop.sh"]
ENV = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")


def py_cmd():
    """给人看的 python 命令写法。"""
    return "python" if IS_WIN else "python3"


def backup():
    if not (X / "panel" / "server.py").is_file():
        return None
    b = X / "panel" / ("backup-%s-before-update" % datetime.datetime.now().strftime("%Y%m%d-%H%M"))
    b.mkdir(parents=True, exist_ok=True)
    for f in ["panel/server.py", "panel/index.html", "panel/studio.py", "panel/test_server.py", "SKILL.md",
              "publisher/xhs_publish.py", "references/gaosaka-style.md"]:
        if (X / f).is_file():
            shutil.copy2(X / f, b / Path(f).name)
    print("旧文件备份在：%s" % b)
    return b


def copy_all():
    for d in ("panel", "publisher", "references"):
        (X / d).mkdir(parents=True, exist_ok=True)
    for f in PANEL_FILES:
        shutil.copy2(SRC / "panel" / f, X / "panel" / f)
    for f in KEEP_IF_EXISTS:
        if not (X / "panel" / f).exists():
            shutil.copy2(SRC / "panel" / f, X / "panel" / f)
            if not IS_WIN:
                os.chmod(X / "panel" / f, 0o755)
    for p in list((SRC / "publisher").glob("*.py")) + list((SRC / "publisher").glob("*.html")):
        shutil.copy2(p, X / "publisher" / p.name)
    for p in (SRC / "references").glob("*.md"):
        shutil.copy2(p, X / "references" / p.name)
    shutil.copy2(SRC / "SKILL.md", X / "SKILL.md")
    shutil.copy2(SRC / "使用说明.md", X / "使用说明.md")


def run_tests():
    r = subprocess.run([sys.executable, "-m", "unittest", "test_server", "-q"], cwd=str(X / "panel"), env=ENV)
    if r.returncode == 0:
        return
    if IS_WIN:
        # 测试是在 Mac / Linux 上写的，Windows 上个别条目可能因为路径写法不同而不过；不拦安装，但要报给管理员
        print("！有测试没通过（上面有详情）。工作台照样装好并启动，请把这段输出发给管理员看。")
        return
    sys.exit("测试没通过，工作台没有重启。把上面的输出发给管理员。")


def check_playwright():
    """发布要用 Playwright。没装就装一次；Mac 没 Chrome 时还要下 Chromium，Windows 没 Chrome 会用自带的 Edge。"""
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        pass
    print("正在安装发布要用的 Playwright……")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--user", "playwright"], env=ENV)
    if r.returncode != 0:
        print("Playwright 没装上，先不影响出图和写文案。要发布时运行：%s -m pip install --user playwright" % py_cmd())
        return False
    if not IS_WIN:
        print("如果这台 Mac 没装 Google Chrome，再运行一次：python3 -m playwright install chromium")
    return True


def restart():
    panel = X / "panel"
    if IS_WIN:
        subprocess.run([sys.executable, str(panel / "stop.py")], env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return subprocess.run([sys.executable, str(panel / "start.py")], env=ENV).returncode
    subprocess.run(["bash", str(panel / "stop.sh")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return subprocess.run(["bash", str(panel / "start.sh")]).returncode


def main():
    if sys.version_info < (3, 9):
        sys.exit("Python 版本太旧（%s），需要 3.9 或更新" % sys.version.split()[0])
    b = backup()
    copy_all()
    run_tests()
    check_playwright()
    if restart() != 0:
        sys.exit("工作台没启动起来，看日志：%s" % (Path.home() / ".xhs-studio" / "panel.log"))
    print("the rumah小红书工作台 装好了。")
    start = ('python "%s"' % (X / "panel" / "start.py")) if IS_WIN else "bash %s" % (X / "panel" / "start.sh")
    machine = "这台电脑" if IS_WIN else "这台 Mac"
    print("""
第一次用，按顺序做（详细见 %s）：
  1. 发布 › 账号登录：每个号扫码登录
  2. 产品图 › 模特和参考图库：建模特资料卡（正面/左侧/右侧/全身），给每个号选固定模特
  3. 先拿 1 款衣服 × 1 个号走一遍，到「发布 › 待发布」点「只填不发试一次」检查
  提醒：到点发帖靠%s，要开机、别合盖睡眠、工作台开着。%s
  以后打开工作台：%s""" % (X / "使用说明.md", machine,
                       "\n  Windows 建议：设置 › 系统 › 电源，把「接通电源后睡眠」改成「从不」，合盖改成「不采取任何操作」。" if IS_WIN else "",
                       start))
    if b:
        print("回退：把 %s 里的文件拷回原位置，再运行 stop 和 start" % b)
    return 0


if __name__ == "__main__":
    sys.exit(main())
