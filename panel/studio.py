#!/usr/bin/env python3
"""女装工作台的命令行入口：让 Codex 在对话里一句话操作工作台（和网页按钮走同一套接口）。

  python3 studio.py status                      # 一屏看全：新款进度、AI 任务、待审稿、排期
  python3 studio.py add 正面.jpg [吊牌.jpg ...]  # 拖照片建新款，AI 自动读吊牌填资料、写档案
  python3 studio.py photo <款> back 背面.jpg     # 补照片：front/back/detail_1/detail_2/tag
  python3 studio.py checks <款>                  # 看检查要点（标了「待确认」的要对着实物看）
  python3 studio.py confirm <款>                 # 档案核对无误
  python3 studio.py info <款> --texture 软糯不扎 --pain 小个子穿长外套显矮   # 填好就自动给这款出 5 条选题
  python3 studio.py shoot <款> [--style mirror|street|own|自己加的组名] [--model 小禾|--ai|--hide|--still] [--refs 1,3,4] [--split] [--no-write]
                                  不指定时按这个号上次的拍法；--still 是参考图里没有人的静物（挂拍、平铺）
  python3 studio.py topics [--gen [--node 双11]] [--add "选题"]   # 看选题库 / 联网出 10 条 / 手动加
  python3 studio.py write <款> [--images a.png,b.png] [--topic 编号或几个字] [--split]
  python3 studio.py drafts                       # 待审稿件和问题
  python3 studio.py show <稿>                    # 看一篇稿
  python3 studio.py edit <稿> [--title T] [--body-file F] [--tags "a b c"]
  python3 studio.py revise <稿> "开头再口语一点"  # 让 AI 按意见改
  python3 studio.py approve <稿> [--at auto|now|"10-22 20:00"] [--no-ai] [--yes]   # 到点工作台开浏览器发，Mac 要开着
  python3 studio.py models | refs

<款> 可以写名称里的几个字或编号；<稿> 写稿件编号或标题里的几个字。默认用第一个女装账号，--account 指定。
"""
import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

STUDIO = Path(os.environ.get("XHS_STUDIO_HOME", str(Path.home() / ".xhs-studio")))
HERE = Path(__file__).resolve().parent


def base_url():
    f = STUDIO / "url.txt"
    url = f.read_text("utf-8").strip() if f.is_file() else "http://127.0.0.1:17865/"
    return url if url.endswith("/") else url + "/"


def call(path, body=None):
    url = base_url() + path.lstrip("/")
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read().decode("utf-8"))
        except Exception:
            j = {}
        die(j.get("error") or "工作台返回错误 %s" % e.code)
    except (urllib.error.URLError, OSError):
        die("连不上工作台。先运行：bash %s/start.sh" % HERE)


def die(msg):
    print("✗ " + msg)
    sys.exit(1)


def fmt(ms):
    d = datetime.datetime.fromtimestamp(ms / 1000)
    return "%d月%d日 %02d:%02d" % (d.month, d.day, d.hour, d.minute)


def state():
    return call("api/state")


def pick_account(D, want):
    accs = D["accounts"]
    if not accs:
        die("工作台里还没有账号，到网页「设置」里加一个")
    if want:
        a = next((a for a in accs if str(a["id"]) == str(want) or want in a["name"]), None)
        if not a:
            die("找不到账号：%s（有：%s）" % (want, "、".join(a["name"] for a in accs)))
        return a
    return next((a for a in accs if any(w in a["name"] for w in "女裙装")), accs[0])


def find_item(D, key):
    items = [i for i in D["items"] if i.get("lockName")]
    hit = [i for i in items if i["id"] == key] or [i for i in items if key in i["name"]] or [i for i in items if key in (i.get("code") or "")]
    if not hit:
        die("找不到新款「%s」。现有：%s" % (key, "、".join(i["name"] for i in items) or "无"))
    if len(hit) > 1:
        die("「%s」对上了好几件：%s，说得再具体点" % (key, "、".join(i["name"] for i in hit)))
    return hit[0]


def find_draft(D, acc, key):
    rs = [r for r in D["reviews"] if str(r["account_id"]) == str(acc["id"]) and r["status"] not in ("驳回", "已发布")]
    hit = [r for r in rs if str(r["id"]) == key] or [r for r in rs if key in (r.get("title") or "")]
    if not hit:
        die("找不到稿件「%s」" % key)
    if len(hit) > 1:
        die("「%s」对上了好几篇：%s" % (key, "、".join("#%s %s" % (r["id"], r["title"]) for r in hit)))
    return hit[0]


