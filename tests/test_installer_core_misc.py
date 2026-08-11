"""installer_core.py 裡其他核心邏輯的測試：檔案完整性驗證、磁碟空間檢查、
靜默安裝命令列參數解析、覆蓋安裝版本偵測、安裝失敗回滾、加入 PATH。

一律用假的 winreg（tests/_fakes.py）或暫存資料夾，不會動到這台機器的登錄表
或真實磁碟空間，可以直接執行。
"""
import os
import sys
import json
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
        silent, install_dir, desktop, log_path = self._parse([])
        self.assertFalse(silent)
        self.assertIsNone(install_dir)
        self.assertTrue(desktop)
        self.assertIsNone(log_path)

    def test_silent_flag_case_insensitive(self):
        silent, _, _, _ = self._parse(["/s"])
        self.assertTrue(silent)

    def test_dir_flag(self):
        _, install_dir, _, _ = self._parse(["/D=C:\\Custom Path"])
        self.assertEqual(install_dir, "C:\\Custom Path")

    def test_long_dir_flag(self):
        _, install_dir, _, _ = self._parse(["/DIR=D:\\Apps\\MyApp"])
        self.assertEqual(install_dir, "D:\\Apps\\MyApp")

    def test_no_desktop_shortcut_flag(self):
        _, _, desktop, _ = self._parse(["/NODESKTOPSHORTCUT"])
        self.assertFalse(desktop)

    def test_log_flag(self):
        _, _, _, log_path = self._parse(["/LOG=D:\\logs\\install.txt"])
        self.assertEqual(log_path, "D:\\logs\\install.txt")

    def test_combined_flags(self):
        silent, install_dir, desktop, log_path = self._parse(
            ["/S", "/D=C:\\X", "/NODESKTOPSHORTCUT", "/LOG=C:\\X\\log.txt"]
        )
        self.assertTrue(silent)
        self.assertEqual(install_dir, "C:\\X")
        self.assertFalse(desktop)
        self.assertEqual(log_path, "C:\\X\\log.txt")


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

    def test_finds_existing_install_in_hklm_even_when_current_settings_use_hkcu(self):
        """真實抓到的 bug：舊版本用預設設定（需要管理員權限）裝在
        Program Files、登錄表寫在 HKLM，這次改用 no_admin_install=True
        重新打包，只查 HKCU 會完全找不到舊版本、誤判成「沒裝過」，跳過
        「是否要更新」的提示。改成兩邊都查，找到的話額外回報是在哪個
        hive 找到的，供 run_upgrade_uninstall() 判斷要不要跨 UAC 呼叫。"""
        self._seed_existing("MyApp", "1.0.0")  # 寫在 HKLM
        api = make_installer_api(app_name="MyApp", version="2.0.0", no_admin_install=True)
        result = api.check_existing_install()
        self.assertTrue(result["exists"])
        self.assertEqual(result["hive"], "HKLM")

    def test_finds_existing_install_in_hkcu_even_when_current_settings_use_hklm(self):
        reg_path = "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\MyApp"
        self.fake_reg.set_hkcu(reg_path, {"InstallLocation": "C:\\Users\\Tester\\AppData\\Local\\Programs\\MyApp", "DisplayVersion": "1.0.0"})
        api = make_installer_api(app_name="MyApp", version="2.0.0", no_admin_install=False)
        result = api.check_existing_install()
        self.assertTrue(result["exists"])
        self.assertEqual(result["hive"], "HKCU")

    def test_hive_matches_current_settings_when_only_that_hive_has_a_record(self):
        self._seed_existing("MyApp", "1.0.0")  # 寫在 HKLM
        api = make_installer_api(app_name="MyApp", version="2.0.0", no_admin_install=False)
        result = api.check_existing_install()
        self.assertTrue(result["exists"])
        self.assertEqual(result["hive"], "HKLM")


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

    def test_adds_target_exe_subdirectory_when_path_target_exe_specified(self):
        """backlog #1：path_target_exe 指定一支跟主程式分開的 CLI 工具時，
        只把它所在的子目錄加進 PATH，不是整個安裝目錄。"""
        self.fake_reg.set_hklm(self._path_key(), {})
        api = make_installer_api(selected_path="C:\\Apps\\MyApp", path_target_exe="tools\\cli.exe")
        with mock.patch("installer_core.ctypes.windll.user32.SendMessageTimeoutW"):
            added_dir = api._add_to_path_env()
        self.assertEqual(added_dir, "C:\\Apps\\MyApp\\tools")
        self.assertEqual(self.fake_reg.hklm(self._path_key())["Path"], "C:\\Apps\\MyApp\\tools")

    def test_falls_back_to_install_root_when_target_exe_in_root(self):
        self.fake_reg.set_hklm(self._path_key(), {})
        api = make_installer_api(selected_path="C:\\Apps\\MyApp", path_target_exe="main.exe")
        with mock.patch("installer_core.ctypes.windll.user32.SendMessageTimeoutW"):
            added_dir = api._add_to_path_env()
        self.assertEqual(added_dir, "C:\\Apps\\MyApp")

    def test_falls_back_to_install_root_when_no_path_target_exe(self):
        self.fake_reg.set_hklm(self._path_key(), {})
        api = make_installer_api(selected_path="C:\\Apps\\MyApp", path_target_exe="")
        with mock.patch("installer_core.ctypes.windll.user32.SendMessageTimeoutW"):
            added_dir = api._add_to_path_env()
        self.assertEqual(added_dir, "C:\\Apps\\MyApp")


class TestLocalAppdataFiles(unittest.TestCase):
    """local_appdata_files：打包時指定某幾支檔案改裝到
    %LOCALAPPDATA%\\Programs\\<folder_name>，不需要系統管理員權限就能寫入，
    典型用途是跟主程式分開的 CLI 工具。"""

    def setUp(self):
        self.env_patcher = mock.patch.dict(os.environ, {"LOCALAPPDATA": "C:\\Users\\Tester\\AppData\\Local"})
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()

    def test_local_appdata_root_uses_folder_name(self):
        api = make_installer_api(folder_name="MyApp")
        self.assertEqual(
            api._local_appdata_root(), "C:\\Users\\Tester\\AppData\\Local\\Programs\\MyApp",
        )

    def test_local_appdata_root_falls_back_to_app_name_without_folder_name(self):
        api = make_installer_api(folder_name="", app_name="MyApp")
        self.assertEqual(
            api._local_appdata_root(), "C:\\Users\\Tester\\AppData\\Local\\Programs\\MyApp",
        )

    def test_resolve_installed_path_routes_listed_file_to_local_appdata(self):
        api = make_installer_api(
            selected_path="C:\\Program Files\\MyApp", folder_name="MyApp",
            local_appdata_files=["cli.exe"],
        )
        self.assertEqual(
            api._resolve_installed_path("cli.exe"),
            "C:\\Users\\Tester\\AppData\\Local\\Programs\\MyApp\\cli.exe",
        )

    def test_resolve_installed_path_keeps_unlisted_file_in_main_install_dir(self):
        api = make_installer_api(
            selected_path="C:\\Program Files\\MyApp", folder_name="MyApp",
            local_appdata_files=["cli.exe"],
        )
        self.assertEqual(
            api._resolve_installed_path("gui.exe"), "C:\\Program Files\\MyApp\\gui.exe",
        )

    def test_resolve_installed_path_matches_regardless_of_slash_direction(self):
        api = make_installer_api(
            selected_path="C:\\Program Files\\MyApp", folder_name="MyApp",
            local_appdata_files=["tools/cli.exe"],
        )
        self.assertEqual(
            api._resolve_installed_path("tools\\cli.exe"),
            "C:\\Users\\Tester\\AppData\\Local\\Programs\\MyApp\\tools\\cli.exe",
        )

    def test_path_target_dir_points_at_local_appdata_when_target_exe_listed(self):
        api = make_installer_api(
            selected_path="C:\\Program Files\\MyApp", folder_name="MyApp",
            path_target_exe="cli.exe", local_appdata_files=["cli.exe"],
        )
        self.assertEqual(
            api._path_target_dir(), "C:\\Users\\Tester\\AppData\\Local\\Programs\\MyApp",
        )


