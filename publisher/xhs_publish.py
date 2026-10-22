#!/usr/bin/env python3
"""
小红书图文发布（独立版）—— 从 RedBeacon 的 xhs/publish.py 拆出来，不再依赖 RedBeacon。

发布步骤、页面选择器和等待方式照搬原版（原版本身复刻 xiaohongshu-mcp），只改了这几处：
  1. 浏览器会话自己管：每个账号一个独立的浏览器资料夹，第一次扫码登录后一直有效；
  2. 话题只选和你写的完全一样的那个联想词，对不上就按普通文字留着，不再默认选第一个；
  3. 点完发布要等到「发布成功」才算成功，看不到就报错并截图，不再一律返回成功；
  4. 截图存到 ~/.xhs-studio/publisher/shots/，不放 /tmp；
  5. 多了「只填不发」（--dry-run）：所有内容填好、截图，但不点发布，用来安全试跑。

  python3 xhs_publish.py login  --account 主号            # 弹出浏览器，扫码登录一次
  python3 xhs_publish.py check  --account 主号            # 看登录是否还有效
  python3 xhs_publish.py publish --account 主号 --title 标题 --body-file 正文.txt \\
          --images 1.jpg,2.jpg --tags "外套 小个子穿搭" [--at "2026-10-22 20:00"] [--ai] [--dry-run]

需要先装一次：pip3 install playwright && python3 -m playwright install chromium
（装了 Google Chrome 会优先用 Chrome，更接近真人浏览器。）
结果最后一行输出 JSON：{"ok": true, ...} 或 {"ok": false, "error": "..."}，方便工作台和 Codex 读取。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("xhs.publish")

HOME = Path(os.environ.get("XHS_PUBLISHER_HOME") or (Path.home() / ".xhs-studio" / "publisher"))
SHOT_DIR = HOME / "shots"
URL_PUBLIC = "https://creator.xiaohongshu.com/publish/publish?source=official"
URL_LOGIN = "https://creator.xiaohongshu.com/login"

VISIBILITY_DEFAULT = "公开可见"
VISIBILITY_SUPPORTED = {"公开可见", "仅自己可见", "仅互关好友可见"}


def _shot(page, name: str) -> str:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = str(SHOT_DIR / f"{datetime.now():%Y%m%d-%H%M%S}_{name}.png")
    try:
        page.screenshot(path=path, full_page=True)
        logger.info(f"[publish] 截图：{path}")
        return path
    except Exception:
        return ""


# ──────────────────────────────────────────────────────────────────────────────
# 浏览器会话（替代 RedBeacon 的 session 模块）
# ──────────────────────────────────────────────────────────────────────────────

def profile_dir(account: str) -> Path:
    safe = re.sub(r"[\\/:*?\"<>|\s]+", "_", str(account)).strip("_") or "default"
    return HOME / "profiles" / safe


def open_context(pw, account: str, headless: bool):
    """每个账号一个持久化浏览器资料夹，登录状态（cookie）就存在里面，和别的账号互不干扰。"""
    d = profile_dir(account)
    d.mkdir(parents=True, exist_ok=True)
    kw = dict(user_data_dir=str(d), headless=headless, viewport={"width": 1366, "height": 900},
              locale="zh-CN", timezone_id="Asia/Shanghai", args=["--disable-blink-features=AutomationControlled"])
    if os.environ.get("XHS_BROWSER_PATH"):
        return pw.chromium.launch_persistent_context(executable_path=os.environ["XHS_BROWSER_PATH"], **kw)
    try:
        return pw.chromium.launch_persistent_context(channel="chrome", **kw)
    except Exception:
        return pw.chromium.launch_persistent_context(**kw)


def run_in_browser(account: str, fn, headless: bool = False):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx = open_context(pw, account, headless)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.set_default_timeout(60_000)
            return fn(page)
        finally:
            ctx.close()


def _logged_in(page) -> bool:
    page.goto(URL_PUBLIC, wait_until="domcontentloaded", timeout=120_000)
    page.wait_for_timeout(3_000)
    return "/login" not in page.url


def login(account: str, wait_sec: int = 300) -> dict:
    """打开有界面的浏览器，等用户在页面上扫码；离开登录页就算成功。"""
    def _do(page):
        if _logged_in(page):
            return {"ok": True, "account": account, "note": "已经是登录状态"}
        page.goto(URL_LOGIN, wait_until="domcontentloaded")
        print("请在弹出的浏览器里用小红书 App 扫码登录（%d 秒内）…" % wait_sec, flush=True)
        deadline = time.time() + wait_sec
        while time.time() < deadline:
            page.wait_for_timeout(2_000)
            if "/login" not in page.url:
                page.wait_for_timeout(3_000)
                return {"ok": True, "account": account}
        raise RuntimeError("等扫码超时，没有登录成功")
    return run_in_browser(account, _do, headless=False)


def check(account: str) -> dict:
    def _do(page):
        ok = _logged_in(page)
        return {"ok": True, "account": account, "loggedIn": ok}
    return run_in_browser(account, _do, headless=True)


# ──────────────────────────────────────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────────────────────────────────────

def publish_image(
    account: str,
    *,
    title: str,
    content: str,
    images: list[str],
    tags: Optional[list[str]] = None,
    schedule_time: Optional[datetime] = None,
    is_original: bool = False,
    is_ai_generated: bool = False,
    visibility: str = VISIBILITY_DEFAULT,
    products: Optional[list[str]] = None,
    dry_run: bool = False,
    headless: bool = False,
    page=None,
) -> dict:
    """
    发图文笔记。成功返回 {"ok": True, "tags": {...}, "shot": 截图}；失败抛 RuntimeError(msg)。
    page 只在测试时传入；平时自己开浏览器。
    """
    if not images:
        raise RuntimeError("图片不能为空")
    if not title.strip():
        raise RuntimeError("标题不能为空")
    if schedule_time is not None and schedule_time <= datetime.now():
        raise RuntimeError("定时发布时间已经过了")

    tags = [t.lstrip("#").strip() for t in (tags or []) if t.lstrip("#").strip()][:10]
    products = products or []
    visibility = visibility or VISIBILITY_DEFAULT

    def _do(page) -> dict:
        _open_publish_page(page, tab="上传图文")
        _upload_images(page, images)
        report = _submit_publish(
            page,
            title=title,
            content=content,
            tags=tags,
            schedule_time=schedule_time,
            is_original=is_original,
            is_ai_generated=is_ai_generated,
            visibility=visibility,
            products=products,
            video=False,
            dry_run=dry_run,
        )
        return dict(report, ok=True, dryRun=dry_run,
                    scheduled=schedule_time.strftime("%Y-%m-%d %H:%M") if schedule_time else "")

    if page is not None:
        return _do(page)
    return run_in_browser(account, _do, headless=headless)


# ──────────────────────────────────────────────────────────────────────────────
# 通用步骤（以下照搬原版，改动处标了「改：」）
# ──────────────────────────────────────────────────────────────────────────────

def _open_publish_page(page, tab: str) -> None:
    """打开发布页并切换到对应 tab。复刻 NewPublishImageAction/NewPublishVideoAction。"""
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.goto(URL_PUBLIC, wait_until="load", timeout=300_000)
    page.wait_for_timeout(2_000)
    # 改：被跳到登录页时直接说清楚，不去等一个永远出不来的发布框
    if "/login" in page.url:
        raise RuntimeError("小红书登录已失效，请先运行 login 扫码")
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10_000)
    except Exception:
        pass
    page.wait_for_timeout(1_000)
    _must_click_publish_tab(page, tab)
    page.wait_for_timeout(1_000)


def _must_click_publish_tab(page, tabname: str) -> None:
    """点击「上传图文 / 上传视频」TAB，必要时移除遮挡。"""
    page.wait_for_selector("div.upload-content", state="visible", timeout=30_000)

    deadline = time.time() + 15
    while time.time() < deadline:
        tab, blocked = _find_tab(page, tabname)
        if tab is None:
            page.wait_for_timeout(200)
            continue
        if blocked:
            logger.info("[publish] tab 被遮挡，尝试 JS click 并清理遮挡层")
            # 先尝试 JS click 绕过 overlay
            try:
                tab.evaluate("el => el.click()")
                logger.info("[publish] tab JS click 成功")
                return
            except Exception:
                pass
            _remove_pop_cover(page)
            page.wait_for_timeout(200)
            continue
        try:
            tab.click()
            return
        except Exception as e:
            logger.warning(f"[publish] 点击 TAB 失败：{e}")
            page.wait_for_timeout(200)

    # 截图留存方便调试
    try:
        ss_path = f"{SHOT_DIR}/xhs_publish_tab_fail_{int(time.time())}.png"
        page.screenshot(path=ss_path)
        logger.info(f"[publish] 截图已保存：{ss_path}")
    except Exception:
        pass
    raise RuntimeError(f"没有找到发布 TAB - {tabname}")


def _find_tab(page, tabname: str):
    """返回 (locator|None, blocked)。"""
    try:
        elems = page.locator("div.creator-tab")
        n = elems.count()
    except Exception:
        return None, False
    for i in range(n):
        elem = elems.nth(i)
        try:
            if not elem.is_visible():
                continue
            text = (elem.inner_text() or "").strip()
        except Exception:
            continue
        if text != tabname:
            continue
        blocked = False
        try:
            blocked = bool(elem.evaluate("""el => {
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) return true;
                const x = rect.left + rect.width / 2;
                const y = rect.top + rect.height / 2;
                const target = document.elementFromPoint(x, y);
                return !(target === el || el.contains(target));
            }"""))
        except Exception:
            pass
        return elem, blocked
    return None, False


def _remove_pop_cover(page) -> None:
    """移除可能的弹窗封面 + 兜底点空位。"""
    try:
        page.evaluate("""() => {
            // 移除各种可能的遮挡层（弹窗、引导、通知等）
            const selectors = [
                'div.d-popover',
                '.el-dialog__wrapper',
                '.el-overlay',
                '.mfe-creator-modal',
                '[class*="modal"]',
                '[class*="dialog"]',
                '[class*="overlay"]',
                '[class*="mask"]',
                '[class*="popup"]',
                '[class*="guide"]',
                '[class*="toast"]',
            ];
            selectors.forEach(sel => {
                document.querySelectorAll(sel).forEach(e => {
                    const style = window.getComputedStyle(e);
                    if (style.position === 'fixed' || style.position === 'absolute') {
                        e.remove();
                    }
                });
            });
        }""")
    except Exception:
        pass
    try:
        x = 380 + random.randint(0, 99)
        y = 20 + random.randint(0, 59)
        page.mouse.move(x, y)
        page.mouse.click(x, y)
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# 上传图片 / 视频
# ──────────────────────────────────────────────────────────────────────────────

def _upload_images(page, image_paths: list[str]) -> None:
    valid = [p for p in image_paths if Path(p).exists()]
    if not valid:
        raise RuntimeError("所有图片文件都不存在")

    for i, path in enumerate(valid):
        selector = ".upload-input" if i == 0 else 'input[type="file"]'
        try:
            page.locator(selector).first.set_input_files(path)
        except Exception as e:
            raise RuntimeError(f"上传第 {i+1} 张图片失败: {e}") from e
        logger.info(f"[publish] 已提交第 {i+1} 张图片：{path}")
        _wait_upload_complete(page, i + 1)
        page.wait_for_timeout(1_000)


def _wait_upload_complete(page, expected_count: int, max_wait_sec: int = 60) -> None:
    deadline = time.time() + max_wait_sec
    last_logged = expected_count - 1
    while time.time() < deadline:
        try:
            n = page.locator(".img-preview-area .pr").count()
        except Exception:
            n = 0
        if n != last_logged:
            logger.info(f"[publish] 上传进度 {n}/{expected_count}")
            last_logged = n
        if n >= expected_count:
            return
        page.wait_for_timeout(500)
    raise RuntimeError(f"第 {expected_count} 张图片上传超时（60s）")


def _upload_video(page, video_path: str) -> None:
    if not Path(video_path).exists():
        raise RuntimeError(f"视频文件不存在：{video_path}")
    # 先用 .upload-input，找不到退化到 input[type=file]
    try:
        page.locator(".upload-input").first.set_input_files(video_path, timeout=10_000)
    except Exception:
        try:
            page.locator('input[type="file"]').first.set_input_files(video_path, timeout=10_000)
        except Exception as e:
            raise RuntimeError(f"未找到视频上传输入框：{e}") from e
    _wait_publish_button_clickable(page, max_wait_sec=600)
    logger.info("[publish] 视频上传/处理完成")


def _wait_publish_button_clickable(page, max_wait_sec: int = 30):
    """等发布按钮变可点击（对齐 xiaohongshu-mcp dee6c25 fix）。

    优先 <xhs-publish-btn> Web Component（host 即按钮）：
      - 用 host 上的属性 is-publish/submit-disabled 判定，不进 shadow root
      - 点击：移到 host 包围盒右侧 65% 位置 + 鼠标点击（host 包含暂存+发布两块）
    回退 .publish-page-publish-btn button.bg-red（旧版）。

    返回 None — 调用方据此跳过 btn.click()（点击已在内部完成）。
    """
    deadline = time.time() + max_wait_sec
    last_reason = ""
    last_ss = 0.0
    while time.time() < deadline:
        # 1. 新版 <xhs-publish-btn>
        try:
            widgets = page.locator("xhs-publish-btn")
            wcount = widgets.count()
        except Exception:
            wcount = 0
        for i in range(wcount):
            w = widgets.nth(i)
            try:
                if not w.is_visible():
                    continue
                is_publish = w.get_attribute("is-publish")
                if is_publish == "false":
                    continue
                if w.get_attribute("submit-disabled") == "true":
                    last_reason = "新版按钮 submit-disabled=true"
                    continue
                _click_publish_widget(page, w)
                logger.info("[publish] 新版 xhs-publish-btn 点击完成")
                return None
            except Exception as e:
                logger.debug(f"[publish] widget {i} 检查失败：{e}")
                continue

        # 2. 旧版 button.bg-red 兜底
        try:
            old_btns = page.locator(".publish-page-publish-btn button.bg-red")
            ocount = old_btns.count()
        except Exception:
            ocount = 0
        for i in range(ocount):
            b = old_btns.nth(i)
            try:
                if not b.is_visible():
                    continue
                if b.get_attribute("disabled") is not None:
                    last_reason = "旧版按钮 disabled"
                    continue
                if b.get_attribute("aria-disabled") == "true":
                    last_reason = "旧版按钮 aria-disabled=true"
                    continue
                cls = b.get_attribute("class") or ""
                if " disabled " in f" {cls} ":
                    last_reason = "旧版按钮 class 含 disabled"
                    continue
                b.scroll_into_view_if_needed()
                page.wait_for_timeout(200)
                b.click()
                logger.info("[publish] 旧版 .bg-red 点击完成")
                return None
            except Exception as e:
                logger.debug(f"[publish] old btn {i} 检查失败：{e}")
                continue

        now = time.time()
        if now - last_ss >= 5:
            try:
                ss_path = f"{SHOT_DIR}/xhs_publish_wait_{int(now)}.png"
                page.screenshot(path=ss_path)
                logger.info(f"[publish] 等待按钮截图：{ss_path}")
                last_ss = now
            except Exception:
                pass
        page.wait_for_timeout(1_000)

    try:
        ss_path = f"{SHOT_DIR}/xhs_publish_btn_fail_{int(time.time())}.png"
        page.screenshot(path=ss_path)
        logger.info(f"[publish] 超时截图：{ss_path}")
    except Exception:
        pass
    suffix = f"：{last_reason}" if last_reason else ""
    raise RuntimeError(f"等待发布按钮可点击超时{suffix}")


def _click_publish_widget(page, widget) -> None:
    """点击 <xhs-publish-btn> host 右侧 65% 位置（避开"暂存离开"那一半）。"""
    widget.scroll_into_view_if_needed()
    page.wait_for_timeout(200)
    box = widget.bounding_box()
    if not box:
        raise RuntimeError("新版发布按钮位置获取失败")
    x = box["x"] + box["width"] * 0.65
    y = box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.click(x, y)


# ──────────────────────────────────────────────────────────────────────────────
# 提交：标题 / 正文 / 标签 / 选项 / 发布
# ──────────────────────────────────────────────────────────────────────────────

def _submit_publish(
    page,
    *,
    title: str,
    content: str,
    tags: list[str],
    schedule_time: Optional[datetime],
    is_original: bool,
    is_ai_generated: bool,
    visibility: str,
    products: list[str],
    video: bool,
    dry_run: bool = False,
) -> dict:
    # 1. 标题
    title_input = page.locator("div.d-input input").first
    title_input.fill(title)
    page.wait_for_timeout(500)
    if not video:
        _check_title_max_length(page)
    page.wait_for_timeout(1_000)

    # 2. 正文
    content_elem = _get_content_element(page)
    content_elem.fill(content)
    # 回点标题输入框（增强后续操作稳定性，复刻 waitAndClickTitleInput）
    page.wait_for_timeout(1_000)
    title_input.click()

    # 3. 标签
    tag_report = _input_tags(page, content_elem, tags)
    page.wait_for_timeout(1_000)
    if not video:
        _check_content_max_length(page)

    # 4. 定时发布
    if schedule_time is not None:
        _set_schedule_publish(page, schedule_time)

    # 5. 可见范围
    _set_visibility(page, visibility)

    # 6. 原创声明 / AI 声明（仅图文）
    if not video and is_original:
        try:
            _set_original(page)
        except Exception as e:
            logger.warning(f"[publish] 设置原创声明失败，继续发布：{e}")
    if not video and is_ai_generated:
        try:
            _set_ai_declaration(page)
        except Exception as e:
            logger.warning(f"[publish] 设置 AI 声明失败，继续发布：{e}")

    # 7. 商品
    if products:
        _bind_products(page, products)

    # 改：只填不发——到这里所有内容都填好了，截图给人看，不点发布
    if dry_run:
        page.wait_for_timeout(1_000)
        return {"tags": tag_report, "shot": _shot(page, "dry_run")}

    # 8. 点击发布（图文和视频都等按钮就绪再点）
    btn = _wait_publish_button_clickable(page)
    if btn is not None:
        btn.click()
    # 改：等到真的看到发布成功才算数（原版睡 5 秒就当成功）
    ok = _wait_publish_success(page)
    shot = _shot(page, "after_publish" if ok else "publish_unconfirmed")
    if not ok:
        raise RuntimeError("点了发布，但 30 秒内没看到「发布成功」，请到创作中心「笔记管理」确认是否发出（截图：%s）" % shot)
    return {"tags": tag_report, "shot": shot}


def _wait_publish_success(page, max_wait_sec: int = 30) -> bool:
    """改：新增。发布成功后创作中心会跳到 /publish/success 或弹出「发布成功」。"""
    deadline = time.time() + max_wait_sec
    while time.time() < deadline:
        try:
            if "publish/success" in page.url:
                return True
            if page.get_by_text(re.compile("发布成功|定时发布成功|已提交发布")).count() > 0:
                return True
        except Exception:
            pass
        page.wait_for_timeout(1_000)
    return False


def _check_title_max_length(page) -> None:
    loc = page.locator("div.title-container div.max_suffix")
    if loc.count() == 0:
        return
    text = loc.first.inner_text() or ""
    raise RuntimeError(_format_max_length_error(text))


def _check_content_max_length(page) -> None:
    loc = page.locator("div.edit-container div.length-error")
    if loc.count() == 0:
        return
    text = loc.first.inner_text() or ""
    raise RuntimeError(_format_max_length_error(text))


def _format_max_length_error(elem_text: str) -> str:
    parts = elem_text.split("/")
    if len(parts) != 2:
        return f"长度超过限制: {elem_text}"
    return f"当前输入长度为{parts[0]}，最大长度为{parts[1]}"


def _get_content_element(page):
    """正文输入框：先试 div.ql-editor，否则按 placeholder 找 [role=textbox]。"""
    # 简单 race：先看 ql-editor
    try:
        loc = page.locator("div.ql-editor").first
        loc.wait_for(state="visible", timeout=3_000)
        return loc
    except Exception:
        pass
    # placeholder fallback
    try:
        cand = page.locator('p[data-placeholder*="输入正文描述"]').first
        cand.wait_for(state="attached", timeout=3_000)
        # 向上找最多 5 层 [role=textbox]
        textbox = cand.evaluate_handle("""el => {
            let cur = el;
            for (let i = 0; i < 5 && cur; i++) {
                cur = cur.parentElement;
                if (cur && cur.getAttribute('role') === 'textbox') return cur;
            }
            return null;
        }""")
        if textbox:
            return textbox.as_element()
    except Exception:
        pass
    raise RuntimeError("没有找到内容输入框")


def _input_tags(page, content_elem, tags: list[str]) -> dict:
    """返回 {"matched": [...选中了平台话题的], "plain": [...没有完全一样的话题、按文字留着的]}。"""
    report = {"matched": [], "plain": []}
    if not tags:
        return report
    page.wait_for_timeout(1_000)
    # 复刻 Go：先按 20 下 ArrowDown 跳到末尾
    for _ in range(20):
        content_elem.press("ArrowDown")
        page.wait_for_timeout(10)
    content_elem.press("Enter")
    content_elem.press("Enter")
    page.wait_for_timeout(1_000)

    for raw in tags:
        tag = raw.lstrip("#")
        if not tag:
            continue
        hit = _input_one_tag(page, content_elem, tag)
        report["matched" if hit else "plain"].append(tag)
    return report


def topic_name(item_text: str) -> str:
    """联想下拉里一项的文字，如「#连衣裙\n2.1亿浏览」或「连衣裙[话题]」→「连衣裙」。"""
    first = (item_text or "").strip().splitlines()[0] if (item_text or "").strip() else ""
    return re.sub(r"\[话题\]#?$", "", first.strip()).lstrip("#").strip()