def data_url(path):
    import base64
    import mimetypes
    p = Path(path).expanduser()
    if not p.is_file():
        die("找不到图片：%s" % path)
    mt = mimetypes.guess_type(str(p))[0] or "image/jpeg"
    return "data:%s;base64,%s" % (mt, base64.b64encode(p.read_bytes()).decode())


def check_draft(D, r):
    errs, warns, t, b = [], [], (r.get("title") or "").strip(), r.get("body") or ""
    if not t:
        errs.append("缺标题")
    elif len(t) > 18:
        errs.append("标题 %d 字，超过 18" % len(t))
    if not b.strip():
        errs.append("缺正文")
    elif len(b) > 888:
        errs.append("正文 %d 字，超过 888" % len(b))
    bad = [w for w in D["fashion"]["riskErrors"] if w in t + b]
    if bad:
        errs.append("违规词：" + "、".join(bad))
    if not r.get("images"):
        errs.append("没有图片")
    n = len(r.get("tags") or [])
    if n < 5 or n > 10:
        warns.append("话题 %d 个，建议 8–10" % n)
    return errs, warns


def draft_state(D, r):
    if r["status"] == "已发布":
        return "已发布"
    if any(str(s["review"]) == str(r["id"]) and s["status"] in ("pending", "running") for s in D["schedule"]):
        return "已排期"
    if str(r["id"]) in D["ready"]:
        return "审过待排期"
    return "待审"


# ---------------------------------------------------------------- commands
def c_status(a):
    D = state()
    acc = pick_account(D, a.account)
    print("账号：%s（%s）" % (acc["name"], "小红书在线" if acc.get("online") else "小红书掉线，发布前要重新扫码"))
    run = [j for j in D["jobs"] if j["status"] in ("queued", "running")]
    print("\nAI 任务：" + ("空闲" if not run else "；".join("%s（%s）" % (j["title"], "进行中" if j["status"] == "running" else "排队") for j in run)))
    fail = [j for j in D["jobs"] if j["status"] == "failed"][:2]
    for j in fail:
        print("  最近失败：%s —— %s" % (j["title"], j.get("summary", "")))
    items = [i for i in D["items"] if i.get("lockName")]
    print("\n新款：" + ("还没有" if not items else ""))
    for i in items:
        print("  · %s —— %s" % (i["name"], i["stage"]))
    rs = [r for r in D["reviews"] if str(r["account_id"]) == str(acc["id"]) and draft_state(D, r) != "已排期" and r["status"] not in ("驳回", "已发布")]
    print("\n待审稿：%d 篇" % len(rs))
    for r in rs[:8]:
        e, _ = check_draft(D, r)
        print("  #%s %s（%s%s）" % (r["id"], r["title"], draft_state(D, r), "，有问题：" + "；".join(e) if e else ""))
    S = sorted([s for s in D["schedule"] if s["account"] == str(acc["id"]) and s["status"] == "pending"], key=lambda s: s["at"])
    print("\n排期：" + ("没有" if not S else ""))
    for s in S:
        print("  %s  %s" % (fmt(s["at"]), s["title"]))
    ns = D.get("nextSlot", {}).get(str(acc["id"]))
    if ns:
        print("下一个空的发布时段：%s" % fmt(ns))


def c_add(a):
    D = state()
    acc = pick_account(D, a.account) if a.account else None
    photos = {"front": data_url(a.photos[0])}
    if len(a.photos) > 1:
        photos["tag"] = data_url(a.photos[1])
    for slot in ("back", "detail_1", "detail_2"):
        v = getattr(a, slot)
        if v:
            photos[slot] = data_url(v)
    j = call("api/item/new", {"name": a.name or "", "account": acc["id"] if acc else "", "photos": photos})
    print("✓ 已建新款「%s」。%s" % (j["item"]["name"], "AI 正在读吊牌、写档案，约 3–5 分钟；好了用 checks 看检查要点。" if j.get("job") else "找不到锁图工作台，档案要在设置里填好路径后再写。"))


def c_photo(a):
    it = find_item(state(), a.item)
    call("api/item/photo", {"id": it["id"], "slot": a.slot, "data": data_url(a.file)})
    print("✓ 已更新 %s 的 %s 照片。照片换了，档案需要重新核对。" % (it["name"], a.slot))


