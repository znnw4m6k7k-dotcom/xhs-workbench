#!/usr/bin/env python3
"""真脸贴图：把模特本人照片里的脸，按角度对齐贴到成品的头部放大图上，交给生图模型只做融合。

为什么要这一步：生图模型编辑一张图时，会保住底图那张脸的脸型骨架。
直接出的图脸是模型自己画的（网红脸），事后怎么修都改不回来；
先把本人的脸贴上去当底图，再让模型只修接缝和光线，脸型就是她本人。

用法:
  python3 tools/face_paste.py <成品> <原图目录> [--size 1024]
      1. 找到成品里的人脸，按头部裁出正方形并放大到 size，存成 <成品名>_head.png
         和 <成品名>_head.json（贴回时 face_fix.py paste 要用）。
      2. 在原图目录里挑角度最接近的本人照片（face_1_* 正脸、face_2_* 半侧、face_3_* 全侧，
         都没有时用 face_closeup / face.jpg / original*；
         朝向相反时自动左右翻转），用眼睛、鼻尖、嘴角 5 个点对齐，贴到头部放大图上，
         边缘羽化并把肤色对齐到原图，存成 <成品名>_贴脸.png。
      打印用了哪张照片、成品脸的大小和角度。
  之后：<成品名>_贴脸.png 当图1 交给 image_gen 只做融合（提示词见 SKILL.md），
        融合结果再用 python3 tools/face_fix.py paste <成品> <融合图> 贴回成品。

依赖：pip3 install opencv-python numpy pillow
人脸检测模型：tools/models/face_detection_yunet_2023mar.onnx（OpenCV Zoo，MIT 许可，可商用）
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

MODEL = Path(__file__).with_name("models") / "face_detection_yunet_2023mar.onnx"
VIEWS = ("face_1_*", "face_2_*", "face_3_*", "face_closeup*", "face.*", "original*", "anchor.*")


def detect(im):
    h, w = im.shape[:2]
    det = cv2.FaceDetectorYN.create(str(MODEL), "", (w, h), 0.5)
    _, faces = det.detect(im)
    if faces is None:
        return None
    return max(faces, key=lambda f: f[2] * f[3])


def yaw(f):
    # 鼻尖相对两眼中点的水平偏移 / 眼距：0 是正脸，±0.3 左右是半侧，±0.8 以上接近全侧
    p = f[4:14].reshape(5, 2)
    mid = (p[0] + p[1]) / 2
    return (p[2][0] - mid[0]) / max(np.linalg.norm(p[1] - p[0]), 1)


def head_box(f, w, h):
    # 以脸框为基准往外扩成整颗头加一点脖子的正方形
    x, y, fw, fh = f[:4]
    side = int(max(fw, fh) * 2.4)
    cx, cy = x + fw / 2, y + fh * 0.45
    side = min(side, w, h)
    x1 = int(max(0, min(w - side, cx - side / 2)))
    y1 = int(max(0, min(h - side, cy - side / 2)))
    return x1, y1, x1 + side, y1 + side


def pick_view(folder, yt):
    best = None
    for pat in VIEWS:
        for path in sorted(Path(folder).glob(pat)):
            if path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                continue
            im = cv2.imread(str(path))
            f = detect(im) if im is not None else None
            if f is None:
                continue
            y = yaw(f)
            if abs(y) > 0.08 and np.sign(y) != np.sign(yt):
                im = np.ascontiguousarray(im[:, ::-1])
                f = detect(im)
                if f is None:
                    continue
                y = yaw(f)
            cand = (abs(y - yt), path.name, im, f, y)
            if best is None or cand[0] < best[0]:
                best = cand
    return best


def paste_face(target, src, fs, ft):
    ps = fs[4:14].reshape(5, 2).astype(np.float32)
    pt = ft[4:14].reshape(5, 2).astype(np.float32)
    A, _ = cv2.estimateAffinePartial2D(ps, pt, method=cv2.LMEDS)
    H, W = target.shape[:2]
    warp = cv2.warpAffine(src, A, (W, H), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT)

    # 蒙版：本人脸框外扩的椭圆（含下颌线和发际线），这样脸型轮廓也是她本人的
    x, y, w, h = fs[:4]
    mask = np.zeros(src.shape[:2], np.uint8)
    cv2.ellipse(mask, (int(x + w / 2), int(y + h * 0.58)), (int(w * 0.56), int(h * 0.6)), 0, 0, 360, 255, -1)
    # 本人照片是纯色影棚背景：和上方、两侧边缘颜色接近的像素不贴，免得把灰背景带进成品（侧脸时最明显）
    top = src[: src.shape[0] // 3]
    border = np.concatenate([src[:8].reshape(-1, 3), top[:, :8].reshape(-1, 3), top[:, -8:].reshape(-1, 3)])
    bg = np.median(border, axis=0)
    near_bg = np.linalg.norm(src.astype(np.float32) - bg, axis=2) < 18
    near_bg = cv2.morphologyEx(near_bg.astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8)).astype(bool)
    mask[near_bg] = 0
    mask = cv2.warpAffine(mask, A, (W, H))
    k = int(max(5, ft[2] * 0.08)) | 1
    mask = cv2.GaussianBlur(mask, (k, k), 0)

    # 肤色对齐：只统计蒙版内像素，在 LAB 空间对齐均值和方差
    m = mask > 128
    wl = cv2.cvtColor(warp, cv2.COLOR_BGR2LAB).astype(np.float32)
    tl = cv2.cvtColor(target, cv2.COLOR_BGR2LAB).astype(np.float32)
    for c in range(3):
        a, b = wl[..., c][m], tl[..., c][m]
        k2 = np.clip(b.std() / max(a.std(), 1), 0.8, 1.25)
        wl[..., c] = (wl[..., c] - a.mean()) * k2 + b.mean()
    warp = cv2.cvtColor(np.clip(wl, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)
    alpha = (mask.astype(np.float32) / 255)[..., None]
    return (warp * alpha + target * (1 - alpha)).astype(np.uint8)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("image")
    p.add_argument("folder")
    p.add_argument("--size", type=int, default=1024)
    a = p.parse_args()

    src = Path(a.image)
    im = cv2.imread(str(src))
    f = detect(im)
    if f is None:
        sys.exit("成品里没找到人脸（可能是背影或脸被挡住），这张不用贴脸。")
    H, W = im.shape[:2]
    b = head_box(f, W, H)
    head = cv2.resize(im[b[1]:b[3], b[0]:b[2]], (a.size, a.size), interpolation=cv2.INTER_LANCZOS4)
    cv2.imwrite(str(src.with_name(f"{src.stem}_head.png")), head)
    src.with_name(f"{src.stem}_head.json").write_text(json.dumps({"box": list(map(int, b))}), encoding="utf-8")

    ft = detect(head)
    if ft is None:
        sys.exit("头部放大图里没找到人脸，请用 face_fix.py crop 手动框头再试。")
    yt = yaw(ft)
    best = pick_view(a.folder, yt)
    if best is None:
        sys.exit(f"{a.folder} 里没有能识别出脸的 face_1_*/face_2_*/face_3_* 照片。")
    _, name, view, fs, ys = best
    out = src.with_name(f"{src.stem}_贴脸.png")
    cv2.imwrite(str(out), paste_face(head, view, fs, ft))
    side = "正脸" if abs(yt) < 0.15 else "半侧脸" if abs(yt) < 0.55 else "侧脸"
    print(f"已生成 {out}")
    print(f"成品脸宽约 {int(f[2])} 像素，{side}（角度值 {yt:+.2f}），用了 {name}（{ys:+.2f}），头部放大 {a.size / (b[2] - b[0]):.1f} 倍")


if __name__ == "__main__":
    main()
