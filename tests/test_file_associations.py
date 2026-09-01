"""
檔案關聯功能測試。

目的：驗證「副檔名關聯」這條資料流从前端表單 -> gui_config.py 解析 ->
builder.py 寫入設定檔 -> installer_core.py 寫登錄表，每一段有沒有問題。

重要：這裡全程用假的 winreg（in-memory dict）替換掉真正的 winreg 呼叫，
不會真的去動這台機器的登錄表，可以放心直接執行，不需要系統管理員權限。

執行方式：python tests/test_file_associations.py
"""
import os
import sys
import shutil
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui_config
import installer_core
from _fakes import make_installer_api, run_threads_synchronously


class TestGuiConfigParsing(unittest.TestCase):
    """測資料流第一段：config.html 送過來的逗號分隔字串，gui_config.py 解析得對不對。"""

    def setUp(self):
        self.api = gui_config.ConfigAPI()
        self.tmp_app_dir = tempfile.mkdtemp()
        with open(os.path.join(self.tmp_app_dir, "main.exe"), "wb") as f:
            f.write(b"fake")
        self.api.app_dir = self.tmp_app_dir
        self.api.png_path = "fake.png"
        self.api.ico_path = "fake.ico"

    def tearDown(self):
        shutil.rmtree(self.tmp_app_dir, ignore_errors=True)

    def _base_data(self, file_associations=""):
        return {
            "app_name": "TestApp",
            "folder_name": "",
            "version": "1.0.0",
            "publisher": "Tester",
            "exe_name": "Setup_TestApp",
            "main_exe": "main.exe",
            "eula_text": "",
            "dependencies": [],
            "file_associations": file_associations,
            "use_custom_doc_icon": False,
            "add_to_path": False,
        }

    def test_parses_mixed_separators_and_missing_dots(self):
        """輸入 "txt, .abc,,  xyz" 應該正確解析成 [".txt", ".abc", ".xyz"]（去重複逗號、補「.」、trim 空白）"""
        captured = {}

        def fake_build_all(**kwargs):
            captured.update(kwargs)

        with mock.patch("gui_config.check_build_environment", return_value={"ready": True}), \
             mock.patch("gui_config.ensure_workspace_files", return_value=None), \
             mock.patch("gui_config.builder.build_all", side_effect=fake_build_all):
            result = self.api.start_pack(self._base_data("txt, .abc,,  xyz"))
            self.assertEqual(result["status"], "processing")
            # start_pack 用背景執行緒跑，這裡等它跑完
            import time
            for _ in range(50):
                if "file_associations" in captured:
                    break
                time.sleep(0.05)

        self.assertEqual(captured.get("file_associations"), [".txt", ".abc", ".xyz"])

    def test_checked_but_empty_extension_is_rejected(self):
        """規格文件 backlog #3 已修：勾選「需要註冊檔案關聯」但副檔名欄位是空的，
        start_pack() 現在會擋下來回傳 error，不會靜默放行編譯。
        """
        data = self._base_data("")
        data["need_file_assoc"] = True

        with mock.patch("gui_config.check_build_environment", return_value={"ready": True}), \
             mock.patch("gui_config.ensure_workspace_files", return_value=None), \
             mock.patch("gui_config.builder.build_all"):
            result = self.api.start_pack(data)

        self.assertEqual(result["status"], "error")
        self.assertIn("副檔名", result["message"])

    def test_unchecked_and_empty_extension_is_still_allowed(self):
        """沒勾選「需要註冊檔案關聯」時，file_associations 留空是正常情況（使用者根本不需要
        這個功能），不應該被新加的驗證誤擋下來。"""
        data = self._base_data("")
        data["need_file_assoc"] = False

        # 打包執行緒要在 with 區塊內跑完。真實抓到的缺陷：原本沒有這一行，
        # start_pack() 起完背景執行緒就回傳、替身隨即被撤掉，那個執行緒接著
        # 呼叫到真正的 build_all()，在 repo 根目錄寫出 installer_config.json
        # 並真的去叫 pyinstaller。留下的那個檔案會被後續建構 InstallerAPI()
        # 的測試撈到（它在建構時就讀那個檔案），造成與執行順序相依的失敗。
        with mock.patch("gui_config.check_build_environment", return_value={"ready": True}), \
             mock.patch("gui_config.ensure_workspace_files", return_value=None), \
             mock.patch("gui_config.threading.Thread",
                        side_effect=run_threads_synchronously()), \
             mock.patch("gui_config.builder.build_all"):
            result = self.api.start_pack(data)

        self.assertEqual(result["status"], "processing")


