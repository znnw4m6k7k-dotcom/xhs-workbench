#!/usr/bin/env python3
"""后台启动工作台（Mac / Windows 通用）；已在运行就直接给出地址（地址写在 ~/.xhs-studio/url.txt）。

  python3 start.py          # Mac
  python start.py           # Windows（或双击 start.cmd）
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
STUDIO = Path(os.environ.get("XHS_STUDIO_HOME", str(Path.home() / ".xhs-studio")))
IS_WIN = os.name == "nt"
if IS_WIN:
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def running_url():
    f = STUDIO / "url.txt"
    if not f.is_file():
        return None
    url = f.read_text("utf-8").strip()
    try:
        with urllib.request.urlopen(url + "api/ping", timeout=2) as r:
            return url if json.loads(r.read().decode("utf-8")).get("app") == "xhs-studio" else None
    except Exception:
        return None


def launch(extra):
    """把 server.py 放到后台跑，关掉这个窗口 / Codex 这条命令结束后也不停。"""
    STUDIO.mkdir(parents=True, exist_ok=True)
    log = open(STUDIO / "panel.log", "a", encoding="utf-8")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    cmd = [sys.executable, str(HERE / "server.py"), "--open"] + extra
    if not IS_WIN:
        return subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env, start_new_session=True)
    # Windows：用 pythonw 不弹黑窗口；先试着脱离 Codex 的进程组（job），不允许就普通后台启动
    pyw = Path(sys.executable).with_name("pythonw.exe")
    if pyw.is_file():
        cmd[0] = str(pyw)
    base = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    for flags in (base | subprocess.CREATE_BREAKAWAY_FROM_JOB, base):
        try:
            return subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                                    creationflags=flags, close_fds=True)
        except OSError:
            continue
    raise SystemExit("没启动起来：无法在后台运行 %s" % cmd[0])


def main():
    url = running_url()
    if url:
        print("工作台地址：" + url)
        return 0
    old = (STUDIO / "url.txt").stat().st_mtime if (STUDIO / "url.txt").is_file() else 0
    launch(sys.argv[1:])
    for _ in range(40):
        time.sleep(0.5)
        f = STUDIO / "url.txt"
        if f.is_file() and f.stat().st_mtime >= old:
            url = running_url()
            if url:
                print("工作台地址：" + url)
                return 0
    print("没启动起来，看日志：%s" % (STUDIO / "panel.log"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
