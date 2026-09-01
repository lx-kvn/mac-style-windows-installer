"""CLI 的訊息語言（第十四輪決議第八項）。

`install_engine.py` 的訊息改為可翻譯的 key 之後，CLI 必須自己決定要用哪一種
語言印出來——它沒有像 GUI 那樣的語言下拉選單。

預設呼叫現成的 `lang_detect.detect_system_language()`，與 GUI 首次啟動時的
預設值一致；再提供 `--lang` 讓 CI 固定輸出語言——CI 的 log 跟著執行那台機器
的區域設定跑是個坑：同一份設定在兩台機器上跑出不同語言的 log，比對就失效了。
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import builder_cli
import install_engine
from _fakes import write_test_png


class TheFlagExists(unittest.TestCase):
    def test_pack_accepts_a_language_flag(self):
        args = builder_cli.build_arg_parser().parse_args(
            ["pack", "--app-dir", "x", "--lang", "en"])
        self.assertEqual(args.lang, "en")

    def test_it_defaults_to_unset_so_the_system_language_decides(self):
        """預設值是 None 而不是某個語言：帶了旗標才覆寫，沒帶就交給偵測。"""
        args = builder_cli.build_arg_parser().parse_args(["pack", "--app-dir", "x"])
        self.assertIsNone(args.lang)

    def test_pack_msix_accepts_it_too(self):
        args = builder_cli.build_arg_parser().parse_args(
            ["pack-msix", "--config", "c.json", "--lang", "en"])
        self.assertEqual(args.lang, "en")


class TheLanguageIsResolved(unittest.TestCase):
    def test_the_flag_wins_over_the_system(self):
        with mock.patch("builder_cli.lang_detect.detect_system_language",
                        return_value="zh-TW"):
            self.assertEqual(builder_cli.resolve_language("en"), "en")

    def test_without_the_flag_the_system_decides(self):
        with mock.patch("builder_cli.lang_detect.detect_system_language",
                        return_value="en") as detect:
            self.assertEqual(builder_cli.resolve_language(None), "en")
        self.assertEqual(detect.call_count, 1)

    def test_the_detection_is_limited_to_the_languages_that_exist(self):
        """偵測到日文時要退回預設語言，而不是拿一把不存在的鍵去查表。"""
        with mock.patch("builder_cli.lang_detect.detect_system_language") as detect:
            builder_cli.resolve_language(None)
        supported = detect.call_args[0][0]
        self.assertEqual(set(supported), set(install_engine.LANGUAGES))


class TheIncompatibilityListComesOutInThatLanguage(unittest.TestCase):
    """真正要驗的是端到端：旗標有沒有一路傳到組訊息的地方。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app_dir = os.path.join(self.tmp, "app")
        os.makedirs(self.app_dir)
        # prep.bat 要真的存在：欄位驗證會先確認腳本檔案在不在，不存在的話
        # 會停在那一步，走不到相容性檢查（也就測不到這裡要測的東西）。
        for name in ("main.exe", "icon.ico", "prep.bat"):
            with open(os.path.join(self.app_dir, name), "wb") as f:
                f.write(b"x")
        write_test_png(os.path.join(self.app_dir, "icon.png"))
        self.config = os.path.join(self.tmp, "cfg.json")
        with open(self.config, "w", encoding="utf-8") as f:
            json.dump({
                "install_engine": "msix",
                "app_dir": self.app_dir,
                "png_icon": os.path.join(self.app_dir, "icon.png"),
                "ico_icon": os.path.join(self.app_dir, "icon.ico"),
                "app_name": "DemoApp",
                "version": "1.0.0",
                "publisher": "Demo",
                "exe_name": "Setup_DemoApp",
                "main_exe": "main.exe",
                "no_admin_install": True,
                # 這一項在 MSIX 下是第三類，會讓驗證擋下來並印出清單
                "pre_install_script": "prep.bat",
                "msix": {"identity_name": "MyCompany.DemoApp",
                         "certificate_subject": "CN=Demo"},
            }, f)

    def _run(self, extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch("builder_cli.lang_detect.detect_system_language",
                           return_value="zh-TW"):
            code = builder_cli.main(["pack-msix", "--config", self.config] + extra)
        return code, out.getvalue() + err.getvalue()

    def test_english_is_requested_and_english_comes_out(self):
        code, output = self._run(["--lang", "en"])
        self.assertEqual(code, 1)
        self.assertIn("pre_install_script", output)
        self.assertNotIn("尚未支援", output)
        self.assertNotIn("此為格式本身的限制", output)

    def test_the_default_is_still_traditional_chinese(self):
        code, output = self._run([])
        self.assertEqual(code, 1)
        self.assertIn("格式本身的限制", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
