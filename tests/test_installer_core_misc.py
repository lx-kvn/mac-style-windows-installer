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

    def tearDown(self):
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
