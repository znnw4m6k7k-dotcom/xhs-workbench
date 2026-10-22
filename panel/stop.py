#!/usr/bin/env python3
"""停止工作台（Mac / Windows 通用）。按 ~/.xhs-studio/panel.pid 找进程；旧版没写 pid 时按命令行里的 server.py 找。"""
import json
import os
import signal
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


def alive():
    f = STUDIO / "url.txt"
    if not f.is_file():
        return False
    try:
        with urllib.request.urlopen(f.read_text("utf-8").strip() + "api/ping", timeout=2) as r:
            return json.loads(r.read().decode("utf-8")).get("app") == "xhs-studio"
    except Exception:
        return False


def kill_by_cmdline():
    """没有 pid 文件时：找命令行里带本文件夹 server.py 的 python 进程。"""
    target = str(HERE / "server.py")
    if IS_WIN:
        ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($env:XHS_TARGET) "
              "-and $_.CommandLine -notlike '*stop.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $_.ProcessId }")
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=dict(os.environ, XHS_TARGET=target))
        return bool(r.stdout.strip())
    return subprocess.run(["pkill", "-f", target]).returncode == 0


def main():
    pid_file = STUDIO / "panel.pid"
    was = alive()
    killed = False
    if pid_file.is_file() and was:
        try:
            os.kill(int(pid_file.read_text("utf-8").strip()), signal.SIGTERM)   # Windows 上等于结束进程
            killed = True
        except (ValueError, OSError):
            pass
    if not killed:
        killed = kill_by_cmdline()
    for _ in range(20):
        if not alive():
            break
        time.sleep(0.3)
    if killed or was:
        pid_file.unlink(missing_ok=True)
        print("工作台已停止" if not alive() else "没停下来，请在任务管理器里结束 python 进程")
    else:
        print("工作台没在运行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