def pick_topic(item_texts: list[str], tag: str) -> int:
    """改：只选和要打的话题完全一样的那一项，返回下标；没有就返回 -1。
    原版总是点第一项，打「小个子穿搭」可能被换成「小个子穿搭技巧」这类别的话题。"""
    want = tag.strip().lstrip("#").lower()
    for i, t in enumerate(item_texts):
        if topic_name(t).lower() == want:
            return i
    return -1


def _input_one_tag(page, content_elem, tag: str) -> bool:
    content_elem.type("#")
    page.wait_for_timeout(200)
    for ch in tag:
        content_elem.type(ch)
        page.wait_for_timeout(50)
    page.wait_for_timeout(1_000)
    # 找联想下拉
    container = page.locator("#creator-editor-topic-container").first
    if container.count() == 0:
        content_elem.type(" ")
        return False
    items = container.locator(".item")
    texts = []
    for i in range(min(items.count(), 20)):
        try:
            texts.append(items.nth(i).inner_text() or "")
        except Exception:
            texts.append("")
    idx = pick_topic(texts, tag)
    if idx < 0:
        # 没有完全一样的话题：按普通文字留着，空格结束联想
        content_elem.type(" ")
        return False
    try:
        items.nth(idx).click()
        page.wait_for_timeout(700)
        return True
    except Exception:
        content_elem.type(" ")
        return False


