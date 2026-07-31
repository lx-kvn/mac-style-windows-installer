"""installer_core.py 裡其他核心邏輯的測試：檔案完整性驗證、磁碟空間檢查、
靜默安裝命令列參數解析、覆蓋安裝版本偵測、安裝失敗回滾、加入 PATH。

一律用假的 winreg（tests/_fakes.py）或暫存資料夾，不會動到這台機器的登錄表
或真實磁碟空間，可以直接執行。
"""
import os
import sys
import shutil
import tempfile
import unittest
import zlib
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import installer_core as ic
from _fakes import FakeWinReg


def make_installer_api(**overrides):
    api = ic.InstallerAPI()
    for k, v in overrides.items():
        setattr(api, k, v)
    return api


class TestFileChecksum(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_matches_manual_crc32(self):
        path = os.path.join(self.tmp_dir, "a.bin")
        content = b"hello world" * 1000
        with open(path, "wb") as f:
            f.write(content)
        self.assertEqual(ic._file_checksum(path), zlib.crc32(content))

    def test_different_content_gives_different_checksum(self):
        p1 = os.path.join(self.tmp_dir, "a.bin")
        p2 = os.path.join(self.tmp_dir, "b.bin")
        with open(p1, "wb") as f:
            f.write(b"content A")
        with open(p2, "wb") as f:
            f.write(b"content B")
        self.assertNotEqual(ic._file_checksum(p1), ic._file_checksum(p2))


class TestCliArgs(unittest.TestCase):
    def _parse(self, argv):
        with mock.patch.object(sys, "argv", ["Setup_App.exe"] + argv):
            return ic._parse_cli_args()

    def test_no_args_defaults(self):
        silent, install_dir, desktop = self._parse([])
        self.assertFalse(silent)
        self.assertIsNone(install_dir)
        self.assertTrue(desktop)

    def test_silent_flag_case_insensitive(self):
        silent, _, _ = self._parse(["/s"])
        self.assertTrue(silent)

    def test_dir_flag(self):
        _, install_dir, _ = self._parse(["/D=C:\\Custom Path"])
        self.assertEqual(install_dir, "C:\\Custom Path")

    def test_long_dir_flag(self):
        _, install_dir, _ = self._parse(["/DIR=D:\\Apps\\MyApp"])
        self.assertEqual(install_dir, "D:\\Apps\\MyApp")

    def test_no_desktop_shortcut_flag(self):
        _, _, desktop = self._parse(["/NODESKTOPSHORTCUT"])
        self.assertFalse(desktop)

    def test_combined_flags(self):
        silent, install_dir, desktop = self._parse(["/S", "/D=C:\\X", "/NODESKTOPSHORTCUT"])
        self.assertTrue(silent)
        self.assertEqual(install_dir, "C:\\X")
        self.assertFalse(desktop)


class TestCheckDiskSpace(unittest.TestCase):
    """_check_disk_space() 本身的邏輯（10% 緩衝、磁碟代號 fallback）已經抽到
    disk_space.py，由 tests/test_disk_space.py 直接測那個純函式。這裡只確認
    InstallerAPI._check_disk_space() 有把正確的參數轉交過去（真正的深模組
    seam 在 disk_space.check_disk_space()，這裡是薄呼叫端）。"""

    def test_delegates_to_disk_space_module_with_correct_args(self):
        api = make_installer_api(selected_path="C:\\FakeApp", default_path="C:\\Fallback")
        with mock.patch.object(api, "_required_size", return_value=1000), \
             mock.patch("installer_core.check_disk_space", return_value=(True, 9999, 1000)) as mock_check:
            result = api._check_disk_space()
        mock_check.assert_called_once_with(1000, "C:\\FakeApp", "C:\\Fallback")
        self.assertEqual(result, (True, 9999, 1000))


class TestCheckExistingInstall(unittest.TestCase):
    def setUp(self):
        self.fake_reg = FakeWinReg()
        self.patcher = mock.patch.dict(sys.modules, {"winreg": self.fake_reg})
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def _seed_existing(self, app_name, version, install_path="C:\\Apps\\Old"):
        reg_path = f"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{app_name}"
        self.fake_reg.set_hklm(reg_path, {
            "InstallLocation": install_path,
            "DisplayVersion": version,
        })

    def test_not_installed_before(self):
        api = make_installer_api(app_name="NeverInstalled", version="1.0.0")
        result = api.check_existing_install()
        self.assertEqual(result, {"exists": False})

    def test_upgrade_scenario_is_newer(self):
        self._seed_existing("MyApp", "1.0.0")
        api = make_installer_api(app_name="MyApp", version="2.0.0")
        result = api.check_existing_install()
        self.assertTrue(result["exists"])
        self.assertTrue(result["is_newer"])
        self.assertFalse(result["is_same"])
        self.assertFalse(result["is_older"])

    def test_downgrade_scenario_is_older(self):
        """本機已安裝的版本比這次要裝的新：這次要裝的版本比較舊。"""
        self._seed_existing("MyApp", "3.0.0")
        api = make_installer_api(app_name="MyApp", version="1.0.0")
        result = api.check_existing_install()
        self.assertFalse(result["is_newer"])
        self.assertFalse(result["is_same"])
        self.assertTrue(result["is_older"])

    def test_same_version_is_same(self):
        """相同版本重裝：不該被誤判成「有更新可以裝」，也不是「比較舊」。"""
        self._seed_existing("MyApp", "1.0.0")
        api = make_installer_api(app_name="MyApp", version="1.0.0")
        result = api.check_existing_install()
        self.assertFalse(result["is_newer"])
        self.assertTrue(result["is_same"])
        self.assertFalse(result["is_older"])


class TestAddToPathEnv(unittest.TestCase):
    def setUp(self):
        self.fake_reg = FakeWinReg()
        self.patcher = mock.patch.dict(sys.modules, {"winreg": self.fake_reg})
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def _path_key(self):
        return r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"

    def test_appends_when_no_existing_path(self):
        # Environment 這個機碼在真實 Windows 上一定存在，只是底下可能沒有 "Path"
        # 這個值（極端但合法的情況）；用空機碼模擬這個狀態。
        self.fake_reg.set_hklm(self._path_key(), {})
        api = make_installer_api(selected_path="C:\\Apps\\MyApp")
        with mock.patch("installer_core.ctypes.windll.user32.SendMessageTimeoutW"):
            api._add_to_path_env()
        self.assertEqual(self.fake_reg.hklm(self._path_key())["Path"], "C:\\Apps\\MyApp")

    def test_does_not_duplicate_existing_entry(self):
        self.fake_reg.set_hklm(self._path_key(), {"Path": "C:\\Windows;C:\\Apps\\MyApp"})
        api = make_installer_api(selected_path="C:\\Apps\\MyApp")
        with mock.patch("installer_core.ctypes.windll.user32.SendMessageTimeoutW"):
            api._add_to_path_env()
        self.assertEqual(self.fake_reg.hklm(self._path_key())["Path"], "C:\\Windows;C:\\Apps\\MyApp")

    def test_registry_failure_propagates(self):
        """修復驗證：PATH 寫入失敗不再被吞掉，讓安裝流程可以整個回滾。"""
        self.fake_reg.fail_on_substring = "Session Manager"
        api = make_installer_api(selected_path="C:\\Apps\\MyApp")
        with self.assertRaises(PermissionError):
            api._add_to_path_env()


class TestUpgradeBackup(unittest.TestCase):
    """_backup_existing_install() / _restore_upgrade_backup() /
    _discard_upgrade_backup() 三個方法本身：更新覆蓋安裝時，刪除舊版本前先
    備份，取消或安裝失敗時把備份搬回原位，成功時清掉備份。"""

    def setUp(self):
        self.old_install_dir = tempfile.mkdtemp()
        with open(os.path.join(self.old_install_dir, "app.exe"), "w") as f:
            f.write("舊版本")
        self.api = make_installer_api(app_name="MyApp")

    def tearDown(self):
        shutil.rmtree(self.old_install_dir, ignore_errors=True)
        if self.api._upgrade_backup_path and os.path.exists(self.api._upgrade_backup_path):
            shutil.rmtree(self.api._upgrade_backup_path, ignore_errors=True)

    def test_backup_copies_install_dir_to_temp(self):
        backup_path = self.api._backup_existing_install(self.old_install_dir)
        self.assertIsNotNone(backup_path)
        self.assertTrue(os.path.exists(os.path.join(backup_path, "app.exe")))

    def test_backup_returns_none_when_source_missing(self):
        backup_path = self.api._backup_existing_install("C:\\不存在的資料夾\\Nope")
        self.assertIsNone(backup_path)

    def test_restore_moves_backup_back_to_original_path(self):
        backup_path = self.api._backup_existing_install(self.old_install_dir)
        self.api._upgrade_backup_path = backup_path
        self.api._upgrade_backup_original_path = self.old_install_dir
        shutil.rmtree(self.old_install_dir)  # 模擬 uninstall.exe 已經把舊資料夾刪了

        self.api._restore_upgrade_backup()

        self.assertTrue(os.path.exists(os.path.join(self.old_install_dir, "app.exe")), "備份應該搬回原位")
        self.assertIsNone(self.api._upgrade_backup_path)
        self.assertIsNone(self.api._upgrade_backup_original_path)

    def test_restore_is_noop_when_no_backup_pending(self):
        self.api._upgrade_backup_path = None
        self.api._restore_upgrade_backup()  # 不應該拋例外
        self.assertIsNone(self.api._upgrade_backup_path)

    def test_discard_removes_backup_folder(self):
        backup_path = self.api._backup_existing_install(self.old_install_dir)
        self.api._upgrade_backup_path = backup_path
        self.api._upgrade_backup_original_path = self.old_install_dir

        self.api._discard_upgrade_backup()

        self.assertFalse(os.path.exists(backup_path))
        self.assertIsNone(self.api._upgrade_backup_path)


class TestRunUpgradeUninstall(unittest.TestCase):
    """run_upgrade_uninstall()：靜默呼叫舊版 uninstall.exe 前先備份，失敗時
    復原備份。這個方法現在只在 trigger_installation() 內部被呼叫（使用者拖曳
    圖示觸發安裝之後），不再由前端在按下確認彈窗當下直接呼叫。"""

    def setUp(self):
        self.fake_reg = FakeWinReg()
        self.patcher = mock.patch.dict(sys.modules, {"winreg": self.fake_reg})
        self.patcher.start()
        self.old_install_dir = tempfile.mkdtemp()
        with open(os.path.join(self.old_install_dir, "uninstall.exe"), "w") as f:
            f.write("fake")
        reg_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\MyApp"
        self.fake_reg.set_hklm(reg_path, {
            "InstallLocation": self.old_install_dir,
            "DisplayVersion": "1.0.0",
        })
        self.api = make_installer_api(app_name="MyApp", version="2.0.0")

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.old_install_dir, ignore_errors=True)
        if self.api._upgrade_backup_path and os.path.exists(self.api._upgrade_backup_path):
            shutil.rmtree(self.api._upgrade_backup_path, ignore_errors=True)

    def test_backs_up_before_calling_uninstall_exe(self):
        call_order = []

        def fake_backup(install_path):
            call_order.append("backup")
            return "C:\\FakeBackup"

        def fake_subprocess_run(*args, **kwargs):
            call_order.append("uninstall_exe")

        with mock.patch.object(self.api, "_backup_existing_install", side_effect=fake_backup), \
             mock.patch("installer_core.subprocess.run", side_effect=fake_subprocess_run), \
             mock.patch("installer_core.time.sleep"):
            result = self.api.run_upgrade_uninstall()

        self.assertEqual(result["status"], "success")
        self.assertEqual(call_order, ["backup", "uninstall_exe"], "必須先備份，才能靜默移除舊版本")

    def test_restores_backup_when_uninstall_exe_fails(self):
        with open(os.path.join(self.old_install_dir, "extra.txt"), "w") as f:
            f.write("舊資料")

        with mock.patch("installer_core.subprocess.run", side_effect=RuntimeError("模擬失敗")):
            result = self.api.run_upgrade_uninstall()

        self.assertEqual(result["status"], "error")
        self.assertTrue(os.path.exists(os.path.join(self.old_install_dir, "extra.txt")), "備份應該被復原回原位")
        self.assertIsNone(self.api._upgrade_backup_path)


