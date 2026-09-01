"""第四類的說明要真的送到使用者眼前。

`install_engine.check_settings()` 早就會產出第四類的說明（不擋建置、只需要
在建置訊息裡說明為什麼那個設定沒有作用），但一直沒有接收端——
`packaging_core.py` 收下 Report 之後只用了 `blocking`，`notices` 就地丟棄。

該處原本留有一段說明，寫著「它只在 MSIX 引擎下產生，而 MSIX 引擎在上一行
就中止了」。那個中止已於引擎實作完成時移除，該說明因此不再是事實，而說明
所描述的暫時狀態變成了永久的缺口：一份設定填了 `folder_name` 又選了 MSIX，
工具從頭到尾不會告訴使用者那個欄位不會有作用。

配置精靈那邊有就地灰掉並附一行原因（第十四輪決議第四項），但 CLI 沒有——
而 CLI 正是 CI 會用的那一個。
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import builder
import install_engine
import packaging_core
from _fakes import write_test_png


class ValidationCarriesTheNoticesForward(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app_dir = os.path.join(self.tmp, "app")
        os.makedirs(self.app_dir)
        for name in ("main.exe", "icon.ico"):
            with open(os.path.join(self.app_dir, name), "wb") as f:
                f.write(b"x")
        self.png = os.path.join(self.tmp, "icon.png")
        write_test_png(self.png)
        self.ico = os.path.join(self.tmp, "icon.ico")
        with open(self.ico, "wb") as f:
            f.write(b"x")

    def _data(self, **overrides):
        data = {
            "install_engine": "msix",
            "app_name": "DemoApp",
            "version": "1.0.0",
            "publisher": "Demo",
            "exe_name": "Setup_DemoApp",
            "main_exe": "main.exe",
            "no_admin_install": True,
            "msix": {"identity_name": "MyCompany.DemoApp",
                     "certificate_subject": "CN=Demo"},
        }
        data.update(overrides)
        return data

    def _validate(self, **overrides):
        return packaging_core.validate_and_build_pack_data(
            self._data(**overrides), self.app_dir, self.png, self.ico, "",
            **{k: overrides.pop(k) for k in () if False})

    def test_a_moot_setting_produces_a_notice(self):
        """folder_name 在 MSIX 下不會有作用——使用者要被告知，不是靜默忽略。"""
        pack_data, error = packaging_core.validate_and_build_pack_data(
            self._data(folder_name="MyAppFolder"), self.app_dir, self.png, self.ico, "")
        self.assertIsNone(error, error)
        self.assertTrue(pack_data.get("engine_notices"),
                        "第四類的說明沒有被帶出來")
        self.assertIn("folder_name", pack_data["engine_notices"][0])

    def test_no_moot_setting_means_no_notice(self):
        pack_data, error = packaging_core.validate_and_build_pack_data(
            self._data(), self.app_dir, self.png, self.ico, "")
        self.assertIsNone(error, error)
        self.assertEqual(pack_data.get("engine_notices", []), [])

    def test_the_traditional_engine_produces_no_notice(self):
        pack_data, error = packaging_core.validate_and_build_pack_data(
            self._data(install_engine="traditional", folder_name="MyAppFolder", msix={}),
            self.app_dir, self.png, self.ico, "")
        self.assertIsNone(error, error)
        self.assertEqual(pack_data.get("engine_notices", []), [])

    def test_the_notice_follows_the_requested_language(self):
        zh, _ = packaging_core.validate_and_build_pack_data(
            self._data(folder_name="MyAppFolder"), self.app_dir, self.png, self.ico, "",
            lang="zh-TW")
        en, _ = packaging_core.validate_and_build_pack_data(
            self._data(folder_name="MyAppFolder"), self.app_dir, self.png, self.ico, "",
            lang="en")
        self.assertNotEqual(zh["engine_notices"], en["engine_notices"])


class TheBuildReportsThem(unittest.TestCase):
    """送到建置紀錄裡，跟其他進度訊息走同一條路——那是使用者實際會看的地方。

    這裡實際跑一次 build_all 並攔下 progress_callback 收到的每一則訊息。
    只斷言參數存在的話，實作忘了真的呼叫也照樣通過。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app_dir = os.path.join(self.tmp, "app")
        os.makedirs(self.app_dir)
        with open(os.path.join(self.app_dir, "main.exe"), "wb") as f:
            f.write(b"x")
        self.workspace = os.path.join(self.tmp, "ws")
        os.makedirs(os.path.join(self.workspace, "ui"))
        for rel in ("ui/index.html", "ui/uninstall.html", "uninstall.py",
                    "installer_core.py"):
            with open(os.path.join(self.workspace, *rel.split("/")), "w", encoding="utf-8") as f:
                f.write("x")
        self.png = os.path.join(self.tmp, "icon.png")
        write_test_png(self.png)
        self.ico = os.path.join(self.tmp, "icon.ico")
        with open(self.ico, "wb") as f:
            f.write(b"x")
        self.dist = os.path.join(self.workspace, "dist")

    def _run(self, **overrides):
        messages = []

        def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
            if "uninstall.py" in cmd:
                os.makedirs(self.dist, exist_ok=True)
                with open(os.path.join(self.dist, "uninstall.exe"), "wb") as f:
                    f.write(b"FAKE")
            return mock.Mock(returncode=0, stdout="", stderr="")

        kwargs = dict(
            app_dir=self.app_dir, exe_name="Setup_Demo", app_name="Demo",
            folder_name="Demo", version="1.0.0", publisher="Demo",
            png_path=self.png, ico_path=self.ico, main_exe="main.exe",
            workspace_dir=self.workspace,
            progress_callback=lambda pct, msg, cap=99, tc=15: messages.append(msg),
        )
        kwargs.update(overrides)
        with mock.patch("builder.subprocess.run", side_effect=fake_run):
            builder.build_all(**kwargs)
        return messages

    def test_each_notice_reaches_the_progress_callback(self):
        notices = ["folder_name 在 MSIX 引擎下不會有作用，也不需要。"]
        messages = self._run(engine_notices=notices)
        self.assertTrue(any(notices[0] in m for m in messages),
                        f"說明沒有出現在建置訊息裡：{messages}")

    def test_no_notices_adds_nothing(self):
        messages = self._run()
        self.assertTrue(messages, "連原本的進度訊息都不見了")
        self.assertFalse(any("不會有作用" in m for m in messages))


