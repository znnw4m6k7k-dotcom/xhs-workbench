#!/usr/bin/env python3
"""按任务单拼出生图提示词和附图清单，Codex 只填空，不自己写提示词。

为什么要这个脚本：以前每张图的提示词是 Codex 现场照着 SKILL.md 拼的，
每张都不一样，返工时还会被它自己缩写，衣服描述一缩写就容易走样。
现在固定由脚本从 spec.md、profile.md 和 job.md 拼，返工只多一行「上一张的问题」。

用法（<序号> 是 job.md「逐张任务」表里的序号）:
  python3 tools/build_prompt.py clothes jobs/<任务> <序号> --old "图1原来衣服的样子和材质" [--side] [--mirror] [--keep "..."] [--fix "..."]
      换衣服（换模特时同时换发型）。
      --old    必填。素材图里人物原来穿的衣服，比如「米白色长毛开衫」「白色光滑衬衫」，用来写材质隔离那一句
      --side   素材图人物是侧身或半侧身
      --mirror 素材图是对镜自拍
      --keep   要额外写死不动的东西，比如「双腿的位置、步态、哪只脚抬起都和图1完全一样」
      --fix    返工时写上一张的问题，比如「领尖画成了圆领」
  python3 tools/build_prompt.py fuse jobs/<任务> <序号> --light "..." --expr "..." [--acc "..."] [--fix "..."]
      贴脸后的融合。附图固定是 base/<文件名>_贴脸.png 和模特的 face_closeup.jpg 两张。
      --light 素材图的光线，比如「户外强日光」「酒店室内暖光」
      --expr  素材图的表情，比如「开心地张嘴大笑、露出上排牙齿、眼睛微微眯起」
      --acc   素材图里头脸上的配饰，写清形状颜色，比如「黑色粗框圆眼镜，实色镜框」；没有就不写
      --fix   返工时写上一张融合后哪里不像，比如「下巴变尖了，保持图1里短而圆的下巴」

输出：在 jobs/<任务>/prompts/ 里存 <序号>_<clothes|fuse>.json，并把同样的 JSON 打印出来：
  {"prompt": "...", "referenced_image_paths": [...], "save_as": "..."}
  python3 tools/build_prompt.py product jobs/<任务> <序号> [--fix "..."]
      产品套图（不用模特）：挂拍、平铺、领口特写、面料特写、多色合拍，类型和颜色都按任务单那一行。
      成品直接存到 out/。

在 Codex 里直接这样用：
  const j=JSON.parse((await tools.exec_command({cmd:"cat jobs/<任务>/prompts/01_clothes.json"})).output);
  await tools.image_gen__imagegen({prompt:j.prompt, referenced_image_paths:j.referenced_image_paths});
"""
import argparse
import json
import re
import sys
from pathlib import Path

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def field(md, key):
    m = re.search(rf"^{key}[:：][ \t]*(.*?)[ \t]*$", md, re.M)
    return m.group(1).strip() if m else ""


def label_rule(md):
    if field(md, "带主标") == "是":
        return ("主标规则优先于档案中‘领标不要画’等旧说明：仅保留或还原本产品实拍中可见的品牌主标，"
                "位置、形状、颜色和清楚可读的内容按产品照片或主标近拍；模糊内容不编造。"
                "被遮挡时自然遮挡，不把内领主标搬到衣服外侧；不使用场景参考图的品牌，不加图片水印。")
    return "移除衣服内领或内侧的品牌主标，保持其他衣服结构和原有设计；不出现参考图里的品牌标识，不加图片水印。"


def task_row(md, no):
    """读「逐张任务」表：| 序号 | 素材图 | 输出文件名 | 附背面 |（附背面这一列老任务单没有）。"""
    for line in md.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 3 and cells[0].isdigit() and int(cells[0]) == no:
            return cells[1], cells[2], len(cells) > 3 and cells[3] == "是"
    sys.exit(f"job.md 的逐张任务表里没有序号 {no}")


def ref_images(md):
    """读「参考图」一节的 图2…图5 路径（图1 是每张自己的素材图）。"""
    return [m.group(1) for m in re.finditer(r"^图[2-5][.．、]\s*(\S+?\.(?:jpe?g|png|webp))", md, re.M | re.I)]