# ── 可见范围 ───────────────────────────────────────────────────────────────────

def _set_visibility(page, visibility: str) -> None:
    if not visibility or visibility == VISIBILITY_DEFAULT:
        return
    if visibility not in VISIBILITY_SUPPORTED:
        raise RuntimeError(f"不支持的可见范围: {visibility}")

    dropdown = page.locator("div.permission-card-wrapper div.d-select-content").first
    dropdown.click()
    page.wait_for_timeout(500)

    opts = page.locator("div.d-options-wrapper div.d-grid-item div.custom-option")
    n = opts.count()
    for i in range(n):
        opt = opts.nth(i)
        try:
            text = opt.inner_text() or ""
        except Exception:
            continue
        if visibility in text:
            opt.click()
            page.wait_for_timeout(200)
            return
    raise RuntimeError(f"未找到可见范围选项: {visibility}")


# ── 定时发布 ───────────────────────────────────────────────────────────────────

def _set_schedule_publish(page, t: datetime) -> None:
    """开启小红书「定时发布」并填入时间。

    全程走 JS：① 开关用原生 .click() 绕过 Playwright 的 pointer_events 检查
    （Playwright 的 .click() 会因开关被遮挡而 "covered by <none>" 失败）；
    ② 日期框用原生 value setter + 派发 input/change/blur，React/Vue 才会认值。
    取法对齐上游 XiaohongshuSkills/scripts/cdp_publish.py 的 _set_schedule_post_time。
    """
    dt_str = t.strftime("%Y-%m-%d %H:%M")
    result = page.evaluate(
        """async (postTime) => {
            const sleep = (ms) => new Promise(r => setTimeout(r, ms));
            const visible = (n) => (
                n instanceof HTMLElement && n.offsetParent !== null &&
                n.getBoundingClientRect().width > 0 && n.getBoundingClientRect().height > 0
            );
            const sw = document.querySelector('.post-time-wrapper .d-switch');
            if (!(sw instanceof HTMLElement) || !visible(sw)) return '定时发布开关未找到';
            if (sw.getAttribute('aria-checked') !== 'true') {
                sw.click();
                await sleep(400);
            }
            const el = document.querySelector('.date-picker-container input');
            if (!(el instanceof HTMLInputElement)) return '定时发布日期输入框未找到';
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            el.focus();
            el.select();
            setter.call(el, postTime);
            el.dispatchEvent(new Event('input',  { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('blur',   { bubbles: true }));
            return 'ok';
        }""",
        dt_str,
    )
    if result != "ok":
        raise RuntimeError(f"设置定时发布失败：{result}")
    page.wait_for_timeout(600)


