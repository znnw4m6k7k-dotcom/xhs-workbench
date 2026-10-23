#!/usr/bin/env python3
"""把成品统一成 1080x1440，并拼一张对比总览图 compare.jpg。

用法: python3 tools/contact_sheet.py jobs/<任务目录>
- 模式 B（参考图换人）：每行是「素材图 | 成品」，最左一列是原图。
- 模式 A（按背景换姿势）：左边一列是产品图，右边按 3 列网格放成品。
如果提示缺少 PIL，先运行 pip3 install pillow。
"""
import re
import sys
from pathlib import Path

from PIL import Image, ImageOps

W, H = 1080, 1440
CELL_W, CELL_H = 360, 480
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def images(folder):
    files = sorted(p for p in Path(folder).glob("*") if p.suffix.lower() in IMG_EXT)
    # 有 detail_1_flat 时不放 detail_1：生图用的是平铺那张
    stems = {p.stem.lower() for p in files}
    return [p for p in files if f"{p.stem.lower()}_flat" not in stems]


def to_34(path, size=(W, H)):
    """把图片居中裁成任务单的比例（默认 3:4 的 1080x1440，「输出」写 1:1 时 1080x1080），覆盖保存原文件。"""
    im = Image.open(path).convert("RGB")
    if im.size != size:
        ImageOps.fit(im, size, Image.LANCZOS).save(path, quality=95)


def cell(path, fit=False):
    im = Image.open(path).convert("RGB")
    if fit:
        return ImageOps.fit(im, (CELL_W, CELL_H))
    return ImageOps.pad(im, (CELL_W, CELL_H), color="white")


def sheet_reference_swap(job, root, md, outs):
    """模式 B：读任务单里的「| 序号 | 素材图 | 输出文件名 |」表格，一行一组对比。"""
    m = re.search(r"原图目录[:：]\s*(.+?)\s*$", md, re.M)
    originals = sorted(images(root / m.group(1)), key=lambda p: (not p.stem.startswith(("original", "anchor")), p.name)) if m else []
    rows = re.findall(r"^\|\s*\d+\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|", md, re.M)
    out_names = {p.name: p for p in outs}
    n = max(len(rows), len(originals), 1)
    sheet = Image.new("RGB", (CELL_W * 3 + 40, CELL_H * n), "white")
    for i, p in enumerate(originals[:n]):
        sheet.paste(cell(p), (0, i * CELL_H))
    for i, (ref, out_name) in enumerate(rows):
        if (root / ref).exists():
            sheet.paste(cell(root / ref), (CELL_W + 20, i * CELL_H))
        if out_name in out_names:
            sheet.paste(cell(out_names[out_name], fit=True), (CELL_W * 2 + 40, i * CELL_H))
    missing = [o for _, o in rows if o not in out_names]
    return sheet, missing


def sheet_background(job, root, md, outs):
    """模式 A：左边一列产品图，右边按 3 列网格放成品。"""
    m = re.search(r"产品目录[:：]\s*(.+?)\s*$", md, re.M)
    if not m:
        sys.exit("job.md 里缺少 '产品目录: library/products/<款号>' 这一行")
    product = root / m.group(1)
    refs = [p for p in images(product) if p.stem == "front" or p.stem.startswith(("back", "detail"))]
    cols = 3
    n = max(len(refs), (len(outs) + cols - 1) // cols, 1)
    sheet = Image.new("RGB", (CELL_W * (cols + 1) + 20, CELL_H * n), "white")
    for i, p in enumerate(refs[:n]):
        sheet.paste(cell(p), (0, i * CELL_H))
    for i, p in enumerate(outs):
        sheet.paste(cell(p, fit=True), (CELL_W + 20 + (i % cols) * CELL_W, (i // cols) * CELL_H))
    return sheet, []


def main():
    job = Path(sys.argv[1])
    root = job.parent.parent
    md = (job / "job.md").read_text(encoding="utf-8")
    outs = images(job / "out")
    if not outs:
        sys.exit(f"{job / 'out'} 里还没有成品图")
    size = (W, W) if re.search(r"^输出[:：].*1:1", md, re.M) else (W, H)
    for p in outs:
        to_34(p, size)

    build = sheet_reference_swap if "参考图换人" in md or "模式: 只锁产品" in md else sheet_background
    sheet, missing = build(job, root, md, outs)
    sheet.save(job / "compare.jpg", quality=90)
    print(f"已生成 {job / 'compare.jpg'}，共 {len(outs)} 张成品已统一为 {size[0]}x{size[1]}")
    if missing:
        print("还没有生成的：" + "、".join(missing))


if __name__ == "__main__":
    main()
