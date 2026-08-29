"""配置精靈「直接輸入安裝密碼」那條路的資料流測試（見 docs/adr/0004）。

這條路存在的理由：`validate_and_build_pack_data()` 收的那包 `data`，欄位
集合就是設定檔的格式，而 GUI 跟 CLI 共用同一個驗證函式。讓「直接輸入
密碼」變成 `data` 的一個一般欄位，等於同時讓設定檔也能寫明文密碼，把當初
繞環境變數要避開的風險原封不動放回來。

所以密碼走的是一條獨立的參數路徑：
`ConfigAPI.start_pack(data, install_password)` → `builder.build_all(
install_password=...)`，全程不進 `data`、不進 `pack_data`。這裡釘住的就是
「它真的沒有進去」以及「它真的有傳到底」這兩件事——前者只要有人為了圖方便
把它塞回 `data` 就會破功，而破功的後果（設定檔可以寫明文密碼）不會有任何
測試以外的地方會叫。
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui_config


class ConfigApiPasswordTestBase(unittest.TestCase):
    def setUp(self):
        self.api = gui_config.ConfigAPI()
        self.app_dir = tempfile.mkdtemp()
        with open(os.path.join(self.app_dir, "main.exe"), "wb") as f:
            f.write(b"fake")
        self.api.app_dir = self.app_dir
        self.api.png_path = "fake.png"
        self.api.ico_path = "fake.ico"
        self.api.doc_icon_path = ""

    def tearDown(self):
        shutil.rmtree(self.app_dir, ignore_errors=True)

    def _data(self, **overrides):
        data = {
            "app_name": "TestApp",
            "folder_name": "",
            "version": "1.0.0",
            "publisher": "Tester",
            "exe_name": "Setup_TestApp",
            "main_exe": "main.exe",
            "dependencies": [],
            "file_associations": "",
            "need_file_assoc": False,
            "use_custom_doc_icon": False,
            "add_to_path": False,
        }
        data.update(overrides)
        return data

    def _start_pack(self, data, install_password=""):
        """跑到 start_pack() 為止，攔下真正的背景打包執行緒，回傳
        (呼叫結果, 交給打包執行緒的 pack_data, 交給它的密碼)。"""
        captured = {}

        def fake_thread(target=None, args=(), **kwargs):
            captured["args"] = args
            return mock.Mock(start=lambda: None)

        with mock.patch("gui_config.check_build_environment", return_value={
                    "ready": True, "pyinstaller_found": True, "python_found": True,
                    "webview_found": True, "pywin32_found": True, "python_path": "python",
                }), \
             mock.patch("gui_config.ensure_workspace_files", return_value=None), \
             mock.patch("gui_config.get_workspace_dir", return_value="."), \
             mock.patch("gui_config.threading.Thread", side_effect=fake_thread):
            result = self.api.start_pack(data, install_password)
        return result, captured.get("args", ())


class TestInlinePasswordReachesTheBuilder(ConfigApiPasswordTestBase):
    def test_start_pack_accepts_a_second_argument(self):
        result, _args = self._start_pack(
            self._data(need_install_password=True), install_password="hunter2",
        )
        self.assertEqual(result["status"], "processing", result.get("message"))

    def test_the_password_is_not_in_pack_data(self):
        _result, args = self._start_pack(
            self._data(need_install_password=True), install_password="hunter2",
        )
        pack_data = args[0]
        self.assertNotIn("install_password", pack_data)
        self.assertNotIn(
            "hunter2", str(pack_data),
            "密碼不該以任何形式出現在 pack_data 裡——那是會被整包傳來傳去的結構",
        )

    def test_the_password_is_passed_alongside_pack_data(self):
        _result, args = self._start_pack(
            self._data(need_install_password=True), install_password="hunter2",
        )
        self.assertIn("hunter2", args[1:], "密碼要以獨立參數交給打包執行緒")

    def test_build_all_receives_the_password(self):
        with mock.patch("gui_config.builder.build_all") as mock_build:
            self.api._run_pack_thread(
                {"app_name": "TestApp", "exe_name": "Setup_TestApp", "version": "1.0.0",
                 "publisher": "Tester", "main_exe": "main.exe", "workspace_dir": "."},
                "hunter2",
            )
        self.assertEqual(mock_build.call_args.kwargs.get("install_password"), "hunter2")

    def test_no_password_passes_an_empty_string(self):
        with mock.patch("gui_config.builder.build_all") as mock_build:
            self.api._run_pack_thread(
                {"app_name": "TestApp", "exe_name": "Setup_TestApp", "version": "1.0.0",
                 "publisher": "Tester", "main_exe": "main.exe", "workspace_dir": "."},
            )
        self.assertEqual(mock_build.call_args.kwargs.get("install_password"), "")


class TestValidationStillAppliesThroughTheGui(ConfigApiPasswordTestBase):
    """驗證規則是共用的，不能因為走 GUI 這條路就繞過去。"""

    def test_enabled_without_anything_supplied_is_rejected(self):
        result, _args = self._start_pack(self._data(need_install_password=True))
        self.assertEqual(result["status"], "error")
        self.assertIn("密碼", result["message"])

    def test_both_sources_at_once_is_rejected(self):
        with mock.patch.dict(os.environ, {"MY_PW_ENV": "hunter2"}):
            result, _args = self._start_pack(
                self._data(need_install_password=True, install_password_env="MY_PW_ENV"),
                install_password="hunter2",
            )
        self.assertEqual(result["status"], "error")
        self.assertIn("擇一", result["message"])


if __name__ == "__main__":
    unittest.main()