class TestRollbackLocalAppdataFiles(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.alt_dir = tempfile.mkdtemp()
        self.env_patcher = mock.patch.dict(os.environ, {"LOCALAPPDATA": os.path.dirname(self.alt_dir)})
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        shutil.rmtree(self.alt_dir, ignore_errors=True)

    def test_rollback_removes_local_appdata_copies_and_prunes_empty_alt_dir(self):
        folder_name = os.path.basename(self.alt_dir)
        alt_root = os.path.join(os.path.dirname(self.alt_dir), "Programs", folder_name)
        os.makedirs(alt_root)
        with open(os.path.join(alt_root, "cli.exe"), "w") as f:
            f.write("copied")
        with open(os.path.join(self.tmp_dir, "gui.exe"), "w") as f:
            f.write("copied")

        api = make_installer_api(
            selected_path=self.tmp_dir, folder_name=folder_name, local_appdata_files=["cli.exe"],
        )
        api._rollback(["gui.exe", "cli.exe"], log=None)

        self.assertFalse(os.path.exists(os.path.join(alt_root, "cli.exe")))
        self.assertFalse(os.path.exists(alt_root), "回滾後空了的別位目錄應該被清掉")
        self.assertFalse(os.path.exists(os.path.join(self.tmp_dir, "gui.exe")))


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

    def test_backup_uses_real_temp_dir_even_when_temp_env_var_is_empty_string(self):
        """真實抓到的 bug：os.environ.get("TEMP", ".") 只有在 TEMP 這個環境變數
        整個不存在時才會用預設值，存在但是空字串（實測發生在某些提權執行的
        情境下）會直接算出相對路徑，落點變成安裝程式當下的工作目錄——如果
        使用者剛好把新安裝檔放在舊安裝目錄本身執行更新，備份會被建到
        install_path 底下，變成對自己複製。改用 tempfile.gettempdir() 之後
        不該再有這個問題，即使 TEMP 環境變數是空字串也一樣。
        """
        with mock.patch.dict(os.environ, {"TEMP": ""}):
            backup_path = self.api._backup_existing_install(self.old_install_dir)
        self.assertIsNotNone(backup_path)
        self.assertNotEqual(os.path.abspath(backup_path), os.path.abspath(self.old_install_dir))
        self.assertFalse(
            os.path.abspath(backup_path).startswith(os.path.abspath(self.old_install_dir) + os.sep),
            "備份資料夾不該落在被備份的來源資料夾底下",
        )
        self.assertTrue(os.path.exists(os.path.join(backup_path, "app.exe")))

    def test_backup_refuses_when_computed_path_would_nest_inside_source(self):
        """就算 tempfile.gettempdir() 本身算出詭異結果，也要有第二道保險：
        算出來的備份路徑如果還是落在 install_path 底下，直接拒絕備份，
        不要冒險對自己複製（shutil.copytree 對這種情況沒有防呆機制，
        會邊複製邊把剛建立的子資料夾也當成來源的一部分，越複製越亂）。
        """
        with mock.patch("installer_core.tempfile.gettempdir", return_value=self.old_install_dir):
            backup_path = self.api._backup_existing_install(self.old_install_dir)
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
        # selected_path 特意指向暫存資料夾（可寫入、一定成功建立），不是預設的
        # Program Files 路徑：_wait_for_selected_path_writable() 遇到真的沒有
        # 寫入權限的路徑會重試到逾時，不能讓測試環境本身的權限狀態影響測試。
        self.new_install_dir = tempfile.mkdtemp()
        shutil.rmtree(self.new_install_dir)  # 讓 os.makedirs() 有東西可以建立
        self.api = make_installer_api(app_name="MyApp", version="2.0.0", selected_path=self.new_install_dir)
        # 這個 class 底下的測試關心的是備份/復原、命令列參數傳遞這些跟提權
        # 無關的行為，一律視為「目前這個行程本身已經是提權的」，走既有的
        # subprocess.run() 路徑——是否要跨 UAC 呼叫的判斷邏輯本身另外在
        # TestRunUpgradeUninstallElevation 測。
        self.elevated_patcher = mock.patch.object(self.api, "_is_current_process_elevated", return_value=True)
        self.elevated_patcher.start()

    def tearDown(self):
        self.elevated_patcher.stop()
        self.patcher.stop()
        shutil.rmtree(self.old_install_dir, ignore_errors=True)
        shutil.rmtree(self.new_install_dir, ignore_errors=True)
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
             mock.patch("installer_core.subprocess.run", side_effect=fake_subprocess_run):
            result = self.api.run_upgrade_uninstall()

        self.assertEqual(result["status"], "success")
        self.assertEqual(call_order, ["backup", "uninstall_exe"], "必須先備份，才能靜默移除舊版本")
        self.assertTrue(os.path.exists(self.new_install_dir), "確認目標路徑真的等到可以建立")

    def test_restores_backup_when_uninstall_exe_fails(self):
        with open(os.path.join(self.old_install_dir, "extra.txt"), "w") as f:
            f.write("舊資料")

        with mock.patch("installer_core.subprocess.run", side_effect=RuntimeError("模擬失敗")):
            result = self.api.run_upgrade_uninstall()

        self.assertEqual(result["status"], "error")
        self.assertTrue(os.path.exists(os.path.join(self.old_install_dir, "extra.txt")), "備份應該被復原回原位")
        self.assertIsNone(self.api._upgrade_backup_path)

    def test_passes_restart_explorer_flag_to_old_uninstall_exe_when_enabled(self):
        """真實抓到的 bug：這裡呼叫的是舊版本的 uninstall.exe，它是否關閉檔案
        總管原本只看它自己那份（可能過期的）install_manifest.json，跟使用者
        這次重新打包的新設定是兩回事，導致行為時好時壞。修正後這次的設定要
        透過命令列參數明確傳給舊版 uninstall.exe，覆蓋掉它自己的 manifest。"""
        self.api.restart_explorer_on_update = True
        captured_cmd = {}

        def fake_subprocess_run(cmd, **kwargs):
            captured_cmd["cmd"] = cmd

        with mock.patch("installer_core.subprocess.run", side_effect=fake_subprocess_run):
            self.api.run_upgrade_uninstall()

        self.assertIn("--restart-explorer", captured_cmd["cmd"])

    def test_does_not_pass_restart_explorer_flag_when_disabled(self):
        self.api.restart_explorer_on_update = False
        captured_cmd = {}

        def fake_subprocess_run(cmd, **kwargs):
            captured_cmd["cmd"] = cmd

        with mock.patch("installer_core.subprocess.run", side_effect=fake_subprocess_run):
            self.api.run_upgrade_uninstall()

        self.assertNotIn("--restart-explorer", captured_cmd["cmd"])

    def test_always_passes_upgrade_flag_to_old_uninstall_exe(self):
        """真實抓到的 bug：舊版 uninstall.exe 尾端的自我刪除是背景、不等待的
        cmd.exe（先延遲約 1 秒才真正 del/rmdir）。這裡用 subprocess.run()
        同步呼叫完就繼續複製新版本檔案，如果複製時間跨過那個延遲視窗，
        背景指令觸發時會把整個資料夾（含新複製好的檔案）一起砍掉，導致
        「安裝回報成功但檔案沒有複製完整」。修正後一律帶 --upgrade 旗標，
        讓舊版 uninstall.exe 完全不排那段背景指令。"""
        captured_cmd = {}

        def fake_subprocess_run(cmd, **kwargs):
            captured_cmd["cmd"] = cmd

        with mock.patch("installer_core.subprocess.run", side_effect=fake_subprocess_run):
            self.api.run_upgrade_uninstall()

        self.assertIn("--upgrade", captured_cmd["cmd"])


class TestRunUpgradeUninstallElevation(unittest.TestCase):
    """真實抓到的問題：舊版本如果是用需要管理員權限的設定裝的（登錄表寫在
    HKLM），但這次新安裝檔是免權限（no_admin_install=True）執行，直接用
    subprocess.run() 呼叫舊版 uninstall.exe 不會跳 UAC——Windows 的 manifest
    自動提權只有透過 ShellExecute 這條路徑才會生效，subprocess.run() 底層
    是 CreateProcess，會用目前（未提權）的權杖把子行程跑起來，導致寫入
    Program Files/刪除 HKLM 機碼時默默失敗卻不拋例外。改成偵測到這種情境
    時改用 _run_uninstall_exe_elevated()（ShellExecuteExW + "runas"）。"""

    def setUp(self):
        self.fake_reg = FakeWinReg()
        self.patcher = mock.patch.dict(sys.modules, {"winreg": self.fake_reg})
        self.patcher.start()
        self.old_install_dir = tempfile.mkdtemp()
        with open(os.path.join(self.old_install_dir, "uninstall.exe"), "w") as f:
            f.write("fake")
        self.new_install_dir = tempfile.mkdtemp()
        shutil.rmtree(self.new_install_dir)
        self.api = make_installer_api(app_name="MyApp", version="2.0.0", selected_path=self.new_install_dir)

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.old_install_dir, ignore_errors=True)
        shutil.rmtree(self.new_install_dir, ignore_errors=True)
        if self.api._upgrade_backup_path and os.path.exists(self.api._upgrade_backup_path):
            shutil.rmtree(self.api._upgrade_backup_path, ignore_errors=True)

    def _seed_hklm(self):
        self.fake_reg.set_hklm(r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\MyApp", {
            "InstallLocation": self.old_install_dir, "DisplayVersion": "1.0.0",
        })

    def _seed_hkcu(self):
        self.fake_reg.set_hkcu(r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\MyApp", {
            "InstallLocation": self.old_install_dir, "DisplayVersion": "1.0.0",
        })

    def test_uses_elevated_call_when_hive_is_hklm_and_current_process_not_elevated(self):
        self._seed_hklm()
        with mock.patch.object(self.api, "_is_current_process_elevated", return_value=False), \
             mock.patch.object(self.api, "_run_uninstall_exe_elevated") as mock_elevated, \
             mock.patch("installer_core.subprocess.run") as mock_run:
            result = self.api.run_upgrade_uninstall()
        self.assertEqual(result["status"], "success")
        mock_elevated.assert_called_once()
        mock_run.assert_not_called()

    def test_uses_subprocess_run_when_hive_is_hkcu(self):
        self._seed_hkcu()
        self.api.no_admin_install = True
        with mock.patch.object(self.api, "_is_current_process_elevated", return_value=False), \
             mock.patch.object(self.api, "_run_uninstall_exe_elevated") as mock_elevated, \
             mock.patch("installer_core.subprocess.run") as mock_run:
            self.api.run_upgrade_uninstall()
        mock_run.assert_called_once()
        mock_elevated.assert_not_called()

    def test_uses_subprocess_run_when_current_process_already_elevated(self):
        self._seed_hklm()
        with mock.patch.object(self.api, "_is_current_process_elevated", return_value=True), \
             mock.patch.object(self.api, "_run_uninstall_exe_elevated") as mock_elevated, \
             mock.patch("installer_core.subprocess.run") as mock_run:
            self.api.run_upgrade_uninstall()
        mock_run.assert_called_once()
        mock_elevated.assert_not_called()


class TestRunUninstallExeElevated(unittest.TestCase):
    """_run_uninstall_exe_elevated()：透過 ShellExecuteExW + "runas" 動詞
    啟動舊版 uninstall.exe 並等待完成，取代 subprocess.run() 在需要跨 UAC
    情境下的角色。"""

    def setUp(self):
        self.api = make_installer_api()

    def test_raises_when_shell_execute_fails(self):
        """ShellExecuteExW 回傳 0（失敗，例如使用者在 UAC 提示按下取消）
        要讓呼叫端看到明確的例外。"""
        with mock.patch("installer_core.ctypes.windll.shell32.ShellExecuteExW", return_value=0):
            with self.assertRaises(Exception):
                self.api._run_uninstall_exe_elevated("C:\\App\\uninstall.exe", ["--silent"])

    def test_waits_for_process_and_closes_handle(self):
        WAIT_OBJECT_0 = 0

        def fake_shell_execute(sei_ptr):
            sei_ptr.contents.hProcess = 12345
            return 1

        with mock.patch("installer_core.ctypes.windll.shell32.ShellExecuteExW", side_effect=fake_shell_execute), \
             mock.patch("installer_core.ctypes.windll.kernel32.WaitForSingleObject", return_value=WAIT_OBJECT_0) as mock_wait, \
             mock.patch("installer_core.ctypes.windll.kernel32.CloseHandle") as mock_close:
            self.api._run_uninstall_exe_elevated("C:\\App\\uninstall.exe", ["--silent"], timeout_ms=5000)

        mock_wait.assert_called_once()
        self.assertEqual(mock_wait.call_args.args[0], 12345)
        mock_close.assert_called_once_with(12345)

    def test_raises_on_timeout(self):
        WAIT_TIMEOUT = 0x102

        def fake_shell_execute(sei_ptr):
            sei_ptr.contents.hProcess = 12345
            return 1

        with mock.patch("installer_core.ctypes.windll.shell32.ShellExecuteExW", side_effect=fake_shell_execute), \
             mock.patch("installer_core.ctypes.windll.kernel32.WaitForSingleObject", return_value=WAIT_TIMEOUT), \
             mock.patch("installer_core.ctypes.windll.kernel32.CloseHandle") as mock_close:
            with self.assertRaises(Exception):
                self.api._run_uninstall_exe_elevated("C:\\App\\uninstall.exe", ["--silent"], timeout_ms=100)
        mock_close.assert_called_once_with(12345)  # 逾時也要記得收尾釋放控制代碼


