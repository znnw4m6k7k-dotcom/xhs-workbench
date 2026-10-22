"""隔离回归：python3 -m unittest discover -s <panel目录> -p test_server.py -v"""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# 测试固定用仓库里的示例设置，不受各店自己 fashion.json 的影响
os.environ["XHS_FASHION_FILE"] = str(Path(__file__).with_name("fashion.example.json"))


class PanelTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("panel_test_subject", Path(__file__).with_name("server.py"))
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.tmp = tempfile.TemporaryDirectory(prefix="xhs-regression-")
        self.m.STUDIO = Path(self.tmp.name)
        self.m.DEFAULT_SETTINGS["lock_studio_path"] = str(Path(self.tmp.name) / "lock")
        self.m.DEMO = True
        self.image = self.m.STUDIO / "images" / "01.png"
        self.image.parent.mkdir()
        self.image.write_bytes(self.m.PNG_1PX)
        self.image2 = self.m.STUDIO / "images" / "02.png"
        self.image2.write_bytes(self.m.PNG_1PX)
        self.rid = self.m.add_drafts("1", "", [{"title": "演示稿", "body": "只用于验证流程的演示正文。",
                                               "tags": ["演示", "女装", "通勤", "穿搭", "参考"], "images": [str(self.image), str(self.image2)]}], "test")[0]

    def tearDown(self):
        self.tmp.cleanup()

    def ready(self, rid=None):
        self.m.r_review({"account": "1", "id": rid or self.rid, "action": "ready"})

    def login(self):
        self.m.set_login("1", True)

    def quote(self, rid=None):
        self.ready(rid)
        self.login()
        return self.m.r_publish_preview({"account": "1", "id": rid or self.rid})["quote"]

    def schedule(self, at=None):
        return self.m.r_publish({"account": "1", "id": self.rid, "quote": self.quote(), "at": at or self.m.now_ms() + 1800000})["entry"]

    def entry(self, eid):
        return next(e for e in self.m.load_state()["schedule"] if e["id"] == eid)

    # ---------------------------------------------------------------- 数据
    def test_01_default_account_and_state(self):
        st = self.m.api_state()
        a = st["accounts"]
        self.assertEqual([(x["id"], x["name"], x["online"]) for x in a], [("1", "女装主号", False)])
        self.assertEqual(st["reviews"][0]["title"], "演示稿")
        self.assertNotIn("redbeacon", json.dumps(list(st.keys())))

    def test_02_existing_login_profile_is_reused(self):
        (self.m.STUDIO / "publisher" / "profiles" / "rb2").mkdir(parents=True)
        st = json.loads(self.m.state_path().read_text())
        st.pop("accounts")
        self.m.state_path().write_text(json.dumps(st))
        self.assertEqual(self.m.xhs_profile("1"), "rb2")

    def test_03_add_and_rename_account(self):
        a = self.m.r_account({"name": "女装二号"})["account"]
        self.assertEqual((a["id"], a["profile"]), ("2", "acc2"))
        self.m.r_account({"id": "2", "name": "副号"})
        self.assertEqual(self.m.account_name("2"), "副号")
        with self.assertRaises(ValueError):
            self.m.r_account({"name": " "})

    def test_04_unknown_account_and_cross_account(self):
        self.m.r_account({"name": "副号"})
        with self.assertRaises(ValueError):
            self.m.r_review({"account": "9", "id": self.rid, "action": "ready"})
        with self.assertRaises(ValueError):
            self.m.r_review({"account": "2", "id": self.rid, "action": "ready"})

    def test_04b_upgrade_from_old_state_cancels_old_schedule(self):
        st = json.loads(self.m.state_path().read_text())
        st.pop("dataVersion")
        st["ready"] = {str(self.rid): {"at": 1}}
        st["published"] = {str(self.rid): {"account": "1", "title": "旧的", "at": 1}}
        st["schedule"] = [{"id": "old", "account": "2", "review": str(self.rid), "status": "pending", "at": self.m.now_ms() + 9e6}]
        self.m.state_path().write_text(json.dumps(st))
        s2 = self.m.load_state()
        self.assertEqual(s2["ready"], {})
        self.assertEqual(s2["schedule"][0]["status"], "cancelled")
        self.assertNotIn(str(self.rid), s2["published"])
        self.assertEqual(len(s2["published"]), 1)

    # ---------------------------------------------------------------- 审稿
    def test_05_no_image_blocks_ready(self):
        rid = self.m.add_drafts("1", "", [{"title": "无图", "body": "正文", "tags": [], "images": []}], "t")[0]
        with self.assertRaises(ValueError):
            self.ready(rid)

    def test_06_length_risk_markdown(self):
        for edit in ({"title": "标" * 19}, {"body": "字" * 889}, {"body": "这是最好的一件"}, {"body": "**粗体**"}):
            with self.assertRaises(ValueError):
                self.m.r_review({"account": "1", "id": self.rid, "action": "save", **edit})

    def test_07_save_clears_ready(self):
        self.ready()
        self.m.r_review({"account": "1", "id": self.rid, "action": "save", "title": "新标题", "tags": ["#a", "b"]})
        r = self.m.review_by_id(self.rid)
        self.assertEqual((r["title"], r["tags"]), ("新标题", ["a", "b"]))
        self.assertNotIn(str(self.rid), self.m.load_state()["ready"])

    def test_08_reorder_cover_and_drop_images(self):
        self.ready()
        self.m.r_review({"account": "1", "id": self.rid, "action": "images", "images": [str(self.image2), str(self.image)]})
        self.assertEqual(self.m.review_by_id(self.rid)["images"][0], str(self.image2))
        self.assertNotIn(str(self.rid), self.m.load_state()["ready"])
        self.m.r_review({"account": "1", "id": self.rid, "action": "images", "images": [str(self.image)]})
        self.assertEqual(self.m.review_by_id(self.rid)["images"], [str(self.image)])
        for bad in ([], ["/etc/passwd"], [str(self.image), str(self.image)], [str(self.image2)]):
            with self.assertRaises(ValueError):
                self.m.r_review({"account": "1", "id": self.rid, "action": "images", "images": bad})

    def test_09_reject_and_delete(self):
        self.m.r_review({"account": "1", "id": self.rid, "action": "reject"})
        with self.assertRaises(ValueError):
            self.ready()
        self.m.r_review({"account": "1", "id": self.rid, "action": "save", "body": "改好了的正文"})
        self.assertEqual(self.m.review_by_id(self.rid)["status"], "未审核")
        self.m.r_review({"account": "1", "id": self.rid, "action": "delete"})
        self.assertIsNone(self.m.review_by_id(self.rid))

    def test_10_scheduled_draft_cannot_be_deleted(self):
        self.schedule()
        with self.assertRaises(ValueError):
            self.m.r_review({"account": "1", "id": self.rid, "action": "delete"})

    def test_11_package_has_all_files(self):
        res = self.m.r_package({"account": "1", "id": self.rid})
        self.assertEqual(res["images"], 2)
        self.assertTrue((Path(res["dir"]) / "body.txt").is_file())

    # ---------------------------------------------------------------- 写稿和改稿
    def test_12_write_job_imports_drafts(self):
        job = self.m.r_job({"kind": "write", "params": {"account": "1", "item": self.item_id(), "images": [str(self.image), str(self.image2)]}})["job"]
        self.assertIn(job["params"]["outFile"], job["prompt"])
        self.assertNotIn("redbeacon review create", job["prompt"])
        self.m.verify_job(job, self.m.demo_job_effect(job), {"t": 0})
        r = self.m.review_by_id(self.m.load_state()["jobs"][-1]["drafts"][0])
        self.assertEqual(r["titles"], ["演示标题甲", "演示标题乙", "演示标题丙"])
        self.assertEqual(r["item"], self.item_id())

    def test_13_write_job_rejects_bad_file(self):
        job = self.m.r_job({"kind": "write", "params": {"account": "1", "item": self.item_id(), "images": [str(self.image)]}})["job"]
        with self.assertRaises(ValueError):
            self.m.verify_job(job, {"ok": True, "drafts": 1}, {"t": 0})
        Path(job["params"]["outFile"]).parent.mkdir(parents=True, exist_ok=True)
        Path(job["params"]["outFile"]).write_text(json.dumps({"drafts": [{"title": "绝对第一", "body": "x", "tags": [], "images": [str(self.image)]}]}))
        with self.assertRaises(ValueError):
            self.m.verify_job(job, {"ok": True, "drafts": 1}, {"t": 0})
        Path(job["params"]["outFile"]).write_text(json.dumps({"drafts": [{"title": "好", "body": "x", "tags": [], "images": ["/etc/hosts.png"]}]}))
        with self.assertRaises(ValueError):
            self.m.verify_job(job, {"ok": True, "drafts": 1}, {"t": 0})
        with self.assertRaises(ValueError):
            self.m.verify_job(job, {"ok": False}, {"t": 0})

    def test_14_rewrite_updates_draft(self):
        self.ready()
        job = self.m.r_review({"account": "1", "id": self.rid, "action": "rewrite", "note": "开头口语一点"})["job"]
        self.assertIn("开头口语一点", job["prompt"])
        self.m.verify_job(job, self.m.demo_job_effect(job), {"t": 0})
        self.assertIn("演示改写已保存", self.m.review_by_id(self.rid)["body"])
        self.assertNotIn(str(self.rid), self.m.load_state()["ready"])

    def test_15_rewrite_without_change_fails(self):
        job = self.m.r_review({"account": "1", "id": self.rid, "action": "rewrite", "note": "x"})["job"]
        r = self.m.review_by_id(self.rid)
        Path(job["params"]["outFile"]).parent.mkdir(parents=True, exist_ok=True)
        Path(job["params"]["outFile"]).write_text(json.dumps({"title": r["title"], "body": r["body"], "tags": r["tags"]}))
        with self.assertRaises(ValueError):
            self.m.verify_job(job, {"ok": True}, {"t": 0})

    def item_id(self):
        if not hasattr(self, "_it"):
            self._it = self.m.r_item_save({"name": "演示单品", "refDir": str(self.image.parent), "goods": "CT-01, 银灰外套"})["item"]["id"]
        return self._it

    # ---------------------------------------------------------------- 发布
    def test_16_preview_needs_ready_and_login(self):
        with self.assertRaises(ValueError):
            self.m.r_publish_preview({"account": "1", "id": self.rid})
        self.ready()
        with self.assertRaises(ValueError):
            self.m.r_publish_preview({"account": "1", "id": self.rid})
        self.login()
        q = self.m.r_publish_preview({"account": "1", "id": self.rid})
        self.assertTrue(q["quote"])

    def test_17_quote_bound_to_content_and_expiry(self):
        q = self.quote()
        self.m.r_review({"account": "1", "id": self.rid, "action": "images", "images": [str(self.image)]})
        self.ready()
        with self.assertRaises(ValueError):
            self.m.r_publish({"account": "1", "id": self.rid, "quote": q})
        q = self.quote()
        self.m.QUOTES[q]["expiresAt"] = 0
        with self.assertRaises(ValueError):
            self.m.r_publish({"account": "1", "id": self.rid, "quote": q})

    def test_18_past_time_and_duplicate(self):
        with self.assertRaises(ValueError):
            self.m.r_publish({"account": "1", "id": self.rid, "quote": self.quote(), "at": self.m.now_ms() - 1000})
        self.schedule()
        with self.assertRaises(ValueError):
            self.m.r_publish({"account": "1", "id": self.rid, "quote": self.quote(), "at": self.m.now_ms() + 2400000})

    def test_19_scheduled_posts_never_go_to_platform(self):
        # 不用小红书定时：排了时间的不马上开浏览器，到点由 scheduler 发，参数里也没有 --at
        with patch.object(self.m.threading, "Thread") as T:
            e = self.m.r_publish({"account": "1", "id": self.rid, "quote": self.quote(), "at": self.m.now_ms() + 3 * 3600000})["entry"]
        self.assertNotIn("platformAt", e)
        T.assert_not_called()
        calls = []
        with patch.object(self.m, "xhs_cli", side_effect=lambda a, timeout=600: (calls.append(a), (True, {"ok": True}, ""))[1]):
            self.m.publish_one(e)
        self.assertNotIn("--at", calls[0])
        with patch.object(self.m.threading, "Thread") as T:
            rid = self.m.add_drafts("1", "", [{"title": "第二篇", "body": "正文", "tags": [], "images": [str(self.image)]}], "t")[0]
            self.m.r_publish({"account": "1", "id": rid, "quote": self.quote(rid), "force": True})
        T.assert_called_once()

    def test_20_publish_args_and_success(self):
        self.m.update_draft(self.rid, item=self.item_id())
        e = self.schedule()
        calls = []
        def cli(args, timeout=600):
            calls.append(args)
            return True, {"ok": True, "shot": "", "tags": {"plain": []}}, ""
        with patch.object(self.m, "xhs_cli", side_effect=cli):
            self.m.publish_one(e)
        a = calls[0]
        self.assertEqual(a[a.index("--goods") + 1], "CT-01,银灰外套")
        self.assertIn("--ai", a)
        self.assertEqual(a[a.index("--images") + 1], ",".join([str(self.image), str(self.image2)]))
        self.assertEqual(self.entry(e["id"])["status"], "done")
        self.assertEqual(self.m.review_by_id(self.rid)["status"], "已发布")
        self.assertIn(str(self.rid), self.m.load_state()["published"])
        self.assertEqual(self.m.api_state()["archive"][0]["title"], "演示稿")
        self.assertFalse(list((self.m.STUDIO / "publisher").glob("body_*")))

    def test_21_failed_publish_keeps_draft_and_flags_login(self):
        e = self.schedule()
        with patch.object(self.m, "xhs_cli", return_value=(False, None, "登录失效了，请重新扫码登录")):
            self.m.publish_one(e)
        self.assertEqual(self.entry(e["id"])["status"], "failed")
        self.assertEqual(self.m.review_by_id(self.rid)["status"], "未审核")
        self.assertFalse(self.m.load_state()["xhsLogin"]["1"]["loggedIn"])

    def test_22_changed_draft_blocks_publish(self):
        e = self.schedule()
        self.m.update_draft(self.rid, body="偷偷改了")
        with patch.object(self.m, "xhs_cli") as cli:
            self.m.publish_one(e)
        cli.assert_not_called()
        self.assertIn("已修改", self.entry(e["id"])["error"])

    def test_23_published_twice_is_blocked(self):
        e = self.schedule()
        with patch.object(self.m, "xhs_cli", return_value=(True, {"ok": True}, "")):
            self.m.publish_one(e)
        with self.assertRaises(ValueError):
            self.m.r_publish_preview({"account": "1", "id": self.rid})

    def test_24_two_hour_spacing(self):
        self.schedule(self.m.now_ms() + 1800000)
        rid = self.m.add_drafts("1", "", [{"title": "第二篇", "body": "正文", "tags": [], "images": [str(self.image)]}], "t")[0]
        q = self.quote(rid)
        with self.assertRaises(ValueError):
            self.m.r_publish({"account": "1", "id": rid, "quote": q, "at": self.m.now_ms() + 2400000})

    def test_25_try_fill_is_dry_run(self):
        self.ready()
        with patch.object(self.m, "xhs_cli", return_value=(True, {"ok": True, "shot": "/x.png", "tags": {}}, "")) as cli:
            res = self.m.r_publish_try({"account": "1", "id": self.rid})
        self.assertIn("--dry-run", cli.call_args[0][0])
        self.assertEqual(res["shot"], "/x.png")

    def test_26_schedule_loop_missed_and_future(self):
        e = self.schedule(self.m.now_ms() + 1800000)
        self.m.set_entry(e["id"], at=self.m.now_ms() - 10 * 3600000)
        st = self.m.load_state()
        grace = int(st["settings"]["late_grace_min"]) * 60000
        for x in st["schedule"]:
            if x["status"] == "pending" and self.m.now_ms() - x["at"] > grace:
                self.m.set_entry(x["id"], status="missed")
        self.assertEqual(self.entry(e["id"])["status"], "missed")

    # ---------------------------------------------------------------- 锁图出图
    def lock_setup(self):
        root = self.m.lock_root()
        for d in ("library/originals", "library/references", "library/models/小禾", "jobs"):
            (root / d).mkdir(parents=True, exist_ok=True)
        (root / "AGENTS.md").write_text("rules", "utf-8")
        png = self.image.read_bytes()
        for f in ("ref1_走路.jpg", "ref2_挥手.jpg"):
            (root / "library/references" / f).write_bytes(png)
        for f in ("face_1_front.jpg", "face_2_half.jpg", "face_closeup.jpg"):
            (root / "library/models/小禾" / f).write_bytes(png)
        (root / "library/models/小禾/profile.md").write_text("# 小禾 160/48", "utf-8")
        it = self.m.r_item_save({"name": "灰色外套", "modelSize": "160cm 48kg 穿 M"})["item"]
        data = "data:image/png;base64," + self.m.base64.b64encode(png * 3).decode()
        self.m.r_item_photo({"id": it["id"], "slot": "front", "data": data})
        return root, it

    def run_one(self, job):
        before = {"t": self.m.now_ms() - 5000}
        result = self.m.demo_job_effect(job)
        self.m.verify_job(job, result, before)
        self.m.update_job(job["id"], status="done")
        return result

    def test_47_item_photo_and_status(self):
        root, it = self.lock_setup()
        v = next(x for x in self.m.api_state()["items"] if x["id"] == it["id"])
        self.assertTrue(v["photos"]["front"].endswith("front.png"))
        self.assertEqual(v["stage"], "待写档案")
        self.assertEqual(self.m.api_state()["lock"]["models"][0]["missing"], ["左侧脸", "右侧脸", "全身"])
        with self.assertRaises(ValueError):
            self.m.r_item_photo({"id": it["id"], "slot": "hack", "data": "x,y"})

    def test_48_spec_job_and_checks(self):
        root, it = self.lock_setup()
        job = self.m.r_job({"kind": "spec", "params": {"item": it["id"]}})["job"]
        self.assertIn("产品档案模板", job["prompt"])
        self.run_one(job)
        v = next(x for x in self.m.api_state()["items"] if x["id"] == it["id"])
        self.assertEqual([c["level"] for c in v["checks"]], ["必过", "必过", "必过", "尽量"])
        self.assertEqual([c["unsure"] for c in v["checks"]], [False, False, True, False])
        self.assertEqual(v["checks"][2]["text"], "背面开衩长度")
        # 人填过的名称不被 AI 覆盖，空着的项补上并记为 AI 填的
        self.assertEqual(v["name"], "灰色外套")
        self.assertEqual(v["fabric"], "羊毛 30% 锦纶 70%")
        self.assertIn("fabric", v["aiFilled"])
        self.assertEqual(v["stage"], "档案待核对")
        with self.assertRaises(ValueError):
            self.m.r_job({"kind": "lock", "params": {"item": it["id"], "face": "ai", "refs": self.m.list_refs()}})
        self.m.r_item_checks({"id": it["id"], "checks": [{"level": "必过", "text": "颜色是银灰"}, {"level": "尽量", "text": "缝线"}], "confirmed": True})
        spec = (self.m.item_dir(it) / "spec.md").read_text()
        self.assertIn("1. 颜色是银灰", spec)
        self.assertIn("锁定参数", spec)
        self.assertEqual(len(self.m.parse_checks(spec)), 2)

    def test_49_lock_job_writes_task_and_verifies(self):
        root, it = self.lock_setup()
        self.m.r_job({"kind": "spec", "params": {"item": it["id"]}})
        self.run_one(self.m.load_state()["jobs"][-1])
        self.m.r_item_checks({"id": it["id"], "confirmed": True})
        with self.assertRaises(ValueError):
            self.m.r_job({"kind": "lock", "params": {"item": it["id"], "face": "model", "model": "没有这人", "refs": self.m.list_refs()}})
        with self.assertRaises(ValueError):
            self.m.r_job({"kind": "lock", "params": {"item": it["id"], "face": "ai", "refs": ["/etc/passwd"]}})
        job = self.m.r_job({"kind": "lock", "params": {"item": it["id"], "face": "model", "model": "小禾", "refs": self.m.list_refs()}})["job"]
        md = (Path(job["params"]["jobDir"]) / "job.md").read_text()
        self.assertIn("模式: 只锁产品", md)
        self.assertIn("library/models/小禾", md)
        self.assertIn("library/references/ref1_走路.jpg", md)
        self.assertEqual(job["estimateMin"], 14)
        with self.assertRaises(ValueError):
            self.m.verify_job(job, {"ok": True, "images": 2}, {"t": 0})
        self.run_one(job)
        v = next(x for x in self.m.api_state()["items"] if x["id"] == it["id"])
        self.assertEqual(len(v["lockJobs"][0]["images"]), 2)
        self.assertEqual([g["grade"] for g in v["grades"].values()], ["A", "B"])
        self.assertEqual(v["stage"], "图已出好")

    def test_54_new_item_from_photos_only(self):
        root, _ = self.lock_setup()
        data = "data:image/png;base64," + self.m.base64.b64encode(self.image.read_bytes() * 3).decode()
        with self.assertRaises(ValueError):
            self.m.r_item_new({"photos": {"tag": data}})
        res = self.m.r_item_new({"photos": {"front": data, "tag": data}})
        it = res["item"]
        self.assertTrue(it["autoName"])
        self.assertTrue(it["name"].startswith("新款"))
        self.assertEqual(res["job"]["kind"], "spec")
        self.assertIn("info.json", res["job"]["prompt"])
        self.assertIn("待确认", res["job"]["prompt"])
        self.assertTrue((self.m.item_dir(it) / "tag.png").is_file())
        self.run_one(res["job"])
        v = self.m.find_item(self.m.load_state(), it["id"])
        self.assertEqual(v["name"], "银灰长毛翻领外套")
        self.assertEqual(v["category"], "外套")
        self.assertNotIn("autoName", v)
        # 人改过的项不再标为 AI 填的
        saved = self.m.r_item_save(dict(v, fabric="棉 100%"))["item"]
        self.assertNotIn("fabric", saved["aiFilled"])
        self.assertIn("colors", saved["aiFilled"])

    def test_55_next_slot_one_per_day_and_peaks(self):
        st = self.m.load_state()
        base = self.m.datetime.datetime(2026, 10, 21, 9, 0)
        t = int(base.timestamp() * 1000)
        a = self.m.next_slot(st, 1, after=t)
        self.assertEqual(self.m.datetime.datetime.fromtimestamp(a / 1000).strftime("%d %H:%M"), "21 12:30")
        st["schedule"] = [{"account": "1", "status": "pending", "at": a}]
        b = self.m.next_slot(st, 1, after=t)
        self.assertEqual(self.m.datetime.datetime.fromtimestamp(b / 1000).strftime("%d %H:%M"), "22 12:30")
        late = int(self.m.datetime.datetime(2026, 10, 21, 19, 50).timestamp() * 1000)
        c = self.m.next_slot({"schedule": []}, 1, after=late)
        self.assertEqual(self.m.datetime.datetime.fromtimestamp(c / 1000).strftime("%d %H:%M"), "22 12:30")
        self.assertIn("1", self.m.api_state()["nextSlot"])

    def test_56_lock_remembers_pick_and_auto_writes(self):
        root, it = self.lock_setup()
        self.run_one(self.m.r_job({"kind": "spec", "params": {"item": it["id"]}})["job"])
        self.m.r_item_checks({"id": it["id"], "confirmed": True})
        refs = self.m.list_refs()
        job = self.m.r_job({"kind": "lock", "params": {"item": it["id"], "face": "ai", "refs": refs,
                                                      "autoWrite": {"account": "1", "count": 2}}})["job"]
        lp = self.m.load_state()["lastPick"]
        self.assertEqual(lp["face"], "ai")
        self.assertEqual(list(lp["refsByCat"].values())[0], refs)
        self.run_one(job)
        self.m.chain_after(job)
        w = self.m.load_state()["jobs"][-1]
        self.assertEqual(w["kind"], "write")
        # 只用 A 级图；不够 4 张就不拆 2 篇
        self.assertEqual([Path(x).name for x in w["params"]["images"]], ["演示_01.png"])
        self.assertEqual(w["params"]["count"], 1)


    # ---------------------------------------------------------------- v5：选题、文案设定、重新排期、风格、AI 标注
    def finish(self, job):
        result = self.m.demo_job_effect(job)
        self.m.verify_job(job, result, {"t": self.m.now_ms() - 5000})
        self.m.update_job(job["id"], status="done")
        return next(j for j in self.m.load_state()["jobs"] if j["id"] == job["id"])

    def test_60_topics_job_imports_and_write_marks_used(self):
        job = self.m.r_job({"kind": "topics", "params": {"account": "1", "count": 4, "node": "双11"}})["job"]
        self.assertIn("联网", job["prompt"])
        self.assertIn("双11", job["prompt"])
        with self.assertRaises(ValueError):
            self.m.r_job({"kind": "topics", "params": {"account": "1"}})   # 同一批还在排队
        done = self.finish(job)
        self.assertEqual(len(done["topics"]), 4)
        st = self.m.api_state()
        self.assertEqual(len(st["topics"]), 4)
        grid = st["topicGrid"]["1"]
        self.assertEqual(sum(sum(v.values()) for v in grid.values()), 4)
        # 同样的选题再出一次：全部重复，不入库
        job2 = self.m.r_job({"kind": "topics", "params": {"account": "1", "count": 4}})["job"]
        with self.assertRaises(ValueError):
            self.finish(job2)
        tid = st["topics"][0]["id"]
        it = self.m.r_item_save({"name": "外套"})["item"]
        w = self.m.r_job({"kind": "write", "params": {"account": "1", "item": it["id"], "images": [str(self.image)], "topic": tid}})["job"]
        self.assertIn(st["topics"][0]["text"], w["prompt"])
        self.finish(w)
        t = self.m.find_topic(self.m.load_state(), tid)
        self.assertEqual(t["status"], "used")
        self.assertTrue(t["drafts"])
        with self.assertRaises(ValueError):
            self.m.r_job({"kind": "write", "params": {"account": "1", "item": it["id"], "images": [str(self.image)], "topic": "tp-nope"}})

    def test_61_texture_and_pain_auto_queue_item_topics(self):
        it = self.m.r_item_save({"name": "长毛外套", "texture": "软糯不扎"})["item"]
        self.assertFalse(self.m.r_item_save(dict(it))["topicJob"])
        res = self.m.r_item_save(dict(it, pain="小个子穿长外套显矮", topicAccount="1"))
        job = res["topicJob"]
        self.assertEqual(job["params"]["count"], 5)
        self.assertEqual(job["params"]["forItem"], it["id"])
        self.assertIn("小个子穿长外套显矮", job["prompt"])
        self.assertFalse(self.m.r_item_save(dict(res["item"]))["topicJob"])   # 没改就不重复出
        self.finish(job)
        st = self.m.load_state()
        self.assertTrue(all(t["item"] == it["id"] and t["source"] == "item" for t in st["topics"]))
        self.assertEqual(self.m.item_topic(st, "1", it["id"])["status"], "unused")
        # 出选题的任务不挡出图（它不占 params.item）
        self.assertNotIn("item", job["params"])

    def test_62_manual_topics(self):
        t = self.m.r_topic({"action": "add", "account": "1", "text": "梨形身材的毛毛外套怎么选", "scene": "约会", "body": "梨形"})["topic"]
        with self.assertRaises(ValueError):
            self.m.r_topic({"action": "add", "account": "1", "text": "梨形身材的毛毛外套怎么选"})
        with self.assertRaises(ValueError):
            self.m.r_topic({"action": "add", "account": "1", "text": "字" * 26})
        self.assertEqual(self.m.api_state()["topicGrid"]["1"]["约会"]["梨形"], 1)
        self.m.r_topic({"action": "delete", "id": t["id"]})
        self.assertFalse(self.m.load_state()["topics"])

    def test_63_mark_ai_is_per_post(self):
        e = self.m.r_publish({"account": "1", "id": self.rid, "quote": self.quote(), "at": self.m.now_ms() + 3 * 3600000, "markAi": False})["entry"]
        self.assertFalse(e["markAi"])
        calls = []
        with patch.object(self.m, "xhs_cli", side_effect=lambda a, timeout=600: (calls.append(a), (True, {"ok": True}, ""))[1]):
            self.m.publish_one(e)
        self.assertNotIn("--ai", calls[0])
        self.assertFalse(self.m.api_state()["archive"][0]["ai"])
        rid = self.m.add_drafts("1", "", [{"title": "第二篇", "body": "正文", "tags": [], "images": [str(self.image)]}], "t")[0]
        self.ready(rid)
        with patch.object(self.m, "xhs_cli", return_value=(True, {"ok": True, "shot": ""}, "")) as cli:
            self.m.r_publish_try({"account": "1", "id": rid})
            self.assertIn("--ai", cli.call_args[0][0])   # 没选就按设置（默认勾）
            self.m.r_publish_try({"account": "1", "id": rid, "markAi": False})
            self.assertNotIn("--ai", cli.call_args[0][0])

    def test_64_reschedule_failed_post(self):
        e = self.schedule()
        with patch.object(self.m, "xhs_cli", return_value=(False, None, "网络断了")):
            self.m.publish_one(e)
        self.assertEqual(self.m.api_state()["stats"]["1"]["failed"], 1)
        self.m.set_login("1", False)
        with self.assertRaises(ValueError):
            self.m.r_reschedule({"id": e["id"]})    # 没登录不让排
        self.login()
        new = self.m.r_reschedule({"id": e["id"]})["entry"]
        self.assertEqual(new["status"], "pending")
        self.assertGreater(new["at"], self.m.now_ms())
        self.assertEqual(self.entry(e["id"])["rescheduled"], new["id"])
        self.assertEqual(self.m.api_state()["stats"]["1"]["failed"], 0)
        with self.assertRaises(ValueError):
            self.m.r_reschedule({"id": e["id"]})
        self.m.r_unschedule({"id": new["id"]})
        self.m.r_review({"account": "1", "id": self.rid, "action": "save", "body": "改过了的正文"})
        with self.assertRaises(ValueError):
            self.m.r_reschedule({"id": new["id"]})

    def test_65_copy_settings_go_into_prompts_and_checks(self):
        self.m.r_account_copy({"id": "1", "position": "小个子通勤女装", "tone": "像闺蜜聊天", "banned": "便宜、清仓", "tagMix": "款式 4、人群 3、场景 3"})
        it = self.m.r_item_save({"name": "外套", "texture": "软糯", "pain": "显矮"})["item"]
        w = self.m.r_job({"kind": "write", "params": {"account": "1", "item": it["id"], "images": [str(self.image)]}})["job"]
        for x in ("小个子通勤女装", "像闺蜜聊天", "款式 4、人群 3、场景 3", "材质手感：软糯", "痛点「显矮」"):
            self.assertIn(x, w["prompt"])
        self.m.update_draft(self.rid, body="清仓价，快来")
        with self.assertRaises(ValueError):
            self.ready()
        self.assertIn("小个子通勤女装", self.m.r_prompt_preview({"account": "1", "item": it["id"]})["prompt"])
        self.assertEqual(self.m.api_state()["accounts"][0]["tone"], "像闺蜜聊天")

    def test_66_trial_does_not_enter_review(self):
        it = self.m.r_item_save({"name": "外套"})["item"]
        n = len(self.m.load_state()["drafts"])
        job = self.m.r_job({"kind": "trial", "params": {"account": "1", "item": it["id"]}})["job"]
        self.assertIn("没有配图", job["prompt"])
        done = self.finish(job)
        self.assertTrue(done["trial"]["title"])
        self.assertEqual(len(self.m.load_state()["drafts"]), n)

    def test_gaosaka_applies_to_write_trial_preview_and_rewrite(self):
        it = self.m.r_item_save({"name": "亚麻衬衫", "fabric": "亚麻 100%"})["item"]
        prompts = [self.m.r_job({"kind": kind, "params": {"account": "1", "item": it["id"],
                    "images": [str(self.image)]}})["job"]["prompt"] for kind in ("write", "trial")]
        prompts.append(self.m.r_prompt_preview({"account": "1", "item": it["id"]})["prompt"])
        self.m.update_draft(self.rid, item=it["id"])
        prompts.append(self.m.r_review({"account": "1", "id": self.rid, "action": "rewrite", "note": "更克制"})["job"]["prompt"])
        for prompt in prompts:
            for rule in ("高坂风格", "先讲事实，再解释价值", "不虚构亲历故事", "不主动联网补词", "亚麻 100%"):
                self.assertIn(rule, prompt)
            self.assertIn("UTF-8 JSON", prompt)
            self.assertIn("18 字", prompt)
        self.assertIn("材质细节 / 季节场景 / 克制判断", prompts[0])

    def test_copy_style_persists_per_account_and_standard_keeps_original_hooks(self):
        other = self.m.r_account({"name": "另一个号"})["account"]
        self.m.r_account_copy({"id": "1", "writeStyle": "standard", "tone": "像朋友分享"})
        it = self.m.r_item_save({"name": "衬衫"})["item"]
        prompt = self.m.r_prompt_preview({"account": "1", "item": it["id"]})["prompt"]
        self.assertIn("身高或身材 + 痛点", prompt)
        self.assertIn("像朋友分享", prompt)
        self.assertNotIn("高坂风格", prompt)
        self.assertEqual(self.m.copy_style(other["id"]), "gaosaka")
        self.assertEqual(self.m.api_state()["accounts"][0]["writeStyle"], "standard")
        self.m.r_account_copy({"id": "1", "writeStyle": "gaosaka"})
        self.assertIn("高坂风格", self.m.r_prompt_preview({"account": "1", "item": it["id"]})["prompt"])

    def test_invalid_copy_style_does_not_change_settings(self):
        before = self.m.account_copy("1")
        with self.assertRaises(ValueError):
            self.m.r_account_copy({"id": "1", "writeStyle": "unknown", "tone": "不应保存"})
        self.assertEqual(self.m.account_copy("1"), before)

    def test_67_reference_styles_and_mirror_job(self):
        root, it = self.lock_setup()
        png = self.image.read_bytes()
        (root / "library/references/电梯镜自拍.jpg").write_bytes(png)
        refs = self.m.list_refs()
        styles = self.m.api_state()["lock"]["refStyles"]
        self.assertEqual(styles[str(root / "library/references/电梯镜自拍.jpg")], "mirror")
        self.assertEqual(styles[str(root / "library/references/ref1_走路.jpg")], "street")
        self.assertEqual(styles[str(root / "library/references/ref2_挥手.jpg")], "other")
        self.m.r_ref_style({"path": str(root / "library/references/ref2_挥手.jpg"), "style": "mirror"})
        self.assertEqual(self.m.api_state()["lock"]["refStyles"][str(root / "library/references/ref2_挥手.jpg")], "mirror")
        up = self.m.r_ref_upload({"name": "a.png", "data": "data:image/png;base64," + self.m.base64.b64encode(png).decode(), "style": "mirror"})
        self.assertEqual(self.m.api_state()["lock"]["refStyles"][up["path"]], "mirror")
        self.m.r_ref_delete({"path": up["path"]})
        self.assertNotIn(up["path"], self.m.list_refs())
        self.assertTrue(list((root / "library/references/_removed").glob("a_*.png")))
        self.run_one(self.m.r_job({"kind": "spec", "params": {"item": it["id"]}})["job"])
        self.m.r_item_checks({"id": it["id"], "confirmed": True})
        job = self.m.r_job({"kind": "lock", "params": {"item": it["id"], "face": "hide", "style": "mirror", "refs": refs[:1]}})["job"]
        md = (Path(job["params"]["jobDir"]) / "job.md").read_text("utf-8")
        self.assertIn("对镜自拍", md)
        self.assertIn("对镜自拍", job["title"])
        self.assertIn("对镜自拍", job["prompt"])
        self.assertEqual(self.m.load_state()["lastPick"]["faceByStyle"]["mirror"], "hide")

    def test_68_upgrade_from_v4_keeps_work_and_shortens_grace(self):
        self.ready()
        st = json.loads(self.m.state_path().read_text())
        st["dataVersion"] = 4
        st["settings"]["late_grace_min"] = 120
        self.m.state_path().write_text(json.dumps(st))
        st = self.m.load_state()
        self.assertEqual(st["settings"]["late_grace_min"], 30)
        self.assertIn(str(self.rid), st["ready"])
        self.assertEqual(st["dataVersion"], 5)

    def test_69_keep_awake_only_while_pending(self):
        self.m.DEMO = False
        proc = type("P", (), {"poll": lambda s: None, "terminate": lambda s: setattr(s, "dead", True)})()
        with patch.object(self.m.sys, "platform", "darwin"), patch.object(self.m.shutil, "which", return_value="/usr/bin/caffeinate"), \
                patch.object(self.m.subprocess, "Popen", return_value=proc) as P:
            self.m.keep_awake(True)
            self.m.keep_awake(True)
            P.assert_called_once()
            self.assertEqual(P.call_args[0][0][:2], ["caffeinate", "-i"])
            self.m.keep_awake(False)
        self.assertTrue(proc.dead)
        self.assertIsNone(self.m.AWAKE["proc"])


    # ---------------------------------------------------------------- 三个号：拍法、实拍图、封面样式
    def b64(self, raw=None):
        return "data:image/png;base64," + self.m.base64.b64encode(raw or self.image.read_bytes()).decode()

    def test_70_style_groups_and_account_preset(self):
        root, it = self.lock_setup()
        self.m.r_account({"name": "the rumah daily"})
        g = self.m.r_style_group({"action": "add", "name": "书桌房间", "note": "白墙木地板中古书桌，裁到下巴以下"})["group"]
        with self.assertRaises(ValueError):
            self.m.r_style_group({"action": "add", "name": "对镜自拍"})
        st = self.m.api_state()
        self.assertEqual(list(st["styles"].values()), ["对镜自拍", "街拍", "书桌房间", "其他"])
        up = self.m.r_ref_upload({"name": "desk.png", "data": self.b64(), "style": g["id"]})
        self.assertEqual(self.m.api_state()["lock"]["refStyles"][up["path"]], g["id"])
        self.run_one(self.m.r_job({"kind": "spec", "params": {"item": it["id"]}})["job"])
        self.m.r_item_checks({"id": it["id"], "confirmed": True})
        job = self.m.r_job({"kind": "lock", "params": {"item": it["id"], "account": "2", "face": "hide", "style": g["id"], "refs": [up["path"]]}})["job"]
        md = (Path(job["params"]["jobDir"]) / "job.md").read_text("utf-8")
        self.assertIn("书桌房间：白墙木地板中古书桌", md)
        self.assertIn("书桌房间", job["title"])
        self.assertIn("「书桌房间」", job["prompt"])
        # 记在这个号名下，别的号不受影响
        lp = self.m.load_state()["lastPick"]["byAcc"]
        self.assertEqual(lp["2"], {"style": g["id"], "face": "hide", "model": "", "refs": [up["path"]]})
        self.assertNotIn("1", lp)
        self.m.update_job(job["id"], status="done")
        # 静物：参考图里没有人
        job = self.m.r_job({"kind": "lock", "params": {"item": it["id"], "account": "1", "face": "none", "refs": self.m.list_refs()[:1]}})["job"]
        self.assertIn("没有人", (Path(job["params"]["jobDir"]) / "job.md").read_text("utf-8"))
        self.assertIn("静物", job["params"]["jobDir"])
        self.assertEqual(job["estimateMin"], 3)
        # 删组：图回到其他，记住的拍法改成自己挑
        self.m.r_style_group({"action": "delete", "id": g["id"]})
        self.assertEqual(self.m.api_state()["lock"]["refStyles"][up["path"]], "other")
        self.assertEqual(self.m.load_state()["lastPick"]["byAcc"]["2"]["style"], "own")
        with self.assertRaises(ValueError):
            self.m.r_style_group({"action": "delete", "id": "mirror"})

    def test_71_real_photos_mix_with_ai_images(self):
        root, it = self.lock_setup()
        up = self.m.r_item_real({"id": it["id"], "action": "add", "name": "挂拍 1.jpg", "data": self.b64()})
        self.assertTrue(up["path"].startswith(str(self.m.item_dir(it) / "real")))
        v = next(x for x in self.m.api_state()["items"] if x["id"] == it["id"])
        self.assertEqual(v["realPhotos"], [up["path"]])
        self.assertEqual(v["photos"]["front"], str(self.m.item_dir(it) / "front.png"))   # 实拍图不占产品照片的位置
        # 只有实拍图也能写文案
        w = self.m.r_job({"kind": "write", "params": {"account": "1", "item": it["id"], "images": [up["path"]]}})["job"]
        self.assertIn(up["path"], w["prompt"])
        self.m.update_job(w["id"], status="done")
        # 出图后自动写文案：A 级图后面接上实拍图
        self.run_one(self.m.r_job({"kind": "spec", "params": {"item": it["id"]}})["job"])
        self.m.r_item_checks({"id": it["id"], "confirmed": True})
        job = self.m.r_job({"kind": "lock", "params": {"item": it["id"], "face": "ai", "refs": self.m.list_refs(), "autoWrite": {"account": "1", "count": 1}}})["job"]
        self.run_one(job)
        self.m.chain_after(job)
        w = self.m.load_state()["jobs"][-1]
        self.assertEqual([Path(x).name for x in w["params"]["images"]], ["演示_01.png", Path(up["path"]).name])
        self.m.r_item_real({"id": it["id"], "action": "delete", "path": up["path"]})
        self.assertEqual(self.m.real_photos(it), [])
        with self.assertRaises(ValueError):
            self.m.r_item_real({"id": it["id"], "action": "delete", "path": str(self.image)})

    def test_72_cover_style_per_account(self):
        root, it = self.lock_setup()
        img = str(self.m.item_dir(it) / "front.png")
        write = lambda **kw: self.m.r_job({"kind": "write", "params": dict({"account": "1", "item": it["id"], "images": [img]}, **kw)})["job"]
        self.assertEqual(self.m.api_state()["accounts"][0]["cover"], "text")      # 老账号默认还是加大字
        self.assertIn("封面加字", write()["prompt"])
        self.m.r_account_cover({"id": "1", "cover": "none"})
        j = write()
        self.assertEqual(j["params"]["cover"], "none")
        self.assertIn("不要加字", j["prompt"])
        self.assertIn("封面加字", write(coverText=True)["prompt"])               # 老的调用方式还能用
        with self.assertRaises(ValueError):
            self.m.r_account_cover({"id": "1", "cover": "logo"})
        with self.assertRaises(ValueError):
            write(cover="logo")
        self.m.r_account_cover({"id": "1", "cover": "logo", "logo": self.b64()})
        a = self.m.api_state()["accounts"][0]
        self.assertEqual(a["cover"], "logo")
        self.assertTrue(Path(a["logo"]).is_file())
        p = write()["prompt"]
        self.assertIn("品牌水印", p)
        self.assertIn(a["logo"], p)
        self.assertNotIn("封面加字", p)
        self.m.r_item_real({"id": it["id"], "action": "add", "name": "实拍", "data": self.b64()})
        self.assertIn("品牌水印", self.m.r_prompt_preview({"account": "1", "item": it["id"]})["prompt"])

    def test_73_copy_guide_risk_words_comment_ref_presets(self):
        # 限流词：内置的和 fashion.json 的合在一起，写稿、保存、置顶评论都查
        for w in ("平替", "一比一", "大牌同款", "淘宝"):
            self.assertIn(w, self.m.FASHION["riskErrors"])
        with self.assertRaises(ValueError):
            self.m.r_review({"account": "1", "id": self.rid, "action": "save", "body": "一比一还原，穿着很舒服。"})
        with self.assertRaises(ValueError):
            self.m.r_review({"account": "1", "id": self.rid, "action": "save", "comment": "加微信问尺码"})
        # 置顶评论：写稿带回来、能保存、改稿保留
        job = self.m.r_job({"kind": "write", "params": {"account": "1", "item": self.item_id(), "images": [str(self.image)],
                                                        "ref": "对标：开头一句痛点，三段，结尾提问。"}})["job"]
        self.assertIn("对标：开头一句痛点", job["prompt"])
        self.assertIn("置顶评论", job["prompt"])
        self.assertIn("平替", job["prompt"])
        self.m.verify_job(job, self.m.demo_job_effect(job), {"t": 0})
        rid = self.m.load_state()["jobs"][-1]["drafts"][0]
        self.assertIn("160cm", self.m.review_by_id(rid)["comment"])
        self.m.r_review({"account": "1", "id": rid, "action": "save", "comment": "模特 165 穿 S，胯宽拿 M。"})
        self.assertEqual(self.m.review_by_id(rid)["comment"], "模特 165 穿 S，胯宽拿 M。")
        rw = self.m.r_review({"account": "1", "id": rid, "action": "rewrite", "note": "开头更抓人"})["job"]
        self.assertIn("模特 165 穿 S", rw["prompt"])
        self.m.verify_job(rw, self.m.demo_job_effect(rw), {"t": 0})
        self.assertEqual(self.m.review_by_id(rid)["comment"], "模特 165 穿 S，胯宽拿 M。")
        # 三个号的预设
        self.assertEqual(set(self.m.api_state()["copyPresets"]), {"the reels", "the rumah daily", "the rumah", "街拍号", "测评号"})

    # ---------------------------------------------------------------- 矩阵：人设模板、起号节奏、一款多号、防撞车
    def test_80_persona_template_and_ramp(self):
        a = self.m.r_account({"name": "街拍号", "persona": "street", "fresh": True})["account"]
        st = self.m.api_state()
        acc = next(x for x in st["accounts"] if x["id"] == a["id"])
        self.assertEqual(acc["persona"], "street")
        self.assertEqual(acc["cover"], "text")
        self.assertIn("城市", acc["must"])
        self.assertEqual((acc["rhythm"]["stage"], acc["rhythm"]["target"]), ("养号", 1))
        self.assertEqual(st["lastPick"]["byAcc"][a["id"]]["style"], "street")
        self.assertIn("review", st["personas"])
        with self.assertRaises(ValueError):
            self.m.r_account({"name": "x", "persona": "nope"})
        # 第 3 周冷启动 4 篇，第 10 周按人设稳定 4 篇；老号没有开始日期算稳定
        start = self.m.load_state()["accounts"][-1]["startedAt"]
        self.assertEqual(self.m.account_rhythm({"startedAt": start}, start + 15 * 864e5)["stage"], "冷启动")
        self.assertEqual(self.m.account_rhythm({"startedAt": start, "persona": "street"}, start + 70 * 864e5)["target"], 4)
        self.assertEqual(self.m.account_rhythm({})["stage"], "稳定")
        # 老号换人设
        self.m.r_account_persona({"id": "1", "persona": "review"})
        self.assertTrue(next(x for x in self.m.api_state()["accounts"] if x["id"] == "1")["realFirst"])

    def test_81_ramp_caps_week_in_next_slot(self):
        a = self.m.r_account({"name": "新号", "fresh": True})["account"]
        st = self.m.load_state()
        t = st["accounts"][-1]["startedAt"] + 9 * 3600000
        first = self.m.next_slot(st, a["id"], after=t)
        st["schedule"] = [{"account": a["id"], "status": "pending", "at": first, "review": "x"}]
        second = self.m.next_slot(st, a["id"], after=t)
        d1, d2 = (self.m.datetime.date.fromtimestamp(x / 1000) for x in (first, second))
        self.assertNotEqual(d1 - self.m.datetime.timedelta(days=d1.weekday()), d2 - self.m.datetime.timedelta(days=d2.weekday()))

    def test_82_cross_account_gap_and_same_item(self):
        self.m.r_account({"name": "二号"})
        st = self.m.load_state()
        base = int(self.m.datetime.datetime(2026, 10, 21, 9, 0).timestamp() * 1000)
        st["drafts"].append({"id": 900, "account_id": "2", "item": "it1", "status": "未审核", "title": "t", "body": "b", "tags": [], "images": []})
        st["schedule"] = [{"account": "2", "status": "pending", "at": base + int(3.5 * 3600000), "review": "900"}]   # 二号 21 日 12:30
        a = self.m.next_slot(st, "1", after=base)
        self.assertEqual(self.m.datetime.datetime.fromtimestamp(a / 1000).strftime("%d %H:%M"), "21 13:10")
        b = self.m.next_slot(st, "1", after=base, item="it1")
        self.assertEqual(self.m.datetime.datetime.fromtimestamp(b / 1000).strftime("%d %H:%M"), "23 12:30")

    def test_83_image_reuse_blocked_and_similar_title_flagged(self):
        self.m.r_account({"name": "二号"})
        rid2 = self.m.add_drafts("2", "", [{"title": "演示稿二", "body": "只用于验证流程的演示正文。", "tags": ["a"] * 5,
                                           "images": [str(self.image)]}], "test")[0]
        with self.assertRaises(ValueError) as e:
            self.m.r_review({"account": "2", "id": rid2, "action": "ready"})
        self.assertIn("女装主号", str(e.exception))
        clash = next(r for r in self.m.api_state()["reviews"] if r["id"] == rid2)["clash"]
        self.assertEqual(clash["images"][0]["account"], "女装主号")
        self.assertEqual(clash["similar"]["account"], "女装主号")

    def test_84_publish_same_item_two_accounts_needs_gap(self):
        self.m.r_account({"name": "二号"})
        it = self.item_id()
        self.m.update_draft(self.rid, item=it)
        self.schedule(at=self.m.now_ms() + 3 * 3600000)
        img3 = self.m.STUDIO / "images" / "03.png"
        img3.write_bytes(self.m.PNG_1PX)
        rid2 = self.m.add_drafts("2", it, [{"title": "另一个角度", "body": "完全不同的开头，讲尺码和身材。", "tags": ["a"] * 5,
                                           "images": [str(img3)]}], "test")[0]
        self.m.r_review({"account": "2", "id": rid2, "action": "ready"})
        self.m.set_login("2", True)
        q = self.m.r_publish_preview({"account": "2", "id": rid2})["quote"]
        with self.assertRaises(ValueError) as e:
            self.m.r_publish({"account": "2", "id": rid2, "quote": q, "at": self.m.now_ms() + 4 * 3600000})
        self.assertIn("隔", str(e.exception))
        q = self.m.r_publish_preview({"account": "2", "id": rid2})["quote"]
        e2 = self.m.r_publish({"account": "2", "id": rid2, "quote": q, "at": "auto"})["entry"]
        self.assertGreaterEqual(e2["at"] - self.entry(self.m.load_state()["schedule"][0]["id"])["at"], 864e5)

    def test_85_lock_multi_queues_per_account(self):
        root, it = self.lock_setup()
        self.run_one(self.m.r_job({"kind": "spec", "params": {"item": it["id"]}})["job"])
        self.m.r_item_checks({"id": it["id"], "confirmed": True})
        refs = self.m.list_refs()
        self.m.r_account({"name": "街拍号", "persona": "street"})
        self.m.r_account({"name": "测评号", "persona": "review"})
        st = self.m.load_state()
        st["lastPick"]["byAcc"]["2"]["refs"] = refs[:1]
        self.m.save_state(st)
        res = self.m.r_lock_multi({"item": it["id"], "accounts": ["2", "3"]})
        self.assertEqual([q["account"] for q in res["queued"]], ["2"])
        self.assertEqual(res["skipped"][0]["account"], "3")
        job = next(j for j in self.m.load_state()["jobs"] if j["id"] == res["queued"][0]["job"])
        self.assertEqual(job["params"]["autoWrite"]["account"], "2")
        # 同一款再给主号出一组也可以（不同号），同一个号重复不行
        self.m.r_job({"kind": "lock", "params": {"item": it["id"], "account": "1", "face": "ai", "refs": refs}})
        with self.assertRaises(ValueError):
            self.m.r_job({"kind": "lock", "params": {"item": it["id"], "account": "2", "face": "ai", "refs": refs}})
        self.run_one(job)
        v = next(x for x in self.m.api_state()["items"] if x["id"] == it["id"])
        self.assertEqual(v["lockJobs"][0]["account"], "2")

    # ---------------------------------------------------------------- 第一批：模特绑定、资料卡、配方、批次、排期跟审核
    def shoot_ready(self):
        root, it = self.lock_setup()
        self.run_one(self.m.r_job({"kind": "spec", "params": {"item": it["id"]}})["job"])
        self.m.r_item_checks({"id": it["id"], "confirmed": True})
        return root, it

    def test_90_cast_fixed_per_item_and_account(self):
        root, it = self.shoot_ready()
        refs = self.m.list_refs()
        self.m.r_account({"name": "the rumah daily"})
        # 第一次出图就定下来，之后换不了
        job = self.m.r_job({"kind": "lock", "params": {"item": it["id"], "account": "1", "face": "model", "model": "小禾", "refs": refs}})["job"]
        c = self.m.cast_of(self.m.load_state(), it["id"], "1")
        self.assertEqual((c["face"], c["model"]), ("model", "小禾"))
        self.run_one(job)
        with self.assertRaises(ValueError) as e:
            self.m.r_job({"kind": "lock", "params": {"item": it["id"], "account": "1", "face": "ai", "refs": refs}})
        self.assertIn("解锁", str(e.exception))
        # 没传脸部处理也自动带上
        j2 = self.m.r_job({"kind": "lock", "params": {"item": it["id"], "account": "1", "refs": refs[:1]}})["job"]
        self.assertEqual((j2["params"]["face"], j2["params"]["model"]), ("model", "小禾"))
        self.m.r_job_cancel({"id": j2["id"]})
        # 另一个号可以用别的
        self.m.r_job({"kind": "lock", "params": {"item": it["id"], "account": "2", "face": "hide", "refs": refs}})
        self.assertEqual(self.m.cast_of(self.m.load_state(), it["id"], "2")["face"], "hide")
        # 解锁要写原因，留记录
        with self.assertRaises(ValueError):
            self.m.r_item_cast({"item": it["id"], "account": "1", "action": "unlock"})
        self.m.r_item_cast({"item": it["id"], "account": "1", "action": "unlock", "why": "小禾不合作了"})
        self.assertIsNone(self.m.cast_of(self.m.load_state(), it["id"], "1"))
        log = self.m.load_state()["castLog"]
        self.assertEqual(log[-1]["why"], "小禾不合作了")
        self.assertTrue(log[-1]["what"].startswith("解锁"))

    def test_91_cast_chosen_at_new_item_and_account_default(self):
        root, _ = self.lock_setup()
        self.m.r_account_cast({"id": "1", "face": "model", "model": "小禾"})
        self.assertEqual(self.m.api_state()["accounts"][0]["cast"], {"face": "model", "model": "小禾"})
        with self.assertRaises(ValueError):
            self.m.r_account_cast({"id": "1", "face": "model", "model": "没有这人"})
        res = self.m.r_item_new({"photos": {"front": self.b64(self.image.read_bytes() * 3)}, "cast": {"1": {"face": "model", "model": "小禾"}}})
        c = self.m.cast_of(self.m.load_state(), res["item"]["id"], "1")
        self.assertEqual((c["face"], c["model"]), ("model", "小禾"))
        self.assertEqual(self.m.load_state()["castLog"][-1]["why"], "建款时选的")

    def test_92_model_card_upload_and_job_md(self):
        root, it = self.shoot_ready()
        self.m.r_model({"action": "new", "name": "阿雅"})
        with self.assertRaises(ValueError):
            self.m.r_model({"action": "new", "name": "阿雅"})
        for slot in ("face_1", "face_left", "face_right", "body_1"):
            self.m.r_model({"action": "photo", "name": "阿雅", "slot": slot, "data": self.b64()})
        self.m.r_model({"action": "photo", "name": "阿雅", "slot": "face_1", "data": self.b64()})   # 换一张，旧的挪走
        self.m.r_model({"action": "info", "name": "阿雅", "hair": "黑色齐肩短发", "height": "165cm 50kg", "ratio": "腿长，肩窄"})
        m = next(x for x in self.m.list_models() if x["name"] == "阿雅")
        self.assertEqual(m["missing"], [])
        self.assertEqual(m["info"]["hair"], "黑色齐肩短发")
        prof = (root / "library/models/阿雅/profile.md").read_text()
        self.assertIn("黑色齐肩短发", prof)
        self.m.r_model({"action": "info", "name": "阿雅", "hair": "棕色长卷发", "height": "", "ratio": ""})
        prof = (root / "library/models/阿雅/profile.md").read_text()
        self.assertNotIn("黑色齐肩短发", prof)
        self.assertEqual(prof.count("工作台资料卡 -->"), 2)
        job = self.m.r_job({"kind": "lock", "params": {"item": it["id"], "account": "1", "face": "model", "model": "阿雅", "refs": self.m.list_refs()}})["job"]
        md = (Path(job["params"]["jobDir"]) / "job.md").read_text()
        self.assertIn("模特资料卡（阿雅）", md)
        self.assertIn("左侧脸：library/models/阿雅/face_left.png", md)
        self.assertIn("棕色长卷发", md)
        self.assertIn("gen.json", job["prompt"])

    def test_93_recipe_recorded_per_batch(self):
        root, it = self.shoot_ready()
        refs = self.m.list_refs()
        job = self.m.r_job({"kind": "lock", "params": {"item": it["id"], "account": "1", "face": "ai", "refs": refs, "note": "扣子全扣上"}})["job"]
        self.run_one(job)
        jd = Path(job["params"]["jobDir"])
        (jd / "gen.json").write_text(json.dumps({"演示_01.png": {"model": "gpt-image", "seed": "", "attached": refs[:1], "retries": 1}}), "utf-8")
        v = next(x for x in self.m.api_state()["items"] if x["id"] == it["id"])
        b = v["lockJobs"][0]
        self.assertEqual(b["recipe"]["refs"], refs)
        self.assertEqual(b["recipe"]["face"], "ai")
        self.assertEqual(b["recipe"]["note"], "扣子全扣上")
        self.assertTrue(b["recipe"]["product"][0].endswith("front.png"))
        self.assertEqual(len(b["recipe"]["rows"]), 2)
        self.assertEqual(b["gen"]["演示_01.png"]["model"], "gpt-image")

    def test_94_batch_only_and_rejected_images_isolated(self):
        root, it = self.shoot_ready()
        refs = self.m.list_refs()
        j1 = self.m.r_job({"kind": "lock", "params": {"item": it["id"], "account": "1", "face": "ai", "refs": refs}})["job"]
        self.run_one(j1)
        a1 = sorted(str(p) for p in (Path(j1["params"]["jobDir"]) / "out").glob("*"))
        j2 = self.m.r_job({"kind": "lock", "params": {"item": it["id"], "account": "1", "refs": refs[:1]}})["job"]
        self.run_one(j2)
        a2 = sorted(str(p) for p in (Path(j2["params"]["jobDir"]) / "out").glob("*"))
        with self.assertRaises(ValueError) as e:
            self.m.r_job({"kind": "write", "params": {"account": "1", "item": it["id"], "images": a1[:1] + a2}})
        self.assertIn("同一批", str(e.exception))
        # 评 C 的不能用
        (Path(j1["params"]["jobDir"]) / "report.md").write_text("演示_01.png：评级 A，好\n演示_02.png：评级 C，领子不对", "utf-8")
        with self.assertRaises(ValueError):
            self.m.r_job({"kind": "write", "params": {"account": "1", "item": it["id"], "images": a1}})
        # 稿子用了一张图，排期后这张图被驳回：稿退回待审，排期停住，发不出去
        rid = self.m.add_drafts("1", it["id"], [{"title": "灰色外套", "body": "演示正文", "tags": ["演示"], "images": a1[:1]}], "test")[0]
        self.ready(rid)
        self.login()
        q = self.m.r_publish_preview({"account": "1", "id": rid})["quote"]
        e1 = self.m.r_publish({"account": "1", "id": rid, "quote": q, "at": self.m.now_ms() + 7200000})["entry"]
        res = self.m.r_image({"action": "reject", "path": a1[0], "why": "手指多了一根"})
        self.assertEqual(res["drafts"], [str(rid)])
        self.assertFalse(Path(a1[0]).exists())
        self.assertEqual(self.entry(e1["id"])["status"], "hold")
        self.assertNotIn(str(rid), self.m.load_state()["ready"])
        with self.assertRaises(ValueError):
            self.m.r_review({"account": "1", "id": rid, "action": "ready"})
        v = next(x for x in self.m.api_state()["items"] if x["id"] == it["id"])
        self.assertNotIn(a1[0], sum((b["images"] for b in v["lockJobs"]), []))
        with self.assertRaises(ValueError):
            self.m.r_job({"kind": "write", "params": {"account": "1", "item": it["id"], "images": a1[:1]}})
        self.m.r_image({"action": "restore", "path": a1[0]})
        self.assertTrue(Path(a1[0]).exists())
        with self.assertRaises(ValueError):
            self.m.r_image({"action": "reject", "path": str(self.image)})

    def test_95_schedule_follows_review(self):
        e = self.schedule()
        # 改文案 → 排期停住，标待重新确认
        self.m.r_review({"account": "1", "id": self.rid, "action": "save", "body": "改过的演示正文，用来验证排期暂停。"})
        self.assertEqual(self.entry(e["id"])["status"], "hold")
        self.assertNotIn(e["id"], [x["id"] for x in self.m.load_state()["schedule"] if x["status"] == "pending"])
        # 重新确认后原来那条换成新的
        q = self.quote()
        e2 = self.m.r_publish({"account": "1", "id": self.rid, "quote": q, "at": e["at"]})["entry"]
        self.assertEqual(self.entry(e["id"])["status"], "cancelled")
        self.assertEqual(self.entry(e["id"])["rescheduled"], e2["id"])
        # 只保存不改内容不动排期
        self.m.r_review({"account": "1", "id": self.rid, "action": "save"})
        self.assertEqual(self.entry(e2["id"])["status"], "pending")
        # 换图 → 停住
        self.m.r_review({"account": "1", "id": self.rid, "action": "images", "images": [str(self.image2), str(self.image)]})
        self.assertEqual(self.entry(e2["id"])["status"], "hold")
        # 驳回 → 直接取消（停住的也取消）
        self.m.r_review({"account": "1", "id": self.rid, "action": "reject"})
        self.assertEqual(self.entry(e2["id"])["status"], "cancelled")
        self.assertIn("驳回", self.entry(e2["id"])["error"])

    def test_96_reject_cancels_pending_schedule(self):
        e = self.schedule()
        self.m.r_review({"account": "1", "id": self.rid, "action": "reject"})
        self.assertEqual(self.entry(e["id"])["status"], "cancelled")

    def test_97_rewrite_holds_schedule(self):
        e = self.schedule()
        job = self.m.r_review({"account": "1", "id": self.rid, "action": "rewrite", "note": "开头改成提问"})["job"]
        self.finish(job)
        self.assertEqual(self.entry(e["id"])["status"], "hold")
        self.m.r_unschedule({"id": e["id"]})
        self.assertEqual(self.entry(e["id"])["status"], "cancelled")


    # ---------------------------------------------------------------- 第二批：统一刹车、场景按号、套装、自动比对
    def test_100_captcha_pauses_all_accounts(self):
        self.m.r_account({"name": "副号"})
        (self.m.STUDIO / "images" / "03.png").write_bytes(self.m.PNG_1PX)
        e = self.schedule()
        rid2 = self.m.add_drafts("2", "", [{"title": "副号的稿", "body": "副号演示正文。", "tags": ["演示"], "images": [str(self.m.STUDIO / "images" / "03.png")]}], "test")[0]
        self.m.r_review({"account": "2", "id": rid2, "action": "ready"})
        self.m.set_login("2", True)
        q = self.m.r_publish_preview({"account": "2", "id": rid2})["quote"]
        e2 = self.m.r_publish({"account": "2", "id": rid2, "quote": q, "at": self.m.now_ms() + 9000000})["entry"]
        with patch.object(self.m, "xhs_cli", return_value=(False, None, "已停下：验证码（截图：/tmp/x.png）")):
            self.m.publish_one(e)
        b = self.m.load_state()["brake"]
        self.assertTrue(b["on"])
        self.assertEqual((b["account"], b["reason"]), ("1", "验证码"))
        self.assertEqual(self.entry(e["id"])["status"], "failed")
        # 刹车时别的号到点也不发
        st = self.m.load_state()
        for x in st["schedule"]:
            x["at"] = self.m.now_ms() - 1000
        self.m.save_state(st)
        with patch.object(self.m, "xhs_cli") as cli:
            self.m.publish_one(self.entry(e2["id"]))
            cli.assert_not_called()
        self.assertEqual(self.entry(e2["id"])["status"], "pending")
        # 立即发被拦，排以后可以
        rid3 = self.m.add_drafts("1", "", [{"title": "第三篇", "body": "演示正文。", "tags": ["演示"], "images": [str(self.image)]}], "test")[0]
        q3 = self.quote(rid3)
        with self.assertRaises(ValueError):
            self.m.r_publish({"account": "1", "id": rid3, "quote": q3})
        self.m.r_brake({"action": "resume"})
        self.assertFalse(self.m.load_state()["brake"]["on"])
        self.assertEqual(self.m.api_state()["brakeLog"][-1]["reason"], "恢复发布")

    def test_101_brake_reasons(self):
        r = self.m.brake_reason
        self.assertEqual(r("已停下：操作频繁，页面上写着「操作频繁」（截图：x）"), "操作频繁")
        self.assertEqual(r("小红书登录已失效，请先运行 login 扫码"), "登录失效")
        self.assertEqual(r("点了发布，但 30 秒内没看到「发布成功」"), "发布结果不确定")
        self.assertEqual(r("上传第 1 张图片失败"), "")
        e = self.schedule()
        with patch.object(self.m, "xhs_cli", return_value=(False, None, "上传第 1 张图片失败: timeout")):
            self.m.publish_one(e)
        self.assertFalse((self.m.load_state().get("brake") or {}).get("on"))
        self.m.r_brake({"action": "pause"})
        self.assertEqual(self.m.load_state()["brake"]["reason"], "手动暂停")

    def test_102_scene_groups_per_account(self):
        root, it = self.lock_setup()
        self.m.r_account({"name": "the rumah"})
        g = self.m.r_style_group({"action": "add", "name": "灰棚 lookbook", "note": "灰色影棚"})["group"]
        self.m.r_style_group({"action": "accounts", "id": g["id"], "accounts": ["2"]})
        self.m.r_style_group({"action": "accounts", "id": "mirror", "accounts": ["1"]})
        self.assertEqual(self.m.api_state()["styleAccounts"], {g["id"]: ["2"], "mirror": ["1"]})
        with self.assertRaises(ValueError):
            self.m.r_style_group({"action": "accounts", "id": "other", "accounts": []})
        with self.assertRaises(ValueError):
            self.m.r_style_group({"action": "accounts", "id": g["id"], "accounts": ["9"]})
        self.m.r_style_group({"action": "delete", "id": g["id"]})
        self.assertNotIn(g["id"], self.m.api_state()["styleAccounts"])

    def test_103_suit_pieces_photos_and_job(self):
        root, it = self.lock_setup()
        with self.assertRaises(ValueError):
            self.m.r_item_photo({"id": it["id"], "slot": "p2_front", "data": self.b64(self.image.read_bytes() * 3)})
        self.m.r_item_piece({"id": it["id"], "action": "add", "name": "半裙"})
        self.m.r_item_piece({"id": it["id"], "action": "main", "name": "西装外套"})
        for k in ("front", "detail", "back"):
            self.m.r_item_photo({"id": it["id"], "slot": "p2_" + k, "data": self.b64(self.image.read_bytes() * 3)})
        self.m.r_item_save(dict(self.m.find_item(self.m.load_state(), it["id"]), colors="黑"))   # 保存资料不丢件
        v = next(x for x in self.m.api_state()["items"] if x["id"] == it["id"])
        self.assertEqual([p["name"] for p in v["pieces"]], ["半裙"])
        self.assertTrue(v["piecePhotos"]["p2"]["front"].endswith("p2_front.png"))
        self.assertEqual(v["photos"]["front"], str(self.m.item_dir(it) / "front.png"))
        spec = self.m.r_job({"kind": "spec", "params": {"item": it["id"]}})["job"]
        self.assertIn("套装", spec["prompt"])
        self.assertIn("p2_front", spec["prompt"])
        self.run_one(spec)
        self.m.r_item_checks({"id": it["id"], "confirmed": True})
        job = self.m.r_job({"kind": "lock", "params": {"item": it["id"], "account": "1", "face": "ai", "refs": self.m.list_refs()}})["job"]
        md = (Path(job["params"]["jobDir"]) / "job.md").read_text()
        self.assertIn("套装：西装外套 + 半裙", md)
        self.assertIn("p2_front.png（半裙正面）", md)
        self.assertLess(md.index("半裙正面"), md.index("半裙细节"))
        self.assertEqual(len(self.m.product_refs(self.m.find_item(self.m.load_state(), it["id"]))), 4)
        for _ in range(2):
            self.m.r_item_piece({"id": it["id"], "action": "add", "name": "马甲"})
        with self.assertRaises(ValueError):
            self.m.r_item_piece({"id": it["id"], "action": "add", "name": "第五件"})
        self.m.r_item_piece({"id": it["id"], "action": "delete", "piece": "p2"})
        self.assertFalse((self.m.item_dir(it) / "p2_front.png").exists())
        self.assertFalse(self.m.find_item(self.m.load_state(), it["id"])["specOk"])

    def test_104_trial_one_then_rest_same_batch(self):
        root, it = self.lock_setup()
        self.run_one(self.m.r_job({"kind": "spec", "params": {"item": it["id"]}})["job"])
        self.m.r_item_checks({"id": it["id"], "confirmed": True})
        refs = self.m.list_refs()
        self.assertGreater(len(refs), 1)
        job = self.m.r_job({"kind": "lock", "params": {"item": it["id"], "account": "1", "face": "ai", "refs": refs, "note": "扣子全扣",
                                                       "trial": True, "autoWrite": {"account": "1", "count": 1}}})["job"]
        # 先只出 1 张，不自动写；记住的拍法还是全部参考图
        self.assertEqual(job["params"]["refs"], refs[:1])
        self.assertIsNone(job["params"]["autoWrite"])
        self.assertTrue(job["title"].startswith("试拍"))
        self.assertEqual(self.m.load_state()["lastPick"]["byAcc"]["1"]["refs"], refs)
        jd = Path(job["params"]["jobDir"])
        rc = json.loads((jd / "recipe.json").read_text())
        self.assertEqual(rc["rest"], refs[1:])
        self.run_one(job)
        trial_img = sorted(str(p) for p in (jd / "out").glob("*"))
        (jd / "report.md").write_text("%s：评级 C，领子形状不对" % Path(trial_img[0]).name, "utf-8")
        n = len(self.m.load_state()["jobs"])
        self.m.chain_after(dict(job, status="done"))
        self.assertEqual(len(self.m.load_state()["jobs"]), n)   # 评 C 不再自动返工，也不自动写
        (jd / "report.md").write_text("%s：评级 A，好" % Path(trial_img[0]).name, "utf-8")
        # 看过没问题：出剩下的，算同一批，出完自动写（试拍那张也算进去）
        rest = self.m.r_lock_rest({"batch": jd.name})["job"]
        self.assertTrue(rest["title"].startswith("续出"))
        self.assertEqual(rest["params"]["refs"], refs[1:])
        self.assertEqual(rest["params"]["reworkOf"], jd.name)
        self.assertEqual(rest["params"]["autoWrite"]["account"], "1")
        self.assertIn("扣子全扣", (Path(rest["params"]["jobDir"]) / "job.md").read_text())
        with self.assertRaises(ValueError):
            self.m.r_lock_rest({"batch": jd.name})
        self.run_one(rest)
        rjd = Path(rest["params"]["jobDir"])
        rout = sorted(str(p) for p in (rjd / "out").glob("*"))
        (rjd / "report.md").write_text("\n".join("%s：评级 A，好" % Path(f).name for f in rout), "utf-8")
        self.m.check_batch_images(trial_img + rout)
        self.m.chain_after(dict(rest, status="done"))
        w = self.m.load_state()["jobs"][-1]
        self.assertEqual(w["kind"], "write")
        self.assertIn(trial_img[0], w["params"]["images"])
        # 只选 1 张参考图时不试拍
        self.m.update_job(w["id"], status="cancelled")
        one = self.m.r_job({"kind": "lock", "params": {"item": it["id"], "account": "1", "face": "ai", "refs": refs[:1], "trial": True}})["job"]
        self.assertTrue(one["title"].startswith("出图"))

    def test_105_color_flag(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("没装 Pillow")
        root, it = self.lock_setup()
        import io
        def png(c):
            b = io.BytesIO(); Image.new("RGB", (60, 80), c).save(b, "PNG"); return b.getvalue()
        (self.m.item_dir(it) / "front.png").write_bytes(png((120, 120, 125)))
        self.run_one(self.m.r_job({"kind": "spec", "params": {"item": it["id"]}})["job"])
        self.m.r_item_checks({"id": it["id"], "confirmed": True})
        job = self.m.r_job({"kind": "lock", "params": {"item": it["id"], "account": "1", "face": "ai", "refs": self.m.list_refs()}})["job"]
        self.run_one(job)
        outs = sorted((Path(job["params"]["jobDir"]) / "out").glob("*"))
        outs[0].write_bytes(png((125, 118, 128)))
        outs[1].write_bytes(png((200, 60, 40)))
        flags = self.m.color_check(job)
        self.assertEqual(list(flags), [str(outs[1])])
        self.assertIn(str(outs[1]), self.m.api_state()["colorFlags"])


    def test_106_lock_rejected_folder_never_usable(self):
        root, it = self.shoot_ready()
        job = self.m.r_job({"kind": "lock", "params": {"item": it["id"], "account": "1", "face": "hide", "refs": self.m.list_refs()}})["job"]
        self.run_one(job)
        jd = Path(job["params"]["jobDir"])
        good = sorted(str(p) for p in (jd / "out").glob("*"))
        (jd / "rejected").mkdir(exist_ok=True)
        bad = jd / "rejected" / "演示_02_v3.png"
        bad.write_bytes(Path(good[0]).read_bytes())
        # agent 自己挪进 rejected/ 的图，工作台没记过驳回，也不能用
        self.assertNotIn(str(bad), self.m.load_state().get("imgRejected") or {})
        self.assertTrue(self.m.rejected_image(str(bad)))
        self.assertFalse(self.m.rejected_image(good[0]))
        for imgs in ([str(bad)], [good[0], str(bad)]):
            with self.assertRaises(ValueError) as e:
                self.m.check_batch_images(imgs)
            self.assertIn("没通过", str(e.exception))
            with self.assertRaises(ValueError):
                self.m.r_job({"kind": "write", "params": {"account": "1", "item": it["id"], "images": imgs}})
        with self.assertRaises(ValueError):
            self.m.clean_draft({"title": "黑色套装", "body": "演示正文", "tags": ["演示"], "images": [str(bad)]}, account="1")
        # 稿子里的图被换成 rejected/ 路径：定稿、预览、发布都拦住
        rid = self.m.add_drafts("1", it["id"], [{"title": "黑色套装", "body": "演示正文", "tags": ["演示"], "images": good[:1]}], "test")[0]
        self.ready(rid)
        self.login()
        q = self.m.r_publish_preview({"account": "1", "id": rid})["quote"]
        self.m.update_draft(rid, images=[str(bad)])
        with self.assertRaises(ValueError):
            self.m.r_review({"account": "1", "id": rid, "action": "ready"})
        with self.assertRaises(ValueError):
            self.m.r_publish_preview({"account": "1", "id": rid})
        with self.assertRaises(ValueError):
            self.m.r_publish({"account": "1", "id": rid, "quote": q, "at": self.m.now_ms() + 7200000})

    def test_107_lock_fail_shows_reason(self):
        root, it = self.shoot_ready()
        job = self.m.r_job({"kind": "lock", "params": {"item": it["id"], "account": "1", "face": "hide", "refs": self.m.list_refs()}})["job"]
        jd = Path(job["params"]["jobDir"])
        (jd / "out").mkdir(parents=True, exist_ok=True)
        (jd / "out" / "a_01.png").write_bytes(self.image.read_bytes())
        (jd / "report.md").write_text("| a_01.png | 评级 B，眼睛鼻子都在画外 |\n| a_02.png | 评级 C，露出鼻子 |", "utf-8")
        (jd / "gen.json").write_text(json.dumps({"a_02.png": {"retries": 2}}), "utf-8")
        with self.assertRaises(ValueError) as e:
            self.m.verify_job(job, {"ok": False, "images": 1}, {"t": 0})
        msg = str(e.exception)
        self.assertIn("交了 1 张", msg)
        self.assertIn("a_02.png 评 C：露出鼻子，返工 2 次仍为 C", msg)
        self.assertNotIn("a_01", msg)
        (jd / "report.md").write_text("第 2 张没做：生图额度用完", "utf-8")
        with self.assertRaises(ValueError) as e:
            self.m.verify_job(job, None, {"t": 0})
        self.assertIn("额度", str(e.exception))
        # 别的任务还是原来的说法
        with self.assertRaises(ValueError) as e:
            self.m.verify_job(dict(job, kind="spec"), None, {"t": 0})
        self.assertIn("回执", str(e.exception))

    def test_108_street_no_face_crop_rules(self):
        root, it = self.shoot_ready()
        refs = self.m.list_refs()
        job = self.m.r_job({"kind": "lock", "params": {"item": it["id"], "account": "1", "face": "hide", "style": "street", "refs": refs}})["job"]
        md = (Path(job["params"]["jobDir"]) / "job.md").read_text()
        self.assertIn("不露脸（街拍，必过）", md)
        self.assertIn("鼻尖", md)
        self.assertIn("不露脸」是必过项", job["prompt"])
        # 换真模特的街拍不加这段
        p2 = {"face": "ai", "style": "street", "refs": refs}
        self.assertNotIn("不露脸（街拍", self.m.lock_job_md(it, p2, "x"))
        self.assertNotIn("不露脸」是必过项", self.m.lock_prompt(p2, "x"))
        # 没过的每一版都留着，最多 3 版
        self.assertIn("_v1、_v2；", job["prompt"])
        self.assertIn("最多出 2 版", job["prompt"])
        self.assertIn("「尽量」的条目看整张图扫一眼", job["prompt"])

    def test_109_human_picks_after_three_fails(self):
        root, it = self.shoot_ready()
        job = self.m.r_job({"kind": "lock", "params": {"item": it["id"], "account": "1", "face": "hide", "refs": self.m.list_refs()}})["job"]
        self.run_one(job)
        jd = Path(job["params"]["jobDir"])
        good = sorted(str(p) for p in (jd / "out").glob("*"))
        (jd / "rejected").mkdir(exist_ok=True)
        vers = []
        for n in (1, 2, 3):
            f = jd / "rejected" / ("街拍_02_v%d.png" % n)
            f.write_bytes(Path(good[0]).read_bytes())
            vers.append(str(f))
        (jd / "report.md").write_text("演示_01.png：评级 A，好\n街拍_02.png：评级 C，三版都露出鼻子", "utf-8")
        (jd / "gen.json").write_text(json.dumps({"街拍_02.png": {"retries": 2}}), "utf-8")
        v = next(x for x in self.m.api_state()["items"] if x["id"] == it["id"])
        self.assertEqual(v["lockJobs"][0]["rejectedFiles"], vers)
        # 人挑第 2 版：回到 out/，标人工，能和同批的图一起写
        res = self.m.r_image({"action": "approve", "path": vers[1], "why": "鼻子只露一点，裁一下能用"})
        picked = res["path"]
        self.assertEqual(Path(picked).parent, jd / "out")
        self.assertFalse(Path(vers[1]).exists())
        self.assertFalse(self.m.rejected_image(picked))
        self.assertTrue(self.m.rejected_image(vers[0]))
        v = next(x for x in self.m.api_state()["items"] if x["id"] == it["id"])
        self.assertEqual(v["grades"][picked]["grade"], "人工")
        self.m.check_batch_images([good[0], picked])
        (jd / "report.md").write_text("演示_01.png：评级 A，好\n街拍_02_v2.png：评级 C，露鼻子", "utf-8")
        self.m.check_batch_images([picked])
        # 人挑的不会再自动返工
        n = len(self.m.load_state()["jobs"])
        self.m.chain_after(dict(job, status="done"))
        self.assertEqual(len(self.m.load_state()["jobs"]), n)
        # 没挑的还是用不了；out 里的图不能「挑」
        with self.assertRaises(ValueError):
            self.m.check_batch_images([vers[0]])
        with self.assertRaises(ValueError):
            self.m.r_image({"action": "approve", "path": good[0]})

class GuideTests(unittest.TestCase):
    """向导：每个按钮都要有一句说明，按钮说明模式和帮助搜索才不会漏。"""
    def test_every_button_has_help(self):
        import re
        html = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
        used = set(re.findall(r'data-do="([A-Za-z]+)"', html))
        start = html.index("const HELP = {")
        helped = set(re.findall(r"^  ([A-Za-z]+): \[", html[start:html.index("};", start)], re.M))
        self.assertEqual(sorted(used - helped), [], "这些按钮还没写说明，补到 index.html 的 HELP 里")

if __name__ == "__main__":
    unittest.main()
