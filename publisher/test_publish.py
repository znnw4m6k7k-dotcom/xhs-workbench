"""用一个模拟的创作中心发布页跑通整套发布步骤，不连小红书、不发东西。"""
import json, os, sys, tempfile, unittest
from datetime import datetime, timedelta
from pathlib import Path
from playwright.sync_api import sync_playwright
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("XHS_PUBLISHER_HOME", tempfile.mkdtemp())
import xhs_publish as X

MOCK = (HERE / "mock_publish.html").read_text("utf-8")


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        exe = "/opt/pw-browsers/chromium"
        cls.b = cls.pw.chromium.launch(executable_path=exe, args=["--headless=new"]) if Path(exe).exists() else cls.pw.chromium.launch()
        cls.img = Path(X.HOME) / "_t.png"
        cls.img.parent.mkdir(parents=True, exist_ok=True)
        cls.img.write_bytes(bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d6a4b30000000049454e44ae426082"))

    @classmethod
    def tearDownClass(cls):
        cls.b.close(); cls.pw.stop()

    def page(self, login=False, success=True):
        p = self.b.new_page()
        def handle(route):
            u = route.request.url
            if "/publish/success" in u:
                return route.fulfill(body="<html><body>发布成功</body></html>", content_type="text/html; charset=utf-8")
            if login and "/publish/publish" in u:
                return route.fulfill(body="<script>location.href='%s'</script>" % X.URL_LOGIN, content_type="text/html")
            if "/login" in u:
                return route.fulfill(body="<html><body>扫码登录</body></html>", content_type="text/html; charset=utf-8")
            html = MOCK if success else MOCK.replace("window.__topics = [];", "window.__topics = []; window.__noSuccess = true;")
            route.fulfill(body=html, content_type="text/html; charset=utf-8")
        p.route("**/*", handle)
        return p

    def test_pick_topic_exact_only(self):
        self.assertEqual(X.pick_topic(["#小个子穿搭技巧\n1亿", "#小个子穿搭\n5亿"], "小个子穿搭"), 1)
        self.assertEqual(X.pick_topic(["#外套搭配", "外套[话题]#"], "#外套"), 1)
        self.assertEqual(X.pick_topic(["#外套搭配"], "外套"), -1)

    def test_publish_full(self):
        p = self.page()
        at = (datetime.now() + timedelta(days=1)).replace(hour=20, minute=0, second=0, microsecond=0)
        r = X.publish_image("t", title="银灰外套", content="软乎乎的长毛外套。", images=[str(self.img)] * 2,
                            tags=["小个子穿搭", "外套", "银灰"], schedule_time=at, is_ai_generated=True, page=p)
        self.assertTrue(r["ok"])
        self.assertEqual(r["tags"], {"matched": ["小个子穿搭", "外套"], "plain": ["银灰"]})
        self.assertIn("publish/success", p.url)
        pub = json.loads(p.evaluate("localStorage.getItem('pub')") or "null")
        self.assertEqual(pub["title"], "银灰外套")
        self.assertEqual(pub["at"], at.strftime("%Y-%m-%d %H:%M"))

    def test_tags_and_fields_before_click(self):
        p = self.page()
        r = X.publish_image("t", title="银灰外套", content="正文", images=[str(self.img)], tags=["小个子穿搭", "外套"],
                            is_ai_generated=True, dry_run=True, page=p)
        self.assertTrue(r["dryRun"])
        self.assertEqual(p.evaluate("window.__topics"), ["小个子穿搭", "外套"])
        self.assertTrue(p.evaluate("window.__ai"))
        self.assertIsNone(p.evaluate("window.__published || null"))
        self.assertEqual(p.locator(".d-input input").input_value(), "银灰外套")
        self.assertTrue(Path(r["shot"]).is_file())

    def test_schedule_sets_platform_time(self):
        p = self.page()
        at = (datetime.now() + timedelta(days=1)).replace(hour=12, minute=30, second=0, microsecond=0)
        X.publish_image("t", title="标题", content="正文", images=[str(self.img)], schedule_time=at, dry_run=True, page=p)
        self.assertEqual(p.locator(".date-picker-container input").input_value(), at.strftime("%Y-%m-%d %H:%M"))
        self.assertEqual(p.locator(".d-switch").get_attribute("aria-checked"), "true")

    def test_no_success_is_failure(self):
        p = self.page(success=False)
        X._wait_publish_success.__defaults__ = (3,)
        try:
            with self.assertRaises(RuntimeError) as e:
                X.publish_image("t", title="标题", content="正文", images=[str(self.img)], page=p)
            self.assertIn("没看到「发布成功」", str(e.exception))
        finally:
            X._wait_publish_success.__defaults__ = (30,)

    def test_login_expired(self):
        p = self.page(login=True)
        with self.assertRaises(RuntimeError) as e:
            X.publish_image("t", title="标题", content="正文", images=[str(self.img)], page=p)
        self.assertIn("登录已失效", str(e.exception))

    def test_past_schedule_rejected(self):
        with self.assertRaises(RuntimeError):
            X.publish_image("t", title="标题", content="正文", images=["x"], schedule_time=datetime.now() - timedelta(hours=1), page=object())


if __name__ == "__main__":
    unittest.main()
