"""system_entries.py 的測試（從 tests/test_uninstall.py 搬過來）。

拆出來的深模組：解除安裝登錄表項目/捷徑/PATH 環境變數這三種系統層級
寫入的「移除」原語。原本這幾個函式活在 uninstall.py 裡，只有真正解除
安裝時會呼叫；現在收斂成獨立模組，讓 installer_core.py 的安裝失敗
rollback 也能呼叫同一份實作清掉這次安裝已經寫入的部分，不用另外寫一份
邏輯幾乎一樣的複本。

跟 file_assoc.py 用同一種 registry seam：`registry` 參數預設是真正的
winreg 模組，測試直接把 tests/_fakes.py 的 FakeWinReg 當參數傳進去，
不需要 monkeypatch sys.modules 或模組屬性。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _fakes import FakeWinReg
import system_entries as se


class TestRemoveRegistryEntry(unittest.TestCase):
    def setUp(self):
        self.fake_reg = FakeWinReg()

    def _reg_path(self, app_name="MyApp"):
        return f"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{app_name}"

    def test_removes_hklm_entry_by_default(self):
        self.fake_reg.set_hklm(self._reg_path(), {})
        self.assertTrue(se.remove_registry_entry("MyApp", registry=self.fake_reg))
        self.assertIsNone(self.fake_reg.hklm(self._reg_path()))

    def test_removes_hkcu_entry_when_no_admin(self):
        self.fake_reg.set_hkcu(self._reg_path(), {})
        self.assertTrue(
            se.remove_registry_entry("MyApp", no_admin_install=True, registry=self.fake_reg)
        )
        self.assertIsNone(self.fake_reg.hkcu(self._reg_path()))

    def test_returns_false_when_missing(self):
        self.assertFalse(se.remove_registry_entry("NoSuchApp", registry=self.fake_reg))


class TestRemoveShortcut(unittest.TestCase):
    def test_removes_user_desktop_shortcut_when_no_admin(self):
        fake_reg = FakeWinReg()
        with mock.patch("system_entries.os.path.expanduser", return_value="C:\\Users\\Tester"), \
             mock.patch("system_entries.os.path.exists", return_value=True) as mock_exists, \
             mock.patch("system_entries.os.remove") as mock_remove:
            result = se.remove_shortcut("MyApp", desktop=True, no_admin_install=True, registry=fake_reg)
        expected_path = os.path.join("C:\\Users\\Tester", "Desktop", "MyApp.lnk")
        mock_exists.assert_called_once_with(expected_path)
        mock_remove.assert_called_once_with(expected_path)
        self.assertTrue(result)

    def test_returns_false_when_shortcut_missing(self):
        fake_reg = FakeWinReg()
        with mock.patch("system_entries.os.path.exists", return_value=False):
            result = se.remove_shortcut("MyApp", registry=fake_reg)
        self.assertFalse(result)


class TestRemoveFromPath(unittest.TestCase):
    def _machine_path_key(self):
        return r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"

    def test_removes_only_matching_entry_from_machine_path(self):
        fake_reg = FakeWinReg()
        fake_reg.set_hklm(self._machine_path_key(), {"Path": "C:\\Windows;C:\\Apps\\MyApp;C:\\Other"})
        with mock.patch("system_entries.ctypes.windll.user32.SendMessageTimeoutW"):
            se.remove_from_path("C:\\Apps\\MyApp", registry=fake_reg)
        self.assertEqual(fake_reg.hklm(self._machine_path_key())["Path"], "C:\\Windows;C:\\Other")

    def test_removes_from_user_environment_when_no_admin(self):
        fake_reg = FakeWinReg()
        fake_reg.set_hkcu("Environment", {"Path": "C:\\Windows;C:\\Apps\\MyApp"})
        with mock.patch("system_entries.ctypes.windll.user32.SendMessageTimeoutW"):
            se.remove_from_path("C:\\Apps\\MyApp", no_admin_install=True, registry=fake_reg)
        self.assertEqual(fake_reg.hkcu("Environment")["Path"], "C:\\Windows")

    def test_swallows_failure(self):
        fake_reg = FakeWinReg()
        fake_reg.fail_on_substring = "Environment"
        se.remove_from_path("C:\\Apps\\MyApp", registry=fake_reg)  # 不應該拋例外


if __name__ == "__main__":
    unittest.main()