class TestWaitForSelectedPathWritable(unittest.TestCase):
    """_wait_for_selected_path_writable()：更新覆蓋安裝後，舊版本 uninstall.exe
    背景延遲自我刪除不保證真的跑完，安裝目標路徑可能還卡在 Windows 的
    pending-delete 狀態。這裡驗證用短暫重試取代原本固定 time.sleep() 賭運氣的
    做法：遇到 PermissionError 要重試、真的可以寫入了要立刻停手、逾時也不拋例外。"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        shutil.rmtree(self.tmp_dir)  # 讓 os.makedirs() 有東西可以建立
        self.api = make_installer_api(selected_path=self.tmp_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_returns_immediately_when_path_already_writable(self):
        with mock.patch("installer_core.time.sleep") as mock_sleep:
            self.api._wait_for_selected_path_writable()
        self.assertTrue(os.path.exists(self.tmp_dir))
        mock_sleep.assert_not_called()

    def test_retries_until_permission_error_clears(self):
        real_makedirs = os.makedirs
        call_count = {"n": 0}

        def flaky_makedirs(path, exist_ok=False):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise PermissionError("模擬 pending-delete 還沒釋放")
            return real_makedirs(path, exist_ok=exist_ok)

        with mock.patch("installer_core.os.makedirs", side_effect=flaky_makedirs), \
             mock.patch("installer_core.time.sleep") as mock_sleep:
            self.api._wait_for_selected_path_writable(timeout=10, interval=0.5)

        self.assertEqual(call_count["n"], 3, "前兩次遇到 PermissionError 應該重試，第三次成功就停手")
        self.assertEqual(mock_sleep.call_count, 2)
        self.assertTrue(os.path.exists(self.tmp_dir))

    def test_gives_up_after_timeout_without_raising(self):
        with mock.patch(
            "installer_core.os.makedirs", side_effect=PermissionError("一直卡住"),
        ), mock.patch("installer_core.time.sleep"):
            self.api._wait_for_selected_path_writable(timeout=0.01, interval=0.01)
        # 不拋例外，把失敗處理權交還給呼叫端（trigger_installation() 後續會再踢出真正的錯誤）


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


class TestCloseRunningMainExe(unittest.TestCase):
    """close_running_main_exe()：使用者在「偵測到主程式執行中」的彈窗按下
    「關閉程式並繼續安裝」時呼叫，寫法比照 uninstall.py 既有的慣例
    （taskkill /f、吞例外回傳布林值）。

    真實抓到的問題：原本只要 subprocess.run() 本身沒拋例外就一律回傳
    True，不管 taskkill 有沒有真的成功結束程序（找不到目標程序時
    taskkill 會用非 0 的 returncode 表示失敗，但 stderr 被導到 DEVNULL、
    呼叫端從來沒檢查過）。改成檢查 returncode，回傳值才真的反映有沒有
    成功。"""

    def test_calls_taskkill_with_main_exe_basename(self):
        api = make_installer_api(main_exe="sub\\app.exe")
        with mock.patch("installer_core.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            result = api.close_running_main_exe()
        self.assertTrue(result)
        args, kwargs = mock_run.call_args
        self.assertEqual(args[0], ["taskkill", "/f", "/im", "app.exe"])

    def test_returns_false_when_taskkill_reports_failure(self):
        api = make_installer_api(main_exe="app.exe")
        with mock.patch("installer_core.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 128  # 例如找不到目標程序
            self.assertFalse(api.close_running_main_exe())

    def test_returns_false_without_main_exe(self):
        api = make_installer_api(main_exe="")
        self.assertFalse(api.close_running_main_exe())

    def test_swallows_failure(self):
        api = make_installer_api(main_exe="app.exe")
        with mock.patch("installer_core.subprocess.run", side_effect=RuntimeError("模擬失敗")):
            self.assertFalse(api.close_running_main_exe())


class TestProcessRunningDetection(unittest.TestCase):
    """真實需求：主程式執行中不該直接判定安裝失敗，要回傳獨立的狀態值
    讓前端可以跳出「關閉程式並繼續安裝／取消」的互動選擇。"""

    def setUp(self):
        self.resource_dir = tempfile.mkdtemp()
        self.app_contents_dir = os.path.join(self.resource_dir, "app_contents")
        os.makedirs(self.app_contents_dir)
        with open(os.path.join(self.app_contents_dir, "app.exe"), "wb") as f:
            f.write(b"fake-app")
        self.install_dir = tempfile.mkdtemp()
        shutil.rmtree(self.install_dir)

    def tearDown(self):
        shutil.rmtree(self.resource_dir, ignore_errors=True)
        shutil.rmtree(self.install_dir, ignore_errors=True)

    def test_returns_process_running_status_instead_of_error(self):
        api = make_installer_api(
            app_name="MyApp", main_exe="app.exe", selected_path=self.install_dir,
            file_associations=[], add_to_path=False,
        )
        with mock.patch("installer_core.get_resource_path", side_effect=lambda p: os.path.join(self.resource_dir, p)), \
             mock.patch.object(api, "check_existing_install", return_value={"exists": False}), \
             mock.patch.object(api, "_check_disk_space", return_value=(True, 10 ** 9, 1)), \
             mock.patch("installer_core._is_process_running", return_value=True):
            result = api.trigger_installation(create_desktop_shortcut=False)

        self.assertEqual(result["status"], "process_running")
        self.assertIn("app.exe", result["message"])
        self.assertFalse(os.path.exists(self.install_dir), "偵測到執行中不該建立安裝目錄、開始複製檔案")

    def test_skip_process_check_bypasses_the_detection(self):
        """保底選項：偵測卡死關不掉時（見 CONTEXT.md 的說明），讓使用者
        可以略過偵測強制繼續，不會被卡死在 process_running 狀態。"""
        api = make_installer_api(
            app_name="MyApp", main_exe="app.exe", selected_path=self.install_dir,
            file_associations=[], add_to_path=False,
        )
        with mock.patch("installer_core.get_resource_path", side_effect=lambda p: os.path.join(self.resource_dir, p)), \
             mock.patch.object(api, "check_existing_install", return_value={"exists": False}), \
             mock.patch.object(api, "_check_disk_space", return_value=(True, 10 ** 9, 1)), \
             mock.patch("installer_core._is_process_running", return_value=True):
            result = api.trigger_installation(create_desktop_shortcut=False, skip_process_check=True)

        self.assertNotEqual(result["status"], "process_running")


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


class TestRollbackCoversSystemEntries(unittest.TestCase):
    """真實抓到的缺口：_rollback() 原本只清『這次安裝已經複製出去的檔案』，
    但安裝流程後段還會依序寫入解除安裝登錄表項目/捷徑/檔案關聯/PATH——
    這幾步任何一步後面的步驟失敗，前面已經成功寫入的部分完全不會被回滾，
    使用者會卡在『安裝回報失敗，但系統裡已經留下登錄表項目/捷徑』的
    半殘狀態。_rollback() 現在多接受這四類狀態，依「後寫的先復原」順序
    呼叫 system_entries.py / file_assoc.py 既有的移除函式清乾淨。"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_does_nothing_extra_when_nothing_was_written(self):
        api = make_installer_api(selected_path=self.tmp_dir, app_name="MyApp")
        with mock.patch("installer_core.system_entries.remove_from_path") as mock_path, \
             mock.patch("installer_core.file_assoc.unregister") as mock_unregister, \
             mock.patch("installer_core.system_entries.remove_shortcut") as mock_shortcut, \
             mock.patch("installer_core.system_entries.remove_registry_entry") as mock_registry:
            api._rollback([], log=None)
        mock_path.assert_not_called()
        mock_unregister.assert_not_called()
        mock_shortcut.assert_not_called()
        mock_registry.assert_not_called()

    def test_undoes_path_file_assoc_shortcuts_and_registry_when_all_written(self):
        api = make_installer_api(
            selected_path=self.tmp_dir, app_name="MyApp", no_admin_install=True,
            file_associations=[".foo"],
        )
        with mock.patch("installer_core.system_entries.remove_from_path") as mock_path, \
             mock.patch("installer_core.file_assoc.unregister") as mock_unregister, \
             mock.patch("installer_core.system_entries.remove_shortcut") as mock_shortcut, \
             mock.patch("installer_core.system_entries.remove_registry_entry") as mock_registry:
            api._rollback(
                [], log=None,
                registry_entry_created=True,
                shortcuts_created=[False, True],
                file_associations_registered=True,
                path_directory="C:\\Apps\\MyApp",
            )
        mock_path.assert_called_once_with("C:\\Apps\\MyApp", True)
        mock_unregister.assert_called_once_with([".foo"])
        self.assertEqual(
            mock_shortcut.call_args_list,
            [
                mock.call("MyApp", desktop=False, no_admin_install=True),
                mock.call("MyApp", desktop=True, no_admin_install=True),
            ],
        )
        mock_registry.assert_called_once_with("MyApp", True)


class TestTriggerInstallationRollsBackSystemEntriesOnLateFailure(unittest.TestCase):
    """比 TestRollbackCoversSystemEntries 高一層的整合測試：模擬登錄表/捷徑/
    檔案關聯/PATH 都已經成功寫入之後，安裝流程才在寫入 manifest 這一步失敗，
    驗證 trigger_installation() 整體回傳失敗時，這四類系統項目確實有被
    清乾淨，不是只有複製的檔案被回滾。"""

    def setUp(self):
        self.resource_dir = tempfile.mkdtemp()
        self.app_contents_dir = os.path.join(self.resource_dir, "app_contents")
        os.makedirs(self.app_contents_dir)
        with open(os.path.join(self.app_contents_dir, "app.exe"), "wb") as f:
            f.write(b"fake-app")
        self.install_dir = tempfile.mkdtemp()
        shutil.rmtree(self.install_dir)

    def tearDown(self):
        shutil.rmtree(self.resource_dir, ignore_errors=True)
        shutil.rmtree(self.install_dir, ignore_errors=True)

    def test_system_entries_removed_when_manifest_write_fails(self):
        api = make_installer_api(
            app_name="MyApp", main_exe="app.exe", selected_path=self.install_dir,
            file_associations=[".foo"], add_to_path=True, no_admin_install=False,
        )
        with mock.patch("installer_core.get_resource_path", side_effect=lambda p: os.path.join(self.resource_dir, p)), \
             mock.patch.object(api, "check_existing_install", return_value={"exists": False}), \
             mock.patch.object(api, "_check_disk_space", return_value=(True, 10 ** 9, 1)), \
             mock.patch.object(api, "_register_uninstall_entry"), \
             mock.patch.object(api, "_create_shortcut", return_value=True), \
             mock.patch("installer_core.file_assoc.register"), \
             mock.patch.object(api, "_add_to_path_env", return_value="C:\\Apps\\MyApp"), \
             mock.patch("installer_core.json.dump", side_effect=RuntimeError("模擬寫入 manifest 失敗")), \
             mock.patch("installer_core.system_entries.remove_from_path") as mock_path, \
             mock.patch("installer_core.file_assoc.unregister") as mock_unregister, \
             mock.patch("installer_core.system_entries.remove_shortcut") as mock_shortcut, \
             mock.patch("installer_core.system_entries.remove_registry_entry") as mock_registry:
            result = api.trigger_installation(create_desktop_shortcut=True)

        self.assertEqual(result["status"], "error")
        mock_path.assert_called_once_with("C:\\Apps\\MyApp", False)
        mock_unregister.assert_called_once_with([".foo"])
        self.assertEqual(mock_shortcut.call_count, 2, "開始功能表 + 桌面捷徑都建立成功過，都該被回滾")
        mock_registry.assert_called_once_with("MyApp", False)


class TestGetUiLanguage(unittest.TestCase):
    def test_returns_whatever_load_config_computed(self):
        api = make_installer_api(ui_language="en")
        self.assertEqual(api.get_ui_language(), "en")


class TestGetEulaTextFallbackChain(unittest.TestCase):
    def test_returns_empty_string_when_no_eula_configured(self):
        api = make_installer_api(eula_texts={}, eula_default_lang="", ui_language="zh-TW")
        self.assertEqual(api.get_eula_text(), "")

    def test_exact_ui_language_match_wins(self):
        api = make_installer_api(
            eula_texts={"zh-TW": "中文合約", "en": "English EULA"},
            eula_default_lang="en", ui_language="zh-TW",
        )
        self.assertEqual(api.get_eula_text(), "中文合約")

    def test_falls_back_to_default_lang_when_ui_language_has_no_text(self):
        api = make_installer_api(
            eula_texts={"zh-TW": "中文合約", "en": "English EULA"},
            eula_default_lang="en", ui_language="ja-JP",
        )
        self.assertEqual(api.get_eula_text(), "English EULA")

    def test_falls_back_to_first_entry_when_default_lang_also_missing(self):
        api = make_installer_api(
            eula_texts={"zh-TW": "中文合約", "en": "English EULA"},
            eula_default_lang="ja-JP", ui_language="ko-KR",
        )
        self.assertEqual(api.get_eula_text(), "中文合約", "開發者忘記設定有效的預設語言時，至少要保底顯示一個版本，而不是整個消失")


class _FakeWinError(OSError):
    """建構帶有 winerror 屬性的 OSError，模擬 Windows 特有的錯誤碼——
    真實的 PermissionError/OSError 在 Windows 上會自動帶 winerror，這裡
    純 Python 測試環境要手動附加才能重現。"""

    def __init__(self, winerror, message="模擬錯誤"):
        super().__init__(message)
        self.winerror = winerror