class TestCloseWindowRestoresPendingBackup(unittest.TestCase):
    """使用者在更新覆蓋安裝流程跑到一半（舊版本已刪、新版本還沒裝完）就關
    視窗，等同取消安裝，備份的舊版本檔案要被復原。"""

    def test_restores_backup_before_destroying_window(self):
        api = make_installer_api()
        api._upgrade_backup_path = "C:\\Fake\\Backup"
        with mock.patch.object(api, "_restore_upgrade_backup") as mock_restore, \
             mock.patch("installer_core.window", create=True) as mock_window:
            api.close_window()
        mock_restore.assert_called_once()
        mock_window.destroy.assert_called_once()

    def test_no_restore_when_no_pending_backup(self):
        api = make_installer_api()
        api._upgrade_backup_path = None
        with mock.patch.object(api, "_restore_upgrade_backup") as mock_restore, \
             mock.patch("installer_core.window", create=True) as mock_window:
            api.close_window()
        mock_restore.assert_not_called()
        mock_window.destroy.assert_called_once()


class TestTriggerInstallationUpgradeFlow(unittest.TestCase):
    """驗證『刪除舊版本』延後到 trigger_installation() 內部才執行：使用者拖曳
    圖示、真正觸發安裝之後才會動舊版本，不是彈窗一按確認鈕就刪（見
    ui/index.html 的 confirmUpgrade() 現在不再呼叫 run_upgrade_uninstall()）。"""

    def setUp(self):
        self.resource_dir = tempfile.mkdtemp()
        self.app_contents_dir = os.path.join(self.resource_dir, "app_contents")
        os.makedirs(self.app_contents_dir)
        with open(os.path.join(self.app_contents_dir, "app.exe"), "wb") as f:
            f.write(b"fake-app")
        self.install_dir = tempfile.mkdtemp()
        shutil.rmtree(self.install_dir)  # trigger_installation 應該自己建立這個資料夾

    def tearDown(self):
        shutil.rmtree(self.resource_dir, ignore_errors=True)
        shutil.rmtree(self.install_dir, ignore_errors=True)

    def _resource_path(self, relative_path):
        return os.path.join(self.resource_dir, relative_path)

    def _make_api(self):
        return make_installer_api(
            app_name="MyApp", main_exe="", selected_path=self.install_dir,
            file_associations=[], add_to_path=False,
        )

    def test_calls_run_upgrade_uninstall_before_copying_when_existing_install_detected(self):
        api = self._make_api()
        call_order = []
        real_copy2 = shutil.copy2

        def recording_copy2(src, dst):
            call_order.append("copy")
            return real_copy2(src, dst)

        def fake_run_upgrade_uninstall():
            call_order.append("upgrade")
            return {"status": "success"}

        with mock.patch("installer_core.get_resource_path", side_effect=self._resource_path), \
             mock.patch.object(api, "check_existing_install", return_value={"exists": True}), \
             mock.patch.object(api, "run_upgrade_uninstall", side_effect=fake_run_upgrade_uninstall), \
             mock.patch.object(api, "_check_disk_space", return_value=(True, 10 ** 9, 1)), \
             mock.patch.object(api, "_register_uninstall_entry"), \
             mock.patch.object(api, "_create_shortcut", return_value=True), \
             mock.patch.object(api, "_discard_upgrade_backup") as mock_discard, \
             mock.patch("installer_core.shutil.copy2", side_effect=recording_copy2):
            result = api.trigger_installation(create_desktop_shortcut=False)

        self.assertEqual(result["status"], "success")
        self.assertEqual(call_order[0], "upgrade", "移除舊版本必須發生在複製檔案之前")
        self.assertIn("copy", call_order)
        mock_discard.assert_called_once()

    def test_upgrade_uninstall_failure_short_circuits_before_copying(self):
        api = self._make_api()

        with mock.patch("installer_core.get_resource_path", side_effect=self._resource_path), \
             mock.patch.object(api, "check_existing_install", return_value={"exists": True}), \
             mock.patch.object(
                 api, "run_upgrade_uninstall",
                 return_value={"status": "error", "message": "移除舊版本失敗: 模擬錯誤"},
             ), \
             mock.patch.object(api, "_check_disk_space", return_value=(True, 10 ** 9, 1)):
            result = api.trigger_installation(create_desktop_shortcut=False)

        self.assertEqual(result["status"], "error")
        self.assertIn("移除舊版本失敗", result["message"])
        self.assertFalse(
            os.path.exists(self.install_dir),
            "移除舊版本失敗就該直接回傳錯誤，不該繼續建立安裝目錄、複製檔案",
        )


