"""配置精靈在 MSIX 引擎下的打包流程（第十三輪決議第三項）。

該決議的判斷：GUI 的表單欄位與 CLI 的設定欄位目前一一對應，沒有任何一方
獨有的功能；MSIX 若停在 CLI，會是這個工具第一項雙方不對等的功能。

GUI 的形狀依「憑證在不在本機」分歧，與 CLI 同一套判準：

- 憑證在本機（有 `signing`）——同一顆「編譯」按鈕一路編到底，體驗與傳統
  引擎完全一致。
- 憑證不在本機——編到 `.msix` 為止，並於畫面說明後續步驟。GUI 沒有等同於
  `pack-msix` 的第二個入口，這條路只能由同一顆按鈕承擔。

使用者不需要理解「兩截式」這個概念才能使用這個工具；只有實際走到雲端代簽
的使用者才會遇到它。
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui_config
from _fakes import write_test_png


class MsixEngineTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app_dir = os.path.join(self.tmp, "app")
        os.makedirs(self.app_dir)
        with open(os.path.join(self.app_dir, "main.exe"), "wb") as f:
            f.write(b"fake")
        self.png = os.path.join(self.tmp, "icon.png")
        write_test_png(self.png)
        self.ico = os.path.join(self.tmp, "icon.ico")
        with open(self.ico, "wb") as f:
            f.write(b"fake ico")

        self.api = gui_config.ConfigAPI()
        self.api.app_dir = self.app_dir
        self.api.png_path = self.png
        self.api.ico_path = self.ico
        self.api.doc_icon_path = ""
        self.api._window = mock.Mock()

        self.cert = os.path.join(self.tmp, "cert.pfx")
        with open(self.cert, "wb") as f:
            f.write(b"fake pfx")
        os.environ["TEST_GUI_MSIX_PW"] = "pw"
        self.addCleanup(os.environ.pop, "TEST_GUI_MSIX_PW", None)

        self.workspace = os.path.join(self.tmp, "ws")
        os.makedirs(os.path.join(self.workspace, "ui"))
        for rel in ("ui/index.html", "ui/uninstall.html", "uninstall.py"):
            with open(os.path.join(self.workspace, *rel.split("/")), "w", encoding="utf-8") as f:
                f.write("x")

    def _data(self, **overrides):
        data = {
            "install_engine": "msix",
            "app_name": "DemoApp",
            "folder_name": "",
            "version": "1.0.0",
            "publisher": "Demo",
            "exe_name": "Setup_DemoApp",
            "main_exe": "main.exe",
            "dependencies": [],
            "file_associations": "",
            "need_file_assoc": False,
            "use_custom_doc_icon": False,
            "add_to_path": False,
            "no_admin_install": True,
            "msix": {
                "identity_name": "MyCompany.DemoApp",
                "certificate_subject": "CN=Demo",
            },
        }
        data.update(overrides)
        return data

    def _signing(self):
        return {
            "cert_path": self.cert,
            "cert_password_env": "TEST_GUI_MSIX_PW",
            "timestamp_url": "http://timestamp.example/ts",
        }

    def _pack(self, data, build_msix_return=None, build_msix_side_effect=None,
              env_overrides=None):
        """跑完 start_pack() 與它啟動的背景執行緒，回傳
        (start_pack 的結果, build_msix 替身, build_all 替身, 回報給畫面的訊息)。"""
        ready = {
            "pyinstaller_found": True, "python_found": True, "python_path": "python",
            "webview_found": True, "pywin32_found": True, "ready": True,
            # MSIX 引擎的安裝檔靠 `winrt-*` 綁定套件呼叫 Windows 的部署介面。
            # 預設值代表「打包機器已經裝好」；缺少時的行為由
            # TheMsixBindingsAreRequiredBeforeAnyPackaging 另外測。
            "msix_backend_found": True,
        }
        ready.update(env_overrides or {})
        reported = []
        self.api._window.evaluate_js.side_effect = lambda js: reported.append(js)

        def run_now(target=None, args=(), **kwargs):
            thread = mock.Mock()
            thread.start.side_effect = lambda: target(*args)
            return thread

        kw = {}
        if build_msix_side_effect is not None:
            kw["side_effect"] = build_msix_side_effect
        else:
            kw["return_value"] = build_msix_return or os.path.join(
                self.workspace, "MyCompany.DemoApp.msix")

        with mock.patch("gui_config.check_build_environment", return_value=ready), \
                mock.patch("gui_config.ensure_workspace_files", return_value=None), \
                mock.patch("gui_config.get_workspace_dir", return_value=self.workspace), \
                mock.patch("gui_config.threading.Thread", side_effect=run_now), \
                mock.patch("gui_config.builder.build_all") as build_all, \
                mock.patch("gui_config.builder.build_msix", **kw) as build_msix:
            result = self.api.start_pack(data)
        return result, build_msix, build_all, "\n".join(reported)


class LocalCertificateRunsAllTheWay(MsixEngineTestBase):
    """憑證在本機時，同一顆按鈕一路編到底——與傳統引擎的體驗一致。"""

    def test_the_package_is_built_and_then_embedded(self):
        result, build_msix, build_all, _ = self._pack(self._data(signing=self._signing()))
        self.assertEqual(result["status"], "processing", result)
        build_msix.assert_called_once()
        build_all.assert_called_once()
        self.assertEqual(build_all.call_args.kwargs["signed_msix"],
                         build_msix.return_value)

    def test_the_certificate_reaches_the_package_build(self):
        """簽章要傳下去，不然串起來的是一份未簽章的套件，而未簽章的套件
        裝不起來。"""
        _, build_msix, _, _ = self._pack(self._data(signing=self._signing()))
        self.assertEqual(build_msix.call_args.kwargs["signing"]["cert_path"], self.cert)

    def test_the_intermediate_package_is_not_left_inside_dist(self):
        """`dist/` 會在編 bootstrapper exe 之前被清空。"""
        _, build_msix, _, _ = self._pack(self._data(signing=self._signing()))
        output = build_msix.call_args.kwargs["output_path"]
        dist = os.path.join(os.path.abspath(self.workspace), "dist")
        self.assertFalse(os.path.abspath(output).startswith(dist + os.sep), output)

    def test_the_engine_is_passed_to_the_build(self):
        _, _, build_all, _ = self._pack(self._data(signing=self._signing()))
        self.assertEqual(build_all.call_args.kwargs["install_engine"], "msix")

    def test_the_success_message_names_the_installer(self):
        _, _, _, reported = self._pack(self._data(signing=self._signing()))
        self.assertIn("packComplete", reported)
        self.assertIn("success", reported)
        self.assertIn("Setup_DemoApp.exe", reported)


class WithoutALocalCertificateItStopsAtThePackage(MsixEngineTestBase):
    """憑證不在本機時編到 `.msix` 為止。GUI 沒有等同於 pack-msix 的第二個
    入口，這條路只能由同一顆按鈕承擔。"""

    def test_the_package_is_built_unsigned(self):
        _, build_msix, _, _ = self._pack(self._data())
        build_msix.assert_called_once()
        self.assertIsNone(build_msix.call_args.kwargs["signing"])

    def test_the_installer_is_not_built(self):
        """未簽章的套件內嵌進去，那份安裝檔要到終端使用者手上才會失敗。"""
        _, _, build_all, _ = self._pack(self._data())
        build_all.assert_not_called()

    def test_the_message_says_what_was_produced_and_what_was_not(self):
        _, _, _, reported = self._pack(self._data())
        self.assertIn(".msix", reported)
        self.assertIn("簽章", reported)

    def test_the_message_does_not_claim_an_installer_was_built(self):
        """按下去的人本來預期拿到一顆安裝檔，訊息說成「編譯完成」會讓他
        以為東西已經齊了。"""
        _, _, _, reported = self._pack(self._data())
        self.assertNotIn("Setup_DemoApp.exe", reported)


class TheTraditionalEngineIsUnaffected(MsixEngineTestBase):
    def test_no_package_is_built(self):
        _, build_msix, build_all, _ = self._pack(
            self._data(install_engine="traditional", signing=self._signing(), msix={}))
        build_msix.assert_not_called()
        build_all.assert_called_once()

    def test_nothing_is_embedded(self):
        _, _, build_all, _ = self._pack(
            self._data(install_engine="traditional", msix={}))
        self.assertEqual(build_all.call_args.kwargs.get("signed_msix", ""), "")


class FailuresStopBeforeTheInstaller(MsixEngineTestBase):
    def test_a_packaging_failure_is_reported_and_nothing_is_embedded(self):
        _, _, build_all, reported = self._pack(
            self._data(signing=self._signing()),
            build_msix_side_effect=Exception("makeappx 掛了"))
        build_all.assert_not_called()
        self.assertIn("error", reported)
        self.assertIn("makeappx", reported)


class TheMsixBindingsAreRequiredBeforeAnyPackaging(MsixEngineTestBase):
    """打包機器缺少 `winrt-*` 綁定套件時，按下編譯就要被擋下來。

    真實踩到的缺陷（2026-09-03）：缺少該綁定不影響打包流程的任何一步，
    工具因此回報編譯成功，而產出的 Setup.exe 一執行即中止於
    「No module named 'winrt'」。整條回饋路徑上唯一會發現問題的人是終端
    使用者，且他手上沒有任何可以據以修正的線索。
    """

    def test_the_build_is_refused_and_nothing_is_packaged(self):
        result, build_msix, build_all, _ = self._pack(
            self._data(signing=self._signing()),
            env_overrides={"msix_backend_found": False})
        self.assertEqual(result["status"], "error", result)
        self.assertIn("winrt", result["message"])
        build_msix.assert_not_called()
        build_all.assert_not_called()

    def test_the_traditional_engine_is_not_blocked_by_them(self):
        """傳統引擎的安裝檔不呼叫部署介面，缺這幾個套件與它無關。"""
        _, _, build_all, _ = self._pack(
            self._data(install_engine="traditional", msix={}),
            env_overrides={"msix_backend_found": False})
        build_all.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
