#!/usr/bin/env python3
"""把成品的脸和衣服局部放大，和原图并排，方便逐项检查。

用法:
  python3 tools/crop_compare.py grid <图片>
      生成 <图片名>_grid.jpg，图上每 100 像素画一条带编号的线，用来读坐标。
  python3 tools/crop_compare.py closeup <原图目录> <源图> x1,y1,x2,y2
      从源图（original.jpg 或脸部拼图）里裁出正脸特写，存成 <原图目录>/face_closeup.jpg。
  python3 tools/crop_compare.py check <任务目录> <成品> --face x1,y1,x2,y2 [--garment x1,y1,x2,y2]
      把成品的脸部框和原图目录里的 face_closeup / face_* 并排，衣服框和 front / detail_* 并排，
      存到 <任务目录>/check/<成品名>.jpg。所有局部统一缩放到同样高度，脸的大小差异不会影响判断。
坐标都按原始像素算，先用 grid 读出来。如果提示缺少 PIL，先运行 pip3 install pillow。
"""
import argparse
import re
from pathlib import Path

from PIL import Image, ImageDraw

H = 560
GAP = 12
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def box(text):
    x1, y1, x2, y2 = (int(v) for v in text.split(","))
    return x1, y1, x2, y2


def fit_h(im, h=H):
    return im.resize((max(1, round(im.width * h / im.height)), h), Image.LANCZOS)


def row(images):
    out = Image.new("RGB", (sum(i.width for i in images) + GAP * (len(images) - 1), H), "white")
    x = 0
    for im in images:
        out.paste(im, (x, 0))
        x += im.width + GAP
    return out


def originals_dir(job):
    md = (job / "job.md").read_text(encoding="utf-8")
    m = re.search(r"原图目录[:：]\s*(.+?)\s*$", md, re.M)
    if not m:
        raise SystemExit("job.md 里缺少「原图目录:」这一行")
    return job.parent.parent / m.group(1)


def refs(folder, prefixes):
    files = [p for p in sorted(folder.iterdir()) if p.suffix.lower() in IMG_EXT]
    # 有 detail_1_flat 时不用 detail_1：卷圈原照只会误导材质判断
    stems = {p.stem.lower() for p in files}
    files = [p for p in files if f"{p.stem.lower()}_flat" not in stems]
    out = []
    for pre in prefixes:
        out += [p for p in files if p.stem.lower().startswith(pre) and p not in out]
    return out


def cmd_grid(a):
    src = Path(a.image)
    im = Image.open(src).convert("RGB")
    d = ImageDraw.Draw(im)
    for x in range(0, im.width, 100):
        d.line([(x, 0), (x, im.height)], fill=(255, 0, 0), width=2)
        d.text((x + 3, 3), str(x), fill=(255, 0, 0))
    for y in range(0, im.height, 100):
        d.line([(0, y), (im.width, y)], fill=(0, 90, 255), width=2)
        d.text((3, y + 3), str(y), fill=(0, 90, 255))
    out = src.with_name(f"{src.stem}_grid.jpg")
    im.save(out, quality=90)
    print(f"已生成 {out}")


def cmd_closeup(a):
    folder = Path(a.folder)
    out = folder / "face_closeup.jpg"
    Image.open(a.source).convert("RGB").crop(box(a.box)).save(out, quality=95)
    print(f"已生成 {out}")


def cmd_check(a):
    job = Path(a.job)
    folder = originals_dir(job)
    out_img = Image.open(a.output).convert("RGB")
    rows = []
    if a.face:
        face_refs = refs(folder, ["face_closeup", "face_"]) or refs(folder, ["face"])
        if not face_refs:
            raise SystemExit(f"{folder} 里没有 face_closeup.jpg 或 face*.jpg，先用 closeup 子命令裁一张")
        rows.append(row([fit_h(Image.open(p).convert("RGB")) for p in face_refs[:4]] + [fit_h(out_img.crop(box(a.face)))]))
    if a.garment:
        g_refs = refs(folder, ["anchor", "original", "front", "detail_2", "detail_1", "detail"])
        rows.append(row([fit_h(Image.open(p).convert("RGB")) for p in g_refs[:3]] + [fit_h(out_img.crop(box(a.garment)))]))
    if not rows:
        raise SystemExit("至少给 --face 或 --garment 其中一个框")
    sheet = Image.new("RGB", (max(r.width for r in rows), H * len(rows) + GAP * (len(rows) - 1)), "white")
    for i, r in enumerate(rows):
        sheet.paste(r, (0, i * (H + GAP)))
    (job / "check").mkdir(exist_ok=True)
    out = job / "check" / f"{Path(a.output).stem}.jpg"
    sheet.save(out, quality=92)
    print(f"已生成 {out}（每行最右边是成品局部，左边是原图）")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("grid")
    g.add_argument("image")
    g.set_defaults(func=cmd_grid)
    c = sub.add_parser("closeup")
    c.add_argument("folder")
    c.add_argument("source")
    c.add_argument("box")
    c.set_defaults(func=cmd_closeup)
    k = sub.add_parser("check")
    k.add_argument("job")
    k.add_argument("output")
    k.add_argument("--face")
    k.add_argument("--garment")
    k.set_defaults(func=cmd_check)
    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