def c_checks(a):
    it = find_item(state(), a.item)
    if not it.get("hasSpec"):
        die("%s 还没有档案（%s）" % (it["name"], it["stage"]))
    for n, c in enumerate(it["checks"], 1):
        print("%d. [%s] %s%s" % (n, c["level"], c["text"], "  ← 待确认，请对着实物看" if c.get("unsure") else ""))
    for n in it.get("photoNotes") or []:
        print("照片提醒：" + n)
    print("\n档案：%s/spec.md；%s" % (it["dir"], "已核对" if it.get("specOk") else "未核对，没问题就运行 confirm"))


def c_confirm(a):
    it = find_item(state(), a.item)
    call("api/item/checks", {"id": it["id"], "confirmed": True,
                             "checks": [{"level": c["level"], "text": c["text"]} for c in it.get("checks") or []] or None})
    print("✓ %s 的档案已核对，可以出图了（shoot）。" % it["name"])


def c_shoot(a):
    D = state()
    it = find_item(D, a.item)
    acc = pick_account(D, a.account)
    lp = D.get("lastPick") or {}
    mine = (lp.get("byAcc") or {}).get(str(acc["id"])) or {}   # 这个号上次的拍法
    styles = D["styles"]
    if a.style and a.style not in styles and a.style != "own":
        a.style = next((k for k, v in styles.items() if v == a.style), None) or die("没有「%s」这组风格（有：%s）" % (a.style, "、".join(styles.values())))
    style = a.style or mine.get("style") or lp.get("style") or "mirror"
    face = ("ai" if a.ai else "hide" if a.hide else "none" if a.still else "model" if a.model else
            (mine.get("face") if style == mine.get("style") else None) or (lp.get("faceByStyle") or {}).get(style) or ("hide" if style == "mirror" else lp.get("face") or "model"))
    model = a.model or mine.get("model") or lp.get("model") or ""
    models = D["lock"]["models"]
    if face == "model":
        if not model or not any(m["name"] == model for m in models):
            model = (next((m for m in models if not m["missing"]), None) or (models[0] if models else {})).get("name", "")
        if not model:
            die("还没有模特，先在锁图工作台 library/models/ 里加一位，或用 --ai / --hide")
    refs = D["lock"]["refs"]
    if a.refs:
        try:
            chosen = [refs[int(x) - 1] for x in a.refs.split(",")]
        except (ValueError, IndexError):
            chosen = [r for r in refs if any(k and k in Path(r).stem for k in a.refs.split(","))]
    elif style == mine.get("style") and [r for r in mine.get("refs") or [] if r in refs]:
        chosen = [r for r in mine["refs"] if r in refs]
    elif style != "own":
        chosen = [r for r in refs if D["lock"]["refStyles"].get(r) == style][:6]
    else:
        chosen = [r for r in (lp.get("refsByCat") or {}).get(it.get("category") or "其他", []) if r in refs] or refs[:4]
    if not chosen:
        die("参考图库是空的，或者没对上。refs 看编号")
    p = {"item": it["id"], "account": acc["id"], "face": face, "model": model, "refs": chosen, "style": style, "note": a.note or ""}
    if not a.no_write:
        p["autoWrite"] = {"account": acc["id"], "count": 2 if a.split else 1}
    j = call("api/job", {"kind": "lock", "params": p})
    who = {"model": "模特 " + model, "ai": "AI 脸", "hide": "不露脸", "none": "没有人的静物"}[face]
    print("✓ 开始出图：%s，%s，%d 张（%s），约 %d 分钟。%s" % (
        it["name"], who, len(chosen), "、".join(Path(r).stem for r in chosen), j["job"]["estimateMin"],
        "出完自动挑 A 级图写文案，写好进审稿。" if not a.no_write else "出完不自动写文案。"))


def c_write(a):
    D = state()
    it = find_item(D, a.item)
    acc = pick_account(D, a.account)
    if a.images:
        imgs = a.images.split(",")
    else:
        last = next((j for j in it.get("lockJobs") or [] if j["images"]), None)
        if not last and not it.get("realPhotos"):
            die("%s 还没有出好的图，也没有实拍图" % it["name"])
        g = it.get("grades") or {}
        imgs = ([p for p in last["images"] if (g.get(p) or {}).get("grade") == "A"] or last["images"]) if last else []
        imgs += it.get("realPhotos") or []   # 实拍图接在 AI 图后面
    un = [t for t in D["topics"] if str(t["account"]) == str(acc["id"]) and t["status"] == "unused"]
    t = None
    if a.topic:
        t = next((x for x in un if x["id"] == a.topic or a.topic in x["text"]), None) or die("选题库里没找到：%s" % a.topic)
    else:
        t = next((x for x in un if x.get("item") == it["id"]), None)
    call("api/job", {"kind": "write", "params": {"account": acc["id"], "item": it["id"], "images": imgs, "topic": t["id"] if t else "",
                                                "count": 2 if a.split else 1, "note": a.note or ""}})
    print("✓ 已交给 AI 写文案（%d 张图%s），写好进审稿。" % (len(imgs), "，选题「%s」" % t["text"] if t else ""))


