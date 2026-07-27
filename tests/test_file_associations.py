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
from _fakes import FakeWinReg


def make_installer_api(**overrides):
    """建立一個不需要真的讀 installer_config.json 的 InstallerAPI 實例，
    直接覆寫測試需要的欄位，繞開 load_config() 對磁碟檔案的依賴。
    """
    api = installer_core.InstallerAPI()
    for k, v in overrides.items():
        setattr(api, k, v)
    return api


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

        with mock.patch("gui_config.check_build_environment", return_value={"ready": True}), \
             mock.patch("gui_config.ensure_workspace_files", return_value=None), \
             mock.patch("gui_config.builder.build_all"):
            result = self.api.start_pack(data)

        self.assertEqual(result["status"], "processing")


class TestFileAssociationRegistration(unittest.TestCase):
    """測資料流最後一段：installer_core.py 實際寫登錄表那個函式。"""

    def setUp(self):
        self.tmp_install_dir = tempfile.mkdtemp()
        self.fake_reg = FakeWinReg()
        self.patcher = mock.patch.dict(sys.modules, {"winreg": self.fake_reg})
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmp_install_dir, ignore_errors=True)

    def test_success_path_writes_expected_registry_shape(self):
        api = make_installer_api(
            file_associations=[".xyz"],
            main_exe="MyApp.exe",
            app_name="MyApp",
            doc_icon="",
            selected_path=self.tmp_install_dir,
        )
        with mock.patch("installer_core.ctypes.windll.shell32.SHChangeNotify"):
            api._register_file_associations()

        store = self.fake_reg.store
        self.assertIn("Software\\Classes\\.xyz", store)
        prog_id = store["Software\\Classes\\.xyz"][""]
        self.assertIn(f"Software\\Classes\\{prog_id}\\shell\\open\\command", store)
        command = store[f"Software\\Classes\\{prog_id}\\shell\\open\\command"][""]
        self.assertIn("MyApp.exe", command)
        self.assertIn('"%1"', command)
        self.assertIn(f"Software\\Classes\\{prog_id}\\DefaultIcon", store)

    def test_registry_failure_now_propagates(self):
        """修復驗證：_register_file_associations() 失敗時，現在會直接讓例外往外拋，
        不再被 print() 靜默吞掉。呼叫端（trigger_installation()）可以接住這個例外，
        觸發回滾並回報安裝失敗，不會誤報「安裝成功」。
        """
        self.fake_reg.fail_on_substring = ".xyz"
        api = make_installer_api(
            file_associations=[".xyz"],
            main_exe="MyApp.exe",
            app_name="MyApp",
            doc_icon="",
            selected_path=self.tmp_install_dir,
        )

        with mock.patch("installer_core.ctypes.windll.shell32.SHChangeNotify"):
            with self.assertRaises(PermissionError):
                api._register_file_associations()

        self.assertNotIn("Software\\Classes\\.xyz", self.fake_reg.store, "確認登錄表真的沒寫成功")

    def test_clears_existing_user_choice_override(self):
        """修復驗證：使用者之前手動選過（或系統自動選過）這個副檔名的預設開啟程式時，
        Windows 8+ 會在 HKCU 留一個 UserChoice 機碼，Explorer 之後只認這個機碼、
        完全無視我們寫的 HKLM 關聯。現在安裝時要順便清掉這個機碼，下次雙擊才會真的
        套用新安裝的關聯，而不是照樣打開使用者先前選的舊程式。
        """
        user_choice_path = (
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.xyz\UserChoice"
        )
        self.fake_reg.store[user_choice_path] = {"ProgId": "Notepad", "Hash": "abc123"}
        api = make_installer_api(
            file_associations=[".xyz"],
            main_exe="MyApp.exe",
            app_name="MyApp",
            doc_icon="",
            selected_path=self.tmp_install_dir,
        )
        logged = []

        with mock.patch("installer_core.ctypes.windll.shell32.SHChangeNotify"):
            api._register_file_associations(log=logged.append)

        self.assertNotIn(user_choice_path, self.fake_reg.store)
        self.assertTrue(any(".xyz" in msg for msg in logged))

    def test_missing_user_choice_does_not_raise(self):
        """最常見的情況：這個副檔名從沒被手動選過，UserChoice 機碼根本不存在，
        清除動作本來就該是「盡量做」，不存在就跳過，不能讓整個檔案關聯因此失敗。"""
        api = make_installer_api(
            file_associations=[".xyz"],
            main_exe="MyApp.exe",
            app_name="MyApp",
            doc_icon="",
            selected_path=self.tmp_install_dir,
        )
        with mock.patch("installer_core.ctypes.windll.shell32.SHChangeNotify"):
            api._register_file_associations()  # 不應該拋例外

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