class TestResolveDocIconRef(unittest.TestCase):
    """installer_core.py 現在只保留「決定用哪個圖示」這段業務邏輯，實際登錄表
    寫入已經收斂進 file_assoc.py（見 tests/test_file_assoc.py）。"""

    def test_custom_doc_icon_points_to_installed_ico(self):
        api = make_installer_api(doc_icon="doc_icon.ico", selected_path="C:\\Apps\\MyApp")
        self.assertEqual(
            api._resolve_doc_icon_ref("C:\\Apps\\MyApp\\MyApp.exe"),
            "C:\\Apps\\MyApp\\doc_icon.ico",
        )

    def test_no_custom_doc_icon_falls_back_to_main_exe_icon(self):
        api = make_installer_api(doc_icon="", selected_path="C:\\Apps\\MyApp")
        self.assertEqual(
            api._resolve_doc_icon_ref("C:\\Apps\\MyApp\\MyApp.exe"),
            "C:\\Apps\\MyApp\\MyApp.exe,0",
        )

    def test_per_extension_icon_takes_priority_over_shared_doc_icon(self):
        """.a 跟 .b 用不同 ICO：doc_icons 裡列出的副檔名要用自己的專屬圖示，
        不是共用的 doc_icon。"""
        api = make_installer_api(
            doc_icon="shared.ico", doc_icons={".a": "icon_a.ico", ".b": "icon_b.ico"},
            selected_path="C:\\Apps\\MyApp",
        )
        self.assertEqual(
            api._resolve_doc_icon_ref("C:\\Apps\\MyApp\\MyApp.exe", ext=".a"),
            "C:\\Apps\\MyApp\\icon_a.ico",
        )
        self.assertEqual(
            api._resolve_doc_icon_ref("C:\\Apps\\MyApp\\MyApp.exe", ext=".b"),
            "C:\\Apps\\MyApp\\icon_b.ico",
        )

    def test_extension_without_per_extension_icon_falls_back_to_shared_doc_icon(self):
        api = make_installer_api(
            doc_icon="shared.ico", doc_icons={".a": "icon_a.ico"},
            selected_path="C:\\Apps\\MyApp",
        )
        self.assertEqual(
            api._resolve_doc_icon_ref("C:\\Apps\\MyApp\\MyApp.exe", ext=".c"),
            "C:\\Apps\\MyApp\\shared.ico",
        )

    def test_extension_without_per_extension_icon_or_shared_icon_falls_back_to_main_exe(self):
        api = make_installer_api(
            doc_icon="", doc_icons={".a": "icon_a.ico"}, selected_path="C:\\Apps\\MyApp",
        )
        self.assertEqual(
            api._resolve_doc_icon_ref("C:\\Apps\\MyApp\\MyApp.exe", ext=".c"),
            "C:\\Apps\\MyApp\\MyApp.exe,0",
        )

    def test_resolve_doc_icon_refs_builds_map_for_all_file_associations(self):
        api = make_installer_api(
            doc_icon="shared.ico", doc_icons={".a": "icon_a.ico"},
            file_associations=[".a", ".b"], selected_path="C:\\Apps\\MyApp",
        )
        self.assertEqual(
            api._resolve_doc_icon_refs("C:\\Apps\\MyApp\\MyApp.exe"),
            {".a": "C:\\Apps\\MyApp\\icon_a.ico", ".b": "C:\\Apps\\MyApp\\shared.ico"},
        )


class TestFileAssociationRegistration(unittest.TestCase):
    """_create_shortcut() 沒有隨這輪收斂搬動，還是留在 installer_core.py。"""

    def setUp(self):
        self.tmp_install_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_install_dir, ignore_errors=True)

    def test_shortcut_failure_stays_non_fatal_but_is_logged(self):
        """_create_shortcut() 是刻意設計成失敗可忽略、不影響安裝，這個行為維持不變，
        但回報管道從無效的 print() 換成真正會寫進 install_log.txt 的 log() callback。
        """
        api = make_installer_api(
            main_exe="MyApp.exe",
            app_name="MyApp",
            selected_path=self.tmp_install_dir,
        )
        logged = []

        with mock.patch("win32com.client.Dispatch", side_effect=RuntimeError("模擬失敗")):
            result = api._create_shortcut(desktop=False, log=logged.append)

        self.assertFalse(result)
        self.assertTrue(logged, "失敗訊息應該透過 log() 傳出去，而不是消失在 print() 裡")
        self.assertIn("可忽略", logged[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
