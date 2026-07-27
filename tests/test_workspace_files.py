"""gui_config.py 的工作目錄機制測試。

這裡測的是規格文件 §4 記錄過的真實 bug 重現場景：installer_core.py /
uninstall.py / ui/index.html 這幾個「內部實作檔案」每次都要無條件覆蓋
（不然重複用同一個工作目錄重新打包新版 InstallerBuilder.exe 時，舊版本永遠
換不掉、後續修正都不會生效），而 ui/ 底下其他「使用者可能自訂過的靜態資源」
（例如 folder_icon.png）要維持「只在缺少時才補」，不能覆蓋使用者的客製化。
這兩條規則刻意設計成相反的行為，最容易在修改時不小心弄反，值得專門測試鎖住。
"""
import os
import sys
import shutil
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui_config


class TestGetWorkspaceDir(unittest.TestCase):
    def test_non_frozen_uses_cwd(self):
        with mock.patch.object(sys, "_MEIPASS", "C:\\fake\\meipass", create=True):
            pass  # 只是確保下面刪除時不會因為屬性本來就不存在而出錯
        if hasattr(sys, "_MEIPASS"):
            del sys._MEIPASS
        self.assertEqual(gui_config.get_workspace_dir(), os.path.abspath("."))

    def test_frozen_uses_exe_directory(self):
        with mock.patch.object(sys, "_MEIPASS", "C:\\fake\\meipass", create=True), \
             mock.patch.object(sys, "executable", "C:\\Users\\Test\\InstallerBuilder.exe"):
            self.assertEqual(gui_config.get_workspace_dir(), "C:\\Users\\Test")


class TestEnsureWorkspaceFiles(unittest.TestCase):
    def setUp(self):
        self.embedded_dir = tempfile.mkdtemp()
        self.workspace_dir = tempfile.mkdtemp()

        with open(os.path.join(self.embedded_dir, "installer_core.py"), "w") as f:
            f.write("# NEW installer_core content")
        with open(os.path.join(self.embedded_dir, "uninstall.py"), "w") as f:
            f.write("# NEW uninstall content")
        os.makedirs(os.path.join(self.embedded_dir, "ui"))
        with open(os.path.join(self.embedded_dir, "ui", "index.html"), "w") as f:
            f.write("<!-- NEW index.html -->")
        with open(os.path.join(self.embedded_dir, "ui", "folder_icon.png"), "wb") as f:
            f.write(b"NEW_ICON_BYTES")

        def fake_get_resource_path(relative_path):
            return os.path.join(self.embedded_dir, relative_path)

        self.patcher_meipass = mock.patch.object(sys, "_MEIPASS", "C:\\fake\\meipass", create=True)
        self.patcher_meipass.start()
        self.patcher_resource = mock.patch.object(
            gui_config, "get_resource_path", side_effect=fake_get_resource_path
        )
        self.patcher_resource.start()

    def tearDown(self):
        self.patcher_resource.stop()
        self.patcher_meipass.stop()
        shutil.rmtree(self.embedded_dir, ignore_errors=True)
        shutil.rmtree(self.workspace_dir, ignore_errors=True)

    def test_non_frozen_mode_does_nothing(self):
        """.py 直接執行（沒有 _MEIPASS）時，工作目錄就是原始碼目錄，不需要複製任何東西。"""
        self.patcher_meipass.stop()
        try:
            result = gui_config.ensure_workspace_files(self.workspace_dir)
            self.assertIsNone(result)
            self.assertEqual(os.listdir(self.workspace_dir), [])
        finally:
            self.patcher_meipass.start()

    def test_core_scripts_are_always_overwritten(self):
        """installer_core.py / uninstall.py 是內部實作，即使工作目錄裡已經有舊版本，
        也必須無條件被目前這顆 exe 內嵌的新版本蓋掉——這正是規格文件記錄過的真實 bug：
        『只在缺少時才複製』會導致重複用同一個工作目錄打包新版時，舊版本永遠換不掉。
        """
        with open(os.path.join(self.workspace_dir, "installer_core.py"), "w") as f:
            f.write("# STALE old content that must be replaced")

        result = gui_config.ensure_workspace_files(self.workspace_dir)

        self.assertIsNone(result)
        with open(os.path.join(self.workspace_dir, "installer_core.py")) as f:
            self.assertEqual(f.read(), "# NEW installer_core content")

    def test_index_html_is_always_overwritten(self):
        """ui/index.html 也是內部實作（安裝端介面），不是使用者自訂項目，同樣要無條件覆蓋。"""
        os.makedirs(os.path.join(self.workspace_dir, "ui"))
        with open(os.path.join(self.workspace_dir, "ui", "index.html"), "w") as f:
            f.write("<!-- STALE old index.html -->")

        gui_config.ensure_workspace_files(self.workspace_dir)

        with open(os.path.join(self.workspace_dir, "ui", "index.html")) as f:
            self.assertEqual(f.read(), "<!-- NEW index.html -->")

    def test_user_customized_static_asset_is_preserved(self):
        """folder_icon.png 這類使用者可能自訂過的靜態資源，行為要跟上面兩個相反：
        已經存在的話絕對不能覆蓋，否則使用者換掉的自訂圖示會被每次重新編譯無聲蓋掉。
        """
        os.makedirs(os.path.join(self.workspace_dir, "ui"))
        with open(os.path.join(self.workspace_dir, "ui", "folder_icon.png"), "wb") as f:
            f.write(b"USER_CUSTOM_ICON_BYTES")

        gui_config.ensure_workspace_files(self.workspace_dir)

        with open(os.path.join(self.workspace_dir, "ui", "folder_icon.png"), "rb") as f:
            self.assertEqual(f.read(), b"USER_CUSTOM_ICON_BYTES")

    def test_missing_static_asset_gets_copied_in(self):
        """使用者還沒自訂過（工作目錄裡根本沒有這個檔案）時，要從內嵌資源補上，
        不然編譯出來的安裝檔會缺這個靜態資源（規格文件記錄過的另一個真實 bug：
        安裝視窗右側資料夾圖示消失）。"""
        gui_config.ensure_workspace_files(self.workspace_dir)

        dest = os.path.join(self.workspace_dir, "ui", "folder_icon.png")
        self.assertTrue(os.path.exists(dest))
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), b"NEW_ICON_BYTES")

    def test_copy_failure_returns_readable_error_message(self):
        with mock.patch.object(shutil, "copy2", side_effect=PermissionError("拒絕存取")):
            result = gui_config.ensure_workspace_files(self.workspace_dir)
        self.assertIsNotNone(result)
        self.assertIn("寫入權限", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