class TestDescribeInstallOsError(unittest.TestCase):
    """_describe_install_os_error()：真實抓到的 bug——trigger_installation()
    原本不管什麼原因，只要是 PermissionError 就一律顯示「權限不足，請以
    管理員身分執行」，但這支安裝程式本身是用 --uac-admin 編譯的，Windows
    執行前就已經要求過使用者用系統管理員身分執行，執行到這裡的程式碼一定
    已經是系統管理員權杖——「以管理員身分重試」對這裡真正常見的成因（檔案
    被其他程式鎖住）完全沒有幫助，反而會誤導使用者。現在改用 winerror
    分辨真正的成因，給出對應的訊息。"""

    def test_sharing_violation_names_the_locking_process_when_detected(self):
        api = make_installer_api()
        error = _FakeWinError(32)
        with mock.patch(
            "installer_core.restart_manager.find_locking_processes",
            return_value=[(111, "某個殼層擴充功能")],
        ):
            message = api._describe_install_os_error(error, r"C:\app\locked.dll")
        self.assertIn("locked.dll", message)
        self.assertIn("某個殼層擴充功能", message)
        self.assertNotIn("以管理員身分重試", message)

    def test_lock_violation_without_detected_process_still_explains_cause(self):
        api = make_installer_api()
        error = _FakeWinError(33)
        with mock.patch("installer_core.restart_manager.find_locking_processes", return_value=[]):
            message = api._describe_install_os_error(error, r"C:\app\locked.dll")
        self.assertIn("正被其他程式使用中", message)

    def test_lock_violation_message_hints_at_antivirus_blocking_termination(self):
        """真實抓到的問題：使用者按下「關閉此程式」之後，即使
        explorer_lock_release.py 那一整套（先關窗、不夠再暫停
        AutoRestartShell 強制終止）都正確執行，防毒/安全軟體（實測案例：
        火絨的「關鍵進程保護」→「資源管理器」→自動阻止）仍然可能在核心層
        直接否決終止系統關鍵行程的動作，讓 OpenProcess 成功但
        TerminateProcess 回報存取被拒——這種情況使用者只會看到「檔案
        使用中」畫面一直卡住，完全沒有線索去查防毒軟體設定。訊息裡要
        提示這個可能性，不能讓使用者卡在無限重試迴圈裡。"""
        api = make_installer_api()
        error = _FakeWinError(32)
        with mock.patch(
            "installer_core.restart_manager.find_locking_processes",
            return_value=[(111, "Windows 檔案總管")],
        ):
            message = api._describe_install_os_error(error, r"C:\app\locked.dll")
        self.assertIn("防毒", message)

    def test_access_denied_does_not_suggest_running_as_admin(self):
        api = make_installer_api()
        error = _FakeWinError(5)
        message = api._describe_install_os_error(error, r"C:\app\file.dll")
        self.assertIn("已經是以系統管理員身分執行", message)
        self.assertIn("受控資料夾存取", message)

    def test_write_protect_reports_readonly_media(self):
        api = make_installer_api()
        error = _FakeWinError(19)
        message = api._describe_install_os_error(error, r"D:\file.dll")
        self.assertIn("唯讀", message)

    def test_unknown_winerror_permission_error_falls_back_to_generic_message(self):
        api = make_installer_api()
        error = PermissionError("其他沒見過的原因")
        message = api._describe_install_os_error(error, r"C:\app\file.dll")
        self.assertIn("已經是以系統管理員身分執行", message)

    def test_non_permission_os_error_uses_generic_message(self):
        api = make_installer_api()
        error = OSError("磁碟走完了之類的其他錯誤")
        message = api._describe_install_os_error(error, None)
        self.assertIn("磁碟走完了之類的其他錯誤", message)


class TestIsLockViolation(unittest.TestCase):
    def test_sharing_violation_is_lock_violation(self):
        api = make_installer_api()
        self.assertTrue(api._is_lock_violation(_FakeWinError(32)))

    def test_lock_violation_is_lock_violation(self):
        api = make_installer_api()
        self.assertTrue(api._is_lock_violation(_FakeWinError(33)))

    def test_other_winerror_is_not_lock_violation(self):
        api = make_installer_api()
        self.assertFalse(api._is_lock_violation(_FakeWinError(5)))

    def test_no_winerror_is_not_lock_violation(self):
        api = make_installer_api()
        self.assertFalse(api._is_lock_violation(OSError("沒有 winerror")))


class TestCloseLockingProcesses(unittest.TestCase):
    """close_locking_processes()：使用者在安裝失敗跳出的『檔案使用中』畫面
    按下「關閉此程式」時呼叫。實際的釋放邏輯（先關瀏覽視窗、不夠才暫停
    AutoRestartShell 強制關殼層）收在 explorer_lock_release.py，這裡只是
    薄包裝——把回傳的 forced_down 狀態存起來，供 trigger_installation()
    之後補做「重啟 explorer.exe / 恢復 AutoRestartShell」。"""

    def test_delegates_to_explorer_lock_release_and_stores_state(self):
        api = make_installer_api()
        fake_state = {"previous_auto_restart_shell": "1"}
        with mock.patch(
            "installer_core.explorer_lock_release.release_locking_processes",
            return_value=fake_state,
        ) as mock_release:
            api.close_locking_processes(
                [{"pid": 111, "name": "notepad.exe"}], path="C:\\Apps\\MyApp",
            )
        args, kwargs = mock_release.call_args
        self.assertEqual(args, ([{"pid": 111, "name": "notepad.exe"}],))
        self.assertEqual(kwargs["path"], "C:\\Apps\\MyApp")
        self.assertTrue(callable(kwargs["log"]))
        self.assertEqual(api._explorer_forced_down_state, fake_state)

    def test_stores_none_state_when_release_does_not_force_shell_restart(self):
        api = make_installer_api()
        with mock.patch(
            "installer_core.explorer_lock_release.release_locking_processes",
            return_value=None,
        ):
            api.close_locking_processes([{"pid": 111, "name": "notepad.exe"}])
        self.assertIsNone(api._explorer_forced_down_state)

    def test_log_callback_appends_timestamped_line_to_debug_log_file(self):
        """實測發現砍 explorer.exe 沒效果時完全無從追查是哪一步壞掉——
        explorer_lock_release.py 內部雖然有 log() 可以注入，但沒有實際
        落地到檔案的話，使用者下次重現問題時還是拿不出任何線索。這裡驗證
        close_locking_processes() 真的會把訊息寫進一個固定、可以事後翻閱
        的除錯紀錄檔（%TEMP% 底下），不是只在記憶體裡飄一下就消失。"""
        api = make_installer_api()
        tmp_dir = tempfile.mkdtemp()
        try:
            with mock.patch("installer_core.tempfile.gettempdir", return_value=tmp_dir), \
                 mock.patch(
                     "installer_core.explorer_lock_release.release_locking_processes",
                     side_effect=lambda *a, **kw: kw["log"]("測試訊息 pid=999"),
                 ):
                api.close_locking_processes([{"pid": 999, "name": "test.exe"}])

            log_files = [f for f in os.listdir(tmp_dir) if "lock" in f.lower()]
            self.assertTrue(log_files, "沒有找到除錯紀錄檔")
            with open(os.path.join(tmp_dir, log_files[0]), encoding="utf-8") as f:
                content = f.read()
            self.assertIn("測試訊息 pid=999", content)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


class TestTriggerInstallationFileLocked(unittest.TestCase):
    """安裝過程中複製檔案遇到 sharing/lock violation 時：能查到是誰鎖住的
    話，回傳結構化的 file_locked 狀態（含 processes 清單），讓前端可以
    跳出『關閉此程式』的互動選擇，而不是只給一段純文字說明、逼使用者自己
    手動去關；查不到是誰鎖住的話（沒東西可以讓使用者按），維持原本的
    error 狀態。比照 TestTriggerInstallationRollsBackSystemEntriesOnLateFailure
    的整合測試手法。"""

    def setUp(self):
        self.resource_dir = tempfile.mkdtemp()
        self.app_contents_dir = os.path.join(self.resource_dir, "app_contents")
        os.makedirs(self.app_contents_dir)
        with open(os.path.join(self.app_contents_dir, "app.exe"), "wb") as f:
            f.write(b"fake-app")
        self.install_dir = tempfile.mkdtemp()
        shutil.rmtree(self.install_dir)

    def tearDown(self):
        shutil.rmtree(self.resource_dir, ignore_errors=True)
        shutil.rmtree(self.install_dir, ignore_errors=True)

    def _make_api(self):
        return make_installer_api(
            app_name="MyApp", main_exe="app.exe", selected_path=self.install_dir,
            file_associations=[], add_to_path=False,
        )

    def test_returns_file_locked_status_with_processes_when_detected(self):
        api = self._make_api()
        with mock.patch("installer_core.get_resource_path", side_effect=lambda p: os.path.join(self.resource_dir, p)), \
             mock.patch.object(api, "check_existing_install", return_value={"exists": False}), \
             mock.patch.object(api, "_check_disk_space", return_value=(True, 10 ** 9, 1)), \
             mock.patch("installer_core.shutil.copy2", side_effect=_FakeWinError(32, "sharing violation")), \
             mock.patch(
                 "installer_core.restart_manager.find_locking_processes",
                 return_value=[(111, "explorer.exe")],
             ):
            result = api.trigger_installation(create_desktop_shortcut=False)

        self.assertEqual(result["status"], "file_locked")
        self.assertEqual(result["processes"], [{"pid": 111, "name": "explorer.exe"}])
        self.assertIn("path", result)
        self.assertIn("message", result)

    def test_falls_back_to_generic_error_when_no_process_detected(self):
        api = self._make_api()
        with mock.patch("installer_core.get_resource_path", side_effect=lambda p: os.path.join(self.resource_dir, p)), \
             mock.patch.object(api, "check_existing_install", return_value={"exists": False}), \
             mock.patch.object(api, "_check_disk_space", return_value=(True, 10 ** 9, 1)), \
             mock.patch("installer_core.shutil.copy2", side_effect=_FakeWinError(32, "sharing violation")), \
             mock.patch("installer_core.restart_manager.find_locking_processes", return_value=[]):
            result = api.trigger_installation(create_desktop_shortcut=False)

        self.assertEqual(result["status"], "error")
        self.assertNotIn("processes", result)


