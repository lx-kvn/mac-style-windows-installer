"""packaging_settings.py 的測試：持久化「打包工具」少數幾個使用者偏好設定
（目前只有 workspace_dir，見 packaging_core.get_workspace_dir()）。

用通用的 key/value JSON 檔案存，不是每個設定各自寫一支函式——以防未來
新增第二個要記住的偏好設定時，不用重新設計持久化機制。

一律把 LOCALAPPDATA 導向暫存資料夾，不會動到這台機器真正的設定檔。
"""
import os
import sys
import json
import shutil
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import packaging_settings as ps


class TestSettingsPath(unittest.TestCase):
    def test_uses_localappdata_when_set(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "C:\\Users\\Tester\\AppData\\Local"}):
            path = ps.settings_path()
        self.assertEqual(
            path,
            os.path.join("C:\\Users\\Tester\\AppData\\Local", "mac-style-windows-installer", "gui_settings.json"),
        )

    def test_falls_back_when_localappdata_missing(self):
        env = dict(os.environ)
        env.pop("LOCALAPPDATA", None)
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch("packaging_settings.os.path.expanduser", return_value="C:\\Users\\Tester"):
            path = ps.settings_path()
        self.assertEqual(
            path,
            os.path.join("C:\\Users\\Tester", "AppData", "Local", "mac-style-windows-installer", "gui_settings.json"),
        )


class TestLoadSaveSettings(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.env_patcher = mock.patch.dict(os.environ, {"LOCALAPPDATA": self.tmp_dir})
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_load_returns_empty_dict_when_file_missing(self):
        self.assertEqual(ps.load_settings(), {})

    def test_save_then_load_roundtrips(self):
        self.assertTrue(ps.save_settings({"workspace_dir": "D:\\Builds\\Workspace"}))
        self.assertEqual(ps.load_settings(), {"workspace_dir": "D:\\Builds\\Workspace"})

    def test_save_creates_parent_directory(self):
        ps.save_settings({"workspace_dir": "D:\\X"})
        self.assertTrue(os.path.exists(ps.settings_path()))

    def test_load_returns_empty_dict_when_file_corrupt(self):
        path = ps.settings_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("not valid json{{{")
        self.assertEqual(ps.load_settings(), {})

    def test_load_returns_empty_dict_when_file_is_not_a_json_object(self):
        path = ps.settings_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump([1, 2, 3], f)
        self.assertEqual(ps.load_settings(), {})

    def test_save_swallows_failure_and_returns_false(self):
        with mock.patch("packaging_settings.open", side_effect=OSError("模擬寫入失敗")):
            self.assertFalse(ps.save_settings({"workspace_dir": "D:\\X"}))


if __name__ == "__main__":
    unittest.main()