def c_info(a):
    D = state()
    it = find_item(D, a.item)
    body = dict(it, topicAccount=pick_account(D, a.account)["id"])
    for k in ("texture", "pain", "modelSize", "goods", "price"):
        if getattr(a, k) is not None:
            body[k] = getattr(a, k)
    j = call("api/item/save", {k: v for k, v in body.items() if not isinstance(v, (list, dict))})
    print("✓ 已保存 %s 的资料。%s" % (it["name"], "材质和痛点填好了，AI 开始给这款出 5 条选题。" if j.get("topicJob") else ""))


def c_topics(a):
    D = state()
    acc = pick_account(D, a.account)
    if a.gen:
        call("api/job", {"kind": "topics", "params": {"account": acc["id"], "count": 10, "node": a.node or ""}})
        print("✓ AI 开始联网找痛点出 10 条选题，大约 3–5 分钟，好了用 topics 看。")
        return
    if a.add:
        call("api/topic", {"action": "add", "account": acc["id"], "text": a.add})
        print("✓ 已加入选题库：%s" % a.add)
        return
    T = [t for t in D["topics"] if str(t["account"]) == str(acc["id"])]
    un = [t for t in T if t["status"] == "unused"]
    print("%s 选题库：%d 条没用，%d 条用过" % (acc["name"], len(un), len(T) - len(un)))
    for t in un:
        print("  %s  %s  %s" % (t["id"], t["text"], " × ".join(x for x in (t.get("scene"), t.get("body")) if x)))
    grid = D["topicGrid"].get(str(acc["id"])) or {}
    gaps = ["%s×%s" % (sc, b) for sc in grid for b in grid[sc] if not grid[sc][b]]
    if gaps:
        print("还空着的格子：" + "、".join(gaps[:12]))


def c_drafts(a):
    D = state()
    acc = pick_account(D, a.account)
    rs = [r for r in D["reviews"] if str(r["account_id"]) == str(acc["id"]) and r["status"] not in ("驳回", "已发布")]
    if not rs:
        print("没有稿件。")
    for r in rs:
        e, w = check_draft(D, r)
        print("#%s %s（%s）%s" % (r["id"], r["title"], draft_state(D, r), "  ✗ " + "；".join(e) if e else "  ⚠ " + "；".join(w) if w else ""))


def c_show(a):
    D = state()
    acc = pick_account(D, a.account)
    r = find_draft(D, acc, a.draft)
    e, w = check_draft(D, r)
    print("#%s %s（%s）\n" % (r["id"], r["title"], draft_state(D, r)))
    others = [t for t in r.get("titles") or [] if t != r["title"]]
    if others:
        print("备选标题：" + " / ".join(others) + "\n")
    print(r["body"] + "\n")
    print("话题：" + " ".join("#" + t for t in r.get("tags") or []))
    print("图片：\n" + "\n".join("  " + p for p in r.get("images") or []))
    print(("问题：" + "；".join(e + w)) if e or w else "检查通过")


def c_edit(a):
    D = state()
    acc = pick_account(D, a.account)
    r = find_draft(D, acc, a.draft)
    b = {"account": acc["id"], "id": r["id"], "action": "save"}
    if a.title:
        b["title"] = a.title
    if a.body_file:
        b["body"] = Path(a.body_file).read_text("utf-8")
    if a.tags:
        b["tags"] = [t.lstrip("#") for t in a.tags.replace("，", " ").split() if t.strip("#")]
    if len(b) == 3:
        die("没给要改的内容")
    call("api/review", b)
    print("✓ 已保存 #%s" % r["id"])


def c_revise(a):
    D = state()
    acc = pick_account(D, a.account)
    r = find_draft(D, acc, a.draft)
    call("api/review", {"account": acc["id"], "id": r["id"], "action": "rewrite", "note": a.note})
    print("✓ 已交给 AI 按「%s」改 #%s" % (a.note, r["id"]))


