#!/usr/bin/env python3
"""两步法第二步：把成品的头部裁出来放大给生图模型修脸，修完再贴回原图。

用法:
  python3 tools/face_fix.py crop <成品> x1,y1,x2,y2 [--size 1024]
      按框裁出头部（自动扩成正方形），放大到 size×size，存成 <成品名>_head.png，
      框的位置记在 <成品名>_head.json，贴回时要用。框用 crop_compare.py grid 读，
      要包住整颗头和一点脖子，脸在框里占一半以上。
  python3 tools/face_fix.py paste <成品> <修好的头部图> [--feather 0.18] [--inner 0.80]
      把修好的头部缩回原来的大小，用边缘羽化的椭圆蒙版贴回成品，存成 <成品名>_fixed.png。
      只替换椭圆里面（脸和头发内侧），边缘的头发和背景保留原图，避免接缝。
      贴之前会把修好的头部的颜色均值对齐到原图同一区域，防止脸色和身体不一致。
"""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat


def box(text):
    return tuple(int(v) for v in text.split(","))


def square(b, w, h):
    x1, y1, x2, y2 = b
    side = max(x2 - x1, y2 - y1)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    side = min(side, w, h)
    x1 = int(max(0, min(w - side, cx - side / 2)))
    y1 = int(max(0, min(h - side, cy - side / 2)))
    return x1, y1, x1 + side, y1 + side


def cmd_crop(a):
    src = Path(a.image)
    im = Image.open(src).convert("RGB")
    b = square(box(a.box), im.width, im.height)
    head = im.crop(b).resize((a.size, a.size), Image.LANCZOS)
    out = src.with_name(f"{src.stem}_head.png")
    head.save(out)
    src.with_name(f"{src.stem}_head.json").write_text(json.dumps({"box": b}), encoding="utf-8")
    print(f"已生成 {out}（原图框 {b}，放大 {a.size / (b[2] - b[0]):.1f} 倍）")


def match_color(fixed, ref, mask):
    # 按通道把修好图的均值和标准差对齐到原图，只统计蒙版里的像素
    out = []
    for f, r in zip(fixed.split(), ref.split()):
        fs, rs = ImageStat.Stat(f, mask), ImageStat.Stat(r, mask)
        k = rs.stddev[0] / fs.stddev[0] if fs.stddev[0] > 1 else 1.0
        k = max(0.85, min(1.15, k))
        m_f, m_r = fs.mean[0], rs.mean[0]
        out.append(f.point(lambda v: max(0, min(255, round((v - m_f) * k + m_r)))))
    return Image.merge("RGB", out)


def cmd_paste(a):
    src = Path(a.image)
    im = Image.open(src).convert("RGB")
    info = json.loads(src.with_name(f"{src.stem}_head.json").read_text(encoding="utf-8"))
    x1, y1, x2, y2 = info["box"]
    side = x2 - x1
    fixed = Image.open(a.fixed).convert("RGB").resize((side, side), Image.LANCZOS)
    orig = im.crop((x1, y1, x2, y2))

    mask = Image.new("L", (side, side), 0)
    m = side * (1 - a.inner) / 2
    ImageDraw.Draw(mask).ellipse([m, m * 0.8, side - m, side - m * 1.2], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(side * a.feather / 2))

    fixed = match_color(fixed, orig, mask.point(lambda v: 255 if v > 128 else 0))
    im.paste(Image.composite(fixed, orig, mask), (x1, y1))
    out = src.with_name(f"{src.stem}_fixed.png")
    im.save(out)
    diff = ImageChops.difference(fixed, orig).convert("L")
    print(f"已生成 {out}（框内平均差异 {ImageStat.Stat(diff, mask).mean[0]:.1f}）")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("crop")
    c.add_argument("image")
    c.add_argument("box")
    c.add_argument("--size", type=int, default=1024)
    c.set_defaults(func=cmd_crop)
    s = sub.add_parser("paste")
    s.add_argument("image")
    s.add_argument("fixed")
    s.add_argument("--feather", type=float, default=0.18)
    s.add_argument("--inner", type=float, default=0.80)
    s.set_defaults(func=cmd_paste)
    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