class TestTriggerInstallationRestoresExplorerLock(unittest.TestCase):
    """trigger_installation() 呼叫端如果之前透過 close_locking_processes()
    強制關過殼層（self._explorer_forced_down_state 非 None），不管這次
    安裝結果是成功、一般錯誤、還是中途拋出未預期例外，最後都要呼叫一次
    explorer_lock_release.restore_after_lock_release() 補做「重啟
    explorer.exe / 恢復 AutoRestartShell」，不能因為某條分支忘記補而讓
    使用者被留在殼層沒被復原的狀態。"""

    def _make_api(self, resource_dir, install_dir):
        return make_installer_api(
            app_name="MyApp", main_exe="app.exe", selected_path=install_dir,
            file_associations=[], add_to_path=False,
        )

    def setUp(self):
        self.resource_dir = tempfile.mkdtemp()
        self.app_contents_dir = os.path.join(self.resource_dir, "app_contents")
        os.makedirs(self.app_contents_dir)
        with open(os.path.join(self.app_contents_dir, "app.exe"), "wb") as f:
            f.write(b"fake-app")
        self.install_dir = tempfile.mkdtemp()
        shutil.rmtree(self.install_dir)

    def tearDown(self):
        shutil.rmtree(self.resource_dir, ignore_errors=True)
        shutil.rmtree(self.install_dir, ignore_errors=True)

    def test_restores_after_successful_install(self):
        api = self._make_api(self.resource_dir, self.install_dir)
        api._explorer_forced_down_state = {"previous_auto_restart_shell": "1"}
        with mock.patch("installer_core.get_resource_path", side_effect=lambda p: os.path.join(self.resource_dir, p)), \
             mock.patch.object(api, "check_existing_install", return_value={"exists": False}), \
             mock.patch.object(api, "_check_disk_space", return_value=(True, 10 ** 9, 1)), \
             mock.patch.object(api, "_register_uninstall_entry"), \
             mock.patch("installer_core.explorer_lock_release.restore_after_lock_release") as mock_restore:
            result = api.trigger_installation(create_desktop_shortcut=False)

        self.assertEqual(result["status"], "success")
        mock_restore.assert_called_once_with({"previous_auto_restart_shell": "1"})
        self.assertIsNone(api._explorer_forced_down_state)

    def test_restores_after_error_result(self):
        api = self._make_api(self.resource_dir, self.install_dir)
        api._explorer_forced_down_state = {"previous_auto_restart_shell": "1"}
        with mock.patch("installer_core.get_resource_path", return_value="C:\\does-not-exist"), \
             mock.patch("installer_core.explorer_lock_release.restore_after_lock_release") as mock_restore:
            result = api.trigger_installation(create_desktop_shortcut=False)

        self.assertEqual(result["status"], "error")
        mock_restore.assert_called_once_with({"previous_auto_restart_shell": "1"})
        self.assertIsNone(api._explorer_forced_down_state)

    def test_restores_even_when_impl_raises_unexpectedly(self):
        """_trigger_installation_impl() 本身已經有一個很寬的
        except Exception 分支，把安裝過程中的例外都轉換成 error 狀態
        （不會真的往外拋）——但 trigger_installation() 這層的 try/finally
        不該假設「impl 永遠不會拋例外」，這裡直接把 impl 換成一個會拋例外
        的假實作，驗證就算真的拋出來，finally 還是會補做恢復。"""
        api = self._make_api(self.resource_dir, self.install_dir)
        api._explorer_forced_down_state = {"previous_auto_restart_shell": "1"}
        with mock.patch.object(
            api, "_trigger_installation_impl", side_effect=RuntimeError("模擬未預期例外"),
        ), mock.patch("installer_core.explorer_lock_release.restore_after_lock_release") as mock_restore:
            with self.assertRaises(RuntimeError):
                api.trigger_installation(create_desktop_shortcut=False)
        mock_restore.assert_called_once_with({"previous_auto_restart_shell": "1"})
        self.assertIsNone(api._explorer_forced_down_state)

    def test_does_not_call_restore_when_nothing_was_forced_down(self):
        api = self._make_api(self.resource_dir, self.install_dir)
        api._explorer_forced_down_state = None
        with mock.patch("installer_core.get_resource_path", side_effect=lambda p: os.path.join(self.resource_dir, p)), \
             mock.patch.object(api, "check_existing_install", return_value={"exists": False}), \
             mock.patch.object(api, "_check_disk_space", return_value=(True, 10 ** 9, 1)), \
             mock.patch.object(api, "_register_uninstall_entry"), \
             mock.patch("installer_core.explorer_lock_release.restore_after_lock_release") as mock_restore:
            api.trigger_installation(create_desktop_shortcut=False)
        mock_restore.assert_called_once_with(None)