def section(text, title):
    m = re.search(rf"^### {title}.*?$\n(.*?)(?=^### |^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def line_value(text, key):
    m = re.search(rf"{key}[:：]\s*(.+?)\s*$", text, re.M)
    return m.group(1).strip() if m else ""


def role(name):
    n = name.lower()
    if n.startswith("label"):
        return "本产品品牌主标近拍，仅用于还原主标，不参考整件衣服结构"
    if n.startswith("front"):
        return "这件产品的正面"
    if n.startswith("detail_2"):
        return "这件衣服最重要的设计的特写，成品里这一块的形状、宽度、起点和走向必须和它一样"
    if n.startswith(("detail_1", "detail_3")):
        return "面料近拍，成品衣服的表面近看要和它一样：绒毛长短、针眼粗细、混色颗粒、光泽和透光都不能变；布料的弯折、卷圈不是图案"
    if n.startswith("detail"):
        return "面料和细节近拍"
    if n.startswith("back"):
        return "这件衣服的背面"
    if n.startswith(("original", "anchor")):
        return "只用来看这件衣服穿在身上的版型和长度，不要用这个人的脸、发型和身材"
    return "产品参考"


def clothes(a, root, job, md):
    ref, out, want_back = task_row(md, a.no)
    orig_dir = root / field(md, "原图目录")
    spec = (orig_dir / "spec.md").read_text(encoding="utf-8")
    garment = section(spec, "衣服")
    if not garment:
        sys.exit(f"{orig_dir}/spec.md 里没有「### 衣服」一节")
    lower = section(spec, "下装")
    model = model_dir(root, md)

    imgs = []
    for p in ref_images(md):
        name = Path(p).name
        if name.lower().startswith("back") and not (want_back or a.back):
            body = [f for f in sorted(orig_dir.iterdir()) if f.suffix.lower() in IMG_EXT and f.stem.lower() in ("original", "anchor")]
            if not body:
                continue
            p = str(body[0].relative_to(root))
            name = body[0].name
        imgs.append((p, role(name)))
    imgs = imgs[:4]

    lines = []
    if a.fix:
        lines.append(f"上一张的问题：{a.fix}。这次把这一处改对，其余要求和下面完全一样。")
    lines.append(f"编辑图1，只换衣服{'和发型' if model else ''}。")
    lines.append("图1 是素材照片，也是要编辑的底图。" + "".join(f"图{i + 2} 是{r}。" for i, (_, r) in enumerate(imgs)))
    keep = "图1 里人物的脸、五官、肤色、身材比例、动作、手的位置、表情、背景、光线、构图和配饰全部保持原样，不要重画。"
    if a.keep:
        keep += a.keep.rstrip("。") + "。"
    lines.append(keep)
    if model:
        hair = line_value((model / "profile.md").read_text(encoding="utf-8"), "- 头发")
        lines.append(f"把人物的发型和发色换成：{hair}。脸先不用管。")
    lines.append(f"把图1人物身上原来的衣服（{a.old}）换成图2起的这件产品，逐项还原：\n{garment}")
    if lower:
        lines.append(lower)
    lines.append(f"图1 人物原来穿的{a.old}的材质、颜色、绒毛和纹路一点都不要参考，新衣服的表面只按面料近拍那张图。")
    if a.side:
        lines.append("人物是侧身的，按上面「侧面看」那一条画这件衣服，不要画成普通衬衫。")
    if a.mirror:
        lines.append("这是对镜自拍，画面里的左右和穿着者的左右相反，不对称的设计按镜像放。")
    extra = re.search(r"^## 补充要求\s*\n(.*?)(?=^## |\Z)", md, re.M | re.S)
    if extra and extra.group(1).strip() not in ("", "无"):
        # 写给 Codex 的流程要求（记录耗时、写报告）不进生图提示词
        keep_lines, dropped = [], []
        for ln in extra.group(1).strip().splitlines():
            (dropped if re.search(r"report|报告|记录|耗时|分钟", ln) else keep_lines).append(ln)
        if dropped:
            print("补充要求里这几行是流程要求，没放进提示词：" + " / ".join(dropped), file=sys.stderr)
        if keep_lines:
            lines.append("补充要求：" + "\n".join(keep_lines))
    lines.append("衣服按人物的姿势和图1的光线自然穿上：褶皱、垂坠、受光和阴影跟着姿势走；被原来衣服挡住、现在露出来的身体部分自然补全。竖图，保持图1的构图，不要裁切。")
    lines.append("禁止：改动人物的脸和身材；改变衣服的颜色、材质光泽、长度、版型、纹路、印花、扣子数量、领型；把褶皱抚平或面料变亮；加额外文字或水印。")
    lines.append(label_rule(md))
    paths = [ref] + [p for p, _ in imgs]
    return "\n".join(lines), paths, f"{job.relative_to(root)}/base/{out}"


def model_dir(root, md):
    m = re.search(r"(library/models/[^\s（(]+)", field(md, "脸部处理"))
    return root / m.group(1) if m else None


def fuse(a, root, job, md):
    _, out, _ = task_row(md, a.no)
    model = model_dir(root, md)
    folder = model or root / field(md, "原图目录")
    prof = (folder / ("profile.md" if model else "spec.md")).read_text(encoding="utf-8")
    face = line_value(prof, "脸型") or "按图2"
    makeup = line_value(prof, "- 妆容") or line_value(prof, "- 皮肤和妆容") or "按图2"
    stem = Path(out).stem
    pasted = job / "base" / f"{stem}_贴脸.png"
    if not pasted.exists():
        print(f"提醒：还没有 {pasted.relative_to(root)}，先运行 face_paste.py", file=sys.stderr)
    closeup = folder / "face_closeup.jpg"

    lines = []
    if a.fix:
        lines.append(f"上一张的问题：{a.fix}。")
    lines.append(f"编辑图1，只做局部修补。图1 中间这张脸就是模特本人（和图2是同一个人），这张脸已经是对的：脸型是{face}。")
    lines.append("脸型、颧骨、眉毛、眼睛形状和大小、鼻子、嘴唇形状都保持图1的样子，不要重画。不要美化、瘦脸、削尖下巴、放大眼睛，不要把脸改得更像网红或模特。")
    lines.append("只修这几处：")
    lines.append("1. 脸的边缘和周围（头发、耳朵、脖子、背景）之间的接缝、硬边、色差，修成自然过渡；脸周围断掉的发丝接成自然连贯的头发；脸边缘露出的灰色影棚背景改成和周围一致的背景。")
    lines.append(f"2. 脸上的光线方向、色温、明暗、清晰度和颗粒感，调成和图1其余部分一致（{a.light}的手机照片），保留毛孔、雀斑和皮肤纹理，不要磨皮。")
    lines.append(f"3. 妆容按图2：{makeup}。")
    lines.append(f"4. 表情：{a.expr}；只动嘴巴、笑肌和眼周，脸型、下巴和五官比例仍然是图1这张脸。")
    if a.acc:
        lines.append(f"5. 配饰：{a.acc}，按素材图的样子补在她脸上，不要遮住眼睛以外的五官。")
    lines.append("头的位置、角度、大小、视线、头发和背景构图都和图1一致。输出和图1同样大小的正方形图，不要裁切、不要缩放。")
    paths = [str(pasted.relative_to(root)), str(closeup.relative_to(root))]
    return "\n".join(lines), paths, f"{job.relative_to(root)}/base/{stem}_融合.png"


# ---------- 产品套图（不用模特）----------
# 场景按「第几张」轮换：同一个序号的变化在不同颜色上用同一个场景，多色一套图看起来是统一的
HANG_SCENES = [
    "浅米色墙面前的原木衣架杆，左边窗户进来的柔和自然光",
    "白色墙角的墙挂钩，旁边有一小盆绿植，上午柔和的侧光",
    "浅灰色亚麻窗帘前的金属细衣架杆，阴天均匀的自然光",
    "暖白色墙面的衣帽间一角，暖色台灯和窗光混合",
]
FLAT_SCENES = [
    "米白色亚麻床单上，旁边放一本合上的书和一小束干花",
    "浅色原木地板上，角落放一个帆布包的一角",
    "白色棉质床单上，旁边放一杯咖啡和一副墨镜",
    "浅灰色针织毯上，旁边放一顶草帽的一角",
]
CLOSE_SCENES = [
    "自然窗光从左上方照进来，浅景深",
    "柔和的侧光，背景是虚化的米白色床单",
    "均匀的阴天自然光，背景是虚化的原木桌面",
]


def first(folder, *prefixes):
    files = sorted(f for f in folder.iterdir() if f.suffix.lower() in IMG_EXT)
    for pre in prefixes:
        for f in files:
            if f.stem.lower().startswith(pre):
                return f
    return None


def color_name(folder):
    spec = folder / "spec.md"
    line = line_value(spec.read_text(encoding="utf-8"), "- 颜色") if spec.exists() else ""
    return f"「{folder.name}」（{line}）" if line else f"「{folder.name}」"


def garment_text(folder, fallback):
    spec = folder / "spec.md"
    if spec.exists() and section(spec.read_text(encoding="utf-8"), "衣服"):
        return section(spec.read_text(encoding="utf-8"), "衣服")
    base = section(fallback.read_text(encoding="utf-8"), "衣服")
    return base + "\n（上面是同款另一个颜色的描述：版型和细节照它，颜色只按图1，「颜色」那一行不适用。）"


def product_row(md, no):
    for line in md.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 5 and cells[0].isdigit() and int(cells[0]) == no:
            return cells[1], cells[2], int(cells[3]) if cells[3].isdigit() else 1, cells[4]
    sys.exit(f"job.md 的逐张任务表里没有序号 {no}")


def product(a, root, job, md):
    kind, where, var, out = product_row(md, a.no)
    colors = [root / c.strip() for c in re.split(r"[、,，]", field(md, "颜色")) if c.strip()]
    with_spec = [c / "spec.md" for c in colors if (c / "spec.md").exists()]
    main_spec = with_spec[0] if with_spec else (root / field(md, "产品目录")) / "spec.md"
    if not main_spec.exists():
        sys.exit("选中的颜色都没有 spec.md，先按 library/originals/产品档案模板.md 写一份")
    ratio = "1:1 方图" if "1:1" in field(md, "输出") else "3:4 竖图"
    bg = field(md, "场景背景")
    bg = None if bg in ("", "无") else bg
    scene_description = field(md, "背景描述")
    i = var - 1
    lines, paths = [], []
    if a.fix:
        lines.append(f"上一张的问题：{a.fix}。这次把这一处改对，其余要求和下面完全一样。")

    if kind == "多色合拍":
        fronts = [(c, first(c, "front")) for c in colors][: 4 if bg else 5]
        paths = [str(f.relative_to(root)) for _, f in fronts if f]
        n = len(paths)
        hang = var % 2 == 1
        lines.append(f"图1 到图{n} 是同一款衣服的 {n} 个颜色，款式、版型、长度和所有细节完全一样，只有颜色不同：" + "；".join(f"图{k + 1} 是{color_name(c)}" for k, (c, _) in enumerate(fronts)) + "。")
        if bg:
            paths.append(bg)
            lines.append(f"图{n + 1} 是场景，只用它的背景、光线和氛围，不要用它里面的任何衣服。")
        scene = "图%d 的场景里" % (n + 1) if bg else (HANG_SCENES[i % len(HANG_SCENES)] if hang else FLAT_SCENES[i % len(FLAT_SCENES)])
        how = f"用同样的原木衣架并排挂在{scene}，衣服之间留一点间隔，全部正面朝镜头" if hang else f"整齐地并排平铺在{scene}，俯拍，每件都摊平、袖子自然摆放"
        lines.append(f"拍一张这 {n} 件衣服的合照：{how}，每一件都完整入镜。")
        style = "\n".join(l for l in section(main_spec.read_text(encoding="utf-8"), "衣服").splitlines() if not l.lstrip().startswith("- 颜色"))
        lines.append("每件衣服的颜色、面料纹理和光泽只按它自己的那张图，颜色不能互相串，也不能变成别的颜色；件数正好是 %d 件，不多不少。款式细节按下面的描述（颜色不按它）：\n%s" % (n, style))
    else:
        folder = root / where
        front = first(folder, "front")
        back = first(folder, "back")
        key = first(folder, "detail_2")
        fabric = first(folder, "detail_1_flat", "detail_1", "detail_3")
        garment = garment_text(folder, main_spec)
        if kind.endswith("背面"):
            if not back:
                sys.exit(f"{folder} 里没有 back.jpg，出不了背面图")
            base, side = back, "背面"
        else:
            base, side = front, "正面"
        if kind == "领口特写":
            base = key or front
        elif kind == "面料特写":
            base = fabric or front
        refs = [(base, "要编辑的底图，也是这件衣服的样子")]
        if bg:
            refs.append((root / bg, "场景，只用它的背景、光线和氛围，不要用它里面的任何衣服"))
        for f, r in ((front, "这件衣服的正面"), (key, "这件衣服最重要的设计的特写，这一块的形状、宽度和走向必须和它一样"),
                     (fabric, "面料近拍，衣服表面近看要和它一样：绒毛长短、针眼粗细、混色颗粒和光泽都不能变；布料的弯折、卷圈不是图案")):
            if f and f != base and len(refs) < 5:
                refs.append((f, r))
        paths = [str(f.relative_to(root)) for f, _ in refs]
        label = field(md, "主标参考")
        if field(md, "带主标") == "是" and label not in ("", "无"):
            if len(paths) >= 5:
                paths.pop()
                refs.pop()
            paths.append(label)
            refs.append((root / label, "本产品品牌主标近拍，只用于还原主标，不作为整件衣服或背景"))
        lines.append("".join(f"图{k + 1} 是{r}。" for k, (_, r) in enumerate(refs)))
        has_bg = bg and kind.startswith(("挂拍", "平铺"))
        if kind.startswith("挂拍"):
            scene = "图2 的场景里" if has_bg else (scene_description or HANG_SCENES[i % len(HANG_SCENES)])
            lines.append(f"编辑图1，把这件衣服拍成一张挂拍照：用原木衣架挂在{scene}，{side}朝镜头。衣服自然下垂，有真实的重量感和轻微的褶皱，整件完整入镜，上方露出衣架钩，下方留一点空白。")
        elif kind.startswith("平铺"):
            scene = "图2 的场景里" if has_bg else (scene_description or FLAT_SCENES[i % len(FLAT_SCENES)])
            lines.append(f"编辑图1，把这件衣服拍成一张平铺图：{side}朝上平铺在{scene}，相机在正上方俯拍。衣服摊平整理好，袖子自然摆放，整件完整入镜；道具只放在角落，不能压住衣服，不超过画面的 15%。")
        elif kind == "领口特写":
            lines.append(f"编辑图1，把它拍成这件衣服领口和关键设计的近拍特写：这一块的形状、走线、宽度、扣子和面料纹理完全按图1，不改任何结构，周围露出一点衣身。{CLOSE_SCENES[i % len(CLOSE_SCENES)]}，真实的微距实拍质感。")
        elif kind == "面料特写":
            lines.append(f"编辑图1，把它拍成这件衣服面料的近拍特写：表面纹理完全按图1，绒毛长短、针眼粗细、混色颗粒和光泽都不能变；面料可以自然起伏，显示出柔软度，但不要拧成卷圈，也不要出现图案。{CLOSE_SCENES[i % len(CLOSE_SCENES)]}，真实的微距实拍质感。")
        else:
            sys.exit(f"不认识的类型：{kind}")
        if bg and kind in ("领口特写", "面料特写"):
            lines.append("背景、光线和氛围以图2场景为准，保留图1的真实结构和纹理，不取图2里的衣服、文字和标识。")
        lines.append(f"衣服本身完全按图1和下面的描述还原，不要美化：\n{garment}")
    extra = re.search(r"^## 补充要求\s*\n(.*?)(?=^## |\Z)", md, re.M | re.S)
    if extra and extra.group(1).strip() not in ("", "无"):
        lines.append("补充要求：" + extra.group(1).strip())
    lines.append(f"画面：{ratio}，衣服居中，四周留出空间方便裁切。真实的相机实拍质感，像小红书博主自己拍的，不要电商白底棚拍感，不要 3D 渲染感。")
    lines.append("禁止：改变衣服的颜色、材质光泽、长度、版型、纹路、印花、扣子数量、领型；把褶皱抚平或面料变亮；出现吊牌、人、手、模特或人台；加额外文字、水印或贴纸。")
    lines.append(label_rule(md))
    return "\n".join(lines), paths, f"{job.relative_to(root)}/out/{out}"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="step", required=True)
    c = sub.add_parser("clothes")
    c.add_argument("job")
    c.add_argument("no", type=int)
    c.add_argument("--old", required=True)
    c.add_argument("--side", action="store_true")
    c.add_argument("--mirror", action="store_true")
    c.add_argument("--back", action="store_true", help="不管任务单，强制附背面图")
    c.add_argument("--keep", default="")
    c.add_argument("--fix", default="")
    f = sub.add_parser("fuse")
    f.add_argument("job")
    f.add_argument("no", type=int)
    f.add_argument("--light", required=True)
    f.add_argument("--expr", required=True)
    f.add_argument("--acc", default="")
    f.add_argument("--fix", default="")
    g = sub.add_parser("product")
    g.add_argument("job")
    g.add_argument("no", type=int)
    g.add_argument("--fix", default="")
    a = p.parse_args()

    job = Path(a.job).resolve()
    root = job.parent.parent
    md = (job / "job.md").read_text(encoding="utf-8")
    prompt, paths, save_as = {"clothes": clothes, "fuse": fuse, "product": product}[a.step](a, root, job, md)
    for q in paths:
        if not (root / q).exists():
            print(f"提醒：找不到附图 {q}", file=sys.stderr)
    data = {"prompt": prompt, "referenced_image_paths": [str(root / q) for q in paths], "save_as": str(root / save_as)}
    (job / "prompts").mkdir(exist_ok=True)
    (job / "prompts" / f"{a.no:02d}_{a.step}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
