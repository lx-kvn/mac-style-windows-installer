"""install_scope.py 的測試。

抽出來的深模組：no_admin_install 這個布林值衍生出的 hive/目錄判斷，
installer_core.py 跟 uninstall.py 原本各自獨立重新推導過一次，這裡
收成一個地方，兩邊共用。這裡只測 InstallScope 本身跟 local_appdata_root()，
不需要建構 InstallerAPI()。
"""
import os
import sys
import winreg
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import install_scope


class TestLocalAppdataRoot(unittest.TestCase):
    def test_uses_localappdata_env_var(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "C:\\Users\\Tester\\AppData\\Local"}):
            result = install_scope.local_appdata_root("MyApp")
        self.assertEqual(result, "C:\\Users\\Tester\\AppData\\Local\\Programs\\MyApp")

    def test_falls_back_when_localappdata_env_var_missing(self):
        env = dict(os.environ)
        env.pop("LOCALAPPDATA", None)
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch("os.path.expanduser", return_value="C:\\Users\\Tester"):
            result = install_scope.local_appdata_root("MyApp")
        self.assertEqual(result, "C:\\Users\\Tester\\AppData\\Local\\Programs\\MyApp")


class TestInstallScopeAdminMode(unittest.TestCase):
    """no_admin_install=False：維持原本「需要系統管理員權限」的機器層級行為。"""

    def setUp(self):
        self.scope = install_scope.InstallScope(no_admin_install=False)

    def test_registry_hive_is_local_machine(self):
        self.assertEqual(self.scope.registry_hive, winreg.HKEY_LOCAL_MACHINE)

    def test_path_env_targets_machine_environment(self):
        hive, sub_key = self.scope.path_env_hive_and_key
        self.assertEqual(hive, winreg.HKEY_LOCAL_MACHINE)
        self.assertEqual(sub_key, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")

    def test_shortcut_dir_desktop_is_public_desktop(self):
        self.assertEqual(self.scope.shortcut_dir(desktop=True), "C:\\Users\\Public\\Desktop")

    def test_shortcut_dir_start_menu_is_programdata(self):
        with mock.patch.dict(os.environ, {"ProgramData": "C:\\ProgramData"}):
            result = self.scope.shortcut_dir(desktop=False)
        self.assertEqual(result, "C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs")

    def test_default_install_root_is_program_files(self):
        with mock.patch.dict(os.environ, {"ProgramFiles": "C:\\Program Files"}):
            result = self.scope.default_install_root("MyApp", "MyAppFolder")
        self.assertEqual(result, "C:\\Program Files\\MyAppFolder")

    def test_default_install_root_falls_back_to_app_name_when_folder_name_empty(self):
        with mock.patch.dict(os.environ, {"ProgramFiles": "C:\\Program Files"}):
            result = self.scope.default_install_root("MyApp", "")
        self.assertEqual(result, "C:\\Program Files\\MyApp")


class TestInstallScopeNoAdminMode(unittest.TestCase):
    """no_admin_install=True：整個安裝流程（含解除安裝）完全不要求提權，
    全部改用使用者自己本來就有寫入權限的位置。"""

    def setUp(self):
        self.scope = install_scope.InstallScope(no_admin_install=True)

    def test_registry_hive_is_current_user(self):
        self.assertEqual(self.scope.registry_hive, winreg.HKEY_CURRENT_USER)

    def test_path_env_targets_user_environment(self):
        hive, sub_key = self.scope.path_env_hive_and_key
        self.assertEqual(hive, winreg.HKEY_CURRENT_USER)
        self.assertEqual(sub_key, "Environment")

    def test_shortcut_dir_desktop_is_users_own_desktop(self):
        with mock.patch("os.path.expanduser", return_value="C:\\Users\\Tester"):
            result = self.scope.shortcut_dir(desktop=True)
        self.assertEqual(result, "C:\\Users\\Tester\\Desktop")

    def test_shortcut_dir_start_menu_is_users_own_appdata(self):
        with mock.patch.dict(os.environ, {"APPDATA": "C:\\Users\\Tester\\AppData\\Roaming"}):
            result = self.scope.shortcut_dir(desktop=False)
        self.assertEqual(result, "C:\\Users\\Tester\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs")

    def test_default_install_root_is_localappdata_programs(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "C:\\Users\\Tester\\AppData\\Local"}):
            result = self.scope.default_install_root("MyApp", "MyAppFolder")
        self.assertEqual(result, "C:\\Users\\Tester\\AppData\\Local\\Programs\\MyAppFolder")


class _FakeWinregModule:
    """最小的假 winreg 模組替身，只需要 InstallScope 用到的兩個常數，
    刻意用跟真正的 winreg 不同的哨兵值，才能確認 InstallScope 真的是在用
    傳進去的這個 registry，不是自己另外 import 了一份真的 winreg。"""
    HKEY_LOCAL_MACHINE = "FAKE_HKLM"
    HKEY_CURRENT_USER = "FAKE_HKCU"


class TestInstallScopeRegistrySeam(unittest.TestCase):
    """registry 參數：跟 file_assoc.py 的 registry seam 同一個道理——
    uninstall.py 是在檔案最上面 import winreg 一次、測試用
    mock.patch.object(un, "winreg", fake) 直接換掉模組屬性，這種情境下
    InstallScope 不能自己另外 import 一份不會被那種 patch 方式影響到的
    「真的」winreg，必須用呼叫端傳進來的這個。"""

    def test_uses_injected_registry_instead_of_real_winreg(self):
        scope = install_scope.InstallScope(no_admin_install=True, registry=_FakeWinregModule)
        self.assertEqual(scope.registry_hive, "FAKE_HKCU")
        hive, _ = scope.path_env_hive_and_key
        self.assertEqual(hive, "FAKE_HKCU")

    def test_defaults_to_real_winreg_when_registry_not_given(self):
        scope = install_scope.InstallScope(no_admin_install=False)
        self.assertEqual(scope.registry_hive, winreg.HKEY_LOCAL_MACHINE)


if __name__ == "__main__":
    unittest.main()