# ── 原创声明 ───────────────────────────────────────────────────────────────────

def _set_original(page) -> None:
    cards = page.locator("div.custom-switch-card")
    n = cards.count()
    for i in range(n):
        card = cards.nth(i)
        try:
            text = card.inner_text() or ""
        except Exception:
            continue
        if "原创声明" not in text:
            continue
        sw = card.locator("div.d-switch").first
        if sw.count() == 0:
            continue
        checked = sw.evaluate("""el => {
            const inp = el.querySelector('input[type="checkbox"]');
            return inp ? inp.checked : false;
        }""")
        if checked:
            return
        sw.click()
        page.wait_for_timeout(500)
        _confirm_original_declaration(page)
        return
    raise RuntimeError("未找到原创声明选项")


def _confirm_original_declaration(page) -> None:
    page.wait_for_timeout(800)
    try:
        page.evaluate("""() => {
            const footers = document.querySelectorAll('div.footer');
            for (const f of footers) {
                if (!f.textContent.includes('原创声明须知')) continue;
                const cb = f.querySelector('div.d-checkbox input[type="checkbox"]');
                if (cb && !cb.checked) cb.click();
                return 'found';
            }
            return 'not_found';
        }""")
    except Exception:
        pass
    page.wait_for_timeout(500)
    status = page.evaluate("""() => {
        const footers = document.querySelectorAll('div.footer');
        for (const f of footers) {
            if (!f.textContent.includes('声明原创')) continue;
            const btn = f.querySelector('button.custom-button');
            if (!btn) continue;
            if (btn.classList.contains('disabled') || btn.disabled) {
                const cb = f.querySelector('div.d-checkbox input[type="checkbox"]');
                if (cb && !cb.checked) cb.click();
                return 'button_disabled';
            }
            btn.click();
            return 'clicked';
        }
        return 'button_not_found';
    }""")
    if status == "button_not_found":
        raise RuntimeError("未找到声明原创按钮")
    if status == "button_disabled":
        raise RuntimeError("声明原创按钮仍处于禁用状态")
    page.wait_for_timeout(300)


