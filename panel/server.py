#!/usr/bin/env python3
"""女装小红书工作台（本机服务）。

在 Codex 里说“打开工作台”时由 xhs-autopilot 启动，然后在 Codex 内置浏览器里打开
http://127.0.0.1:17865（被占用就自动换下一个端口，实际地址写在 ~/.xhs-studio/url.txt）。只用标准库，macOS 自带的 python3 即可运行。

分工：
- 数据：账号、稿件、排期都存在 ~/.xhs-studio/state.json，不依赖 RedBeacon。
- 出图：交给锁图工作台 xhs-lock-studio（codex exec 在那个文件夹里跑）。
- 写文案、按意见改稿：调用 `codex exec`，只用 GPT/Codex 订阅；Codex 把稿件写成 JSON 文件，工作台读回来。
- 发布：xhs-autopilot/publisher/xhs_publish.py 自己开浏览器发，扫码登录，不扣点。
  不用小红书平台的定时发布：到点由本服务打开浏览器直接发，所以那时 Mac 要开着、工作台要开着；
  有待发的稿时用 caffeinate 不让 Mac 睡眠（合盖仍会睡）。
- 界面四大块：产品图（照片资料 / 出图 / 出好的图 / 模特和参考图库）、文案（选题库 / 审稿 / 写新的一篇 / 文案设定）、
  发布（待发布 / 排期日历 / 账号登录）、历史记录。

用法:
  python3 server.py               # 启动（默认 127.0.0.1:17865）
  python3 server.py --open        # 启动并用系统浏览器打开
  python3 server.py --demo        # 演示数据，不调用 Codex、不开浏览器发布
"""
import argparse
import base64
import datetime
import json
import mimetypes
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path.home()
STUDIO = Path(os.environ.get("XHS_STUDIO_HOME", str(HOME / ".xhs-studio")))
# 各店自己的场景/身材/类目设置放 fashion.json（不进仓库）；没有就用仓库里的示例
FASHION_FILE = Path(os.environ.get("XHS_FASHION_FILE") or (HERE / "fashion.json" if (HERE / "fashion.json").exists() else HERE / "fashion.example.json"))
FASHION = json.loads(FASHION_FILE.read_text("utf-8"))
# 小红书女装常见限流词：内置一份，和店里 fashion.json 里自己加的合在一起用
RISK_ERRORS = ["最好", "第一", "绝对", "最佳", "顶级", "极致", "首选", "唯一", "100%", "永不", "永久", "万能", "全网最低", "最低价", "史上",
               "平替", "一比一", "1:1", "大牌同款", "高仿", "原单", "专柜", "复刻", "微信", "vx", "淘宝", "拼多多", "私信下单", "链接在"]
RISK_WARNS = ["显瘦", "封神", "闭眼入", "天花板", "必买", "必入", "断货", "爆款", "yyds", "绝绝子"]
FASHION["riskErrors"] = list(dict.fromkeys(RISK_ERRORS + FASHION.get("riskErrors", [])))
FASHION["riskWarns"] = list(dict.fromkeys(RISK_WARNS + FASHION.get("riskWarns", [])))
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".heic"}
DEMO = False
LOCK = threading.RLock()
PUBLISH_LOCK = threading.Lock()
PROCS = {}
QUOTES = {}
LOGIN_PROCS = {}
APP_ID = "xhs-studio"
DEFAULT_PORT = 17865
PUBLISHER = HERE.parent / "publisher" / "xhs_publish.py"
TOPICS_GUIDE = HERE.parent / "references" / "topics.md"
AWAKE = {"proc": None}
STYLES = {"mirror": "对镜自拍", "street": "街拍", "other": "其他"}   # 自带的几组；店里自己加的组存在 state.json 的 styleGroups
COVERS = {"none": "不加字", "text": "封面加一行大字", "logo": "每张图右下角放品牌水印"}

DEFAULT_SETTINGS = {
    "codex_path": "",
    "codex_args": "exec --skip-git-repo-check -s danger-full-access",
    "late_grace_min": 30,
    "lock_studio_path": "~/Documents/xhs-lock-studio",
    "mark_ai": True,          # 发布时默认勾选「笔记含 AI 合成内容」（每篇可以单独改）
    "xhs_headless": False,    # 发布时不弹出浏览器窗口
}


def now_ms():
    return int(time.time() * 1000)


def fmt_ms(ms):
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%m-%d %H:%M")


# ---------------------------------------------------------------- state
def state_path():
    return STUDIO / "state.json"


def default_profile():
    """发布用的登录资料夹名。之前在面板里扫过码的（profiles/ 下已有的）直接沿用，不用再扫。"""
    d = STUDIO / "publisher" / "profiles"
    have = sorted(x.name for x in d.iterdir() if x.is_dir()) if d.is_dir() else []
    return have[0] if have else "主号"


def load_state():
    with LOCK:
        try:
            st = json.loads(state_path().read_text("utf-8"))
        except Exception:
            st = {}
        st.setdefault("items", [])
        st.setdefault("schedule", [])
        st.setdefault("jobs", [])
        st.setdefault("ready", {})
        st.setdefault("drafts", [])
        st.setdefault("published", {})
        st.setdefault("xhsLogin", {})
        st.setdefault("settings", {})
        st.setdefault("topics", [])
        st.setdefault("refStyles", {})
        st.setdefault("styleGroups", [])
        if not st.get("accounts"):
            st["accounts"] = [{"id": "1", "name": "女装主号", "profile": default_profile()}]
        if st.get("dataVersion", 0) < 4:
            # 从旧版（稿件在 RedBeacon 里）升级：旧的“审过”和待发排期指向 RedBeacon 的稿件编号，
            # 和这里的新编号会撞，全部作废，免得把别的稿发出去
            st["ready"] = {}
            for e in st["schedule"]:
                if e.get("status") == "pending":
                    e["status"] = "cancelled"
                    e["error"] = "工作台升级，旧排期已取消，请重新排"
            st["published"] = {("old-%s" % k): v for k, v in st["published"].items()}
        if st.get("dataVersion", 0) < 5:
            # v5 不再交给小红书平台定时，错过发布时间不补发：旧的默认 120 分钟改成 30
            if st["settings"].get("late_grace_min") == 120:
                st["settings"]["late_grace_min"] = 30
            st["dataVersion"] = 5
        for k, v in DEFAULT_SETTINGS.items():
            st["settings"].setdefault(k, v)
        return st


def save_state(st):
    with LOCK:
        STUDIO.mkdir(parents=True, exist_ok=True)
        st["jobs"] = st["jobs"][-60:]
        st["schedule"] = [s for s in st["schedule"] if s.get("status") == "pending" or now_ms() - s.get("at", 0) < 30 * 864e5]
        tmp = state_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, state_path())


def mutate(fn):
    with LOCK:
        st = load_state()
        out = fn(st)
        save_state(st)
        return out


# ---------------------------------------------------------------- tools
def find_bin(name, configured, extra):
    if configured and Path(configured).expanduser().exists():
        return str(Path(configured).expanduser())
    p = shutil.which(name)
    if p:
        return p
    for c in extra:
        c = Path(c).expanduser()
        if c.exists():
            return str(c)
    return None


def codex_bin():
    return find_bin("codex", load_state()["settings"].get("codex_path"),
                    ["/Applications/Codex.app/Contents/Resources/codex", "~/.local/bin/codex",
                     "/opt/homebrew/bin/codex", "/usr/local/bin/codex"])


def parse_json_loose(text):
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        s, e = text.rfind(opener), text.rfind(closer)
        start = text.find(opener)
        for begin in (s, start):
            if begin != -1 and e > begin:
                try:
                    return json.loads(text[begin:e + 1])
                except Exception:
                    continue
    return None