class TestRollback(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_removes_only_copied_files_and_prunes_empty_dirs(self):
        os.makedirs(os.path.join(self.tmp_dir, "sub"))
        with open(os.path.join(self.tmp_dir, "sub", "a.txt"), "w") as f:
            f.write("copied")
        with open(os.path.join(self.tmp_dir, "b.txt"), "w") as f:
            f.write("copied")
        with open(os.path.join(self.tmp_dir, "keep_me.txt"), "w") as f:
            f.write("使用者自己放的東西，不在複製清單內，不該被回滾清掉")

        api = make_installer_api(selected_path=self.tmp_dir)
        api._rollback(["sub/a.txt", "b.txt"], log=None)

        self.assertFalse(os.path.exists(os.path.join(self.tmp_dir, "sub", "a.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp_dir, "b.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp_dir, "sub")), "清空的子目錄應該被清掉")
        self.assertTrue(os.path.exists(os.path.join(self.tmp_dir, "keep_me.txt")), "不在複製清單內的檔案不該被回滾動到")

    def test_removes_whole_dir_when_nothing_left(self):
        with open(os.path.join(self.tmp_dir, "only.txt"), "w") as f:
            f.write("copied")
        api = make_installer_api(selected_path=self.tmp_dir)
        api._rollback(["only.txt"], log=None)
        self.assertFalse(os.path.exists(self.tmp_dir), "回滾後資料夾裡如果真的空了，應該連資料夾一起刪掉")


if __name__ == "__main__":
    unittest.main(verbosity=2)