# ── AI 声明 ────────────────────────────────────────────────────────────────────

def _set_ai_declaration(page) -> None:
    already = page.evaluate("""() => {
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode())) {
            if (node.textContent.trim() === '笔记含AI合成内容') {
                const p = node.parentElement;
                if (p && !p.closest('[class*="options"], [class*="popover"], [class*="dropdown"]')) return true;
            }
        }
        return false;
    }""")
    if already:
        return

    clicked = page.evaluate("""() => {
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode())) {
            const t = node.textContent.trim();
            if (t === '添加内容类型声明' || t === '内容类型声明') {
                let el = node.parentElement;
                for (let i = 0; i < 5; i++) {
                    if (!el) break;
                    el.click();
                    return true;
                }
            }
        }
        return false;
    }""")
    if not clicked:
        raise RuntimeError("未找到内容类型声明下拉框")
    page.wait_for_timeout(800)

    result = page.evaluate("""() => {
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode())) {
            if (node.textContent.trim() === '笔记含AI合成内容') {
                let el = node.parentElement;
                for (let i = 0; i < 5; i++) {
                    if (!el) break;
                    const s = window.getComputedStyle(el);
                    if (s.cursor === 'pointer' || el.onclick || el.tagName === 'LI') {
                        el.click();
                        return 'clicked';
                    }
                    el = el.parentElement;
                }
                node.parentElement.click();
                return 'clicked_fallback';
            }
        }
        return 'not_found';
    }""")
    if result == "not_found":
        raise RuntimeError("未找到[笔记含AI合成内容]选项")
    page.wait_for_timeout(300)


