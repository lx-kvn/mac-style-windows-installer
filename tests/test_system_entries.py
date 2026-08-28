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
import shutil
import sys
import tempfile
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

    def test_returns_true_when_entry_already_absent(self):
        """F04：回傳值的語義是「這個函式結束之後，目標是否確實不存在」，
        不是「這次有沒有刪到東西」。兩個 hive 都找不到目標機碼時
        DeleteKey 會拋 FileNotFoundError——代表目標本來就不存在（使用者
        自己清過、或當初根本沒寫成功），對解除安裝而言結果跟「剛剛才刪掉」
        完全一樣，不該被 _perform_uninstall_steps() 收進 failures，變成
        使用者畫面上的假警告。
        """
        self.assertTrue(se.remove_registry_entry("NoSuchApp", registry=self.fake_reg))

    def test_returns_false_when_delete_fails_for_other_reason(self):
        """相對地，FileNotFoundError 以外的例外（權限不足、機碼底下還有
        子機碼）代表目標還在、而且移除失敗，這才是真正該回報的失敗。"""
        self.fake_reg.set_hklm(self._reg_path(), {})
        with mock.patch.object(self.fake_reg, "DeleteKey", side_effect=PermissionError("模擬權限不足")):
            self.assertFalse(se.remove_registry_entry("MyApp", registry=self.fake_reg))

    def test_returns_false_when_primary_hive_fails_even_if_other_hive_is_clean(self):
        """主 hive 的移除因權限不足失敗、另一個 hive 只是找不到目標時，整體
        仍算失敗——主 hive 的殘留項目確實還留在「已安裝的應用程式」清單裡，
        不能因為另一邊乾淨就回報成功。"""
        real_delete = self.fake_reg.DeleteKey

        def fake_delete(hive, subkey):
            if hive == self.fake_reg.HKEY_LOCAL_MACHINE:
                raise PermissionError("模擬權限不足")
            return real_delete(hive, subkey)

        with mock.patch.object(self.fake_reg, "DeleteKey", side_effect=fake_delete):
            self.assertFalse(se.remove_registry_entry("MyApp", registry=self.fake_reg))

    def test_falls_back_to_other_hive_when_entry_is_there_instead(self):
        """真實抓到的 bug：no_admin_install 從 manifest 讀出來的值可能跟
        舊版本實際安裝時用的模式對不上（例如手動編輯過 manifest、或
        manifest 遺失這個欄位時 uninstall.py 預設回退成 False）——這裡
        如果只查衍生出來的單一 hive，真正的登錄表項目在另一個 hive 時
        完全找不到，留下永久殘留在「已安裝的應用程式」清單裡。跟
        check_existing_install() 的雙 hive 探測是同一個道理，這裡也該
        兩邊都試。"""
        self.fake_reg.set_hkcu(self._reg_path(), {})
        self.assertTrue(se.remove_registry_entry("MyApp", no_admin_install=False, registry=self.fake_reg))
        self.assertIsNone(self.fake_reg.hkcu(self._reg_path()))


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

    def test_returns_true_when_shortcut_already_absent(self):
        """F04：捷徑檔案本來就不存在是完全正常的情境——使用者可能自己把
        捷徑刪掉了，或安裝當時捷徑建立就失敗過（`_create_shortcut()` 的
        失敗是可忽略的設計）。函式結束後目標確實不存在，回傳成功。"""
        fake_reg = FakeWinReg()
        with mock.patch("system_entries.os.path.exists", return_value=False):
            result = se.remove_shortcut("MyApp", registry=fake_reg)
        self.assertTrue(result)

    def test_returns_false_when_shortcut_removal_fails(self):
        fake_reg = FakeWinReg()
        with mock.patch("system_entries.os.path.exists", return_value=True), \
             mock.patch("system_entries.os.remove", side_effect=PermissionError("檔案被鎖住")):
            result = se.remove_shortcut("MyApp", registry=fake_reg)
        self.assertFalse(result)


