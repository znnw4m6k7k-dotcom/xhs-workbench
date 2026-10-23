#!/usr/bin/env python3
"""出图前检查环境：Python 库和人脸检测模型是否都在。缺库时自动安装。

用法: python3 tools/check_env.py
"""
import importlib
import subprocess
import sys
from pathlib import Path

NEED = {"PIL": "pillow", "numpy": "numpy", "cv2": "opencv-python"}
MODEL = Path(__file__).with_name("models") / "face_detection_yunet_2023mar.onnx"


def main():
    missing = []
    for mod, pkg in NEED.items():
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        print("正在安装缺少的库：" + "、".join(missing))
        cmd = [sys.executable, "-m", "pip", "install", "--user", *missing]
        if subprocess.run(cmd).returncode != 0:
            sys.exit("安装失败。请在终端运行：" + " ".join(cmd))
    import cv2

    if not MODEL.exists():
        sys.exit(f"缺少人脸检测模型 {MODEL}，请从工作台仓库重新复制 tools/models 文件夹。")
    if not hasattr(cv2, "FaceDetectorYN"):
        sys.exit(f"OpenCV 版本太旧（{cv2.__version__}），请运行：{sys.executable} -m pip install --user -U opencv-python")
    print(f"环境正常：Python {sys.version.split()[0]}，OpenCV {cv2.__version__}")


if __name__ == "__main__":
    main()