# ── 绑定商品 ───────────────────────────────────────────────────────────────────

def _bind_products(page, products: list[str]) -> None:
    _click_add_product_button(page)
    page.wait_for_timeout(1_000)

    modal = _wait_product_modal(page)
    failed: list[str] = []
    for kw in products:
        try:
            _search_and_select_product(page, modal, kw)
        except Exception as e:
            logger.warning(f"[publish] 选商品失败 keyword={kw}: {e}")
            failed.append(kw)
        page.wait_for_timeout(500)

    _click_modal_save_button(modal)
    _wait_modal_close(page)

    if failed:
        raise RuntimeError(f"部分商品未找到: {failed}")


def _click_add_product_button(page) -> None:
    spans = page.locator("span.d-text")
    n = spans.count()
    for i in range(n):
        span = spans.nth(i)
        try:
            text = (span.inner_text() or "").strip()
        except Exception:
            continue
        if text != "添加商品":
            continue
        # 向上找最多 5 层可点击父
        cur = span
        for _ in range(5):
            try:
                parent = cur.locator("xpath=..")
            except Exception:
                break
            try:
                tag = (parent.evaluate("el => el.tagName.toLowerCase()") or "").lower()
            except Exception:
                cur = parent
                continue
            cls = parent.get_attribute("class") or ""
            if tag == "button" or "d-button" in cls:
                parent.click()
                page.wait_for_timeout(300)
                return
            cur = parent
    raise RuntimeError("未找到添加商品按钮，账号可能未开通商品功能")