class PackMsixShowsThemToo(unittest.TestCase):
    """真實抓到的缺口：`pack-msix` 走的是 build_msix，根本不經過 build_all，
    因此把說明掛在 build_all 的進度回報上，這條指令的使用者永遠收不到。

    而 `pack-msix` 正是 CI 走的那一條——第四類的說明在那裡反而最需要，因為
    CI 上沒有人盯著畫面看有沒有欄位被灰掉。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app_dir = os.path.join(self.tmp, "app")
        os.makedirs(self.app_dir)
        for name in ("main.exe", "icon.ico"):
            with open(os.path.join(self.app_dir, name), "wb") as f:
                f.write(b"x")
        write_test_png(os.path.join(self.app_dir, "icon.png"))
        self.config = os.path.join(self.tmp, "cfg.json")

    def _write_config(self, **overrides):
        import json
        data = {
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
            "msix": {"identity_name": "MyCompany.DemoApp",
                     "certificate_subject": "CN=Demo"},
        }
        data.update(overrides)
        with open(self.config, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def _run(self, extra=()):
        import contextlib
        import io as _io
        import builder_cli
        out, err = _io.StringIO(), _io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch("builder_cli.builder.build_msix", return_value="p.msix"):
            code = builder_cli.main(
                ["pack-msix", "--config", self.config,
                 "--workspace-dir", os.path.join(self.tmp, "ws")] + list(extra))
        return code, out.getvalue() + err.getvalue()

    def test_a_moot_setting_is_explained(self):
        self._write_config(folder_name="DemoFolder")
        code, output = self._run()
        self.assertEqual(code, 0, output)
        self.assertIn("folder_name", output)
        self.assertIn("不會有作用", output)

    def test_nothing_is_said_when_there_is_nothing_to_say(self):
        self._write_config()
        code, output = self._run()
        self.assertEqual(code, 0, output)
        self.assertNotIn("不會有作用", output)

    def test_it_follows_the_language_flag(self):
        self._write_config(folder_name="DemoFolder")
        _, output = self._run(["--lang", "en"])
        self.assertIn("no effect under the MSIX engine", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