def parse_at(s):
    if s in (None, "", "auto"):
        return "auto"
    if s == "now":
        return None
    now = datetime.datetime.now()
    for f in ("%Y-%m-%d %H:%M", "%m-%d %H:%M", "%H:%M"):
        try:
            d = datetime.datetime.strptime(s, f)
        except ValueError:
            continue
        if f == "%m-%d %H:%M":
            d = d.replace(year=now.year)
        elif f == "%H:%M":
            d = now.replace(hour=d.hour, minute=d.minute, second=0, microsecond=0)
        return int(d.timestamp() * 1000)
    die("时间格式看不懂：%s（写 auto、now、20:00 或 10-22 20:00）" % s)


def c_approve(a):
    D = state()
    acc = pick_account(D, a.account)
    r = find_draft(D, acc, a.draft)
    e, _ = check_draft(D, r)
    if e:
        die("这篇还有问题，先改：" + "；".join(e))
    at = parse_at(a.at)
    when = D["nextSlot"].get(str(acc["id"])) if at == "auto" else at
    if draft_state(D, r) == "待审":
        call("api/review", {"account": acc["id"], "id": r["id"], "action": "ready"})
    q = call("api/publish/preview", {"account": acc["id"], "id": r["id"]})
    mark_ai = not a.no_ai
    print("「%s」%s发布%s；%s「笔记含 AI 合成内容」；不扣点。" % (r["title"], ("排在 %s " % fmt(when)) if when else "现在就",
                                         "（到点工作台开浏览器直接发，那时 Mac 要开着、工作台要开着）" if when else "", "勾选" if mark_ai else "不勾选"))
    if not a.yes:
        print("用户同意后加 --yes 再运行一次。")
        return
    j = call("api/publish", {"account": acc["id"], "id": r["id"], "at": at, "quote": q["quote"], "markAi": mark_ai})
    print("✓ " + ("已排期 %s" % fmt(j["entry"]["at"]) if at else "开始发布，进度用 status 看"))


def c_models(a):
    for m in state()["lock"]["models"]:
        print("%s %s %s" % (m["name"], m["size"], "照片齐了" if not m["missing"] else "缺" + "、".join(m["missing"])))


def c_refs(a):
    D = state()
    for n, r in enumerate(D["lock"]["refs"], 1):
        print("%d. %s  [%s]" % (n, Path(r).stem, D["styles"].get(D["lock"]["refStyles"].get(r), "其他")))


def main():
    ap = argparse.ArgumentParser(description="女装工作台命令行")
    ap.add_argument("--account")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("status")
    x = sp.add_parser("add"); x.add_argument("photos", nargs="+"); x.add_argument("--name"); x.add_argument("--back"); x.add_argument("--detail_1"); x.add_argument("--detail_2")
    x = sp.add_parser("photo"); x.add_argument("item"); x.add_argument("slot", choices=["front", "back", "detail_1", "detail_2", "tag"]); x.add_argument("file")
    x = sp.add_parser("checks"); x.add_argument("item")
    x = sp.add_parser("confirm"); x.add_argument("item")
    x = sp.add_parser("info"); x.add_argument("item"); x.add_argument("--texture"); x.add_argument("--pain"); x.add_argument("--modelSize"); x.add_argument("--goods"); x.add_argument("--price")
    x = sp.add_parser("topics"); x.add_argument("--gen", action="store_true"); x.add_argument("--node"); x.add_argument("--add")
    x = sp.add_parser("shoot"); x.add_argument("item"); x.add_argument("--style"); x.add_argument("--model"); x.add_argument("--ai", action="store_true"); x.add_argument("--hide", action="store_true"); x.add_argument("--still", action="store_true")
    x.add_argument("--refs"); x.add_argument("--split", action="store_true"); x.add_argument("--no-write", action="store_true"); x.add_argument("--note")
    x = sp.add_parser("write"); x.add_argument("item"); x.add_argument("--images"); x.add_argument("--topic"); x.add_argument("--split", action="store_true"); x.add_argument("--note")
    sp.add_parser("drafts")
    x = sp.add_parser("show"); x.add_argument("draft")
    x = sp.add_parser("edit"); x.add_argument("draft"); x.add_argument("--title"); x.add_argument("--body-file"); x.add_argument("--tags")
    x = sp.add_parser("revise"); x.add_argument("draft"); x.add_argument("note")
    x = sp.add_parser("approve"); x.add_argument("draft"); x.add_argument("--at"); x.add_argument("--no-ai", action="store_true"); x.add_argument("--yes", action="store_true")
    sp.add_parser("models"); sp.add_parser("refs")
    for sub in sp.choices.values():
        sub.add_argument("--account", default=argparse.SUPPRESS)
    a = ap.parse_args()
    globals()["c_" + a.cmd](a)


if __name__ == "__main__":
    main()