def _wait_product_modal(page):
    deadline = time.time() + 10
    while time.time() < deadline:
        modal = page.locator(".multi-goods-selector-modal").first
        if modal.count() > 0 and modal.is_visible():
            return modal
        page.wait_for_timeout(100)
    raise RuntimeError("等待商品选择弹窗超时")


def _search_and_select_product(page, modal, keyword: str) -> None:
    search_input = modal.locator('input[placeholder="搜索商品ID 或 商品名称"]').first
    try:
        search_input.fill(keyword)
    except Exception as e:
        raise RuntimeError(f"输入搜索关键词失败: {e}") from e
    page.wait_for_timeout(300)
    page.keyboard.press("Enter")
    page.wait_for_timeout(1_000)

    # 等 loading 消失
    deadline = time.time() + 10
    while time.time() < deadline:
        loading = modal.locator(".goods-list-loading").first
        if loading.count() == 0 or not loading.is_visible():
            break
        page.wait_for_timeout(100)

    # 等商品出现
    while time.time() < deadline:
        if modal.locator(".goods-list-normal .good-card-container").count() > 0:
            break
        page.wait_for_timeout(100)
    page.wait_for_timeout(500)

    cb = modal.locator(".goods-list-normal .good-card-container .d-checkbox").first
    if cb.count() == 0:
        raise RuntimeError("未找到商品选择框")

    already = cb.evaluate("""el => (
        el.querySelector('.d-checkbox-simulator.checked') !== null ||
        el.querySelector('input[type="checkbox"]:checked') !== null
    )""")
    if already:
        return
    cb.click()
    page.wait_for_timeout(800 + random.randint(0, 700))