def xhs_cli(args, timeout=600):
    """跑独立发布脚本 publisher/xhs_publish.py，返回 (ok, data, raw)。它最后一行输出 JSON。"""
    if DEMO:
        return demo_xhs(args)
    if not PUBLISHER.is_file():
        return False, None, "找不到发布脚本：%s" % PUBLISHER
    env = dict(os.environ, XHS_PUBLISHER_HOME=str(STUDIO / "publisher"))
    try:
        p = subprocess.run([sys.executable, str(PUBLISHER)] + [str(a) for a in args], capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return False, None, "发布脚本超时"
    data = None
    for line in reversed((p.stdout or "").strip().splitlines()):
        data = parse_json_loose(line.strip())
        if data:
            break
    ok = p.returncode == 0 and isinstance(data, dict) and data.get("ok") is True
    raw = (data or {}).get("error") or ((p.stdout or "") + (p.stderr or ""))[-1500:]
    return ok, data, raw


def xhs_profile(aid):
    acc = next((a for a in load_state()["accounts"] if str(a["id"]) == str(aid)), None)
    return (acc or {}).get("profile") or "acc%s" % aid


def set_login(aid, logged_in):
    mutate(lambda s: s["xhsLogin"].__setitem__(str(aid), {"loggedIn": bool(logged_in), "checkedAt": now_ms()}))


def xhs_check(aid):
    ok, data, raw = xhs_cli(["check", "--account", xhs_profile(aid)], timeout=180)
    if not ok:
        raise ValueError("检查小红书登录失败：%s" % raw[-300:])
    set_login(aid, data.get("loggedIn"))
    return bool(data.get("loggedIn"))


def write_tmp_json(obj, name):
    d = STUDIO / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    f = d / ("%s-%s.json" % (name, uuid.uuid4().hex[:8]))
    f.write_text(json.dumps(obj, ensure_ascii=False, indent=1), "utf-8")
    return f


# ---------------------------------------------------------------- 数据（账号、稿件、发布记录，都在 state.json）
def split_tags(s):
    if not s:
        return []
    if isinstance(s, list):
        return [str(x).lstrip("#").strip() for x in s if str(x).lstrip("#").strip()]
    return [t for t in re.split(r"[、,，#\s]+", s) if t]


def read_data():
    st = load_state()
    login = st["xhsLogin"]
    accounts = [dict({"id": a["id"], "name": a["name"], "online": bool(login.get(str(a["id"]), {}).get("loggedIn")),
                      "checkedAt": login.get(str(a["id"]), {}).get("checkedAt"),
                      "cover": account_cover(a), "logo": account_logo(a), "persona": a.get("persona") or "",
                      "startedAt": a.get("startedAt") or 0, "rhythm": account_rhythm(a),
                      "realFirst": bool((PERSONAS.get(a.get("persona") or "") or {}).get("realFirst"))},
                     **{k: a.get(k, "") for k in COPY_KEYS}) for a in st["accounts"]]
    reviews = sorted((dict(d) for d in st["drafts"]), key=lambda d: d["id"], reverse=True)
    names = {str(a["id"]): a["name"] for a in st["accounts"]}
    for r in reviews:
        if r["status"] != "已发布":
            r["clash"] = draft_clash(st, r, names)
    archive = sorted(({"account_id": v["account"], "title": v["title"], "at": v["at"], "shot": v.get("shot", ""),
                       "review": k, "ai": v.get("ai"),
                       "published_at": fmt_ms(v["at"]) + ("（交给小红书定时）" if v.get("platform") else "")}
                      for k, v in st["published"].items()), key=lambda x: x["at"], reverse=True)[:60]
    return {"accounts": accounts, "reviews": reviews, "archive": archive}


def review_by_id(rid):
    return next((dict(d) for d in load_state()["drafts"] if str(d["id"]) == str(rid)), None)


def add_drafts(aid, item, drafts, origin):
    """把 Codex 写好的稿件存进来，返回新稿件 id。"""
    ids = []

    def f(s):
        for d in drafts:
            nid = max([x["id"] for x in s["drafts"]] + [s.get("lastDraftId", 0)]) + 1
            s["lastDraftId"] = nid
            s["drafts"].append({"id": nid, "account_id": str(aid), "item": item or "", "status": "未审核",
                                "title": d["title"], "body": d["body"], "tags": d["tags"], "images": d["images"],
                                "titles": d.get("titles") or [], "comment": d.get("comment", ""), "origin": origin, "created_at": fmt_ms(now_ms())})
            ids.append(nid)
    mutate(f)
    return ids


def update_draft(rid, **kw):
    def f(s):
        for d in s["drafts"]:
            if str(d["id"]) == str(rid):
                d.update(kw)
    mutate(f)


def clean_draft(d, images=True, account=None):
    """Codex 写回的一篇稿：只取需要的字段并检查。"""
    if not isinstance(d, dict):
        raise ValueError("稿件格式不对")
    out = {"title": str(d.get("title") or "").strip(), "body": str(d.get("body") or "").strip(),
           "tags": split_tags(d.get("tags"))[:10],
           "titles": [str(t).strip()[:18] for t in (d.get("titles") or []) if str(t).strip()][:3],
           "comment": str(d.get("comment") or "").strip()[:300]}
    if images:
        out["images"] = [str(Path(x).expanduser()) for x in (d.get("images") or [])]
    if account is not None:
        out["account_id"] = str(account)
    validate_draft(out, images=images)
    out.pop("account_id", None)
    return out


def draft_clash(st, r, names=None):
    """矩阵防撞车：这篇的图有没有在别的号用过；标题、开头和别的号同款那篇是不是太像。"""
    import difflib
    names = names or {str(a["id"]): a["name"] for a in st["accounts"]}
    aid = str(r["account_id"])
    others = [d for d in st["drafts"] if str(d["account_id"]) != aid]
    used = []
    for p in r.get("images") or []:
        d = next((d for d in others if p in (d.get("images") or [])), None)
        if d:
            used.append({"img": p, "account": names.get(str(d["account_id"]), "别的号")})
    similar = None
    head = lambda d: re.sub(r"\s+", "", d.get("body") or "")[:40]
    pool = [d for d in others if d.get("item") == r["item"]] if r.get("item") else others[-50:]
    for d in pool:
        t = difflib.SequenceMatcher(None, r.get("title") or "", d.get("title") or "").ratio()
        b = difflib.SequenceMatcher(None, head(r), head(d)).ratio() if head(r) and head(d) else 0
        if t >= 0.6 or b >= 0.7:
            similar = {"account": names.get(str(d["account_id"]), "别的号"), "title": d.get("title") or "",
                       "what": "标题" if t >= 0.6 else "开头"}
            break
    return {"images": used, "similar": similar}


def import_topics(p, data):
    """Codex 出的选题入库：去掉空的、超长的、和已有选题重复的。返回新选题 id。"""
    raw = data.get("topics") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        raise ValueError("选题文件里没有选题")
    added = []

    def f(s):
        seen = {t["text"] for t in s["topics"] if str(t["account"]) == str(p["account"])}
        seen |= {v["title"] for v in s["published"].values()}
        for x in raw[:p["count"] + 5]:
            if not isinstance(x, dict):
                continue
            text = str(x.get("text") or "").strip().strip("「」\"")
            if not text or len(text) > 25 or text in seen:
                continue
            seen.add(text)
            t = {"id": "tp" + uuid.uuid4().hex[:8], "account": str(p["account"]), "text": text,
                 "scene": x.get("scene") if x.get("scene") in FASHION["scenes"] else "",
                 "body": x.get("body") if x.get("body") in FASHION["bodies"] else "",
                 "why": str(x.get("why") or "").strip()[:60], "item": p.get("forItem") or "",
                 "node": p.get("node") or "", "source": "item" if p.get("forItem") else "ai",
                 "status": "unused", "createdAt": now_ms()}
            s["topics"].append(t)
            added.append(t["id"])
    mutate(f)
    if not added:
        raise ValueError("出的选题都和已有的重复了，没有新选题")
    return added


# ---------------------------------------------------------------- prompts for Codex
FASHION_RULES = ("女装合规：只用单品资料里的真实信息，不编造价格、面料成分、功效、销量和用户评价；"
                 "不出现 大牌同款、平替、一比一、高仿、原单、专柜 等字样；不用 绝对、第一、最好、永不起球 这类极限词；"
                 "身材相关表述友善，不制造身材焦虑。"
                 "这些词标题、正文、评论里一个都不能出现：%s。" % "、".join(FASHION["riskErrors"]))
COPY_GUIDE = HERE.parent / "references" / "copywriting.md"
GAOSAKA_GUIDE = HERE.parent / "references" / "gaosaka-style.md"
COPY_STYLES = {"gaosaka": "高坂风格 · 品牌主理人", "standard": "日常分享"}


def item_facts(item):
    if not item:
        return ""
    keys = [("code", "款号"), ("name", "名称"), ("category", "品类"), ("colors", "颜色"), ("sizes", "尺码"),
            ("fabric", "面料成分"), ("texture", "材质手感"), ("pain", "痛点（这款解决什么）"),
            ("price", "价格"), ("fit", "版型和选码建议"), ("points", "卖点")]
    parts = ["%s：%s" % (label, item.get(k)) for k, label in keys if item.get(k)]
    return "单品资料（唯一可信来源）：" + "；".join(parts) + "。"


def find_item(st, iid):
    return next((x for x in st["items"] if x.get("id") == iid), None)


def account_name(aid):
    for a in load_state()["accounts"]:
        if str(a["id"]) == str(aid):
            return a["name"]
    return "%s号" % aid


COPY_KEYS = {"writeStyle": "写作风格", "position": "账号定位", "tone": "口吻", "must": "每篇必写", "banned": "不要用这些词", "tagMix": "话题配比"}
# 三个号的文案设定预设（按主页风格整理），在「文案设定」一键套用后还能改
COPY_PRESETS = {
    "the reels": {"position": "独立女装工作室的日常：一件衣服的面料、做工和版型讲透",
                  "tone": "像做衣服的人在聊料子，克制、具体，不喊口号，不用感叹号和网络热词",
                  "must": "面料成分和摸上去的手感 + 一个做工或版型细节 + 尺码建议（平铺尺寸或模特数据）",
                  "banned": "", "tagMix": "款式 3、面料 2、人群 2、季节 2"},
    "the rumah daily": {"position": "同一个女生每天穿什么：真实上身的日常穿搭记录",
                        "tone": "第一人称，像和朋友说今天穿了什么，轻松自然，短句",
                        "must": "我的身高体重和穿的码 + 今天的场景 + 一套完整搭配（上衣、下装、鞋包）",
                        "banned": "", "tagMix": "款式 3、人群 2、场景 2、季节 2"},
    "the rumah": {"position": "the rumah 品牌主号：每一季的系列和设计想法",
                  "tone": "品牌口吻，简洁有质感，像杂志旁白，不卖力推销",
                  "must": "系列或主题名 + 一句设计想法 + 面料和版型 + 尺码信息",
                  "banned": "", "tagMix": "品牌和系列 2、款式 3、风格 2、季节 2"},
    "街拍号": {"position": "城市通勤女生的街头穿搭：一件衣服在路上怎么搭",
               "tone": "朋友分享「今天穿」，短句，带上城市、天气或气温，不端着",
               "must": "城市或天气 + 一套完整搭配（上衣、下装、鞋包）+ 模特身高体重和穿的码",
               "banned": "", "tagMix": "场景 3、款式 3、城市或天气 2、季节 2"},
    "测评号": {"position": "店主试穿笔记：身材真实，替你把尺码和缺点先试一遍",
               "tone": "客观、先说缺点再说优点，像认真试过的人在提醒朋友，不夸张不吹",
               "must": "试穿人身高体重和穿的码 + 至少一个缺点 + 选码建议（偏大偏小、什么身材慎入）",
               "banned": "闭眼入、必买、天花板", "tagMix": "身材 3、款式 3、测评 2、季节 2"},
}

# 加号时选的人设：一次带出拍法、封面、文案设定和稳定后的每周篇数，之后都能改
PERSONAS = {
    "street": {"name": "街拍", "copy": "街拍号", "style": "street", "face": "hide", "cover": "text", "weekly": 4,
               "desc": "户外全身抓拍，大字条封面，讲怎么搭"},
    "review": {"name": "测评", "copy": "测评号", "style": "mirror", "face": "hide", "cover": "text", "weekly": 3, "realFirst": True,
               "desc": "对镜试穿 + 细节特写，先说缺点给尺码；图建议用实拍"},
    "still": {"name": "无人静物", "copy": "the reels", "style": "own", "face": "none", "cover": "none", "weekly": 3,
              "desc": "挂拍、平铺、手摸面料，封面不加字"},
    "room": {"name": "房间上身不露脸", "copy": "the rumah daily", "style": "own", "face": "hide", "cover": "none", "weekly": 4,
             "desc": "固定房间上身照，裁头不露脸"},
    "studio": {"name": "影棚 lookbook", "copy": "the rumah", "style": "own", "face": "model", "cover": "logo", "weekly": 3,
               "desc": "固定模特露脸，品牌水印"},
}
# 新号起号节奏：开始后第几周 → 阶段名、这周最多排几篇（稳定后按人设的 weekly）
RAMP = ((1, "养号", 1), (4, "冷启动", 4), (8, "定型", 5))
ITEM_GAP_DAYS = 2        # 同一款在不同号之间至少隔几天
CROSS_GAP_MIN = 30       # 不同号的发布时间至少错开几分钟


def account_rhythm(a, at=None):
    """起号进度：第几周、什么阶段、这周最多发几篇。没设开始日期的老号算稳定。"""
    p = PERSONAS.get(a.get("persona") or "")
    weekly = int(a.get("weekly") or (p or {}).get("weekly") or 0)
    start = a.get("startedAt")
    if not start:
        return {"week": 0, "stage": "稳定", "target": weekly, "ramping": False}
    week = max(1, int(((at or now_ms()) - start) // (7 * 864e5)) + 1)
    for last, stage, cap in RAMP:
        if week <= last:
            return {"week": week, "stage": stage, "target": cap, "ramping": True}
    return {"week": week, "stage": "稳定", "target": weekly, "ramping": False}


def account_copy(aid):
    a = next((x for x in load_state()["accounts"] if str(x["id"]) == str(aid)), {})
    return {k: str(a.get(k) or "").strip() for k in COPY_KEYS}


def banned_words(aid):
    return [w for w in re.split(r"[、,，\s]+", account_copy(aid)["banned"]) if w]


def copy_style(aid):
    return account_copy(aid)["writeStyle"] or "gaosaka"


def copy_rules(aid):
    """文案设定（每个号设一次）写进给 AI 的指令。"""
    c = account_copy(aid)
    lines = ["%s：%s。" % (COPY_KEYS[k], c[k]) for k in ("position", "tone", "must", "banned") if c[k]]
    style = copy_style(aid)
    lines.append("写作风格：%s。" % COPY_STYLES[style])
    if style == "gaosaka":
        # 将规则直接放进任务，避免后台任务只看到路径、没有实际采用风格。
        lines.append(GAOSAKA_GUIDE.read_text("utf-8"))
    return "\n".join(lines)


def tag_rule(aid):
    mix = account_copy(aid)["tagMix"]
    return "话题 8–10 个，按 %s 搭配。" % (mix or "款式词 3 个、人群词 2 个、场景词 2 个、季节词 2 个")


def account_cover(a):
    """这个号的封面样式：不加字 / 大字条 / 品牌水印。没设过的按以前的默认（加大字）。"""
    return a.get("cover") if a.get("cover") in COVERS else "text"


def account_logo(a):
    f = a.get("logo") or ""
    return f if f and Path(f).is_file() else ""


def find_topic(st, tid):
    return next((t for t in st["topics"] if t["id"] == tid), None)


def topic_line(t, count):
    if not t:
        return ""
    cell = " × ".join(x for x in (t.get("scene"), t.get("body")) if x)
    return ("选题：「%s」%s。%s" % (t["text"], "（%s）" % cell if cell else "",
            "两篇都围绕这个选题，一篇从身材尺码切入，一篇从场景搭配切入。" if count == 2 else "这篇围绕这个选题写，标题和开头直接回应它。"))


def item_topic(st, aid, iid):
    """和这款最贴的一条没用过的选题：填材质痛点时给这款出的那几条。"""
    return next((t for t in st["topics"] if t.get("item") == iid and str(t["account"]) == str(aid) and t["status"] == "unused"), None)


def topic_grid(st, aid):
    """场景 × 身材：每格还有几条没用的选题。"""
    grid = {sc: {b: 0 for b in FASHION["bodies"]} for sc in FASHION["scenes"]}
    for t in st["topics"]:
        if str(t["account"]) == str(aid) and t["status"] == "unused" and t.get("scene") in grid and t.get("body") in grid[t["scene"]]:
            grid[t["scene"]][t["body"]] += 1
    return grid


# ---------------------------------------------------------------- jobs
def update_job(jid, **kw):
    def f(s):
        for j in s["jobs"]:
            if j["id"] == jid:
                j.update(kw)
    mutate(f)


def read_result_file(job):
    f = Path(job["params"]["outFile"])
    if not f.is_file():
        raise ValueError("Codex 没写出稿件文件")
    try:
        return json.loads(f.read_text("utf-8"))
    except ValueError:
        raise ValueError("稿件文件不是有效的 JSON")


def verify_job(job, result, before):
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise ValueError("未收到有效成果回执，不能确认任务完成")
    p, kind = job["params"], job["kind"]
    if kind == "spec":
        it = find_item(load_state(), p["item"])
        spec = item_dir(it) / "spec.md"
        if not spec.is_file() or spec.stat().st_mtime * 1000 < before["t"] - 1000 or not parse_checks(spec.read_text("utf-8")):
            raise ValueError("没有读到新写的档案和检查要点")
        apply_info(it)
        return
    if kind == "lock":
        out = Path(p["jobDir"]) / "out"
        files = [f for f in out.glob("*") if f.suffix.lower() in IMG_EXT]
        if not files:
            raise ValueError("没有出图成品，看 report.md 里的原因")
        if result.get("images") != len(files):
            raise ValueError("出图回执与实际成品数量不符")
        return
    data = read_result_file(job)
    if kind == "write":
        drafts = data.get("drafts") if isinstance(data, dict) else None
        if not isinstance(drafts, list) or len(drafts) != p["count"]:
            raise ValueError("应该写 %d 篇，稿件文件里不是" % p["count"])
        clean = [clean_draft(d, account=p["account"]) for d in drafts]
        ids = add_drafts(p["account"], p.get("item"), clean, "codex")
        update_job(job["id"], drafts=ids)
        if p.get("topic"):
            def used(s):
                t = find_topic(s, p["topic"])
                if t:
                    t.update(status="used", drafts=(t.get("drafts") or []) + ids, usedAt=now_ms())
            mutate(used)
    elif kind == "trial":
        drafts = data.get("drafts") if isinstance(data, dict) else None
        if not isinstance(drafts, list) or not drafts:
            raise ValueError("稿件文件里没有试写的稿")
        update_job(job["id"], trial=clean_draft(drafts[0], images=False, account=p["account"]))
    elif kind == "topics":
        update_job(job["id"], topics=import_topics(p, data))
    elif kind == "rewrite":
        d = clean_draft(data, images=False, account=p["account"])
        r = review_by_id(p["review"])
        if not r:
            raise ValueError("这篇稿已经不在了")
        if (d["title"], d["body"], d["tags"], d["comment"]) == (r["title"], r["body"], r["tags"], r.get("comment", "")):
            raise ValueError("没有读到稿件改动，不能确认改写完成")
        update_draft(p["review"], title=d["title"], body=d["body"], tags=d["tags"], titles=d["titles"] or r.get("titles") or [],
                     comment=d["comment"] or r.get("comment", ""))
        mutate(lambda s: s["ready"].pop(str(p["review"]), None))


def run_job(job):
    cur = next((j for j in load_state()["jobs"] if j["id"] == job["id"]), {})
    if cur.get("status") != "queued":
        return
    log_dir = STUDIO / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / ("%s.log" % job["id"])
    update_job(job["id"], status="running", startedAt=now_ms(), log=str(log_path))
    try:
        before = {"t": now_ms()}
        if job["params"].get("outFile"):
            Path(job["params"]["outFile"]).unlink(missing_ok=True)
    except (ValueError, OSError) as e:
        update_job(job["id"], status="failed", endedAt=now_ms(), summary=str(e))
        return
    if DEMO:
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write("[演示] 这里会显示 Codex 的执行过程。\n\n" + job["prompt"] + "\n")
        time.sleep(4)
        cur = next((j for j in load_state()["jobs"] if j["id"] == job["id"]), {})
        if cur.get("status") == "cancelled":
            return
        try:
            result = demo_job_effect(job)
            verify_job(job, result, before)
            update_job(job["id"], status="done", endedAt=now_ms(), result=result, summary="演示完成（模拟数据）")
            chain_after(job)
        except ValueError as e:
            update_job(job["id"], status="failed", endedAt=now_ms(), summary=str(e))
        return
    cx = codex_bin()
    if not cx:
        update_job(job["id"], status="failed", endedAt=now_ms(), summary="找不到 codex 命令行，请在设置里填路径")
        return
    work = Path(job.get("cwd") or (STUDIO / "work"))
    work.mkdir(parents=True, exist_ok=True)
    args = shlex.split(load_state()["settings"].get("codex_args") or DEFAULT_SETTINGS["codex_args"])
    cmd = [cx] + args + ["-C", str(work), job["prompt"]]
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write("$ codex %s\n\n" % " ".join(args))
        fh.flush()
        try:
            proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=str(work))
        except Exception as e:
            update_job(job["id"], status="failed", endedAt=now_ms(), summary="启动 codex 失败：%s" % e)
            return
        PROCS[job["id"]] = proc
        code = proc.wait()
        PROCS.pop(job["id"], None)
    tail = log_path.read_text("utf-8", errors="replace")[-3000:]
    result = None
    for line in reversed(tail.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            result = parse_json_loose(line)
            if result:
                break
    cur = next((j for j in load_state()["jobs"] if j["id"] == job["id"]), {})
    if cur.get("status") == "cancelled":
        return
    ok = code == 0
    summary = ""
    if ok:
        try:
            verify_job(job, result, before)
        except (ValueError, TypeError, OSError) as e:
            ok, summary = False, str(e)
    if ok and isinstance(result, dict):
        summary = "；".join("%s=%s" % (k, v) for k, v in result.items() if k != "ok")
    update_job(job["id"], status="done" if ok else "failed", endedAt=now_ms(), result=result,
               summary=summary or ("完成" if ok else "Codex 退出码 %s，看日志" % code))
    if ok:
        chain_after(job)


def chain_after(job):
    """出图完成后，按出图时的设置自动挑 A 级图（没有 A 就用 B）写文案；失败只记在任务里，不影响出图结果。"""
    p = job.get("params") or {}
    if job["kind"] != "lock" or not p.get("autoWrite"):
        return
    try:
        files = sorted(str(f) for f in (Path(p["jobDir"]) / "out").glob("*") if f.suffix.lower() in IMG_EXT)
        rep_f = Path(p["jobDir"]) / "report.md"
        grades = image_grades(rep_f.read_text("utf-8", errors="replace") if rep_f.is_file() else "", files)
        pick = [f for f in files if grades[f]["grade"] == "A"] or [f for f in files if grades[f]["grade"] in ("B", "")]
        if not pick:
            raise ValueError("没有 A/B 级图，没自动写文案")
        it = find_item(load_state(), p["item"])
        pick += real_photos(it)[:max(0, 9 - len(pick))] if it else []   # 这款传过的实拍图接在 AI 图后面一起用
        a = p["autoWrite"]
        count = a["count"] if len(pick) >= 4 else 1
        t = item_topic(load_state(), a["account"], p["item"])
        r_write_from_images({"account": a["account"], "item": p["item"], "images": pick, "count": count,
                             "cover": a.get("cover") or "", "note": a.get("note", ""), "auto": True, "topic": t["id"] if t else ""})
    except (ValueError, OSError) as e:
        update_job(job["id"], summary="出图完成；%s" % e)


def job_worker():
    while True:
        try:
            st = load_state()
            if not any(j["status"] == "running" for j in st["jobs"]):
                q = [j for j in st["jobs"] if j["status"] == "queued"]
                if q:
                    run_job(q[0])
                    continue
        except Exception as e:
            print("job worker error:", e)
        time.sleep(2)


# ---------------------------------------------------------------- publishing
def publish_args(r, mark_ai=None):
    """发布脚本的参数：标题、正文文件、图片、话题、AI 声明（每篇可选，没选按设置）、挂商品。"""
    f = STUDIO / "publisher" / ("body_%s_%s.txt" % (r["id"], uuid.uuid4().hex[:6]))
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(r["body"], "utf-8")
    st = load_state()
    args = ["publish", "--account", xhs_profile(r["account_id"]), "--title", r["title"], "--body-file", str(f),
            "--images", ",".join(r["images"]), "--tags", " ".join(r.get("tags") or [])]
    it = find_item(st, r.get("item")) if r.get("item") else None
    goods = [g for g in re.split(r"[,，、\s]+", (it or {}).get("goods") or "") if g]
    if goods:
        args += ["--goods", ",".join(goods)]
    if st["settings"].get("mark_ai", True) if mark_ai is None else mark_ai:
        args.append("--ai")
    return args, f


def publish_one(entry):
    """到点用工作台自己的浏览器直接发这一篇（不用小红书的定时发布）。过程写回 entry['log']。"""
    aid, rid = str(entry["account"]), str(entry["review"])
    log = []

    def step(msg):
        log.append("%s %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg))
        set_entry(entry["id"], log=list(log))

    with PUBLISH_LOCK:
        current = next((e for e in load_state()["schedule"] if e["id"] == entry["id"]), None)
        if not current or current["status"] != "pending":
            return
        set_entry(entry["id"], status="running", startedAt=now_ms())
        body = None
        try:
            r = review_by_id(rid)
            if not r or str(r["account_id"]) != aid:
                return fail_entry(entry, log, "找不到这篇稿，可能被删了")
            validate_draft(r)
            if entry.get("fingerprint") != draft_fingerprint(r):
                return fail_entry(entry, log, "稿件在确认后已修改，请重新审稿排期")
            if rid in load_state()["published"]:
                return fail_entry(entry, log, "这篇已经发过了，没有重复发")
            args, body = publish_args(r, entry.get("markAi"))
            if load_state()["settings"].get("xhs_headless"):
                args.append("--headless")
            step("打开浏览器发布" + ("，勾选「笔记含 AI 合成内容」" if "--ai" in args else "，不勾选 AI 合成内容"))
            ok, data, raw = xhs_cli(args, timeout=1800)
            if not ok:
                if "登录" in raw or "扫码" in raw:
                    set_login(aid, False)
                return fail_entry(entry, log, "发布没有成功：%s" % raw[-400:])
            tags = (data or {}).get("tags") or {}
            if tags.get("plain"):
                step("这些话题平台上没有完全一样的，按文字留着：%s" % "、".join(tags["plain"]))
            step("发布成功")
            rec = {"account": aid, "title": r["title"], "at": now_ms(), "entry": entry["id"],
                   "shot": (data or {}).get("shot", ""), "ai": "--ai" in args}

            def done(s):
                s["published"][rid] = rec
                s["ready"].pop(rid, None)
                for d in s["drafts"]:
                    if str(d["id"]) == rid:
                        d["status"] = "已发布"
            mutate(done)
            set_entry(entry["id"], status="done", endedAt=now_ms(), log=list(log), shot=rec["shot"])
        except Exception as e:
            fail_entry(entry, log, "出错：%s" % e)
        finally:
            if body:
                body.unlink(missing_ok=True)


def fail_entry(entry, log, msg):
    log.append("%s 失败：%s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg))
    set_entry(entry["id"], status="failed", endedAt=now_ms(), log=list(log), error=msg)


def set_entry(eid, **kw):
    def f(s):
        for e in s["schedule"]:
            if e["id"] == eid:
                e.update(kw)
    mutate(f)


def scheduler():
    while True:
        try:
            st = load_state()
            grace = int(st["settings"].get("late_grace_min") or 120) * 60000
            for e in st["schedule"]:
                if e.get("status") != "pending" or e.get("at", 0) > now_ms():
                    continue
                if now_ms() - e["at"] > grace:
                    set_entry(e["id"], status="missed", error="错过了发布时间（当时 Mac 睡着了或工作台没开），没有补发，可以重新排期")
                    continue
                publish_one(e)
            keep_awake(any(e.get("status") == "pending" for e in load_state()["schedule"]))
        except Exception as ex:
            print("scheduler error:", ex)
        time.sleep(20)


def keep_awake(need):
    """有待发的稿时开 caffeinate -i，不让 Mac 闲置睡眠（合盖还是会睡）；没有就关掉。只在 macOS 上。"""
    p = AWAKE["proc"]
    alive = p is not None and p.poll() is None
    if need and not alive and not DEMO and sys.platform == "darwin" and shutil.which("caffeinate"):
        AWAKE["proc"] = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
    elif not need and alive:
        p.terminate()
        AWAKE["proc"] = None


# ---------------------------------------------------------------- files
def allowed_roots():
    roots = [STUDIO, lock_root() / "library", lock_root() / "jobs"]
    for it in load_state()["items"]:
        if it.get("refDir"):
            roots.append(Path(it["refDir"]).expanduser())
    return [str(r.resolve()) for r in roots if r]


def list_images(d, limit=12):
    d = Path(d).expanduser()
    if not d.is_dir():
        return []
    files = [p for p in d.rglob("*") if p.suffix.lower() in IMG_EXT and p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [str(p) for p in files[:limit]]



# ---------------------------------------------------------------- 锁图工作台（xhs-lock-studio）
PHOTO_SLOTS = {"front": "正面", "back": "背面", "detail_1": "面料近拍", "detail_2": "关键设计特写", "tag": "吊牌"}
INFO_FIELDS = ("name", "code", "category", "colors", "sizes", "fabric", "price", "fit", "points")
PEAK_SLOTS = ((12, 30), (20, 0))
# 高峰被别的号占了，就往前后挪一点（矩阵里不同号错开 30 分钟以上）
PEAK_NEAR = {(12, 30): ((13, 10), (11, 50), (13, 50)), (20, 0): ((20, 40), (19, 20), (21, 20), (21, 50))}
FACE_SLOTS = [("face_1", "正脸"), ("face_2", "半侧"), ("face_3", "全侧"), ("face_closeup", "特写"), ("body_1", "全身")]
FACE_MODES = {
    "model": "换成我们的模特 library/models/{model}（按 SKILL.md 模式 C3「换成我们的模特」三步做：出图时换发型，衣服通过后贴真脸，再融合）",
    "ai": "出完后把脸换成一张不对应任何真人的普通面孔（按 SKILL.md 模式 C 的「换 AI 脸」做）",
    "hide": "构图往下移或让手机挡住脸，成品里看不到眼睛和鼻子（按 SKILL.md 模式 C 的「不露脸」做）",
    "none": "参考图里没有人（挂拍、平铺、静物），成品也不要出现人；衣架、桌面、布料堆叠、光线和构图都按参考图",
}
MIN_PER_IMAGE = {"model": 7, "ai": 3, "hide": 3, "none": 3}


def lock_root():
    return Path(load_state()["settings"].get("lock_studio_path") or DEFAULT_SETTINGS["lock_studio_path"]).expanduser()


def safe_name(s):
    return re.sub(r"[\\/:*?\"<>|\s]+", "", s or "")[:40] or "未命名"


def item_dir(it):
    return lock_root() / "library" / "originals" / it["lockName"]


def find_slot(d, slot):
    """front → front.jpg；detail_2 → detail_2_*.jpg；detail_1 优先用平铺的 detail_1_flat。"""
    if not d.is_dir():
        return None
    files = sorted(p for p in d.iterdir() if p.suffix.lower() in IMG_EXT and p.is_file())
    if slot == "detail_1":
        flat = [p for p in files if p.stem.lower().startswith("detail_1_flat")]
        if flat:
            return flat[0]
    hit = [p for p in files if p.stem.lower() == slot or p.stem.lower().startswith(slot + "_")]
    return hit[0] if hit else None


def parse_checks(spec_text):
    """取 spec.md「检查要点」一节，返回 [{level: 必过|尽量, text}]。"""
    m = re.search(r"^#{2,4}\s*检查要点.*?$(.*?)(?=^#{1,3}\s|\Z)", spec_text, re.M | re.S)
    if not m:
        return []
    out, level = [], "必过"
    for line in m.group(1).splitlines():
        t = line.strip()
        if t.startswith("必过"):
            level = "必过"
        elif t.startswith("尽量"):
            level = "尽量"
        mm = re.match(r"^(?:\d+[.、)]|[-*])\s*(.+)$", t)
        if mm:
            text = mm.group(1).strip()
            unsure = bool(re.search(r"[（(]\s*待确认[^）)]*[）)]", text))
            out.append({"level": level, "text": re.sub(r"\s*[（(]\s*待确认[^）)]*[）)]", "", text).strip(), "unsure": unsure})
    return out


def write_checks(spec_text, checks):
    must = [c["text"].strip() for c in checks if c.get("level") != "尽量" and c.get("text", "").strip()]
    soft = [c["text"].strip() for c in checks if c.get("level") == "尽量" and c.get("text", "").strip()]
    if not must:
        raise ValueError("至少要有一条必过的检查要点")
    lines = ["### 检查要点（放大逐条看）", "必过（任何一条不对判 C，返工）："]
    lines += ["%d. %s" % (i + 1, t) for i, t in enumerate(must)]
    if soft:
        lines.append("尽量（不对评 B，写进报告，不返工）：")
        lines += ["%d. %s" % (len(must) + i + 1, t) for i, t in enumerate(soft)]
    block = "\n".join(lines) + "\n"
    pat = re.compile(r"^#{2,4}\s*检查要点.*?$.*?(?=^#{1,3}\s|\Z)", re.M | re.S)
    if pat.search(spec_text):
        return pat.sub(lambda _: block + "\n", spec_text, count=1).rstrip() + "\n"
    return spec_text.rstrip() + "\n\n" + block


def lock_jobs_for(it):
    """该单品在锁图工作台 jobs/ 下的出图任务，新的在前。"""
    jd = lock_root() / "jobs"
    if not jd.is_dir():
        return []
    out = []
    for d in sorted(jd.iterdir(), reverse=True):
        if not d.is_dir() or not (d / "job.md").is_file():
            continue
        head = (d / "job.md").read_text("utf-8", errors="replace")[:600]
        if "library/originals/%s\n" % it["lockName"] not in head + "\n":
            continue
        rej = d / "rejected"
        out.append({"dir": str(d), "name": d.name,
                    "images": sorted(str(p) for p in (d / "out").glob("*") if p.suffix.lower() in IMG_EXT),
                    "rejected": len([p for p in rej.glob("*") if p.suffix.lower() in IMG_EXT]) if rej.is_dir() else 0,
                    "report": (d / "report.md").read_text("utf-8", errors="replace")[-6000:] if (d / "report.md").is_file() else ""})
    return out


def image_grades(report, files):
    """从 report.md 里按文件名找评级 A/B/C 和一句原因；找不到就空着。"""
    out = {}
    for f in files:
        stem = Path(f).stem
        g, note = "", ""
        for line in report.splitlines():
            if stem in line or (re.search(r"_(\d{2})_", stem) and ("第 %d 张" % int(re.search(r"_(\d{2})_", stem).group(1))) in line):
                mm = re.search(r"(?:评级|评)[：: ]*\**([ABC])\b|\b([ABC])\s*级", line)
                if mm:
                    g = mm.group(1) or mm.group(2)
                    tail = re.sub(r"[|*`]", " ", line[mm.end():]).strip(" ，,。;；:：-")
                    note = (tail or re.sub(r"[|*`]", " ", line).strip())[:80]
                    break
        out[f] = {"grade": g, "note": note}
    return out


def item_view(it, rb, st):
    it = dict(it)
    if not it.get("lockName"):
        return it
    d = item_dir(it)
    it["dir"] = str(d)
    it["photos"] = {k: (str(find_slot(d, k)) if find_slot(d, k) else "") for k in PHOTO_SLOTS}
    it["realPhotos"] = real_photos(it)
    spec = d / "spec.md"
    text = spec.read_text("utf-8", errors="replace") if spec.is_file() else ""
    it["hasSpec"] = bool(text.strip())
    it["specText"] = text[:8000]
    it["checks"] = parse_checks(text)
    notes = d / "photo_notes.txt"
    it["photoNotes"] = [l.strip() for l in notes.read_text("utf-8", errors="replace").splitlines() if l.strip()] if notes.is_file() else []
    jobs = lock_jobs_for(it)
    owner = st.get("jobAcc") or {}
    for j in jobs:
        j["account"] = owner.get(j["name"], "")
    # 每个号最近一批 + 没标号的最近几批（老数据）；图片评级各批分开算
    shown, seen = [], set()
    for j in jobs:
        if j["account"] and j["account"] in seen:
            continue
        seen.add(j["account"])
        shown.append(j)
    it["lockJobs"] = shown[:8]
    imgs = jobs[0]["images"] if jobs else []
    it["grades"] = {}
    for j in it["lockJobs"]:
        it["grades"].update(image_grades(j["report"], j["images"]))
    it["drafts"] = len([r for r in rb["reviews"] if any(str(x) in str(r.get("images")) for x in imgs)]) if imgs else 0
    running = [j for j in st["jobs"] if j.get("params", {}).get("item") == it["id"] and j["status"] in ("queued", "running")]
    it["busy"] = running[0]["kind"] if running else ""
    if running:
        it["stage"] = {"spec": "AI 写档案中", "lock": "出图中", "write": "写文案中"}.get(running[0]["kind"], "处理中")
    elif not it["photos"]["front"]:
        it["stage"] = "缺正面图"
    elif not it["hasSpec"]:
        it["stage"] = "待写档案"
    elif not it.get("specOk"):
        it["stage"] = "档案待核对"
    elif not imgs:
        it["stage"] = "待出图"
    else:
        it["stage"] = "图已出好"
    return it


def real_photos(it):
    """这款自己拍的实拍图（上身、挂拍、平铺、手摸面料），不过 AI，直接和 AI 图一起配文案。存在原图目录的 real/ 下。"""
    d = item_dir(it) / "real"
    return sorted(str(p) for p in d.iterdir() if p.suffix.lower() in IMG_EXT and p.is_file()) if d.is_dir() else []


def list_models():
    md = lock_root() / "library" / "models"
    out = []
    if not md.is_dir():
        return out
    for d in sorted(md.iterdir()):
        if not d.is_dir() or d.name.startswith("示例"):
            continue
        prof = (d / "profile.md").read_text("utf-8", errors="replace") if (d / "profile.md").is_file() else ""
        hw = re.search(r"(\d{3})\s*(?:cm)?\s*[/，, ]\s*(\d{2,3})\s*(?:kg|斤)?", prof)
        faces = {k: (str(find_slot(d, k)) if find_slot(d, k) else "") for k, _ in FACE_SLOTS}
        missing = [label for k, label in FACE_SLOTS[:4] if not faces[k]]
        out.append({"name": d.name, "faces": faces, "missing": missing,
                    "size": ("%s / %s" % hw.groups()) if hw else "", "hasProfile": bool(prof.strip())})
    return out


def list_refs():
    rd = lock_root() / "library" / "references"
    if not rd.is_dir():
        return []
    return sorted(str(p) for p in rd.iterdir() if p.suffix.lower() in IMG_EXT and p.is_file())


def all_styles(st):
    """参考图的分组：对镜自拍、街拍、店里自己加的（比如 工作室静物 / 书桌房间 / 灰棚 lookbook），最后是其他。"""
    out = {"mirror": STYLES["mirror"], "street": STYLES["street"]}
    out.update((g["id"], g["name"]) for g in st.get("styleGroups") or [])
    out["other"] = STYLES["other"]
    return out


def style_note(st, key):
    """写进出图任务单的风格要求：自带的两组用固定说明，自己加的组用加组时写的那句。"""
    if key in STYLE_NOTES:
        return STYLE_NOTES[key]
    g = next((x for x in st.get("styleGroups") or [] if x["id"] == key), None)
    return ("%s：%s" % (g["name"], g["note"] or "成品要和这组参考图的场景、光线、构图、氛围一致")) if g else ""


def ref_style(path, st):
    """参考图属于哪组风格：上传时选的；没选过就按文件名猜（镜/自拍 → 对镜自拍，街 → 街拍）。"""
    if st["refStyles"].get(path) in all_styles(st):
        return st["refStyles"][path]
    name = Path(path).stem.lower()
    if re.search(r"镜|自拍|mirror|selfie", name):
        return "mirror"
    if re.search(r"街|street|walk|路", name):
        return "street"
    return "other"


STYLE_NOTES = {
    "mirror": "对镜自拍：成品要像本人举着手机对着镜子拍的。保留素材图里的镜子、手机、举手机的手和镜面反射；"
              "画面是手机镜头的透视和室内自然光，不要做成影棚大片；手机挡住或部分挡住脸。",
    "street": "街拍：成品要像朋友在街上随手抓拍的。保留素材图的街景、走路或回头的动态、自然光和景深；"
              "不要换背景，不要摆拍感过重。",
}


def lock_job_md(it, p, job_name):
    d = item_dir(it)
    base = "library/originals/%s" % it["lockName"]
    rel = lambda f: "%s/%s" % (base, Path(f).name)
    front, key, d1 = find_slot(d, "front"), find_slot(d, "detail_2"), find_slot(d, "detail_1")
    d3 = find_slot(d, "detail_3")
    fab = d1 if key else d3
    back = find_slot(d, "back")
    prod = [front and "%s（衣服正面）" % rel(front),
            (key or d1) and "%s（%s）" % (rel(key or d1), "关键设计特写，这一块的形状、宽度、走向必须一样" if key else "面料和细节"),
            fab and "%s（面料近拍：绒毛、针眼、混色、光泽都按这张）" % rel(fab),
            back and "%s（衣服背面；素材图看不到背面时不附）" % rel(back)]
    prod = [x for x in prod if x][:4]
    face = FACE_MODES[p["face"]].format(model=p.get("model") or "")
    tag = "模特" if p["face"] == "model" else "产品"
    rows = []
    for i, ref in enumerate(p["refs"]):
        rows.append("| %d | library/references/%s | %s_%s_%02d_%s.png |" % (i + 1, Path(ref).name, safe_name(it["lockName"]), tag, i + 1, safe_name(Path(ref).stem)))
    extra = (p.get("note") or "").strip() or "无"
    note = style_note(load_state(), p.get("style"))
    if note:
        extra = "风格：" + note + "\n\n" + extra
    if p["face"] == "model":
        extra += "\n\n## 模特\n脸和发型换成 library/models/%s（profile.md 和 face_* 照片）。衣服先按 C4 检查通过，再做贴脸和融合；脸按「检查」一节的对照表和她本人照片比。" % p["model"]
    return """# 出图任务 %s

模式: 只锁产品
原图目录: %s
脸部处理: %s
输出: 3:4，1080x1440，共 %d 张

## 参考图（每张都附这几张，按这个顺序；生图一次最多 5 张）
图1. 这一行对应的素材图（编辑底图：人、动作、比例、场景、配饰都保持原样）
%s

## 逐张任务
| 序号 | 素材图 | 输出文件名 |
|---|---|---|
%s

## 补充要求
%s
""" % (job_name, base, face, len(p["refs"]), "\n".join("图%d. %s" % (i + 2, x) for i, x in enumerate(prod)), "\n".join(rows), extra)


def lock_prompt(p, job_name):
    with_model = p["face"] == "model"
    return ("读 AGENTS.md，使用 xhs-lock 技能的「模式 C：只锁产品」，执行 jobs/%s/job.md：" % job_name
            + ("每张把素材图当编辑底图，动作、比例、场景和配饰都不动，衣服换成原图目录里的产品；脸和发型换成任务单「脸部处理」写的模特，按 C3「换成我们的模特」三步做（衣服先过检查，再贴真脸、融合）。附图按任务单的顺序。"
               if with_model else
               "每张把素材图当编辑底图，人、脸、动作、比例、场景和配饰都不动，只把衣服换成原图目录里的产品，附图按任务单的顺序；脸按任务单的「脸部处理」来。")
            + "每张都用 tools/crop_compare.py 把衣服关键部位放大和产品图并排检查，评 A/B/C，没过的只返工那一项，成品按表里的文件名存到 jobs/%s/out，最后运行 tools/contact_sheet.py 并写 report.md，report.md 里每张成品单独一行写「<文件名>：评级 A/B/C，一句原因」。" % job_name
            + (("风格按任务单「补充要求」里写的「%s」来。" % all_styles(load_state())[p["style"]]) if style_note(load_state(), p.get("style")) else "")
            + "我已经在工作台点了出图，这就是确认，不用再问我。生图额度用完时停下，在 report.md 写明做到哪一张。"
            + "全部完成后，最后一行只输出：{\"ok\":true,\"images\":<out 里的张数>}")


def spec_prompt(it):
    base = "library/originals/%s" % it["lockName"]
    return ("读 AGENTS.md 和 library/originals/产品档案模板.md，再看 %s/ 里的全部照片，按模板给这件衣服写 %s/spec.md：一级标题「# 产品档案：%s」，"
            "「## 锁定参数」下写「### 衣服」各项，最后写「### 检查要点（放大逐条看）」，分「必过」和「尽量」两组，每条一行、编号、放大看一眼就能判对错。"
            "已有 spec.md 时在原文基础上补全，不要删掉人写的内容。"
            "面料近拍（detail_1*）是拧成卷圈或堆成大褶的，从里面裁一块平整的区域存成 detail_1_flat.jpg；没有 detail_2_* 关键设计特写时，从 front.jpg 把最有辨识度的那块裁出来放大，存成 detail_2_<部位英文>.jpg。"
            "检查要点里只凭照片看不准的条目（比如背面细节没拍到、颜色可能偏色），在这一条末尾加「（待确认）」，让人对着实物核对；看得准的不要加。"
            "照片有需要人处理的问题（缺背面、面料图模糊、光线偏色等），一行一条写进 %s/photo_notes.txt，没有就写一个空文件。"
            "再写 %s/info.json，只读照片和吊牌（tag*）上看得到的，看不到的留空字符串，不要猜："
            "{\"name\":\"颜色+特征+品类，≤12 字，如 银灰长毛翻领外套\",\"code\":\"吊牌款号\",\"category\":\"从 %s 里选一个\","
            "\"colors\":\"\",\"sizes\":\"吊牌尺码，空格分开\",\"fabric\":\"吊牌成分原文\",\"price\":\"吊牌价，只写数字\",\"fit\":\"\",\"points\":\"照片里看得到的设计点，逗号分开，不写功效\"}。"
            "只写这几个文件，不要出图。我已经在工作台点了按钮，这就是确认。最后一行只输出：{\"ok\":true}") % (base, base, it.get("name") or it["lockName"], base, base, "、".join(FASHION.get("categories", [])))


def write_prompt(p, st):
    it = find_item(st, p["item"]) or {}
    imgs = p.get("images") or []
    lines = [
        "为小红书账号「%s」写 %d 篇女装图文笔记。只写文案，不要发布，也不要调用 RedBeacon。" % (account_name(p["account"]), p["count"]),
        ("先读写法手册 %s，按里面的骨架和口吻写；和下面账号设定冲突时听账号设定。" % COPY_GUIDE) if COPY_GUIDE.is_file() else "",
        copy_rules(p["account"]),
        item_facts(it),
        ("模特和尺码：%s。正文里要写清模特身高体重穿什么码，再给一句选码建议。" % it["modelSize"]) if it.get("modelSize") else "",
        ("痛点「%s」要在开头三行里点出来，正文给出这款怎么解决。" % it["pain"]) if it.get("pain") else "",
        topic_line(find_topic(st, p.get("topic") or ""), p["count"]),
    ]
    if imgs:
        lines += ["图片已经出好，不要再生成图片。先逐张看这些图，文案里写到的场景、搭配、动作要和图一致：",
                  "\n".join("%d. %s" % (i + 1, x) for i, x in enumerate(imgs))]
    else:
        lines.append("这次没有配图，只按单品资料写，images 写空数组 []，不要生成图片。")
    if imgs and p["count"] == 2:
        lines.append("拆成 2 篇：一篇偏「身材/尺码怎么穿」，一篇偏「场景怎么搭」，角度、标题、开头都不能重复。图片分给两篇，每篇至少 2 张，按上面的顺序分，各自第一张作封面。")
    elif imgs:
        lines.append("1 篇，图片全用，按上面的顺序，第一张是封面。")
    cover = p.get("cover") or ("text" if p.get("coverText") else "none")
    cdir = STUDIO / "covers" / uuid.uuid4().hex[:8]
    if imgs and cover == "text":
        lines.append("封面加字：用 Python PIL 在每篇封面图上方 1/5 处加一行 ≤12 字的大字（粗体、白字带半透明深色底条，字号约图宽的 1/12，中文字体用 /System/Library/Fonts/PingFang.ttc），另存到 %s/ 下，用加字后的图替换这一篇的第一张；原图不改。" % cdir)
    elif imgs and cover == "logo":
        logo = account_logo(next((a for a in st["accounts"] if str(a["id"]) == str(p["account"])), {}))
        lines.append("品牌水印：用 Python PIL 把 %s 贴到这一篇每张图的右下角（宽约图宽的 1/5，离右边和下边各留图宽的 4%%，PNG 的透明部分保持透明），"
                     "另存到 %s/ 下，用贴好的图按原顺序替换；原图不改。封面不加别的字。" % (logo, cdir))
    elif imgs:
        lines.append("封面和其他图都不要加字、不要加贴纸，原图直接用。")
    lines += [
        ("额外要求：%s。" % p["note"]) if p.get("note") else "",
        ("下面是一篇对标笔记，只学它的结构和节奏（开头怎么抓人、分几段、每段讲什么、结尾怎么互动），"
         "不照抄句子，不用它的产品事实：\n<<<\n%s\n>>>" % p["ref"]) if p.get("ref") else "",
        ("每篇写 3 个不同钩子的标题候选（材质细节 / 季节场景 / 克制判断；有真实开发或市场证据才使用故事或数字），以 6–18 字为主；title 用第 1 个。"
         if copy_style(p["account"]) == "gaosaka" else "每篇写 3 个不同方向的标题候选（身高或身材 + 痛点 / 场景 / 细节钩子），都 ≤18 字；title 用第 1 个。"),
        tag_rule(p["account"]),
        "标题 ≤18 字，正文 ≤888 字，不用 Markdown 符号，不写 # 号和列表符号。" + FASHION_RULES,
        "写完把全部稿件存成一个 UTF-8 JSON 文件 %s，格式：" % p["outFile"],
        '{"drafts":[{"title":"入审用的标题","titles":["标题1","标题2","标题3"],"body":"正文","tags":["话题1","话题2"],"comment":"置顶评论","images":["本篇用的图片绝对路径，第一张是封面"]}]}',
        "每篇另写一条 ≤150 字的置顶评论（comment）：再写一次模特身高体重和穿的码，按 2–3 种身材给选码建议，最后一句请大家报身高体重来问码。",
        "images 只能用上面给的图片路径（加了字或水印的用另存的新路径）。我已经在工作台点了按钮，这就是确认，不用再问我。",
        "全部完成后，最后一行只输出：{\"ok\":true,\"drafts\":<篇数>}",
    ]
    return "\n".join(l for l in lines if l)


def topics_prompt(p, st):
    aid = p["account"]
    it = find_item(st, p.get("forItem") or "")
    have = [t["text"] for t in st["topics"] if str(t["account"]) == str(aid)][-40:]
    have += [v["title"] for v in st["published"].values() if str(v.get("account")) == str(aid)][-20:]
    grid = topic_grid(st, aid)
    gaps = ["%s×%s" % (sc, b) for sc in grid for b in grid[sc] if not grid[sc][b]]
    node = next((n for n in FASHION.get("nodes", []) if n["name"] == p.get("node")), None)
    lines = [
        "为小红书女装账号「%s」出 %d 条图文选题。只出选题，不写正文，不发布，不调用 RedBeacon。" % (account_name(aid), p["count"]),
        ("选题方法按 %s 来。" % TOPICS_GUIDE) if TOPICS_GUIDE.is_file() else "",
        "先联网搜一下小红书上女装用户最近真实在问的痛点和热词（身材、尺码、场景、季节、材质），再出题；搜不到就按常识出，不要编造数据和销量。",
        copy_rules(aid),
    ]
    if it:
        lines += [item_facts(it),
                  "这批选题全部围绕这一款：从它的材质手感和痛点出发，每条都能直接用这款的对镜自拍或街拍图来写。"]
    lines += [
        "每条标上场景和身材：场景从 %s 里选，身材从 %s 里选。" % ("、".join(FASHION["scenes"]), "、".join(FASHION["bodies"])),
        ("优先补这些还空着的格子：%s。" % "、".join(gaps[:12])) if gaps and not it else "",
        ("这批选题围绕「%s」（%s），提前 2–3 周发。" % (node["name"], node["md"])) if node
        else ("这批选题围绕「%s」，提前 2–3 周发。" % p["node"]) if p.get("node") else "",
        ("不要和这些已有选题、已发标题撞题：%s。" % "；".join(have)) if have else "",
        "每条 ≤25 字，是一篇笔记要回答的那个问题或钩子，比如「160 小个子冬天穿长外套怎么不显矮」，不要标题党。" + FASHION_RULES,
        "写成 UTF-8 JSON 文件 %s，格式：" % p["outFile"],
        '{"topics":[{"text":"选题","scene":"场景","body":"身材","why":"一句话：这个痛点从哪来"}]}',
        "我已经在工作台点了按钮，这就是确认，不用再问我。最后一行只输出：{\"ok\":true,\"topics\":<条数>}",
    ]
    return "\n".join(l for l in lines if l)


def queue_topics(aid, count, item_id="", node=""):
    require_account(aid)
    st = load_state()
    if any(j["kind"] == "topics" and j["status"] in ("queued", "running") and j["params"].get("forItem", "") == item_id
           and j["params"]["account"] == str(aid) for j in st["jobs"]):
        raise ValueError("已经在出选题了，等这一批好了再出")
    jid = uuid.uuid4().hex[:10]
    p = {"account": str(aid), "count": count, "forItem": item_id, "node": node,
         "outFile": str(STUDIO / "work" / ("topics_%s.json" % jid))}
    it = find_item(st, item_id) if item_id else None
    job = {"id": jid, "kind": "topics", "params": p, "status": "queued", "createdAt": now_ms(),
           "title": "出选题 · %s · %d 条" % (it["name"] if it else account_name(aid), count), "prompt": topics_prompt(p, st)}
    mutate(lambda s: s["jobs"].append(job))
    return job


def apply_info(it):
    """把 AI 从照片和吊牌读出的 info.json 填进单品资料：只填空着的项，名称是占位名时也替换。"""
    f = item_dir(it) / "info.json"
    try:
        info = json.loads(f.read_text("utf-8")) if f.is_file() else {}
    except ValueError:
        info = {}
    if not isinstance(info, dict):
        return []
    filled = []

    def g(s):
        for x in s["items"]:
            if x["id"] != it["id"]:
                continue
            for k in INFO_FIELDS:
                v = str(info.get(k) or "").strip()[:200]
                if k == "category" and v not in FASHION.get("categories", []):
                    v = ""
                if v and (not x.get(k) or (k == "name" and x.get("autoName"))):
                    x[k] = v
                    filled.append(k)
            if "name" in filled:
                x.pop("autoName", None)
            x["aiFilled"] = sorted(set(x.get("aiFilled", [])) | set(filled))
    mutate(g)
    return filled


def r_item_new(body):
    """只拖照片就建新款：名称可空（先用占位名，AI 读吊牌后补上），存照片，自动交给 AI 写档案和填资料。"""
    photos = body.get("photos") or {}
    if not isinstance(photos, dict) or not photos.get("front"):
        raise ValueError("至少放一张衣服正面照")
    if any(k not in PHOTO_SLOTS for k in photos):
        raise ValueError("未知照片位置")
    name = (body.get("name") or "").strip()
    auto = not name
    if auto:
        name = "新款" + datetime.datetime.now().strftime("%m%d%H%M")
    it = r_item_save({"name": name, "account": body.get("account") or "", "status": "在售"})["item"]
    if auto:
        mutate(lambda s: [x.__setitem__("autoName", True) for x in s["items"] if x["id"] == it["id"]])
    for slot, data in photos.items():
        if data:
            r_item_photo({"id": it["id"], "slot": slot, "data": data})
    job = None
    if (lock_root() / "AGENTS.md").is_file():
        job = r_lock_job("spec", {"item": it["id"]})["job"]
    return {"ok": True, "item": find_item(load_state(), it["id"]), "job": job}


def next_slot(st, account, after=None, item=None):
    """下一个空着的发布时段：至少 20 分钟后；这个号当天还没排过、前后 2 小时没有别的；
    和别的号错开 30 分钟；同一款和别的号隔 2 天；起号阶段这周没排满。"""
    t0 = (after or now_ms()) + 20 * 60000
    live = [s for s in st["schedule"] if s["status"] in ("pending", "running", "done")]
    busy = [s["at"] for s in live if s.get("account") == str(account)]
    others = [s["at"] for s in live if s.get("account") != str(account)]
    same_item = [s["at"] for s in live if s.get("account") != str(account) and item and schedule_item(st, s) == item]
    a = next((x for x in st.get("accounts", []) if str(x["id"]) == str(account)), {})
    day = datetime.date.fromtimestamp(t0 / 1000)
    for d in range(45):
        dd = day + datetime.timedelta(days=d)
        if any(datetime.date.fromtimestamp(b / 1000) == dd for b in busy):
            continue
        if any(abs((datetime.date.fromtimestamp(b / 1000) - dd).days) < ITEM_GAP_DAYS for b in same_item):
            continue
        cap = account_rhythm(a, int(datetime.datetime(dd.year, dd.month, dd.day).timestamp() * 1000))["target"]
        if cap:
            mon = dd - datetime.timedelta(days=dd.weekday())
            if len([b for b in busy if mon <= datetime.date.fromtimestamp(b / 1000) < mon + datetime.timedelta(days=7)]) >= cap:
                continue
        for peak in PEAK_SLOTS:
            if int(datetime.datetime(dd.year, dd.month, dd.day, *peak).timestamp() * 1000) < t0:
                continue
            for h, m in (peak,) + PEAK_NEAR[peak]:
                at = int(datetime.datetime(dd.year, dd.month, dd.day, h, m).timestamp() * 1000)
                if at >= t0 and all(abs(at - b) >= 2 * 3600000 for b in busy) and all(abs(at - b) >= CROSS_GAP_MIN * 60000 for b in others):
                    return at
    return None


def schedule_item(st, entry):
    d = next((x for x in st.get("drafts", []) if str(x["id"]) == str(entry.get("review"))), None)
    return (d or {}).get("item") or ""


def r_item_photo(body):
    need(body, "id", "slot", "data")
    if body["slot"] not in PHOTO_SLOTS:
        raise ValueError("未知照片位置")
    it = find_item(load_state(), body["id"])
    if not it or not it.get("lockName"):
        raise ValueError("找不到这个单品")
    data, ext = decode_image(body["data"])
    if len(data) < 100:
        raise ValueError("图片是空的")
    d = item_dir(it)
    d.mkdir(parents=True, exist_ok=True)
    old = find_slot(d, body["slot"])
    if old and not (body["slot"] == "detail_1" and old.stem.lower().startswith("detail_1_flat")):
        old.unlink()
    name = {"detail_2": "detail_2_key"}.get(body["slot"], body["slot"]) + ext
    (d / name).write_bytes(data)
    if body["slot"] == "detail_1":
        for f in d.glob("detail_1_flat*"):
            f.unlink()
    mutate(lambda s: [x.__setitem__("specOk", False) for x in s["items"] if x["id"] == it["id"]])
    return {"ok": True, "path": str(d / name)}


def r_item_checks(body):
    need(body, "id")
    it = find_item(load_state(), body["id"])
    if not it or not it.get("lockName"):
        raise ValueError("找不到这个单品")
    spec = item_dir(it) / "spec.md"
    if not spec.is_file():
        raise ValueError("还没有档案，先让 AI 写档案")
    if body.get("checks") is not None:
        spec.write_text(write_checks(spec.read_text("utf-8"), body["checks"]), "utf-8")
    confirmed = bool(body.get("confirmed"))
    mutate(lambda s: [x.__setitem__("specOk", confirmed) for x in s["items"] if x["id"] == it["id"]])
    return {"ok": True}


def r_ref_upload(body):
    need(body, "data", "name")
    data, ext = decode_image(body["data"])
    rd = lock_root() / "library" / "references"
    rd.mkdir(parents=True, exist_ok=True)
    stem = safe_name(Path(body["name"]).stem)
    f = rd / (stem + ext)
    n = 2
    while f.exists():
        f = rd / ("%s_%d%s" % (stem, n, ext))
        n += 1
    f.write_bytes(data)
    if body.get("style") in all_styles(load_state()):
        mutate(lambda s: s["refStyles"].__setitem__(str(f), body["style"]))
    return {"ok": True, "path": str(f)}


def r_ref_style(body):
    need(body, "path", "style")
    if body["path"] not in list_refs() or body["style"] not in all_styles(load_state()):
        raise ValueError("找不到这张参考图")
    mutate(lambda s: s["refStyles"].__setitem__(body["path"], body["style"]))
    return {"ok": True}


def r_style_group(body):
    """参考图库里自己加一组风格（名字 + 一句给 AI 的说明），或删掉一组（组里的图回到「其他」，不删图）。"""
    need(body, "action")
    if body["action"] == "add":
        need(body, "name")
        name, note = str(body["name"]).strip()[:12], str(body.get("note") or "").strip()[:120]
        if name in all_styles(load_state()).values():
            raise ValueError("已经有「%s」这一组了" % name)
        g = {"id": "g" + uuid.uuid4().hex[:6], "name": name, "note": note}
        mutate(lambda s: s["styleGroups"].append(g))
        return {"ok": True, "group": g}
    need(body, "id")
    if not any(g["id"] == body["id"] for g in load_state()["styleGroups"]):
        raise ValueError("只能改自己加的组")
    if body["action"] == "edit":
        need(body, "name")

        def f(s):
            for g in s["styleGroups"]:
                if g["id"] == body["id"]:
                    g.update(name=str(body["name"]).strip()[:12], note=str(body.get("note") or "").strip()[:120])
        mutate(f)
    elif body["action"] == "delete":
        def f(s):
            s["styleGroups"] = [g for g in s["styleGroups"] if g["id"] != body["id"]]
            s["refStyles"] = {k: v for k, v in s["refStyles"].items() if v != body["id"]}
            for st in (s.get("lastPick") or {}).get("byAcc", {}).values():
                if st.get("style") == body["id"]:
                    st["style"] = "own"
        mutate(f)
    else:
        raise ValueError("未知操作")
    return {"ok": True}


def decode_image(data):
    head, _, b64 = str(data or "").partition(",")
    ext = ".png" if "png" in head else ".webp" if "webp" in head else ".jpg"
    try:
        raw = base64.b64decode(re.sub(r"\s", "", b64), validate=True)
    except Exception:
        raise ValueError("图片数据无效")
    if not raw:
        raise ValueError("图片是空的")
    return raw, ext


def r_item_real(body):
    """实拍图：上传一张（存进原图目录 real/），或移除一张（挪进 real/_removed，不真删）。"""
    need(body, "id", "action")
    it = find_item(load_state(), body["id"])
    if not it or not it.get("lockName"):
        raise ValueError("找不到这个单品")
    d = item_dir(it) / "real"
    if body["action"] == "add":
        raw, ext = decode_image(body.get("data"))
        d.mkdir(parents=True, exist_ok=True)
        stem = safe_name(Path(str(body.get("name") or "实拍")).stem)
        f = d / (stem + ext)
        n = 2
        while f.exists():
            f = d / ("%s_%d%s" % (stem, n, ext))
            n += 1
        f.write_bytes(raw)
        return {"ok": True, "path": str(f)}
    if body["action"] == "delete":
        if body.get("path") not in real_photos(it):
            raise ValueError("找不到这张实拍图")
        src = Path(body["path"])
        (d / "_removed").mkdir(exist_ok=True)
        shutil.move(str(src), str(d / "_removed" / ("%s_%s%s" % (src.stem, uuid.uuid4().hex[:4], src.suffix))))
        return {"ok": True}
    raise ValueError("未知操作")


def r_account_cover(body):
    """每个号的封面样式；选品牌水印时上传一张 logo（透明底 PNG 最好）。"""
    need(body, "id", "cover")
    if body["cover"] not in COVERS:
        raise ValueError("未知封面样式")
    require_account(body["id"])
    logo = None
    if body.get("logo"):
        raw, ext = decode_image(body["logo"])
        d = STUDIO / "logos"
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("acc%s.*" % body["id"]):
            old.unlink()
        logo = d / ("acc%s%s" % (body["id"], ext))
        logo.write_bytes(raw)

    def f(s):
        for a in s["accounts"]:
            if str(a["id"]) == str(body["id"]):
                if logo:
                    a["logo"] = str(logo)
                if body["cover"] == "logo" and not account_logo(a):
                    raise ValueError("选品牌水印要先上传一张 logo 图")
                a["cover"] = body["cover"]
    mutate(f)
    return {"ok": True}


def r_ref_delete(body):
    """从参考图库移除：挪进 references/_removed/，不真删，要找回去那个文件夹拿。"""
    need(body, "path")
    if body["path"] not in list_refs():
        raise ValueError("找不到这张参考图")
    src = Path(body["path"])
    dst = src.parent / "_removed"
    dst.mkdir(exist_ok=True)
    shutil.move(str(src), str(dst / ("%s_%s%s" % (src.stem, uuid.uuid4().hex[:4], src.suffix))))
    mutate(lambda s: s["refStyles"].pop(body["path"], None))
    return {"ok": True}


# ---------------------------------------------------------------- HTTP
class Server(ThreadingHTTPServer):
    allow_reuse_address = False  # macOS 上开着它可能和占同一端口的别的程序抢连接


class H(BaseHTTPRequestHandler):
    server_version = "xhs-studio"

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            self._do_GET()
        except (OSError, ValueError) as e:
            self.send(500, {"ok": False, "error": "读取工作台数据失败：%s" % e})

    def _do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            return self.send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if u.path == "/api/ping":
            return self.send(200, {"app": APP_ID})
        if u.path == "/api/state":
            return self.send(200, api_state())
        if u.path == "/api/log":
            jid = (q.get("id") or [""])[0]
            j = next((x for x in load_state()["jobs"] if x["id"] == jid), None)
            text = ""
            if j and j.get("log") and Path(j["log"]).exists():
                text = Path(j["log"]).read_text("utf-8", errors="replace")[-20000:]
            return self.send(200, {"text": text, "prompt": (j or {}).get("prompt", "")})
        if u.path == "/img":
            p = Path((q.get("p") or [""])[0]).expanduser()
            try:
                rp = str(p.resolve())
            except Exception:
                return self.send(404, {"error": "not found"})
            if p.suffix.lower() not in IMG_EXT or not any(rp.startswith(r + os.sep) for r in allowed_roots()) or not p.is_file():
                return self.send(404, {"error": "not allowed"})
            return self.send(200, p.read_bytes(), mimetypes.guess_type(rp)[0] or "image/png")
        if u.path == "/api/dir":
            p = Path((q.get("p") or [""])[0]).expanduser()
            return self.send(200, {"exists": p.is_dir(), "images": len(list_images(p, 999)) if p.is_dir() else 0})
        return self.send(404, {"error": "not found"})

    def do_POST(self):
        if self.headers.get("Origin") not in (None, "null") and "127.0.0.1" not in self.headers.get("Origin", "") and "localhost" not in self.headers.get("Origin", ""):
            return self.send(403, {"error": "forbidden origin"})
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self.send(400, {"error": "bad json"})
        route = urllib.parse.urlparse(self.path).path
        fn = ROUTES.get(route)
        if not fn:
            return self.send(404, {"error": "not found"})
        try:
            res = fn(body)
            return self.send(200, res if isinstance(res, dict) else {"ok": True})
        except ValueError as e:
            return self.send(400, {"ok": False, "error": str(e)})
        except Exception as e:
            return self.send(500, {"ok": False, "error": "出错了：%s" % e})


def api_state():
    st = load_state()
    rb = read_data()
    items = []
    for it in st["items"]:
        it = item_view(it, rb, st)
        it["refImages"] = list_images(it.get("refDir") or "", 6) if it.get("refDir") else []
        it["genImages"] = list_images(STUDIO / "images" / it["id"], 12)
        items.append(it)
    lr = lock_root()
    refs = list_refs()
    return {
        "demo": DEMO,
        "codex": bool(DEMO or codex_bin()),
        "publisherOk": bool(DEMO or PUBLISHER.is_file()),
        "accounts": rb["accounts"], "reviews": rb["reviews"], "archive": rb["archive"],
        "items": items, "schedule": st["schedule"], "ready": st["ready"],
        "jobs": [{k: v for k, v in j.items() if k != "prompt"} for j in st["jobs"]][::-1],
        "settings": st["settings"], "fashion": FASHION, "copyPresets": COPY_PRESETS,
        "personas": {k: {"name": v["name"], "desc": v["desc"]} for k, v in PERSONAS.items()}, "now": now_ms(),
        "lastPick": st.get("lastPick", {}),
        "nextSlot": {str(a["id"]): next_slot(st, a["id"]) for a in rb["accounts"]},
        # 定稿待排的每篇：按它那一款算（同款和别的号隔开）
        "nextSlotFor": {rid: next_slot(st, d["account_id"], item=d.get("item")) for rid, d in
                        ((rid, next((x for x in st["drafts"] if str(x["id"]) == rid), None)) for rid in st["ready"]) if d},
        "lock": {"ok": (lr / "AGENTS.md").is_file(), "path": str(lr), "models": list_models(), "refs": refs,
                 "refStyles": {r: ref_style(r, st) for r in refs}, "minPerImage": MIN_PER_IMAGE},
        "styles": all_styles(st), "styleGroups": st["styleGroups"], "covers": COVERS,
        "topics": sorted(st["topics"], key=lambda t: (t["status"] != "unused", -t.get("createdAt", 0))),
        "topicGrid": {str(a["id"]): topic_grid(st, a["id"]) for a in rb["accounts"]},
        "stats": {str(a["id"]): account_stats(st, a["id"]) for a in rb["accounts"]},
        "awake": bool(AWAKE["proc"] and AWAKE["proc"].poll() is None),
    }


def account_stats(st, aid):
    """历史记录顶上的 4 个数：待审文案、已排期、本月已发、没发出要处理。"""
    aid = str(aid)
    month = datetime.date.today().replace(day=1)
    m0 = int(datetime.datetime(month.year, month.month, 1).timestamp() * 1000)
    drafts = {str(d["id"]): d for d in st["drafts"] if str(d["account_id"]) == aid}
    sched = [e for e in st["schedule"] if str(e["account"]) == aid]
    return {
        "todo": len([d for d in drafts.values() if d["status"] == "未审核" and str(d["id"]) not in st["ready"]]),
        "scheduled": len([e for e in sched if e["status"] in ("pending", "running")]),
        "monthPublished": len([v for v in st["published"].values() if str(v.get("account")) == aid and v.get("at", 0) >= m0]),
        "failed": len([e for e in sched if e["status"] in ("failed", "missed") and not e.get("rescheduled")
                       and str(e["review"]) in drafts and str(e["review"]) not in st["published"]]),
    }


def need(body, *keys):
    for k in keys:
        v = body.get(k)
        if v is None or v == "" or (isinstance(v, str) and not v.strip()):
            raise ValueError("缺少 %s" % k)


def require_account(aid):
    if not any(str(a["id"]) == str(aid) for a in load_state()["accounts"]):
        raise ValueError("找不到这个账号")


def owned_review(aid, rid):
    require_account(aid)
    r = review_by_id(rid)
    if not r:
        raise ValueError("找不到这篇稿")
    if str(r["account_id"]) != str(aid):
        raise ValueError("这篇稿不属于所选账号")
    return r


def image_path(src):
    if not isinstance(src, str):
        raise ValueError("图片必须是本机文件")
    p = Path(src).expanduser().resolve()
    if p.suffix.lower() not in IMG_EXT or not p.is_file() or p.stat().st_size == 0:
        raise ValueError("图片缺失或为空：%s" % p.name)
    if not any(p.is_relative_to(Path(r)) for r in allowed_roots()):
        raise ValueError("图片不在授权素材目录内：%s" % p.name)
    return p


def validate_draft(r, images=True):
    title, body = str(r.get("title") or "").strip(), str(r.get("body") or "")
    if not title or len(title) > 18:
        raise ValueError("标题须为 1–18 字")
    if not body.strip() or len(body) > 888:
        raise ValueError("正文须为 1–888 字")
    bad = [w for w in FASHION["riskErrors"] + (banned_words(r["account_id"]) if r.get("account_id") else []) if w in title + body + str(r.get("comment") or "")]
    if bad:
        raise ValueError("请修改违规词：" + "、".join(bad))
    if re.search(r"^\s{0,3}#{1,6}\s|\*\*[^*]+\*\*|`", body, re.M):
        raise ValueError("正文不能有 Markdown 符号")
    if images:
        if not r.get("images"):
            raise ValueError("至少需要 1 张本机图片")
        for src in r["images"]:
            image_path(src)


def bounded_int(value, default, maximum):
    if value in (None, ""):
        value = default
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise ValueError("数量须为整数")
    value = int(value)
    if not 1 <= value <= maximum:
        raise ValueError("数量须为 1–%s" % maximum)
    return value


def r_job(body):
    need(body, "kind")
    kind = body["kind"]
    if kind not in ("write", "spec", "lock", "topics", "trial"):
        raise ValueError("未知任务类型")
    p = dict(body.get("params") or {})
    if kind in ("spec", "lock"):
        return r_lock_job(kind, p)
    need(p, "account")
    require_account(p["account"])
    if kind == "topics":
        if p.get("item") and not find_item(load_state(), p["item"]):
            raise ValueError("找不到这个单品")
        return {"ok": True, "job": queue_topics(p["account"], bounded_int(p.get("count"), 10, 20), p.get("item") or "", str(p.get("node") or ""))}
    if kind == "trial":
        return r_trial(p)
    return r_write_from_images(p)


def r_trial(p):
    """文案设定页的「试写一篇」：按现在的设定写 1 篇，只在设定页显示，不进审稿。"""
    st = load_state()
    it = find_item(st, p.get("item") or "")
    if not it:
        raise ValueError("先选一件新款来试写")
    imgs = [x for x in (p.get("images") or []) if isinstance(x, str)][:6]
    for src in imgs:
        image_path(src)
    jid = uuid.uuid4().hex[:10]
    q = {"account": str(p["account"]), "item": it["id"], "images": imgs, "count": 1, "cover": "none",
         "outFile": str(STUDIO / "work" / ("trial_%s.json" % jid))}
    job = {"id": jid, "kind": "trial", "params": q, "title": "试写 · %s" % it["name"],
           "prompt": write_prompt(q, st), "status": "queued", "createdAt": now_ms()}
    mutate(lambda s: s["jobs"].append(job))
    return {"ok": True, "job": job}


def r_prompt_preview(body):
    """文案设定页的「查看实际发给 AI 的提示词」（只读）。"""
    need(body, "account")
    require_account(body["account"])
    st = load_state()
    it = find_item(st, body.get("item") or "") or {}
    jobs = lock_jobs_for(it) if it.get("lockName") else []
    imgs = (next((j["images"] for j in jobs if j["images"]), []) + (real_photos(it) if it.get("lockName") else []))[:4]
    a = next((x for x in st["accounts"] if str(x["id"]) == str(body["account"])), {})
    p = {"account": str(body["account"]), "item": it.get("id", ""), "images": imgs, "count": 1, "cover": account_cover(a),
         "outFile": str(STUDIO / "work" / "drafts_<任务编号>.json")}
    return {"ok": True, "prompt": write_prompt(p, st)}


def r_lock_job(kind, p):
    need(p, "item")
    st = load_state()
    it = find_item(st, p["item"])
    if not it or not it.get("lockName"):
        raise ValueError("找不到这个单品")
    if not (lock_root() / "AGENTS.md").is_file():
        raise ValueError("找不到锁图工作台，请在设置里填 xhs-lock-studio 文件夹的位置")
    # 同一款可以给不同的号各排一个出图任务（矩阵一款多号），同一个号同一款只排一个
    acc_of = lambda j: str((j.get("params") or {}).get("account") or "")
    if any(j.get("params", {}).get("item") == it["id"] and j["status"] in ("queued", "running")
           and (kind == "spec" or j["kind"] != "lock" or acc_of(j) == str(p.get("account") or "")) for j in st["jobs"]):
        raise ValueError("这件衣服已经有任务在做了")
    d = item_dir(it)
    if not find_slot(d, "front"):
        raise ValueError("先上传衣服正面照")
    if kind == "spec":
        job = {"id": uuid.uuid4().hex[:10], "kind": "spec", "params": {"item": it["id"]}, "cwd": str(lock_root()),
               "title": "写档案 · %s" % it["name"], "prompt": spec_prompt(it), "status": "queued", "createdAt": now_ms()}
    else:
        if not it.get("specOk"):
            raise ValueError("档案还没核对，先在第 1 步核对检查要点并确认")
        if p.get("face") not in FACE_MODES:
            raise ValueError("请选择脸怎么处理")
        if p.get("account"):
            require_account(p["account"])
        if p["face"] == "model":
            m = next((x for x in list_models() if x["name"] == p.get("model")), None)
            if not m:
                raise ValueError("找不到这个模特")
            if not m["faces"]["face_closeup"] and not m["faces"]["face_1"]:
                raise ValueError("这个模特还没有正脸照片")
        refs = p.get("refs") or []
        known = set(list_refs())
        if not refs or any(r not in known for r in refs):
            raise ValueError("请从参考图库里选至少 1 张参考图")
        if len(refs) > 9:
            raise ValueError("一次最多出 9 张")
        styles = all_styles(st)
        if p.get("style") not in styles or p["style"] == "other":
            p["style"] = "own"
        tag = styles.get(p["style"]) if p["style"] != "own" else ("换模特" if p["face"] == "model" else "静物" if p["face"] == "none" else "只锁产品")
        name = "%s_%s_%s" % (datetime.datetime.now().strftime("%Y%m%d_%H%M"), safe_name(it["lockName"]), tag)
        jd = lock_root() / "jobs" / name
        if jd.exists():
            name += "_" + uuid.uuid4().hex[:4]
            jd = lock_root() / "jobs" / name
        (jd / "out").mkdir(parents=True)
        (jd / "rejected").mkdir()
        (jd / "job.md").write_text(lock_job_md(it, p, name), "utf-8")
        auto = p.get("autoWrite") if isinstance(p.get("autoWrite"), dict) else None
        if auto:
            require_account(auto.get("account"))
            auto = {"account": str(auto["account"]), "count": 2 if str(auto.get("count")) == "2" else 1,
                    "cover": auto.get("cover") if auto.get("cover") in COVERS else "", "note": str(auto.get("note") or "")[:300]}

        def remember(s):
            lp = s.setdefault("lastPick", {})
            lp.update(face=p["face"], model=p.get("model") or lp.get("model", ""), style=p["style"])
            lp.setdefault("refsByCat", {})[it.get("category") or "其他"] = refs
            lp.setdefault("faceByStyle", {})[p["style"]] = p["face"]
            # 每个号的拍法：下次在这个号出图，风格、脸、模特、参考图自动带上
            acc = p.get("account") or (auto or {}).get("account")
            if acc:
                lp.setdefault("byAcc", {})[str(acc)] = {"style": p["style"], "face": p["face"], "model": p.get("model") or "", "refs": refs}
                s.setdefault("jobAcc", {})[name] = str(acc)   # 这批图是给哪个号出的
        mutate(remember)
        job = {"id": uuid.uuid4().hex[:10], "kind": "lock", "cwd": str(lock_root()),
               "params": {"item": it["id"], "account": str(p.get("account") or (auto or {}).get("account") or ""), "face": p["face"], "model": p.get("model") or "", "refs": refs, "jobDir": str(jd), "autoWrite": auto, "style": p["style"]},
               "title": "出图 · %s · %s%d 张" % (it["name"], (styles[p["style"]] + " ") if p["style"] in styles else "", len(refs)), "prompt": lock_prompt(p, name),
               "status": "queued", "createdAt": now_ms(), "estimateMin": MIN_PER_IMAGE[p["face"]] * len(refs)}
    mutate(lambda s: s["jobs"].append(job))
    return {"ok": True, "job": job}


def r_lock_multi(p):
    """一款多号：勾选的每个号按自己记住的拍法各出一组图，出完各自写稿。没在这个号出过图的跳过。"""
    need(p, "item")
    accs = [str(a) for a in (p.get("accounts") or [])]
    if not accs:
        raise ValueError("至少勾一个号")
    st = load_state()
    picks = (st.get("lastPick") or {}).get("byAcc") or {}
    known = set(list_refs())
    queued, skipped = [], []
    for aid in accs:
        require_account(aid)
        pk = picks.get(aid) or {}
        refs = [r for r in pk.get("refs") or [] if r in known]
        if not refs:
            skipped.append({"account": aid, "why": "这个号还没选过参考图，先切到这个号出一次"})
            continue
        try:
            job = r_lock_job("lock", {"item": p["item"], "account": aid, "face": pk.get("face"), "model": pk.get("model") or "",
                                      "refs": refs, "style": pk.get("style"), "note": str(p.get("note") or "")[:300],
                                      "autoWrite": {"account": aid, "count": 1} if p.get("autoWrite", True) else None})["job"]
            queued.append({"account": aid, "job": job["id"], "estimateMin": job.get("estimateMin", 0)})
        except ValueError as e:
            skipped.append({"account": aid, "why": str(e)})
    return {"ok": True, "queued": queued, "skipped": skipped}


def r_write_from_images(p):
    need(p, "item")
    st = load_state()
    it = find_item(st, p["item"])
    if not it:
        raise ValueError("找不到这个单品")
    imgs = p.get("images") or []
    if not isinstance(imgs, list) or not imgs:
        raise ValueError("至少选 1 张图")
    for src in imgs:
        image_path(src)
    p["count"] = 2 if str(p.get("count")) == "2" else 1
    if p["count"] == 2 and len(imgs) < 4:
        raise ValueError("拆成 2 篇至少要 4 张图")
    if p.get("cover") not in COVERS:
        # 没指定就按这个号的设定；老的调用只传 coverText
        a = next((x for x in st["accounts"] if str(x["id"]) == str(p.get("account"))), {})
        p["cover"] = ("text" if p["coverText"] else "none") if "coverText" in p else account_cover(a)
    p.pop("coverText", None)
    p["ref"] = str(p.get("ref") or "").strip()[:2000]
    if p["cover"] == "logo" and not account_logo(next((x for x in st["accounts"] if str(x["id"]) == str(p.get("account"))), {})):
        raise ValueError("这个号的封面设成了品牌水印，但还没上传 logo，到「文案 › 文案设定」里传一张")
    if p.get("topic"):
        t = find_topic(st, p["topic"])
        if not t or str(t["account"]) != str(p["account"]):
            raise ValueError("找不到这条选题")
    jid = uuid.uuid4().hex[:10]
    p["outFile"] = str(STUDIO / "work" / ("drafts_%s.json" % jid))
    job = {"id": jid, "kind": "write", "params": p, "title": "写文案 · %s · %d 篇" % (it["name"], p["count"]),
           "prompt": write_prompt(p, st), "status": "queued", "createdAt": now_ms()}
    mutate(lambda s: s["jobs"].append(job))
    return {"ok": True, "job": job}


def r_job_cancel(body):
    jid = body.get("id")
    job = next((j for j in load_state()["jobs"] if j["id"] == jid), None)
    if not job or job["status"] not in ("queued", "running"):
        raise ValueError("此任务不能取消")
    proc = PROCS.get(jid)
    if proc:
        proc.terminate()
    update_job(jid, status="cancelled", endedAt=now_ms(), summary="已取消")
    return {"ok": True}


def r_review(body):
    need(body, "account", "id", "action")
    aid, rid, act = str(body["account"]), str(body["id"]), body["action"]
    r = owned_review(aid, rid)
    if r["status"] == "已发布" and act != "delete":
        raise ValueError("这篇已经发出去了")
    if act == "ready":
        if r["status"] == "驳回":
            raise ValueError("稿件已驳回，请先修改")
        validate_draft(r)
        used = draft_clash(load_state(), r)["images"]
        if used:
            raise ValueError("第 %s 张图已经在「%s」用过了，同一张图发两个号容易被判搬运限流；把这张去掉再定稿"
                             % ("、".join(str(r["images"].index(u["img"]) + 1) for u in used), used[0]["account"]))
        mutate(lambda s: s["ready"].__setitem__(rid, {"at": now_ms()}))
    elif act == "unready":
        mutate(lambda s: s["ready"].pop(rid, None))
    elif act == "save":
        candidate = dict(r)
        candidate.update({k: body[k] for k in ("title", "body", "tags", "comment") if k in body})
        candidate["comment"] = str(candidate.get("comment") or "").strip()[:300]
        candidate["tags"] = split_tags(candidate["tags"])[:10]
        validate_draft(candidate, images=False)
        update_draft(rid, title=candidate["title"].strip(), body=candidate["body"], tags=candidate["tags"], comment=candidate["comment"], status="未审核" if r["status"] == "驳回" else r["status"])
        mutate(lambda s: s["ready"].pop(rid, None))
    elif act == "images":
        # 换封面、调顺序、去掉不要的图：只能用这篇原有的图
        imgs = body.get("images")
        if not isinstance(imgs, list) or not imgs or len(set(imgs)) != len(imgs) or any(x not in r["images"] for x in imgs):
            raise ValueError("图片只能从这篇原有的图里选，至少留 1 张")
        update_draft(rid, images=imgs)
        mutate(lambda s: s["ready"].pop(rid, None))
    elif act == "reject":
        update_draft(rid, status="驳回")
        mutate(lambda s: s["ready"].pop(rid, None))
    elif act == "delete":
        if any(str(e["review"]) == rid and e["status"] in ("pending", "running") for e in load_state()["schedule"]):
            raise ValueError("这篇已排期，先取消排期再删")
        mutate(lambda s: (s.__setitem__("drafts", [d for d in s["drafts"] if str(d["id"]) != rid]), s["ready"].pop(rid, None)))
    elif act == "rewrite":
        need(body, "note")
        jid = uuid.uuid4().hex[:10]
        out = STUDIO / "work" / ("rewrite_%s.json" % jid)
        src = {"title": r["title"], "body": r["body"], "tags": r["tags"], "comment": r.get("comment", "")}
        prompt = ("按我的意见改写这篇女装小红书笔记。意见：%s。\n原稿（JSON）：%s\n"
                  "%s\n标题 ≤18 字，正文 ≤888 字，不用 Markdown 符号。%s\n"
                  "置顶评论 comment ≤150 字，意见没提到就保持原样（原稿没有就补一条尺码答疑）。"
                  "改完存成 UTF-8 JSON 文件 %s，格式：{\"title\":\"\",\"titles\":[\"标题1\",\"标题2\",\"标题3\"],\"body\":\"\",\"tags\":[],\"comment\":\"\"}。不要发布。"
                  "最后一行只输出：{\"ok\":true}") % (body["note"], json.dumps(src, ensure_ascii=False),
                                                "\n".join([("写法参考 %s。" % COPY_GUIDE) if COPY_GUIDE.is_file() else "",
                                                           copy_rules(aid), item_facts(find_item(load_state(), r.get("item")) or {}), tag_rule(aid)]), FASHION_RULES, out)
        job = {"id": jid, "kind": "rewrite", "params": {"account": aid, "review": rid, "outFile": str(out)},
               "title": "改写「%s」" % r.get("title", ""), "prompt": prompt, "status": "queued", "createdAt": now_ms()}
        mutate(lambda s: s["jobs"].append(job))
        return {"ok": True, "job": job}
    else:
        raise ValueError("未知操作")
    return {"ok": True}


def r_publish(body):
    with LOCK:
        return _r_publish(body)


def _r_publish(body):
    need(body, "account", "id")
    r = owned_review(body["account"], body["id"])
    validate_draft(r)
    if r["status"] == "驳回":
        raise ValueError("稿件已驳回")
    if str(r["id"]) not in load_state()["ready"]:
        raise ValueError("请先审过这篇稿")
    at = body.get("at")
    if at == "auto":
        at = next_slot(load_state(), body["account"], item=r.get("item"))
        if not at:
            raise ValueError("30 天内没有空的发布时段，请自选时间")
    if at is not None and (isinstance(at, bool) or not re.fullmatch(r"[0-9]+", str(at))):
        raise ValueError("发布时间无效")
    if at is not None and int(at) <= now_ms():
        raise ValueError("发布时间已经过了")
    if any(str(s["review"]) == str(body["id"]) and s["status"] in ("pending", "running") for s in load_state()["schedule"]):
        raise ValueError("这篇稿已经排期或正在发布")
    when = int(at) if at else now_ms()
    near = next((s for s in load_state()["schedule"] if s["account"] == str(body["account"])
                 and s["status"] in ("pending", "running", "done") and abs(s.get("at", 0) - when) < 2 * 3600000), None)
    if near and not body.get("force"):
        raise ValueError("这个账号 %s 已经排了「%s」，前后 2 小时内不要再发；换个时间，或确认后强制排期" % (fmt_ms(near["at"]), near.get("title", "")))
    if at and not body.get("force"):
        st = load_state()
        live = [s for s in st["schedule"] if s["account"] != str(body["account"]) and s["status"] in ("pending", "running", "done")]
        names = {str(a["id"]): a["name"] for a in st["accounts"]}
        same = next((s for s in live if r.get("item") and schedule_item(st, s) == r["item"]
                     and abs((datetime.date.fromtimestamp(s["at"] / 1000) - datetime.date.fromtimestamp(when / 1000)).days) < ITEM_GAP_DAYS), None)
        if same:
            raise ValueError("同一款在「%s」%s 已经排了，不同号发同一款至少隔 %d 天；点「自动」会帮你错开"
                             % (names.get(same["account"], "别的号"), fmt_ms(same["at"]), ITEM_GAP_DAYS))
        close = next((s for s in live if abs(s["at"] - when) < CROSS_GAP_MIN * 60000), None)
        if close:
            raise ValueError("「%s」%s 要发，和它错开 %d 分钟以上，免得几个号同时发被当成批量操作"
                             % (names.get(close["account"], "别的号"), fmt_ms(close["at"]), CROSS_GAP_MIN))
    with LOCK:
        quote = QUOTES.get(body.get("quote"))
        if not quote or quote["expiresAt"] < now_ms() or quote["account"] != str(body["account"]) or quote["review"] != str(body["id"]) or quote["fingerprint"] != draft_fingerprint(r):
            raise ValueError("稿件在确认后改过，请重新点「通过并排期」")
        QUOTES.pop(body["quote"], None)
    mark_ai = body.get("markAi")
    entry = {"id": uuid.uuid4().hex[:10], "account": str(body["account"]), "review": str(body["id"]),
             "title": r["title"], "at": int(at) if at else now_ms(),
             "markAi": bool(load_state()["settings"].get("mark_ai", True) if mark_ai is None else mark_ai),
             "status": "pending", "createdAt": now_ms(), "fingerprint": draft_fingerprint(r)}
    # 不交给小红书平台定时：立即发的现在开浏览器，排了时间的由 scheduler 到点开浏览器发
    mutate(lambda s: s["schedule"].append(entry))
    if not at:
        threading.Thread(target=publish_one, args=(entry,), daemon=True).start()
    else:
        keep_awake(True)
    return {"ok": True, "entry": entry}


def r_reschedule(body):
    """没发出的（失败、错过、取消）一键重新排：稿件没改过就直接排进下一个空的高峰，改过要回去重新确认。"""
    need(body, "id")

    def check():
        old = next((e for e in load_state()["schedule"] if e["id"] == body["id"]), None)
        if not old or old["status"] not in ("failed", "missed", "cancelled"):
            raise ValueError("只有没发出的才能重新排期")
        if old.get("rescheduled"):
            raise ValueError("这篇已经重新排过了")
        r = review_by_id(old["review"])
        if not r:
            raise ValueError("这篇稿已经删了")
        if str(r["id"]) in load_state()["published"]:
            raise ValueError("这篇已经发过了")
        if str(r["id"]) not in load_state()["ready"] or draft_fingerprint(r) != old.get("fingerprint"):
            raise ValueError("稿件排期后改过，请到「发布 › 待发布」重新确认")
        return old
    old = check()
    q = r_publish_preview({"account": old["account"], "id": old["review"]})   # 顺便查登录，不在锁里等浏览器
    with LOCK:
        old = check()
        mark_ai = body["markAi"] if "markAi" in body else old.get("markAi")
        res = _r_publish({"account": old["account"], "id": old["review"], "at": body.get("at") or "auto", "quote": q["quote"], "markAi": mark_ai})
        set_entry(old["id"], rescheduled=res["entry"]["id"])
    return res


def draft_fingerprint(r):
    import hashlib
    content = {k: r.get(k) for k in ("id", "account_id", "title", "body", "tags", "images")}
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def r_publish_preview(body):
    """排期前的检查：稿件没问题、这个号在工作台扫过码。返回 10 分钟内有效的确认码。"""
    need(body, "account", "id")
    aid, rid = str(body["account"]), str(body["id"])
    r = owned_review(aid, rid)
    validate_draft(r)
    if r["status"] == "驳回" or rid not in load_state()["ready"]:
        raise ValueError("请先审过这篇稿")
    if rid in load_state()["published"]:
        raise ValueError("这篇已经发过了")
    if not PUBLISHER.is_file() and not DEMO:
        raise ValueError("找不到发布脚本：%s" % PUBLISHER)
    if not xhs_check(aid):
        raise ValueError("这个号还没在工作台扫码登录（或登录过期了），先点右上角「扫码登录」")
    token = uuid.uuid4().hex
    with LOCK:
        for key in list(QUOTES):
            if QUOTES[key]["expiresAt"] < now_ms():
                QUOTES.pop(key)
        QUOTES[token] = dict(account=aid, review=rid, expiresAt=now_ms() + 600000, fingerprint=draft_fingerprint(r))
    st = load_state()["settings"]
    it = find_item(load_state(), r.get("item")) if r.get("item") else None
    return {"ok": True, "quote": token, "goods": (it or {}).get("goods", ""), "markAi": st.get("mark_ai", True),
            "config": {"browser_visible": not st.get("xhs_headless"), "mark_ai_generated": st.get("mark_ai", True)}}


def r_publish_try(body):
    """只填不发：在发布页把这篇填好、截图，不点发布。"""
    need(body, "account", "id")
    r = owned_review(str(body["account"]), str(body["id"]))
    validate_draft(r)
    args, f = publish_args(r, body.get("markAi"))
    try:
        ok, data, raw = xhs_cli(args + ["--dry-run"], timeout=900)
    finally:
        f.unlink(missing_ok=True)
    if not ok:
        if "登录" in raw or "扫码" in raw:
            set_login(r["account_id"], False)
        raise ValueError("试填没成功：%s" % raw[-300:])
    return {"ok": True, "shot": data.get("shot", ""), "tags": data.get("tags") or {}}


def r_xhs_login(body):
    """弹出有界面的浏览器让人扫码；结束后自动记下是否登录成功。"""
    need(body, "account")
    aid = str(body["account"])
    require_account(aid)
    old = LOGIN_PROCS.get(aid)
    if old and old.poll() is None:
        return {"ok": True, "note": "扫码窗口已经开着"}
    if DEMO:
        set_login(aid, True)
        return {"ok": True}
    if not PUBLISHER.is_file():
        raise ValueError("找不到发布脚本：%s" % PUBLISHER)
    env = dict(os.environ, XHS_PUBLISHER_HOME=str(STUDIO / "publisher"))
    proc = subprocess.Popen([sys.executable, str(PUBLISHER), "login", "--account", xhs_profile(aid)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    LOGIN_PROCS[aid] = proc

    def wait():
        out = proc.communicate()[0] or ""
        data = None
        for line in reversed(out.strip().splitlines()):
            data = parse_json_loose(line.strip())
            if data:
                break
        set_login(aid, isinstance(data, dict) and data.get("ok") is True)
    threading.Thread(target=wait, daemon=True).start()
    return {"ok": True}


def r_xhs_check(body):
    need(body, "account")
    require_account(body["account"])
    return {"ok": True, "loggedIn": xhs_check(body["account"])}


def r_account(body):
    """加账号或改名。每个账号单独扫码登录。"""
    name = str(body.get("name") or "").strip()[:20]
    if not name:
        raise ValueError("写个账号名")
    aid = str(body.get("id") or "")
    persona = str(body.get("persona") or "")
    if persona and persona not in PERSONAS:
        raise ValueError("没有这个人设")

    def f(s):
        for a in s["accounts"]:
            if aid and str(a["id"]) == aid:
                a["name"] = name
                return a
        if aid:
            raise ValueError("找不到这个账号")
        nid = str(max([int(a["id"]) for a in s["accounts"] if str(a["id"]).isdigit()] + [0]) + 1)
        a = {"id": nid, "name": name, "profile": "acc" + nid}
        if body.get("fresh"):
            # 新注册的号：从今天起按起号节奏排（养号 → 冷启动 → 定型 → 稳定）
            t = datetime.date.today()
            a["startedAt"] = int(datetime.datetime(t.year, t.month, t.day).timestamp() * 1000)
        if persona:
            apply_persona(s, a, persona)
        s["accounts"].append(a)
        return a
    return {"ok": True, "account": mutate(f)}


def apply_persona(s, a, key):
    """套人设：文案设定、封面样式、出图拍法（风格 + 脸怎么处理）一次填好。"""
    p = PERSONAS[key]
    a["persona"] = key
    a.update(COPY_PRESETS[p["copy"]])
    a["cover"] = p["cover"]
    lp = s.setdefault("lastPick", {}).setdefault("byAcc", {})
    lp[str(a["id"])] = {"style": p["style"], "face": p["face"], "model": "", "refs": []}


def r_account_persona(body):
    """老号也能换人设（会覆盖文案设定、封面和拍法），或者设 / 取消起号日期。"""
    need(body, "id")
    require_account(body["id"])

    def f(s):
        a = next(x for x in s["accounts"] if str(x["id"]) == str(body["id"]))
        if "persona" in body:
            if body["persona"] not in PERSONAS:
                raise ValueError("没有这个人设")
            apply_persona(s, a, body["persona"])
        if "fresh" in body:
            if body["fresh"]:
                t = datetime.date.today()
                a["startedAt"] = int(datetime.datetime(t.year, t.month, t.day).timestamp() * 1000)
            else:
                a.pop("startedAt", None)
        if "weekly" in body:
            a["weekly"] = bounded_int(body["weekly"], 4, 14)
    mutate(f)
    return {"ok": True}


def r_account_copy(body):
    """文案设定：账号定位、口吻、每篇必写、禁用词、话题配比。"""
    need(body, "id")
    if "writeStyle" in body and body["writeStyle"] not in COPY_STYLES:
        raise ValueError("请选择高坂风格或日常分享")

    def f(s):
        for a in s["accounts"]:
            if str(a["id"]) == str(body["id"]):
                for k in COPY_KEYS:
                    if k in body:
                        a[k] = str(body[k] or "").strip()[:300]
                return
        raise ValueError("找不到这个账号")
    mutate(f)
    return {"ok": True}


def r_topic(body):
    """选题库：手动加一条、删掉、标回没用过。"""
    need(body, "action")
    act = body["action"]
    if act == "add":
        need(body, "account", "text")
        require_account(body["account"])
        text = str(body["text"]).strip()
        if len(text) > 25:
            raise ValueError("选题不能超过 25 字")
        st = load_state()
        if any(t["text"] == text and str(t["account"]) == str(body["account"]) for t in st["topics"]):
            raise ValueError("选题库里已经有这条了")
        t = {"id": "tp" + uuid.uuid4().hex[:8], "account": str(body["account"]), "text": text,
             "scene": body.get("scene") if body.get("scene") in FASHION["scenes"] else "",
             "body": body.get("body") if body.get("body") in FASHION["bodies"] else "",
             "why": "", "item": body.get("item") if find_item(st, body.get("item") or "") else "",
             "node": "", "source": "manual", "status": "unused", "createdAt": now_ms()}
        mutate(lambda s: s["topics"].append(t))
        return {"ok": True, "topic": t}
    need(body, "id")
    if not find_topic(load_state(), body["id"]):
        raise ValueError("找不到这条选题")
    if act == "delete":
        mutate(lambda s: s.__setitem__("topics", [t for t in s["topics"] if t["id"] != body["id"]]))
    elif act == "unuse":
        mutate(lambda s: find_topic(s, body["id"]).update(status="unused"))
    else:
        raise ValueError("未知操作")
    return {"ok": True}


def r_unschedule(body):
    need(body, "id")
    with LOCK:
        entry = next((e for e in load_state()["schedule"] if e["id"] == body["id"]), None)
        if not entry or entry["status"] != "pending":
            raise ValueError("只能取消待发排期")
        set_entry(body["id"], status="cancelled")
    return {"ok": True}


def r_item_save(body):
    need(body, "name")
    if body.get("account"):
        require_account(body["account"])
    item = {k: (body.get(k) or "") for k in ("id", "code", "name", "category", "colors", "sizes", "fabric", "texture", "pain", "price", "fit", "points", "status", "refDir", "account", "modelSize", "goods")}
    old = find_item(load_state(), item["id"]) if item["id"] else None
    item["lockName"] = (old or {}).get("lockName") or ("" if body.get("refDir") else safe_name(body.get("name")))
    item["specOk"] = bool((old or {}).get("specOk"))
    if old:
        # 人改过的项不再算 AI 填的；名称改过就不是占位名了
        item["aiFilled"] = [k for k in old.get("aiFilled", []) if item.get(k) == old.get(k)]
        if old.get("autoName") and item["name"] == old.get("name"):
            item["autoName"] = True
    if item["lockName"]:
        if not old:
            base, n = item["lockName"], 2
            while item_dir(item).exists() and not body.get("adopt"):
                item["lockName"] = "%s_%d" % (base, n)
                n += 1
        item["refDir"] = str(item_dir(item))
        item_dir(item).mkdir(parents=True, exist_ok=True)
    elif item["refDir"] and not Path(item["refDir"]).expanduser().is_dir():
        raise ValueError("参考图文件夹不存在：%s" % item["refDir"])
    if not item["id"]:
        item["id"] = "it" + uuid.uuid4().hex[:8]
        item["createdAt"] = now_ms()

    def f(s):
        for i, x in enumerate(s["items"]):
            if x["id"] == item["id"]:
                item["createdAt"] = x.get("createdAt", now_ms())
                s["items"][i] = item
                return
        s["items"].append(item)
    mutate(f)
    job = None
    # 材质和痛点都填好（或改过）就自动给这款出 5 条选题，进「文案 › 选题库」
    if item["texture"].strip() and item["pain"].strip() and (not old or (old.get("texture"), old.get("pain")) != (item["texture"], item["pain"])):
        aid = body.get("topicAccount") or item["account"] or load_state()["accounts"][0]["id"]
        try:
            job = queue_topics(str(aid), 5, item["id"])
        except ValueError:
            job = None
    return {"ok": True, "item": item, "topicJob": job}


def r_item_delete(body):
    need(body, "id")
    mutate(lambda s: s.__setitem__("items", [x for x in s["items"] if x["id"] != body["id"]]))
    return {"ok": True}


def r_settings(body):
    def f(s):
        for k in DEFAULT_SETTINGS:
            if k in body:
                s["settings"][k] = body[k]
    mutate(f)
    return {"ok": True}


def r_package(body):
    """手动发布包：不扣点，自己在小红书 App 或创作中心发。"""
    need(body, "account", "id")
    r = owned_review(body["account"], body["id"])
    validate_draft(r)
    safe = re.sub(r"[\\/:*?\"<>|\s]+", "", r["title"] or "untitled")[:20]
    d = STUDIO / "packages" / ("%s-%s-%s" % (datetime.datetime.now().strftime("%Y%m%d-%H%M"), safe, uuid.uuid4().hex[:6]))
    (d / "images").mkdir(parents=True, exist_ok=True)
    tags = " ".join("#" + t for t in r["tags"])
    (d / "title.txt").write_text(r["title"] or "", "utf-8")
    (d / "body.txt").write_text((r["body"] or "") + ("\n\n" + tags if tags else ""), "utf-8")
    (d / "tags.txt").write_text(tags, "utf-8")
    n = 0
    for i, src in enumerate(r["images"]):
        sp = Path(src).expanduser()
        if sp.is_file():
            shutil.copy2(sp, d / "images" / ("%02d%s" % (i + 1, sp.suffix.lower())))
            n += 1
    if n != len(r["images"]):
        raise ValueError("图片在导出期间发生变化，未生成完整发布包，请重试")
    if not DEMO and shutil.which("open"):
        subprocess.Popen(["open", str(d)])
    return {"ok": True, "dir": str(d), "images": n}


ROUTES = {
    "/api/publish/preview": r_publish_preview,
    "/api/package": r_package,
    "/api/job": r_job, "/api/job/cancel": r_job_cancel,
    "/api/review": r_review, "/api/publish": r_publish, "/api/unschedule": r_unschedule,
    "/api/item/save": r_item_save, "/api/item/delete": r_item_delete,
    "/api/item/photo": r_item_photo, "/api/item/new": r_item_new, "/api/item/checks": r_item_checks, "/api/ref/upload": r_ref_upload,
    "/api/settings": r_settings, "/api/account": r_account,
    "/api/xhs/login": r_xhs_login, "/api/xhs/check": r_xhs_check, "/api/publish/try": r_publish_try,
    "/api/reschedule": r_reschedule, "/api/account/copy": r_account_copy, "/api/topic": r_topic,
    "/api/prompt/preview": r_prompt_preview, "/api/ref/style": r_ref_style, "/api/ref/delete": r_ref_delete,
    "/api/style/group": r_style_group, "/api/item/real": r_item_real, "/api/account/cover": r_account_cover,
    "/api/account/persona": r_account_persona, "/api/lock/multi": r_lock_multi,
}


# ---------------------------------------------------------------- demo
PNG_1PX = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGO4dvYIAATkAmijj946AAAAAElFTkSuQmCC")  # 演示用的 1 像素米色图


def demo_seed():
    """演示模式第一次启动时放两篇稿，方便点一遍审稿和发布。"""
    if load_state()["drafts"]:
        return
    d = STUDIO / "images" / "demo"
    d.mkdir(parents=True, exist_ok=True)
    imgs = []
    for i in range(3):
        f = d / ("seed_%02d.png" % (i + 1))
        f.write_bytes(PNG_1PX)
        imgs.append(str(f))
    add_drafts("1", "", [
        {"title": "黑色无袖裙，细节在低腰拼接", "titles": ["160小个子穿黑裙不压个", "通勤约会一条裙搞定", "低腰拼接的小心机"],
         "body": "这条裙子最打动我的是低腰拼接，坐下来也不勒。\n\n面料是醋酸混纺，垂感好，夏天穿不闷。\n\n通勤配一件短开衫，周末换成凉拖草编包。",
         "tags": ["黑色连衣裙", "无袖连衣裙", "小个子穿搭", "通勤穿搭", "约会穿搭", "夏日女装", "简约穿搭", "显瘦连衣裙"], "images": imgs},
        {"title": "这件大衣是最好的平替", "body": "一比一还原大牌，绝对不起球。", "tags": ["大衣"], "images": []},
    ], "demo")
    r_topic({"action": "add", "account": "1", "text": "160 小个子冬天穿长外套怎么不显矮", "scene": "通勤", "body": "小个子"})
    r_topic({"action": "add", "account": "1", "text": "微胖女生约会裙子避开这 3 种", "scene": "约会", "body": "微胖"})


def demo_xhs(args):
    if args[0] == "check":
        aid = next((a["id"] for a in load_state()["accounts"] if xhs_profile(a["id"]) == args[2]), "")
        return True, {"ok": True, "loggedIn": bool(load_state()["xhsLogin"].get(str(aid), {}).get("loggedIn"))}, ""
    if args[0] == "publish":
        time.sleep(2)
        tags = args[args.index("--tags") + 1].split() if "--tags" in args else []
        return True, {"ok": True, "dryRun": "--dry-run" in args, "shot": "", "tags": {"matched": tags[:-1], "plain": tags[-1:]}}, ""
    return False, None, "不支持"


def demo_job_effect(job):
    p = job["params"]
    if job["kind"] == "write":
        imgs = p["images"]
        n = p["count"]
        parts = [imgs[i::n] for i in range(n)]
        drafts = [{"title": "演示稿件 %d" % (i + 1), "titles": ["演示标题甲", "演示标题乙", "演示标题丙"],
                   "body": "这是一条演示稿，用于验证写稿和审稿流程，不包含商品事实。",
                   "tags": ["演示", "穿搭", "女装", "通勤", "秋冬穿搭", "小个子穿搭", "外套", "日常穿搭"],
                   "comment": "演示置顶评论：模特 160cm 48kg 穿 M。", "images": parts[i]} for i in range(n)]
        Path(p["outFile"]).parent.mkdir(parents=True, exist_ok=True)
        Path(p["outFile"]).write_text(json.dumps({"drafts": drafts}, ensure_ascii=False), "utf-8")
        return {"ok": True, "drafts": n}
    if job["kind"] == "trial":
        Path(p["outFile"]).parent.mkdir(parents=True, exist_ok=True)
        Path(p["outFile"]).write_text(json.dumps({"drafts": [{"title": "试写：160 穿它不显矮", "titles": ["试写：160 穿它不显矮"],
                                                              "body": "这是一条演示试写，按文案设定写的样子。", "tags": ["演示", "穿搭"], "images": []}]}, ensure_ascii=False), "utf-8")
        return {"ok": True, "drafts": 1}
    if job["kind"] == "topics":
        sc, bd = FASHION["scenes"], FASHION["bodies"]
        tp = [{"text": "演示选题 %s × %s 怎么穿" % (sc[i % len(sc)], bd[i % len(bd)]), "scene": sc[i % len(sc)], "body": bd[i % len(bd)], "why": "演示"}
              for i in range(p["count"])]
        Path(p["outFile"]).parent.mkdir(parents=True, exist_ok=True)
        Path(p["outFile"]).write_text(json.dumps({"topics": tp}, ensure_ascii=False), "utf-8")
        return {"ok": True, "topics": len(tp)}
    if job["kind"] == "rewrite":
        r = review_by_id(p["review"])
        Path(p["outFile"]).parent.mkdir(parents=True, exist_ok=True)
        Path(p["outFile"]).write_text(json.dumps({"title": r["title"], "body": r["body"] + "\n演示改写已保存。", "tags": r["tags"]}, ensure_ascii=False), "utf-8")
        return {"ok": True}
    if job["kind"] == "spec":
        it = find_item(load_state(), p["item"])
        (item_dir(it) / "spec.md").write_text("# 产品档案：演示\n\n## 锁定参数\n### 衣服\n- 颜色：演示\n\n### 检查要点（放大逐条看）\n必过（任何一条不对判 C，返工）：\n1. 颜色对\n2. 领型对\n3. 背面开衩长度（待确认）\n尽量（不对评 B，写进报告，不返工）：\n4. 缝线拐角\n", "utf-8")
        (item_dir(it) / "info.json").write_text(json.dumps({"name": "银灰长毛翻领外套", "category": "外套", "colors": "银灰", "sizes": "S M L", "fabric": "羊毛 30% 锦纶 70%", "price": "", "code": "CT-2410-02"}, ensure_ascii=False), "utf-8")
        (item_dir(it) / "photo_notes.txt").write_text("", "utf-8")
        return {"ok": True}
    if job["kind"] == "lock":
        out = Path(p["jobDir"]) / "out"
        for i, ref in enumerate(p["refs"]):
            (out / ("演示_%02d.png" % (i + 1))).write_bytes(PNG_1PX)
        (Path(p["jobDir"]) / "report.md").write_text("\n".join("演示_%02d.png：评级 %s，演示" % (i + 1, "AB"[i % 2]) for i in range(len(p["refs"]))), "utf-8")
        return {"ok": True, "images": len(p["refs"])}
    raise ValueError("不支持的演示任务")


# ---------------------------------------------------------------- main
def ping(url):
    try:
        with urllib.request.urlopen(url + "api/ping", timeout=2) as r:
            return json.loads(r.read().decode("utf-8")).get("app") == APP_ID
    except Exception:
        return False


def main():
    global DEMO, STUDIO
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("XHS_STUDIO_PORT", str(DEFAULT_PORT))))
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    DEMO = a.demo
    url_file = STUDIO / "url.txt"
    if DEMO:
        STUDIO = STUDIO.parent / (STUDIO.name + "-demo")
    STUDIO.mkdir(parents=True, exist_ok=True)
    url_file.parent.mkdir(parents=True, exist_ok=True)
    # 端口可能被别的本机应用占用：已是本工作台就复用，否则往后找空端口
    srv, url = None, None
    for port in range(a.port, a.port + 20):
        url = "http://127.0.0.1:%d/" % port
        if ping(url):
            print("工作台已经在运行：" + url)
            url_file.write_text(url, "utf-8")
            if a.open:
                webbrowser.open(url)
            return
        try:
            srv = Server(("127.0.0.1", port), H)
            break
        except OSError:
            print("端口 %d 被别的程序占用，换下一个" % port)
    if srv is None:
        print("找不到可用端口")
        return
    url_file.write_text(url, "utf-8")

    def reset(s):
        for j in s["jobs"]:
            if j["status"] == "running":
                j["status"] = "failed"
                j["summary"] = "工作台重启时中断"
        for e in s["schedule"]:
            if e["status"] == "running":
                e["status"] = "failed"
                e["error"] = "工作台重启时中断，请到小红书创作中心确认是否已发出"
    mutate(reset)
    if DEMO:
        demo_seed()
    threading.Thread(target=job_worker, daemon=True).start()
    threading.Thread(target=scheduler, daemon=True).start()
    print("女装小红书工作台已启动：" + url + ("（演示模式）" if DEMO else ""))
    if a.open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