class TestRemoveFromPath(unittest.TestCase):
    def _machine_path_key(self):
        return r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"

    def test_removes_only_matching_entry_from_machine_path(self):
        fake_reg = FakeWinReg()
        fake_reg.set_hklm(self._machine_path_key(), {"Path": "C:\\Windows;C:\\Apps\\MyApp;C:\\Other"})
        with mock.patch("system_entries.ctypes.windll.user32.SendMessageTimeoutW"):
            result = se.remove_from_path("C:\\Apps\\MyApp", registry=fake_reg)
        self.assertEqual(fake_reg.hklm(self._machine_path_key())["Path"], "C:\\Windows;C:\\Other")
        self.assertTrue(result)

    def test_removes_from_user_environment_when_no_admin(self):
        fake_reg = FakeWinReg()
        fake_reg.set_hkcu("Environment", {"Path": "C:\\Windows;C:\\Apps\\MyApp"})
        with mock.patch("system_entries.ctypes.windll.user32.SendMessageTimeoutW"):
            se.remove_from_path("C:\\Apps\\MyApp", no_admin_install=True, registry=fake_reg)
        self.assertEqual(fake_reg.hkcu("Environment")["Path"], "C:\\Windows")

    def test_returns_false_when_registry_access_fails(self):
        """F02：這個函式原本整段包在一個 try/except: pass 裡、不回傳任何值，
        呼叫端（uninstall.py 的 PATH 移除步驟）因此無條件記錄成功。改成回傳
        布林值，語義跟 remove_registry_entry()／remove_shortcut() 一致：權限
        不足讀不到 PATH 這個值，等於安裝路徑還留在 PATH 裡沒清掉，是失敗。
        """
        fake_reg = FakeWinReg()
        fake_reg.fail_on_substring = "Environment"
        self.assertFalse(se.remove_from_path("C:\\Apps\\MyApp", registry=fake_reg))  # 也不應該拋例外

    def test_returns_true_when_path_value_does_not_exist(self):
        """PATH 這個值根本不存在（或那個機碼不存在）時，安裝路徑當然也不在
        裡面——結束後目標確實不存在，依 F04 定下的語義算成功，不是失敗。"""
        fake_reg = FakeWinReg()
        fake_reg.set_hklm(self._machine_path_key(), {})
        with mock.patch("system_entries.ctypes.windll.user32.SendMessageTimeoutW"):
            self.assertTrue(se.remove_from_path("C:\\Apps\\MyApp", registry=fake_reg))

    def test_broadcast_failure_does_not_make_it_a_failure(self):
        """環境變數變更廣播（SendMessageTimeoutW）在既有修正中已定性為
        best-effort：登錄表已經寫成功、PATH 實際上已經清掉了，廣播沒送出只
        影響「已開啟的視窗何時看到新的 PATH」，不該回報成移除失敗。"""
        fake_reg = FakeWinReg()
        fake_reg.set_hklm(self._machine_path_key(), {"Path": "C:\\Windows;C:\\Apps\\MyApp"})
        with mock.patch(
            "system_entries.ctypes.windll.user32.SendMessageTimeoutW",
            side_effect=OSError("模擬廣播失敗"),
        ):
            self.assertTrue(se.remove_from_path("C:\\Apps\\MyApp", registry=fake_reg))
        self.assertEqual(fake_reg.hklm(self._machine_path_key())["Path"], "C:\\Windows")


class TestCleanupEmptyDirs(unittest.TestCase):
    """installer_core.py 的 rollback（清掉這次安裝已複製的檔案）跟
    uninstall.py 的解除安裝流程都要清掉刪檔後留下的空目錄，原本兩邊各自
    有一份逐位元組相同的實作，收斂到這裡（見 tests/test_uninstall.py 的
    對應測試已搬移過來）。"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_removes_dir_when_empty(self):
        se.cleanup_empty_dirs(self.tmp_dir)
        self.assertFalse(os.path.exists(self.tmp_dir))

    def test_keeps_dir_when_files_remain(self):
        with open(os.path.join(self.tmp_dir, "keep.txt"), "w") as f:
            f.write("still here")
        se.cleanup_empty_dirs(self.tmp_dir)
        self.assertTrue(os.path.exists(self.tmp_dir))

    def test_removes_nested_empty_subdirs_bottom_up(self):
        nested = os.path.join(self.tmp_dir, "a", "b")
        os.makedirs(nested)
        se.cleanup_empty_dirs(self.tmp_dir)
        self.assertFalse(os.path.exists(self.tmp_dir))

    def test_swallows_missing_root_dir(self):
        se.cleanup_empty_dirs(os.path.join(self.tmp_dir, "does-not-exist"))  # 不應該拋例外


class TestKillProcessByName(unittest.TestCase):
    """installer_core.py（安裝流程偵測到主程式正在執行，使用者選擇強制
    關閉）跟 uninstall.py（解除安裝時同樣的情境）原本各自有一份逐位元組
    相同的 taskkill 呼叫，收斂到這裡。"""

    def test_calls_taskkill_with_basename_and_returns_true_on_success(self):
        with mock.patch("system_entries.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            result = se.kill_process_by_name("sub\\app.exe")
        self.assertTrue(result)
        args, kwargs = mock_run.call_args
        self.assertEqual(args[0], ["taskkill", "/f", "/im", "app.exe"])

    def test_returns_false_when_taskkill_reports_failure(self):
        with mock.patch("system_entries.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 128  # 例如找不到目標程序
            self.assertFalse(se.kill_process_by_name("app.exe"))

    def test_returns_false_without_exe_name(self):
        self.assertFalse(se.kill_process_by_name(""))

    def test_swallows_exception(self):
        with mock.patch("system_entries.subprocess.run", side_effect=RuntimeError("模擬失敗")):
            self.assertFalse(se.kill_process_by_name("app.exe"))


if __name__ == "__main__":
    unittest.main()