def _click_modal_save_button(modal) -> None:
    btn = modal.locator(".goods-selected-footer button").first
    if btn.count() > 0:
        try:
            btn.click()
            return
        except Exception:
            pass
    primary = modal.locator(".goods-selected-footer .d-button--primary").first
    if primary.count() > 0:
        try:
            primary.click()
        except Exception:
            pass


def _wait_modal_close(page) -> None:
    deadline = time.time() + 5
    while time.time() < deadline:
        if page.locator(".multi-goods-selector-modal").count() == 0:
            return
        page.wait_for_timeout(200)
    # 关不掉不致命，只警告
    logger.warning("[publish] 商品弹窗 5s 内未关闭，继续")



# ──────────────────────────────────────────────────────────────────────────────
# 命令行
# ──────────────────────────────────────────────────────────────────────────────

def _parse_at(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    for f in ("%Y-%m-%d %H:%M", "%m-%d %H:%M"):
        try:
            d = datetime.strptime(s, f)
            return d.replace(year=datetime.now().year) if f == "%m-%d %H:%M" else d
        except ValueError:
            pass
    raise RuntimeError("定时时间格式不对，写成 2026-10-22 20:00")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="小红书图文发布（独立版）")
    sp = ap.add_subparsers(dest="cmd", required=True)
    for name in ("login", "check", "publish"):
        x = sp.add_parser(name)
        x.add_argument("--account", required=True, help="账号的名字，自己起，比如 主号；每个名字一套登录")
    x = sp.choices["publish"]
    x.add_argument("--title", required=True)
    g = x.add_mutually_exclusive_group(required=True)
    g.add_argument("--body", help="正文")
    g.add_argument("--body-file", help="正文所在的 UTF-8 文本文件")
    x.add_argument("--images", required=True, help="图片路径，逗号分开，第一张是封面")
    x.add_argument("--tags", default="", help="话题，空格或逗号分开，最多 10 个")
    x.add_argument("--at", help="小红书定时发布，例如 2026-10-22 20:00（平台代为定时，Mac 不用开着）")
    x.add_argument("--ai", action="store_true", help="勾选「笔记含 AI 合成内容」")
    x.add_argument("--goods", default="", help="挂的商品：店铺里的商品 ID 或名称，逗号分开（账号要开通商品功能）")
    x.add_argument("--original", action="store_true", help="勾选原创声明")
    x.add_argument("--visibility", default=VISIBILITY_DEFAULT, choices=sorted(VISIBILITY_SUPPORTED))
    x.add_argument("--dry-run", action="store_true", help="只填不发：填好后截图，不点发布")
    x.add_argument("--headless", action="store_true", help="不显示浏览器窗口")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    try:
        if a.cmd == "login":
            res = login(a.account)
        elif a.cmd == "check":
            res = check(a.account)
        else:
            body = a.body if a.body is not None else Path(a.body_file).expanduser().read_text("utf-8")
            images = [str(Path(p.strip()).expanduser()) for p in a.images.split(",") if p.strip()]
            missing = [p for p in images if not Path(p).is_file()]
            if missing:
                raise RuntimeError("找不到图片：" + "、".join(missing))
            if len(a.title) > 20:
                raise RuntimeError("标题 %d 字，小红书最多 20 字" % len(a.title))
            if len(body) > 1000:
                raise RuntimeError("正文 %d 字，小红书最多 1000 字" % len(body))
            res = publish_image(a.account, title=a.title, content=body, images=images,
                                tags=re.split(r"[\s,，、]+", a.tags.strip()) if a.tags.strip() else [],
                                schedule_time=_parse_at(a.at), is_original=a.original, is_ai_generated=a.ai,
                                visibility=a.visibility, dry_run=a.dry_run, headless=a.headless,
                                products=[g.strip() for g in re.split(r"[,，、]+", a.goods) if g.strip()])
    except ModuleNotFoundError:
        res = {"ok": False, "error": "还没装 Playwright：pip3 install playwright && python3 -m playwright install chromium"}
    except Exception as e:
        res = {"ok": False, "error": str(e)}
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