class TestTriggerInstallationCreatesWindowsService(unittest.TestCase):
    """windows_service packaging 欄位有設定時，trigger_installation() 應該
    呼叫 windows_service.create_service() 建立服務、並把服務名稱記進
    install_manifest.json；沒設定時完全不呼叫；建立失敗不應該讓整個安裝
    回報失敗（比照 post_install_script 失敗只記錄警告的既有慣例）。"""

    def setUp(self):
        self.resource_dir = tempfile.mkdtemp()
        self.app_contents_dir = os.path.join(self.resource_dir, "app_contents")
        os.makedirs(self.app_contents_dir)
        with open(os.path.join(self.app_contents_dir, "app.exe"), "wb") as f:
            f.write(b"fake-app")
        self.install_dir = tempfile.mkdtemp()
        shutil.rmtree(self.install_dir)

    def tearDown(self):
        shutil.rmtree(self.resource_dir, ignore_errors=True)
        shutil.rmtree(self.install_dir, ignore_errors=True)

    def _make_api(self, **overrides):
        return make_installer_api(
            app_name="MyApp", main_exe="app.exe", selected_path=self.install_dir,
            file_associations=[], add_to_path=False, **overrides,
        )

    def test_creates_service_when_configured(self):
        api = self._make_api(windows_service={
            "service_name": "MySvc", "exe_relative_path": "app.exe",
            "display_name": "My Service", "start_type": "auto",
        })
        with mock.patch("installer_core.get_resource_path", side_effect=lambda p: os.path.join(self.resource_dir, p)), \
             mock.patch.object(api, "check_existing_install", return_value={"exists": False}), \
             mock.patch.object(api, "_check_disk_space", return_value=(True, 10 ** 9, 1)), \
             mock.patch.object(api, "_register_uninstall_entry"), \
             mock.patch("installer_core.windows_service.create_service", return_value=True) as mock_create:
            result = api.trigger_installation(create_desktop_shortcut=False)

        self.assertEqual(result["status"], "success")
        mock_create.assert_called_once_with(
            "MySvc", os.path.join(self.install_dir, "app.exe"),
            display_name="My Service", start_type="auto",
        )

    def test_no_service_configured_skips_creation(self):
        api = self._make_api()
        with mock.patch("installer_core.get_resource_path", side_effect=lambda p: os.path.join(self.resource_dir, p)), \
             mock.patch.object(api, "check_existing_install", return_value={"exists": False}), \
             mock.patch.object(api, "_check_disk_space", return_value=(True, 10 ** 9, 1)), \
             mock.patch.object(api, "_register_uninstall_entry"), \
             mock.patch("installer_core.windows_service.create_service") as mock_create:
            api.trigger_installation(create_desktop_shortcut=False)

        mock_create.assert_not_called()

    def test_manifest_records_service_name_when_created(self):
        api = self._make_api(windows_service={"service_name": "MySvc", "exe_relative_path": "app.exe"})
        with mock.patch("installer_core.get_resource_path", side_effect=lambda p: os.path.join(self.resource_dir, p)), \
             mock.patch.object(api, "check_existing_install", return_value={"exists": False}), \
             mock.patch.object(api, "_check_disk_space", return_value=(True, 10 ** 9, 1)), \
             mock.patch.object(api, "_register_uninstall_entry"), \
             mock.patch("installer_core.windows_service.create_service", return_value=True):
            api.trigger_installation(create_desktop_shortcut=False)

        with open(os.path.join(self.install_dir, "install_manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["windows_service_name"], "MySvc")

    def test_manifest_omits_service_name_when_creation_fails(self):
        api = self._make_api(windows_service={"service_name": "MySvc", "exe_relative_path": "app.exe"})
        with mock.patch("installer_core.get_resource_path", side_effect=lambda p: os.path.join(self.resource_dir, p)), \
             mock.patch.object(api, "check_existing_install", return_value={"exists": False}), \
             mock.patch.object(api, "_check_disk_space", return_value=(True, 10 ** 9, 1)), \
             mock.patch.object(api, "_register_uninstall_entry"), \
             mock.patch("installer_core.windows_service.create_service", return_value=False):
            result = api.trigger_installation(create_desktop_shortcut=False)

        self.assertEqual(result["status"], "success")
        with open(os.path.join(self.install_dir, "install_manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["windows_service_name"], "")


class TestTriggerInstallationCreatesScheduledTask(unittest.TestCase):
    """scheduled_task packaging 欄位有設定時，trigger_installation() 應該
    呼叫 scheduled_task.create_scheduled_task() 建立排程工作、並把工作
    名稱記進 install_manifest.json；沒設定時完全不呼叫；建立失敗不應該
    讓整個安裝回報失敗。"""

    def setUp(self):
        self.resource_dir = tempfile.mkdtemp()
        self.app_contents_dir = os.path.join(self.resource_dir, "app_contents")
        os.makedirs(self.app_contents_dir)
        with open(os.path.join(self.app_contents_dir, "app.exe"), "wb") as f:
            f.write(b"fake-app")
        self.install_dir = tempfile.mkdtemp()
        shutil.rmtree(self.install_dir)

    def tearDown(self):
        shutil.rmtree(self.resource_dir, ignore_errors=True)
        shutil.rmtree(self.install_dir, ignore_errors=True)

    def _make_api(self, **overrides):
        return make_installer_api(
            app_name="MyApp", main_exe="app.exe", selected_path=self.install_dir,
            file_associations=[], add_to_path=False, **overrides,
        )

    def test_creates_task_when_configured(self):
        api = self._make_api(scheduled_task={
            "task_name": "MyTask", "exe_relative_path": "app.exe", "trigger": "daily",
        })
        with mock.patch("installer_core.get_resource_path", side_effect=lambda p: os.path.join(self.resource_dir, p)), \
             mock.patch.object(api, "check_existing_install", return_value={"exists": False}), \
             mock.patch.object(api, "_check_disk_space", return_value=(True, 10 ** 9, 1)), \
             mock.patch.object(api, "_register_uninstall_entry"), \
             mock.patch("installer_core.scheduled_task.create_scheduled_task", return_value=True) as mock_create:
            result = api.trigger_installation(create_desktop_shortcut=False)

        self.assertEqual(result["status"], "success")
        mock_create.assert_called_once_with(
            "MyTask", os.path.join(self.install_dir, "app.exe"), trigger="daily",
        )

    def test_no_task_configured_skips_creation(self):
        api = self._make_api()
        with mock.patch("installer_core.get_resource_path", side_effect=lambda p: os.path.join(self.resource_dir, p)), \
             mock.patch.object(api, "check_existing_install", return_value={"exists": False}), \
             mock.patch.object(api, "_check_disk_space", return_value=(True, 10 ** 9, 1)), \
             mock.patch.object(api, "_register_uninstall_entry"), \
             mock.patch("installer_core.scheduled_task.create_scheduled_task") as mock_create:
            api.trigger_installation(create_desktop_shortcut=False)

        mock_create.assert_not_called()

    def test_manifest_records_task_name_when_created(self):
        api = self._make_api(scheduled_task={"task_name": "MyTask", "exe_relative_path": "app.exe"})
        with mock.patch("installer_core.get_resource_path", side_effect=lambda p: os.path.join(self.resource_dir, p)), \
             mock.patch.object(api, "check_existing_install", return_value={"exists": False}), \
             mock.patch.object(api, "_check_disk_space", return_value=(True, 10 ** 9, 1)), \
             mock.patch.object(api, "_register_uninstall_entry"), \
             mock.patch("installer_core.scheduled_task.create_scheduled_task", return_value=True):
            api.trigger_installation(create_desktop_shortcut=False)

        with open(os.path.join(self.install_dir, "install_manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["scheduled_task_name"], "MyTask")

    def test_manifest_records_empty_task_name_when_creation_fails(self):
        api = self._make_api(scheduled_task={"task_name": "MyTask", "exe_relative_path": "app.exe"})
        with mock.patch("installer_core.get_resource_path", side_effect=lambda p: os.path.join(self.resource_dir, p)), \
             mock.patch.object(api, "check_existing_install", return_value={"exists": False}), \
             mock.patch.object(api, "_check_disk_space", return_value=(True, 10 ** 9, 1)), \
             mock.patch.object(api, "_register_uninstall_entry"), \
             mock.patch("installer_core.scheduled_task.create_scheduled_task", return_value=False):
            result = api.trigger_installation(create_desktop_shortcut=False)

        self.assertEqual(result["status"], "success")
        with open(os.path.join(self.install_dir, "install_manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["scheduled_task_name"], "")


class TestTriggerInstallationCreatesRestorePoint(unittest.TestCase):
    """create_restore_point_before_install 開啟時，trigger_installation()
    要呼叫 restore_point.create_restore_point()；關閉（預設）時完全不呼叫；
    建立失敗不應該讓整個安裝回報失敗，也不需要記進 install_manifest.json
    （還原點是系統層級的，不需要解除安裝時清除）。"""

    def setUp(self):
        self.resource_dir = tempfile.mkdtemp()
        self.app_contents_dir = os.path.join(self.resource_dir, "app_contents")
        os.makedirs(self.app_contents_dir)
        with open(os.path.join(self.app_contents_dir, "app.exe"), "wb") as f:
            f.write(b"fake-app")
        self.install_dir = tempfile.mkdtemp()
        shutil.rmtree(self.install_dir)

    def tearDown(self):
        shutil.rmtree(self.resource_dir, ignore_errors=True)
        shutil.rmtree(self.install_dir, ignore_errors=True)

    def _make_api(self, **overrides):
        return make_installer_api(
            app_name="MyApp", version="1.2.3", main_exe="app.exe", selected_path=self.install_dir,
            file_associations=[], add_to_path=False, **overrides,
        )

    def test_creates_restore_point_when_enabled(self):
        api = self._make_api(create_restore_point_before_install=True)
        with mock.patch("installer_core.get_resource_path", side_effect=lambda p: os.path.join(self.resource_dir, p)), \
             mock.patch.object(api, "check_existing_install", return_value={"exists": False}), \
             mock.patch.object(api, "_check_disk_space", return_value=(True, 10 ** 9, 1)), \
             mock.patch.object(api, "_register_uninstall_entry"), \
             mock.patch("installer_core.restore_point.create_restore_point", return_value=True) as mock_create:
            result = api.trigger_installation(create_desktop_shortcut=False)

        self.assertEqual(result["status"], "success")
        mock_create.assert_called_once_with("安裝 MyApp 1.2.3")

    def test_disabled_by_default_skips_creation(self):
        api = self._make_api()
        with mock.patch("installer_core.get_resource_path", side_effect=lambda p: os.path.join(self.resource_dir, p)), \
             mock.patch.object(api, "check_existing_install", return_value={"exists": False}), \
             mock.patch.object(api, "_check_disk_space", return_value=(True, 10 ** 9, 1)), \
             mock.patch.object(api, "_register_uninstall_entry"), \
             mock.patch("installer_core.restore_point.create_restore_point") as mock_create:
            api.trigger_installation(create_desktop_shortcut=False)

        mock_create.assert_not_called()

    def test_creation_failure_does_not_fail_install(self):
        api = self._make_api(create_restore_point_before_install=True)
        with mock.patch("installer_core.get_resource_path", side_effect=lambda p: os.path.join(self.resource_dir, p)), \
             mock.patch.object(api, "check_existing_install", return_value={"exists": False}), \
             mock.patch.object(api, "_check_disk_space", return_value=(True, 10 ** 9, 1)), \
             mock.patch.object(api, "_register_uninstall_entry"), \
             mock.patch("installer_core.restore_point.create_restore_point", return_value=False):
            result = api.trigger_installation(create_desktop_shortcut=False)

        self.assertEqual(result["status"], "success")


class TestGetDependencyWarnings(unittest.TestCase):
    """get_dependency_warnings()：現在額外回傳 key（前端要用它呼叫
    install_dependency(key) 觸發自動安裝），跟 DEPENDENCY_CHECKERS 從
    3-tuple 擴充成 4-tuple（多了自動安裝要用的靜默參數）配套。"""

    def test_missing_dependency_includes_key_name_and_url(self):
        api = make_installer_api(dependencies=["vcredist_x64"])
        with mock.patch.dict(
            ic.DEPENDENCY_CHECKERS,
            {"vcredist_x64": (lambda: False, "Visual C++ Redistributable (x64)", "https://example.test/vc.exe", ["/quiet"])},
        ):
            warnings = api.get_dependency_warnings()
        self.assertEqual(warnings, [{
            "key": "vcredist_x64",
            "name": "Visual C++ Redistributable (x64)",
            "url": "https://example.test/vc.exe",
        }])

    def test_installed_dependency_produces_no_warning(self):
        api = make_installer_api(dependencies=["vcredist_x64"])
        with mock.patch.dict(
            ic.DEPENDENCY_CHECKERS,
            {"vcredist_x64": (lambda: True, "Visual C++ Redistributable (x64)", "https://example.test/vc.exe", ["/quiet"])},
        ):
            warnings = api.get_dependency_warnings()
        self.assertEqual(warnings, [])


class TestInstallDependency(unittest.TestCase):
    """install_dependency()：下載官方安裝檔＋靜默執行＋重新檢查登錄表。

    真實情境：Visual C++ Redistributable 官方文件記載，如果機器上已經裝了
    更新版本，/quiet 模式下子程序會回傳非 0 的錯誤碼，但這其實不是真正的
    失敗——所以「這次安裝到底成功了沒」不能看子程序結束碼，必須裝完後
    重新呼叫 check_fn() 才是最終依據，底下的測試專門驗證這一點。"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.fake_key = "fake_dep"

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _register_fake_checker(self, check_fn):
        return mock.patch.dict(
            ic.DEPENDENCY_CHECKERS,
            {self.fake_key: (check_fn, "Fake Dependency", "https://example.test/fake.exe", ["/quiet"])},
        )

    def _fake_url_response(self, body=b"fake-exe-bytes"):
        response = mock.MagicMock()
        response.__enter__ = mock.Mock(return_value=response)
        response.__exit__ = mock.Mock(return_value=False)
        response.getheader.return_value = str(len(body))
        chunks = [body, b""]
        response.read.side_effect = chunks
        return response

    def test_unknown_key_returns_error_without_downloading(self):
        api = make_installer_api()
        with mock.patch("installer_core.urllib.request.urlopen") as mock_urlopen:
            result = api.install_dependency("not_a_real_key")
        self.assertEqual(result["status"], "error")
        mock_urlopen.assert_not_called()

    def test_success_when_recheck_confirms_installed(self):
        api = make_installer_api()
        with self._register_fake_checker(lambda: True), \
             mock.patch("installer_core.bits_download.download_via_bits", return_value=False), \
             mock.patch("installer_core.urllib.request.urlopen", return_value=self._fake_url_response()), \
             mock.patch("installer_core.subprocess.run", return_value=mock.Mock(returncode=0)):
            result = api.install_dependency(self.fake_key)
        self.assertEqual(result, {"status": "success", "name": "Fake Dependency"})

    def test_nonzero_exit_code_still_succeeds_if_recheck_confirms_installed(self):
        """真實抓到的坑：vcredist 偵測到已裝更新版本時，/quiet 模式下會回傳
        非 0 結束碼，但這其實不是失敗——不能只看結束碼判斷。"""
        api = make_installer_api()
        with self._register_fake_checker(lambda: True), \
             mock.patch("installer_core.bits_download.download_via_bits", return_value=False), \
             mock.patch("installer_core.urllib.request.urlopen", return_value=self._fake_url_response()), \
             mock.patch("installer_core.subprocess.run", return_value=mock.Mock(returncode=1638)):
            result = api.install_dependency(self.fake_key)
        self.assertEqual(result["status"], "success")

    def test_download_failure_returns_error_without_running_installer(self):
        api = make_installer_api()
        with self._register_fake_checker(lambda: False), \
             mock.patch("installer_core.bits_download.download_via_bits", return_value=False), \
             mock.patch("installer_core.urllib.request.urlopen", side_effect=OSError("模擬連線失敗")), \
             mock.patch("installer_core.subprocess.run") as mock_run:
            result = api.install_dependency(self.fake_key)
        self.assertEqual(result["status"], "error")
        self.assertIn("下載", result["message"])
        mock_run.assert_not_called()

    def test_installer_process_failure_returns_error(self):
        api = make_installer_api()
        with self._register_fake_checker(lambda: False), \
             mock.patch("installer_core.bits_download.download_via_bits", return_value=False), \
             mock.patch("installer_core.urllib.request.urlopen", return_value=self._fake_url_response()), \
             mock.patch("installer_core.subprocess.run", side_effect=OSError("模擬子程序啟動失敗")):
            result = api.install_dependency(self.fake_key)
        self.assertEqual(result["status"], "error")
        self.assertIn("執行", result["message"])

    def test_recheck_still_missing_after_install_returns_error(self):
        api = make_installer_api()
        with self._register_fake_checker(lambda: False), \
             mock.patch("installer_core.bits_download.download_via_bits", return_value=False), \
             mock.patch("installer_core.urllib.request.urlopen", return_value=self._fake_url_response()), \
             mock.patch("installer_core.subprocess.run", return_value=mock.Mock(returncode=0)):
            result = api.install_dependency(self.fake_key)
        self.assertEqual(result["status"], "error")
        self.assertIn("Fake Dependency", result["message"])

    def test_temp_installer_file_is_removed_after_success(self):
        api = make_installer_api()
        expected_tmp_path = os.path.join(tempfile.gettempdir(), f"dep_installer_{self.fake_key}.exe")
        with self._register_fake_checker(lambda: True), \
             mock.patch("installer_core.bits_download.download_via_bits", return_value=False), \
             mock.patch("installer_core.urllib.request.urlopen", return_value=self._fake_url_response()), \
             mock.patch("installer_core.subprocess.run", return_value=mock.Mock(returncode=0)):
            api.install_dependency(self.fake_key)
        self.assertFalse(os.path.exists(expected_tmp_path))

    def test_temp_installer_file_is_removed_even_when_installer_process_fails(self):
        expected_tmp_path = os.path.join(tempfile.gettempdir(), f"dep_installer_{self.fake_key}.exe")
        api = make_installer_api()
        with self._register_fake_checker(lambda: False), \
             mock.patch("installer_core.bits_download.download_via_bits", return_value=False), \
             mock.patch("installer_core.urllib.request.urlopen", return_value=self._fake_url_response()), \
             mock.patch("installer_core.subprocess.run", side_effect=OSError("模擬子程序啟動失敗")):
            api.install_dependency(self.fake_key)
        self.assertFalse(os.path.exists(expected_tmp_path))

    def test_bits_success_skips_urllib_entirely(self):
        api = make_installer_api()
        with self._register_fake_checker(lambda: True), \
             mock.patch("installer_core.bits_download.download_via_bits", return_value=True) as mock_bits, \
             mock.patch("installer_core.urllib.request.urlopen") as mock_urlopen, \
             mock.patch("installer_core.subprocess.run", return_value=mock.Mock(returncode=0)), \
             mock.patch("os.path.exists", return_value=True), \
             mock.patch("os.remove"):
            result = api.install_dependency(self.fake_key)
        self.assertEqual(result["status"], "success")
        mock_bits.assert_called_once()
        mock_urlopen.assert_not_called()

    def test_bits_failure_falls_back_to_urllib(self):
        api = make_installer_api()
        with self._register_fake_checker(lambda: True), \
             mock.patch("installer_core.bits_download.download_via_bits", return_value=False) as mock_bits, \
             mock.patch("installer_core.urllib.request.urlopen", return_value=self._fake_url_response()) as mock_urlopen, \
             mock.patch("installer_core.subprocess.run", return_value=mock.Mock(returncode=0)):
            result = api.install_dependency(self.fake_key)
        self.assertEqual(result["status"], "success")
        mock_bits.assert_called_once()
        mock_urlopen.assert_called_once()


class TestGenericRegistryCheck(unittest.TestCase):
    """_generic_registry_check()：泛用登錄表偵測，取代原本每個相依元件各自
    寫一個檢查函式的做法，custom_dependencies 的自訂相依元件也靠它。"""

    def setUp(self):
        self.fake_reg = FakeWinReg()
        self.patcher = mock.patch.dict(sys.modules, {"winreg": self.fake_reg})
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_key_missing_returns_false(self):
        self.assertFalse(ic._generic_registry_check("HKLM", "Software\\NotThere"))

    def test_value_name_none_only_checks_key_exists(self):
        self.fake_reg.set_hklm("Software\\SomeApp", {})
        self.assertTrue(ic._generic_registry_check("HKLM", "Software\\SomeApp"))

    def test_value_matches_expected(self):
        self.fake_reg.set_hklm("Software\\SomeApp", {"Installed": 1})
        self.assertTrue(ic._generic_registry_check("HKLM", "Software\\SomeApp", "Installed", 1))

    def test_value_mismatch_returns_false(self):
        self.fake_reg.set_hklm("Software\\SomeApp", {"Installed": 0})
        self.assertFalse(ic._generic_registry_check("HKLM", "Software\\SomeApp", "Installed", 1))

    def test_hkcu_hive_is_respected(self):
        self.fake_reg.set_hkcu("Software\\SomeApp", {"Installed": 1})
        self.assertTrue(ic._generic_registry_check("HKCU", "Software\\SomeApp", "Installed", 1))
        self.assertFalse(ic._generic_registry_check("HKLM", "Software\\SomeApp", "Installed", 1))


class TestGenericRegistryVersionCheck(unittest.TestCase):
    """_generic_registry_version_check()：相依元件版本檢查可以指定最低
    需求版本，min_version 是 None 時退化成純粹的存在性判斷。

    enum_subkeys=True 對應 .NET Desktop Runtime 那種「子機碼名稱本身就是
    版本號」的登錄表佈局（InstalledVersions\\...\\sharedfx\\...\\8.0.10）；
    enum_subkeys=False 對應 vcredist 那種「某個值本身存的就是版本字串」
    的佈局。"""

    def setUp(self):
        self.fake_reg = FakeWinReg()
        self.patcher = mock.patch.dict(sys.modules, {"winreg": self.fake_reg})
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_key_missing_returns_false_regardless_of_min_version(self):
        self.assertFalse(ic._generic_registry_version_check("HKLM", "Software\\NotThere", min_version="1.0"))

    def test_no_min_version_is_pure_existence_check(self):
        self.fake_reg.set_hklm("Software\\SomeApp", {"Version": "1.0.0"})
        self.assertTrue(ic._generic_registry_version_check("HKLM", "Software\\SomeApp", value_name="Version"))

    def test_value_name_mode_meets_min_version(self):
        self.fake_reg.set_hklm("Software\\SomeApp", {"Version": "14.38.33135"})
        self.assertTrue(ic._generic_registry_version_check(
            "HKLM", "Software\\SomeApp", value_name="Version", min_version="14.30",
        ))

    def test_value_name_mode_below_min_version(self):
        self.fake_reg.set_hklm("Software\\SomeApp", {"Version": "14.20.0"})
        self.assertFalse(ic._generic_registry_version_check(
            "HKLM", "Software\\SomeApp", value_name="Version", min_version="14.30",
        ))

    def test_enum_subkeys_mode_uses_highest_subkey_version(self):
        self.fake_reg.set_hklm("Software\\SomeApp", {})
        self.fake_reg.set_hklm("Software\\SomeApp\\8.0.1", {})
        self.fake_reg.set_hklm("Software\\SomeApp\\8.0.10", {})
        self.assertTrue(ic._generic_registry_version_check(
            "HKLM", "Software\\SomeApp", enum_subkeys=True, min_version="8.0.5",
        ))

    def test_enum_subkeys_mode_below_min_version(self):
        self.fake_reg.set_hklm("Software\\SomeApp", {})
        self.fake_reg.set_hklm("Software\\SomeApp\\7.0.0", {})
        self.assertFalse(ic._generic_registry_version_check(
            "HKLM", "Software\\SomeApp", enum_subkeys=True, min_version="8.0.0",
        ))

    def test_enum_subkeys_mode_no_subkeys_returns_false(self):
        self.fake_reg.set_hklm("Software\\SomeApp", {})
        self.assertFalse(ic._generic_registry_version_check(
            "HKLM", "Software\\SomeApp", enum_subkeys=True, min_version="1.0",
        ))


class TestCheckVcredistX64VersionAware(unittest.TestCase):
    def setUp(self):
        self.fake_reg = FakeWinReg()
        self.patcher = mock.patch.dict(sys.modules, {"winreg": self.fake_reg})
        self.patcher.start()
        self.path = "SOFTWARE\\Microsoft\\VisualStudio\\14.0\\VC\\Runtimes\\x64"

    def tearDown(self):
        self.patcher.stop()

    def test_installed_flag_missing_is_false_even_without_min_version(self):
        self.assertFalse(ic._check_vcredist_x64())

    def test_no_min_version_only_checks_installed_flag(self):
        self.fake_reg.set_hklm(self.path, {"Installed": 1})
        self.assertTrue(ic._check_vcredist_x64())

    def test_min_version_met(self):
        self.fake_reg.set_hklm(self.path, {"Installed": 1, "Version": "14.38.33135"})
        self.assertTrue(ic._check_vcredist_x64(min_version="14.30"))

    def test_min_version_not_met(self):
        self.fake_reg.set_hklm(self.path, {"Installed": 1, "Version": "14.20.0"})
        self.assertFalse(ic._check_vcredist_x64(min_version="14.30"))


class TestCheckDotnetDesktopVersionAware(unittest.TestCase):
    def setUp(self):
        self.fake_reg = FakeWinReg()
        self.patcher = mock.patch.dict(sys.modules, {"winreg": self.fake_reg})
        self.patcher.start()
        self.path = "SOFTWARE\\WOW6432Node\\dotnet\\Setup\\InstalledVersions\\x64\\sharedfx\\Microsoft.WindowsDesktop.App"

    def tearDown(self):
        self.patcher.stop()

    def test_no_min_version_true_when_any_version_subkey_present(self):
        self.fake_reg.set_hklm(self.path, {})
        self.fake_reg.set_hklm(self.path + "\\8.0.10", {})
        self.assertTrue(ic._check_dotnet_desktop())

    def test_min_version_met(self):
        self.fake_reg.set_hklm(self.path, {})
        self.fake_reg.set_hklm(self.path + "\\8.0.10", {})
        self.assertTrue(ic._check_dotnet_desktop(min_version="8.0.0"))

    def test_min_version_not_met(self):
        self.fake_reg.set_hklm(self.path, {})
        self.fake_reg.set_hklm(self.path + "\\6.0.0", {})
        self.assertFalse(ic._check_dotnet_desktop(min_version="8.0.0"))


class TestBuildDependencyCheckersMinVersion(unittest.TestCase):
    """dependencies_min_version packaging 欄位有設定時，_build_dependency_checkers()
    要把對應的最低版本綁進內建 checker；沒設定的 key 維持原本零參數呼叫，
    不會因為改動而讓既有（可能被測試 patch 成零參數 lambda 的）checker 爆炸。"""

    def setUp(self):
        self.fake_reg = FakeWinReg()
        self.patcher = mock.patch.dict(sys.modules, {"winreg": self.fake_reg})
        self.patcher.start()
        self.path = "SOFTWARE\\Microsoft\\VisualStudio\\14.0\\VC\\Runtimes\\x64"

    def tearDown(self):
        self.patcher.stop()

    def test_no_min_version_configured_calls_checker_with_no_args(self):
        api = make_installer_api(dependencies=["vcredist_x64"])
        with mock.patch.dict(
            ic.DEPENDENCY_CHECKERS,
            {"vcredist_x64": (lambda: True, "VC++", "https://example.test/vc.exe", ["/quiet"])},
        ):
            checkers = api._build_dependency_checkers()
            self.assertTrue(checkers["vcredist_x64"][0]())

    def test_min_version_configured_is_bound_into_builtin_checker(self):
        self.fake_reg.set_hklm(self.path, {"Installed": 1, "Version": "14.20.0"})
        api = make_installer_api(dependencies=["vcredist_x64"], dependencies_min_version={"vcredist_x64": "14.30"})
        checkers = api._build_dependency_checkers()
        self.assertFalse(checkers["vcredist_x64"][0]())

        self.fake_reg.set_hklm(self.path, {"Installed": 1, "Version": "14.38.0"})
        checkers = api._build_dependency_checkers()
        self.assertTrue(checkers["vcredist_x64"][0]())


class TestCustomDependencies(unittest.TestCase):
    """custom_dependencies：讓封裝者自訂相依元件，不再侷限於內建的
    vcredist_x64/dotnet_desktop。"""

    def setUp(self):
        self.fake_reg = FakeWinReg()
        self.patcher = mock.patch.dict(sys.modules, {"winreg": self.fake_reg})
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_custom_dependency_appears_in_warnings_when_missing(self):
        api = make_installer_api(
            dependencies=["my_runtime"],
            custom_dependencies=[{
                "key": "my_runtime",
                "display_name": "My Runtime",
                "download_url": "https://example.test/my_runtime.exe",
                "silent_args": ["/quiet"],
                "registry_check": {"hive": "HKLM", "path": "Software\\MyRuntime", "value_name": "Installed", "expected": 1},
            }],
        )
        warnings = api.get_dependency_warnings()
        self.assertEqual(warnings, [{
            "key": "my_runtime", "name": "My Runtime", "url": "https://example.test/my_runtime.exe",
        }])

    def test_custom_dependency_no_warning_when_installed(self):
        self.fake_reg.set_hklm("Software\\MyRuntime", {"Installed": 1})
        api = make_installer_api(
            dependencies=["my_runtime"],
            custom_dependencies=[{
                "key": "my_runtime",
                "display_name": "My Runtime",
                "download_url": "https://example.test/my_runtime.exe",
                "silent_args": ["/quiet"],
                "registry_check": {"hive": "HKLM", "path": "Software\\MyRuntime", "value_name": "Installed", "expected": 1},
            }],
        )
        self.assertEqual(api.get_dependency_warnings(), [])

    def test_built_in_dependencies_still_work_alongside_custom(self):
        """自訂清單不能把內建的 vcredist_x64/dotnet_desktop 擠掉。"""
        api = make_installer_api(
            dependencies=["vcredist_x64"],
            custom_dependencies=[{
                "key": "my_runtime", "display_name": "My Runtime",
                "download_url": "https://example.test/x.exe", "silent_args": [],
                "registry_check": {"hive": "HKLM", "path": "Software\\X"},
            }],
        )
        with mock.patch.dict(
            ic.DEPENDENCY_CHECKERS,
            {"vcredist_x64": (lambda: False, "Visual C++ Redistributable (x64)", "https://example.test/vc.exe", ["/quiet"])},
        ):
            warnings = api.get_dependency_warnings()
        self.assertEqual(warnings, [{
            "key": "vcredist_x64", "name": "Visual C++ Redistributable (x64)", "url": "https://example.test/vc.exe",
        }])

    def test_custom_dependency_min_version_met(self):
        self.fake_reg.set_hklm("Software\\MyRuntime", {"Version": "2.5.0"})
        api = make_installer_api(
            dependencies=["my_runtime"],
            custom_dependencies=[{
                "key": "my_runtime", "display_name": "My Runtime",
                "download_url": "https://example.test/my_runtime.exe", "silent_args": [],
                "registry_check": {
                    "hive": "HKLM", "path": "Software\\MyRuntime",
                    "value_name": "Version", "min_version": "2.0.0",
                },
            }],
        )
        self.assertEqual(api.get_dependency_warnings(), [])

    def test_custom_dependency_min_version_not_met(self):
        self.fake_reg.set_hklm("Software\\MyRuntime", {"Version": "1.0.0"})
        api = make_installer_api(
            dependencies=["my_runtime"],
            custom_dependencies=[{
                "key": "my_runtime", "display_name": "My Runtime",
                "download_url": "https://example.test/my_runtime.exe", "silent_args": [],
                "registry_check": {
                    "hive": "HKLM", "path": "Software\\MyRuntime",
                    "value_name": "Version", "min_version": "2.0.0",
                },
            }],
        )
        warnings = api.get_dependency_warnings()
        self.assertEqual(warnings, [{
            "key": "my_runtime", "name": "My Runtime", "url": "https://example.test/my_runtime.exe",
        }])


class TestBundleDependencies(unittest.TestCase):
    """bundle_dependencies：打包時內嵌的相依元件安裝檔，install_dependency()
    要優先用內嵌檔案，不要再連網下載。"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_uses_bundled_file_instead_of_downloading(self):
        dep_dir = os.path.join(self.tmp_dir, "dependencies")
        os.makedirs(dep_dir)
        bundled_path = os.path.join(dep_dir, "fake_dep.exe")
        with open(bundled_path, "wb") as f:
            f.write(b"fake bundled installer")

        api = make_installer_api(bundle_dependencies=["fake_dep"])
        with mock.patch("installer_core.get_resource_path", side_effect=lambda rel: os.path.join(self.tmp_dir, rel)), \
             mock.patch.dict(ic.DEPENDENCY_CHECKERS, {"fake_dep": (lambda: True, "Fake Dep", "https://example.test/fake.exe", ["/quiet"])}), \
             mock.patch("installer_core.urllib.request.urlopen") as mock_urlopen, \
             mock.patch("installer_core.subprocess.run", return_value=mock.Mock(returncode=0)) as mock_run:
            result = api.install_dependency("fake_dep")
        mock_urlopen.assert_not_called()
        mock_run.assert_called_once_with(
            [bundled_path, "/quiet"], creationflags=mock.ANY, timeout=600,
        )
        self.assertEqual(result, {"status": "success", "name": "Fake Dep"})
        self.assertTrue(os.path.exists(bundled_path), "內嵌檔案不是我們下載的暫存檔，不該被刪除")

    def test_falls_back_to_download_when_bundled_file_missing(self):
        api = make_installer_api(bundle_dependencies=["fake_dep"])
        response = mock.MagicMock()
        response.__enter__ = mock.Mock(return_value=response)
        response.__exit__ = mock.Mock(return_value=False)
        response.getheader.return_value = None
        response.read.side_effect = [b"data", b""]
        with mock.patch("installer_core.get_resource_path", side_effect=lambda rel: os.path.join(self.tmp_dir, rel)), \
             mock.patch.dict(ic.DEPENDENCY_CHECKERS, {"fake_dep": (lambda: True, "Fake Dep", "https://example.test/fake.exe", ["/quiet"])}), \
             mock.patch("installer_core.urllib.request.urlopen", return_value=response) as mock_urlopen, \
             mock.patch("installer_core.subprocess.run", return_value=mock.Mock(returncode=0)):
            result = api.install_dependency("fake_dep")
        mock_urlopen.assert_called_once()
        self.assertEqual(result["status"], "success")


class TestComputeDefaultPath(unittest.TestCase):
    """_compute_default_path()：custom_install_dir 有值時優先套用（展開
    %APPDATA% 這類環境變數，讓自訂路徑照使用者電腦當下的環境變數解析，
    不是打包當下開發者電腦的值），沒有自訂就照 no_admin_install 決定
    Program Files 還是 %LOCALAPPDATA%\\Programs\\<folder>——跟既有的
    _scope property 一樣，故意寫成方法而不是只在 __init__ 算一次，因為
    make_installer_api(**overrides) 這個測試 helper 是在建構完成後才用
    setattr 覆蓋屬性。"""

    def test_custom_install_dir_expands_env_vars(self):
        api = make_installer_api(
            custom_install_dir="%TESTVAR%\\MyApp", app_name="MyApp", no_admin_install=False,
        )
        with mock.patch.dict(os.environ, {"TESTVAR": "C:\\CustomRoot"}):
            self.assertEqual(api._compute_default_path(), "C:\\CustomRoot\\MyApp")

    def test_custom_install_dir_overrides_no_admin_install_choice(self):
        api = make_installer_api(
            custom_install_dir="C:\\FixedPath", app_name="MyApp", no_admin_install=True,
        )
        self.assertEqual(api._compute_default_path(), "C:\\FixedPath")

    def test_empty_custom_install_dir_falls_back_to_program_files(self):
        api = make_installer_api(custom_install_dir="", app_name="MyApp", no_admin_install=False)
        with mock.patch.dict(os.environ, {"ProgramFiles": "C:\\Program Files"}):
            self.assertEqual(api._compute_default_path(), "C:\\Program Files\\MyApp")

    def test_empty_custom_install_dir_falls_back_to_local_appdata_when_no_admin(self):
        api = make_installer_api(
            custom_install_dir="", app_name="MyApp", folder_name="MyApp", no_admin_install=True,
        )
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "C:\\Users\\Tester\\AppData\\Local"}):
            self.assertEqual(
                api._compute_default_path(),
                os.path.join("C:\\Users\\Tester\\AppData\\Local", "Programs", "MyApp"),
            )


class TestNoAdminInstall(unittest.TestCase):
    """no_admin_install：免系統管理員權限（per-user）安裝模式，登錄表/PATH/
    捷徑都要改寫到使用者層級，而不是系統層級。"""

    def setUp(self):
        self.fake_reg = FakeWinReg()
        self.patcher = mock.patch.dict(sys.modules, {"winreg": self.fake_reg})
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_register_uninstall_entry_uses_hkcu_when_no_admin(self):
        api = make_installer_api(
            no_admin_install=True, app_name="MyApp", selected_path="C:\\Fake\\MyApp",
            main_exe="app.exe",
        )
        with mock.patch.object(api, "_required_size", return_value=0):
            api._register_uninstall_entry()
        reg_path = "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\MyApp"
        self.assertIsNotNone(self.fake_reg.hkcu(reg_path))
        self.assertIsNone(self.fake_reg.hklm(reg_path))

    def test_register_uninstall_entry_uses_hklm_by_default(self):
        api = make_installer_api(
            no_admin_install=False, app_name="MyApp", selected_path="C:\\Fake\\MyApp",
            main_exe="app.exe",
        )
        with mock.patch.object(api, "_required_size", return_value=0):
            api._register_uninstall_entry()
        reg_path = "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\MyApp"
        self.assertIsNotNone(self.fake_reg.hklm(reg_path))
        self.assertIsNone(self.fake_reg.hkcu(reg_path))

    def test_add_to_path_uses_hkcu_environment_when_no_admin(self):
        self.fake_reg.set_hkcu("Environment", {})
        api = make_installer_api(no_admin_install=True, selected_path="C:\\Apps\\MyApp")
        with mock.patch("installer_core.ctypes.windll.user32.SendMessageTimeoutW"):
            api._add_to_path_env()
        self.assertEqual(self.fake_reg.hkcu("Environment")["Path"], "C:\\Apps\\MyApp")

    def test_check_existing_install_reads_hkcu_when_no_admin(self):
        self.fake_reg.set_hkcu(
            "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\MyApp",
            {"InstallLocation": "C:\\Apps\\Old", "DisplayVersion": "1.0.0"},
        )
        api = make_installer_api(no_admin_install=True, app_name="MyApp", version="1.0.0")
        result = api.check_existing_install()
        self.assertTrue(result["exists"])
        self.assertTrue(result["is_same"])


class TestInstallScriptHook(unittest.TestCase):
    """_run_install_script()：pre/post-install 自訂腳本。"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_missing_script_is_a_no_op(self):
        api = make_installer_api()
        with mock.patch("installer_core.get_resource_path", return_value="C:\\does\\not\\exist.bat"):
            ok, msg = api._run_install_script("pre_install_script.bat")
        self.assertTrue(ok)
        self.assertEqual(msg, "")

    def test_empty_script_field_is_a_no_op(self):
        api = make_installer_api()
        ok, msg = api._run_install_script("")
        self.assertTrue(ok)

    def test_success_returns_ok(self):
        script_path = os.path.join(self.tmp_dir, "pre_install_script.bat")
        with open(script_path, "w") as f:
            f.write("@echo off")
        api = make_installer_api()
        with mock.patch("installer_core.get_resource_path", return_value=script_path), \
             mock.patch("installer_core.subprocess.run", return_value=mock.Mock(returncode=0, stdout="", stderr="")):
            ok, msg = api._run_install_script("pre_install_script.bat")
        self.assertTrue(ok)

    def test_nonzero_exit_code_returns_failure_with_message(self):
        script_path = os.path.join(self.tmp_dir, "pre_install_script.bat")
        with open(script_path, "w") as f:
            f.write("@echo off")
        api = make_installer_api()
        with mock.patch("installer_core.get_resource_path", return_value=script_path), \
             mock.patch("installer_core.subprocess.run", return_value=mock.Mock(returncode=1, stdout="oops", stderr="")):
            ok, msg = api._run_install_script("pre_install_script.bat")
        self.assertFalse(ok)
        self.assertIn("oops", msg)

    def test_exception_returns_failure(self):
        script_path = os.path.join(self.tmp_dir, "pre_install_script.bat")
        with open(script_path, "w") as f:
            f.write("@echo off")
        api = make_installer_api()
        with mock.patch("installer_core.get_resource_path", return_value=script_path), \
             mock.patch("installer_core.subprocess.run", side_effect=OSError("boom")):
            ok, msg = api._run_install_script("pre_install_script.bat")
        self.assertFalse(ok)
        self.assertIn("boom", msg)


class TestSilentInstallLogPath(unittest.TestCase):
    """/LOG= 指定靜默安裝紀錄檔路徑，寫入失敗要 fallback 回 %TEMP%。"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_writes_to_custom_log_path(self):
        log_path = os.path.join(self.tmp_dir, "custom", "install.log")
        api_result = {"status": "success", "message": "安裝成功"}
        with mock.patch("installer_core.InstallerAPI") as MockAPI, \
             mock.patch("installer_core._acquire_single_instance_lock", return_value=(True, None)):
            instance = MockAPI.return_value
            instance.app_name = "MyApp"
            instance.check_existing_install.return_value = {"exists": False}
            instance.get_dependency_warnings.return_value = []
            instance.trigger_installation.return_value = api_result
            exit_code = ic.run_silent_install(log_path=log_path)
        self.assertEqual(exit_code, 0)
        self.assertTrue(os.path.exists(log_path))
        with open(log_path, encoding="utf-8") as f:
            self.assertIn("成功", f.read())

    def test_falls_back_to_temp_when_custom_path_unwritable(self):
        bogus_path = "Z:\\definitely\\not\\a\\real\\drive\\install.log"
        with mock.patch("installer_core.InstallerAPI") as MockAPI, \
             mock.patch("installer_core._acquire_single_instance_lock", return_value=(True, None)):
            instance = MockAPI.return_value
            instance.app_name = "FallbackApp"
            instance.check_existing_install.return_value = {"exists": False}
            instance.get_dependency_warnings.return_value = []
            instance.trigger_installation.return_value = {"status": "success", "message": "ok"}
            exit_code = ic.run_silent_install(log_path=bogus_path)
        self.assertEqual(exit_code, 0)
        fallback_path = os.path.join(tempfile.gettempdir(), "FallbackApp_silent_install_log.txt")
        self.assertTrue(os.path.exists(fallback_path))
        os.remove(fallback_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
