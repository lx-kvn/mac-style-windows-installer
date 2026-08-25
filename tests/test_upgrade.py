"""upgrade.py 的測試（從 tests/test_installer_core_misc.py 搬過來——見
該檔案的異動說明）。

InstallerAPI.check_existing_install()/run_upgrade_uninstall()/
close_window() 對這個模組的委派仍留在 test_installer_core_misc.py 測
（TestCloseWindowRestoresPendingBackup / TestTriggerInstallationUpgradeFlow /
TestProcessRunningDetection），屬於整合層級；這裡測 UpgradeCoordinator
本身的行為，不需要建構 InstallerAPI。
"""
import os
import sys
import shutil
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _fakes import FakeWinReg
from install_scope import InstallScope
import upgrade


def _scope(no_admin_install=False):
    return InstallScope(no_admin_install)


class TestCheckExisting(unittest.TestCase):
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
        result = upgrade.check_existing("NeverInstalled", "1.0.0", _scope())
        self.assertEqual(result, {"exists": False})

    def test_upgrade_scenario_is_newer(self):
        self._seed_existing("MyApp", "1.0.0")
        result = upgrade.check_existing("MyApp", "2.0.0", _scope())
        self.assertTrue(result["exists"])
        self.assertTrue(result["is_newer"])
        self.assertFalse(result["is_same"])
        self.assertFalse(result["is_older"])

    def test_downgrade_scenario_is_older(self):
        """本機已安裝的版本比這次要裝的新：這次要裝的版本比較舊。"""
        self._seed_existing("MyApp", "3.0.0")
        result = upgrade.check_existing("MyApp", "1.0.0", _scope())
        self.assertFalse(result["is_newer"])
        self.assertFalse(result["is_same"])
        self.assertTrue(result["is_older"])

    def test_same_version_is_same(self):
        """相同版本重裝：不該被誤判成「有更新可以裝」，也不是「比較舊」。"""
        self._seed_existing("MyApp", "1.0.0")
        result = upgrade.check_existing("MyApp", "1.0.0", _scope())
        self.assertFalse(result["is_newer"])
        self.assertTrue(result["is_same"])
        self.assertFalse(result["is_older"])

    def test_finds_existing_install_in_hklm_even_when_current_settings_use_hkcu(self):
        """真實抓到的 bug：舊版本用預設設定（需要管理員權限）裝在
        Program Files、登錄表寫在 HKLM，這次改用 no_admin_install=True
        重新打包，只查 HKCU 會完全找不到舊版本、誤判成「沒裝過」，跳過
        「是否要更新」的提示。改成兩邊都查，找到的話額外回報是在哪個
        hive 找到的，供 run() 判斷要不要跨 UAC 呼叫。"""
        self._seed_existing("MyApp", "1.0.0")  # 寫在 HKLM
        result = upgrade.check_existing("MyApp", "2.0.0", _scope(no_admin_install=True))
        self.assertTrue(result["exists"])
        self.assertEqual(result["hive"], "HKLM")

    def test_missing_display_version_is_not_treated_as_not_installed(self):
        """真實抓到的問題（B13）：登錄表項目缺 DisplayVersion 這個值
        （手動建立的項目、舊版打包工具留下的、或損毀的登錄表）時，
        QueryValueEx() 拋出的 FileNotFoundError 原本被最外層那個 bare
        except 一律當成「這個 hive 沒有這個項目」處理，換下一個 hive
        試，兩邊都試完就回報「沒裝過」——明明 InstallLocation 都還在，
        只是版本號讀不到，卻整個升級偵測被跳過，新舊兩份安裝並存。"""
        reg_path = "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\MyApp"
        self.fake_reg.set_hklm(reg_path, {"InstallLocation": "C:\\Apps\\Old"})  # 沒有 DisplayVersion
        result = upgrade.check_existing("MyApp", "2.0.0", _scope())
        self.assertTrue(result["exists"])
        self.assertEqual(result["install_path"], "C:\\Apps\\Old")

    def test_finds_existing_install_in_hkcu_even_when_current_settings_use_hklm(self):
        reg_path = "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\MyApp"
        self.fake_reg.set_hkcu(reg_path, {"InstallLocation": "C:\\Users\\Tester\\AppData\\Local\\Programs\\MyApp", "DisplayVersion": "1.0.0"})
        result = upgrade.check_existing("MyApp", "2.0.0", _scope(no_admin_install=False))
        self.assertTrue(result["exists"])
        self.assertEqual(result["hive"], "HKCU")

    def test_hive_matches_current_settings_when_only_that_hive_has_a_record(self):
        self._seed_existing("MyApp", "1.0.0")  # 寫在 HKLM
        result = upgrade.check_existing("MyApp", "2.0.0", _scope(no_admin_install=False))
        self.assertTrue(result["exists"])
        self.assertEqual(result["hive"], "HKLM")


class TestUpgradeCoordinatorBackup(unittest.TestCase):
    """backup() / restore_backup() / discard_backup() 三個方法本身：更新
    覆蓋安裝時，刪除舊版本前先備份，取消或安裝失敗時把備份搬回原位，成功
    時清掉備份。"""

    def setUp(self):
        self.old_install_dir = tempfile.mkdtemp()
        with open(os.path.join(self.old_install_dir, "app.exe"), "w") as f:
            f.write("舊版本")
        self.coord = upgrade.UpgradeCoordinator()

    def tearDown(self):
        shutil.rmtree(self.old_install_dir, ignore_errors=True)
        if self.coord.backup_path and os.path.exists(self.coord.backup_path):
            shutil.rmtree(self.coord.backup_path, ignore_errors=True)

    def test_backup_copies_install_dir_to_temp(self):
        backup_path = self.coord.backup(self.old_install_dir)
        self.assertIsNotNone(backup_path)
        self.assertTrue(os.path.exists(os.path.join(backup_path, "app.exe")))

    def test_backup_returns_none_when_source_missing(self):
        backup_path = self.coord.backup("C:\\不存在的資料夾\\Nope")
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
            backup_path = self.coord.backup(self.old_install_dir)
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
        with mock.patch("upgrade.tempfile.gettempdir", return_value=self.old_install_dir):
            backup_path = self.coord.backup(self.old_install_dir)
        self.assertIsNone(backup_path)

    def test_restore_moves_backup_back_to_original_path(self):
        backup_path = self.coord.backup(self.old_install_dir)
        self.coord.backup_path = backup_path
        self.coord.backup_original_path = self.old_install_dir
        shutil.rmtree(self.old_install_dir)  # 模擬 uninstall.exe 已經把舊資料夾刪了

        self.coord.restore_backup()

        self.assertTrue(os.path.exists(os.path.join(self.old_install_dir, "app.exe")), "備份應該搬回原位")
        self.assertIsNone(self.coord.backup_path)
        self.assertIsNone(self.coord.backup_original_path)

    def test_restore_is_noop_when_no_backup_pending(self):
        self.coord.backup_path = None
        self.coord.restore_backup()  # 不應該拋例外
        self.assertIsNone(self.coord.backup_path)

    def test_discard_removes_backup_folder(self):
        backup_path = self.coord.backup(self.old_install_dir)
        self.coord.backup_path = backup_path
        self.coord.backup_original_path = self.old_install_dir

        self.coord.discard_backup()

        self.assertFalse(os.path.exists(backup_path))
        self.assertIsNone(self.coord.backup_path)


class TestUpgradeCoordinatorRun(unittest.TestCase):
    """run()：靜默呼叫舊版 uninstall.exe 前先備份，失敗時復原備份。這個
    方法現在只在 InstallerAPI.trigger_installation() 內部被呼叫（使用者
    拖曳圖示觸發安裝之後），不再由前端在按下確認彈窗當下直接呼叫。"""

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
        # Program Files 路徑：wait_for_path_writable() 遇到真的沒有
        # 寫入權限的路徑會重試到逾時，不能讓測試環境本身的權限狀態影響測試。
        self.new_install_dir = tempfile.mkdtemp()
        shutil.rmtree(self.new_install_dir)  # 讓 os.makedirs() 有東西可以建立
        self.coord = upgrade.UpgradeCoordinator()
        # 這個 class 底下的測試關心的是備份/復原、命令列參數傳遞這些跟提權
        # 無關的行為，一律視為「目前這個行程本身已經是提權的」，走既有的
        # subprocess.run() 路徑——是否要跨 UAC 呼叫的判斷邏輯本身另外在
        # TestUpgradeCoordinatorRunElevation 測。
        self.elevated_patcher = mock.patch.object(self.coord, "is_current_process_elevated", return_value=True)
        self.elevated_patcher.start()

    def tearDown(self):
        self.elevated_patcher.stop()
        self.patcher.stop()
        shutil.rmtree(self.old_install_dir, ignore_errors=True)
        shutil.rmtree(self.new_install_dir, ignore_errors=True)
        if self.coord.backup_path and os.path.exists(self.coord.backup_path):
            shutil.rmtree(self.coord.backup_path, ignore_errors=True)

    def _run(self, restart_explorer_on_update=False):
        return self.coord.run("MyApp", "2.0.0", _scope(), self.new_install_dir, restart_explorer_on_update)

    def test_backs_up_before_calling_uninstall_exe(self):
        call_order = []

        def fake_backup(install_path):
            call_order.append("backup")
            return "C:\\FakeBackup"

        def fake_subprocess_run(*args, **kwargs):
            call_order.append("uninstall_exe")
            return mock.Mock(returncode=0)

        with mock.patch.object(self.coord, "backup", side_effect=fake_backup), \
             mock.patch("upgrade.subprocess.run", side_effect=fake_subprocess_run):
            result = self._run()

        self.assertEqual(result["status"], "success")
        self.assertEqual(call_order, ["backup", "uninstall_exe"], "必須先備份，才能靜默移除舊版本")
        self.assertTrue(os.path.exists(self.new_install_dir), "確認目標路徑真的等到可以建立")

    def test_restores_backup_when_uninstall_exe_fails(self):
        with open(os.path.join(self.old_install_dir, "extra.txt"), "w") as f:
            f.write("舊資料")

        with mock.patch("upgrade.subprocess.run", side_effect=RuntimeError("模擬失敗")):
            result = self._run()

        self.assertEqual(result["status"], "error")
        self.assertTrue(os.path.exists(os.path.join(self.old_install_dir, "extra.txt")), "備份應該被復原回原位")
        self.assertIsNone(self.coord.backup_path)

    def test_passes_restart_explorer_flag_to_old_uninstall_exe_when_enabled(self):
        """真實抓到的 bug：這裡呼叫的是舊版本的 uninstall.exe，它是否關閉檔案
        總管原本只看它自己那份（可能過期的）install_manifest.json，跟使用者
        這次重新打包的新設定是兩回事，導致行為時好時壞。修正後這次的設定要
        透過命令列參數明確傳給舊版 uninstall.exe，覆蓋掉它自己的 manifest。"""
        captured_cmd = {}

        def fake_subprocess_run(cmd, **kwargs):
            captured_cmd["cmd"] = cmd
            return mock.Mock(returncode=0)

        with mock.patch("upgrade.subprocess.run", side_effect=fake_subprocess_run):
            self._run(restart_explorer_on_update=True)

        self.assertIn("--restart-explorer", captured_cmd["cmd"])

    def test_does_not_pass_restart_explorer_flag_when_disabled(self):
        captured_cmd = {}

        def fake_subprocess_run(cmd, **kwargs):
            captured_cmd["cmd"] = cmd
            return mock.Mock(returncode=0)

        with mock.patch("upgrade.subprocess.run", side_effect=fake_subprocess_run):
            self._run(restart_explorer_on_update=False)

        self.assertNotIn("--restart-explorer", captured_cmd["cmd"])

    def test_nonzero_exit_code_from_old_uninstall_exe_is_reported_as_failure(self):
        """真實抓到的問題（B6）：舊版 uninstall.exe 的結束碼原本完全被
        忽略——subprocess.run() 的回傳值連變數都沒接。uninstall.py 自己
        的慣例是 0=成功、非 0=失敗（見 run_silent_uninstall()），舊版
        uninstall.exe 如果因為檔案被鎖住、manifest 損毀等原因回報失敗，
        這裡完全偵測不到，新版安裝流程會誤以為舊版本已經清乾淨，繼續
        往下覆蓋安裝，實際上舊版本殘留的檔案可能還在。"""
        with open(os.path.join(self.old_install_dir, "extra.txt"), "w") as f:
            f.write("舊資料")

        with mock.patch("upgrade.subprocess.run", return_value=mock.Mock(returncode=1)):
            result = self._run()

        self.assertEqual(result["status"], "error")
        self.assertTrue(os.path.exists(os.path.join(self.old_install_dir, "extra.txt")), "失敗時備份應該被復原回原位")

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
            return mock.Mock(returncode=0)

        with mock.patch("upgrade.subprocess.run", side_effect=fake_subprocess_run):
            self._run()

        self.assertIn("--upgrade", captured_cmd["cmd"])


class TestUpgradeCoordinatorRunElevation(unittest.TestCase):
    """真實抓到的問題：舊版本如果是用需要管理員權限的設定裝的（登錄表寫在
    HKLM），但這次新安裝檔是免權限（no_admin_install=True）執行，直接用
    subprocess.run() 呼叫舊版 uninstall.exe 不會跳 UAC——Windows 的 manifest
    自動提權只有透過 ShellExecute 這條路徑才會生效，subprocess.run() 底層
    是 CreateProcess，會用目前（未提權）的權杖把子行程跑起來，導致寫入
    Program Files/刪除 HKLM 機碼時默默失敗卻不拋例外。改成偵測到這種情境
    時改用 run_uninstall_exe_elevated()（ShellExecuteExW + "runas"）。"""

    def setUp(self):
        self.fake_reg = FakeWinReg()
        self.patcher = mock.patch.dict(sys.modules, {"winreg": self.fake_reg})
        self.patcher.start()
        self.old_install_dir = tempfile.mkdtemp()
        with open(os.path.join(self.old_install_dir, "uninstall.exe"), "w") as f:
            f.write("fake")
        self.new_install_dir = tempfile.mkdtemp()
        shutil.rmtree(self.new_install_dir)
        self.coord = upgrade.UpgradeCoordinator()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.old_install_dir, ignore_errors=True)
        shutil.rmtree(self.new_install_dir, ignore_errors=True)
        if self.coord.backup_path and os.path.exists(self.coord.backup_path):
            shutil.rmtree(self.coord.backup_path, ignore_errors=True)

    def _seed_hklm(self):
        self.fake_reg.set_hklm(r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\MyApp", {
            "InstallLocation": self.old_install_dir, "DisplayVersion": "1.0.0",
        })

    def _seed_hkcu(self):
        self.fake_reg.set_hkcu(r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\MyApp", {
            "InstallLocation": self.old_install_dir, "DisplayVersion": "1.0.0",
        })

    def _run(self, no_admin_install=False):
        return self.coord.run("MyApp", "2.0.0", _scope(no_admin_install), self.new_install_dir, False)

    def test_uses_elevated_call_when_hive_is_hklm_and_current_process_not_elevated(self):
        self._seed_hklm()
        with mock.patch.object(self.coord, "is_current_process_elevated", return_value=False), \
             mock.patch.object(self.coord, "run_uninstall_exe_elevated") as mock_elevated, \
             mock.patch("upgrade.subprocess.run") as mock_run:
            result = self._run()
        self.assertEqual(result["status"], "success")
        mock_elevated.assert_called_once()
        mock_run.assert_not_called()

    def test_uses_subprocess_run_when_hive_is_hkcu(self):
        self._seed_hkcu()
        with mock.patch.object(self.coord, "is_current_process_elevated", return_value=False), \
             mock.patch.object(self.coord, "run_uninstall_exe_elevated") as mock_elevated, \
             mock.patch("upgrade.subprocess.run") as mock_run:
            self._run(no_admin_install=True)
        mock_run.assert_called_once()
        mock_elevated.assert_not_called()

    def test_uses_subprocess_run_when_current_process_already_elevated(self):
        self._seed_hklm()
        with mock.patch.object(self.coord, "is_current_process_elevated", return_value=True), \
             mock.patch.object(self.coord, "run_uninstall_exe_elevated") as mock_elevated, \
             mock.patch("upgrade.subprocess.run") as mock_run:
            self._run()
        mock_run.assert_called_once()
        mock_elevated.assert_not_called()

    def test_refuses_to_run_uninstall_exe_from_hkcu_when_current_process_is_elevated(self):
        """真實抓到的安全性問題：HKCU 是一般使用者身分就寫得進去的登錄表
        位置。如果目前這個安裝程式行程已經持有系統管理員權杖，執行從 HKCU
        找到的 uninstall.exe——不管是透過 subprocess.run 還是
        ShellExecute，子行程都會繼承呼叫端目前的權杖——等於讓任何能以
        同一個使用者身分寫入 HKCU 的人，都能讓這支已提權的安裝程式代為
        執行任意程式碼，還帶著系統管理員權限。這個組合（hive=HKCU 且目前
        行程已經是提權狀態）必須直接拒絕自動執行，改成請使用者自行移除
        舊版本，不能像其他情境一樣靜默呼叫。"""
        self._seed_hkcu()
        with mock.patch.object(self.coord, "is_current_process_elevated", return_value=True), \
             mock.patch.object(self.coord, "run_uninstall_exe_elevated") as mock_elevated, \
             mock.patch("upgrade.subprocess.run") as mock_run:
            result = self._run()

        self.assertEqual(result["status"], "error")
        mock_run.assert_not_called()
        mock_elevated.assert_not_called()

    def test_still_runs_uninstall_exe_from_hkcu_when_current_process_not_elevated(self):
        """對照組：hive=HKCU 但目前行程本身沒有提權時，沒有權限差異、沒有
        風險（跟一般使用者身分下的正常重新安裝完全一樣），這個組合原本就
        該正常執行，不該被上面那條新增的圍堵規則誤傷。"""
        self._seed_hkcu()
        with mock.patch.object(self.coord, "is_current_process_elevated", return_value=False), \
             mock.patch.object(self.coord, "run_uninstall_exe_elevated") as mock_elevated, \
             mock.patch("upgrade.subprocess.run", return_value=mock.Mock(returncode=0)) as mock_run:
            result = self._run(no_admin_install=True)

        self.assertEqual(result["status"], "success")
        mock_run.assert_called_once()
        mock_elevated.assert_not_called()


class TestRunUninstallExeElevatedSeam(unittest.TestCase):
    """run_uninstall_exe_elevated() 的 shell32=/kernel32= 選填注入點：跟
    file_assoc.py/system_entries.py 的 registry= 是同一種 seam 模式——
    預設用真正的 ctypes.windll.shell32/kernel32，測試可以換成假的
    「提權後行程」adapter，不用透過 mock.patch 改寫 ctypes.windll 這個
    行程全域共用物件的屬性。真實 UAC 互動本身仍然沒辦法在開發環境重現，
    這個 seam 只讓「成功／逾時／非 0 回傳」這幾條分支變得可測。"""

    def setUp(self):
        self.coord = upgrade.UpgradeCoordinator()

    def _fake_shell32(self, ok=1, hprocess=12345):
        shell32 = mock.Mock()

        def fake_shell_execute(sei_ptr):
            sei_ptr.contents.hProcess = hprocess
            return ok
        shell32.ShellExecuteExW.side_effect = fake_shell_execute
        return shell32

    def _fake_kernel32(self, wait_result=0, exit_code=0):
        kernel32 = mock.Mock()
        kernel32.WaitForSingleObject.return_value = wait_result

        def fake_get_exit_code(handle, exit_code_ptr):
            exit_code_ptr.contents.value = exit_code
            return 1
        kernel32.GetExitCodeProcess.side_effect = fake_get_exit_code
        return kernel32

    def test_success_via_injected_fake_adapters_without_touching_ctypes_windll(self):
        shell32 = self._fake_shell32()
        kernel32 = self._fake_kernel32()
        self.coord.run_uninstall_exe_elevated(
            "C:\\App\\uninstall.exe", ["--silent"], shell32=shell32, kernel32=kernel32,
        )
        shell32.ShellExecuteExW.assert_called_once()
        kernel32.CloseHandle.assert_called_once_with(12345)

    def test_injected_fake_reporting_nonzero_exit_code_still_raises(self):
        shell32 = self._fake_shell32()
        kernel32 = self._fake_kernel32(exit_code=1)
        with self.assertRaises(Exception):
            self.coord.run_uninstall_exe_elevated(
                "C:\\App\\uninstall.exe", ["--silent"], shell32=shell32, kernel32=kernel32,
            )

    def test_omitting_shell32_kernel32_falls_back_to_real_ctypes_windll(self):
        """沒有注入時，行為要跟原本一樣去打真正的 ctypes.windll——保留
        既有 mock.patch("upgrade.ctypes.windll...") 那條測試路徑的相容性。"""
        with mock.patch("upgrade.ctypes.windll.shell32.ShellExecuteExW", return_value=0):
            with self.assertRaises(Exception):
                self.coord.run_uninstall_exe_elevated("C:\\App\\uninstall.exe", ["--silent"])


class TestRunUninstallExeElevated(unittest.TestCase):
    """run_uninstall_exe_elevated()：透過 ShellExecuteExW + "runas" 動詞
    啟動舊版 uninstall.exe 並等待完成，取代 subprocess.run() 在需要跨 UAC
    情境下的角色。"""

    def setUp(self):
        self.coord = upgrade.UpgradeCoordinator()

    def test_raises_when_shell_execute_fails(self):
        """ShellExecuteExW 回傳 0（失敗，例如使用者在 UAC 提示按下取消）
        要讓呼叫端看到明確的例外。"""
        with mock.patch("upgrade.ctypes.windll.shell32.ShellExecuteExW", return_value=0):
            with self.assertRaises(Exception):
                self.coord.run_uninstall_exe_elevated("C:\\App\\uninstall.exe", ["--silent"])

    def _fake_get_exit_code(self, exit_code_value):
        def fake_get_exit_code(handle, exit_code_ptr):
            exit_code_ptr.contents.value = exit_code_value
            return 1
        return fake_get_exit_code

    def test_waits_for_process_and_closes_handle(self):
        WAIT_OBJECT_0 = 0

        def fake_shell_execute(sei_ptr):
            sei_ptr.contents.hProcess = 12345
            return 1

        with mock.patch("upgrade.ctypes.windll.shell32.ShellExecuteExW", side_effect=fake_shell_execute), \
             mock.patch("upgrade.ctypes.windll.kernel32.WaitForSingleObject", return_value=WAIT_OBJECT_0) as mock_wait, \
             mock.patch("upgrade.ctypes.windll.kernel32.GetExitCodeProcess", side_effect=self._fake_get_exit_code(0)), \
             mock.patch("upgrade.ctypes.windll.kernel32.CloseHandle") as mock_close:
            self.coord.run_uninstall_exe_elevated("C:\\App\\uninstall.exe", ["--silent"], timeout_ms=5000)

        mock_wait.assert_called_once()
        self.assertEqual(mock_wait.call_args.args[0], 12345)
        mock_close.assert_called_once_with(12345)

    def test_raises_on_timeout(self):
        WAIT_TIMEOUT = 0x102

        def fake_shell_execute(sei_ptr):
            sei_ptr.contents.hProcess = 12345
            return 1

        with mock.patch("upgrade.ctypes.windll.shell32.ShellExecuteExW", side_effect=fake_shell_execute), \
             mock.patch("upgrade.ctypes.windll.kernel32.WaitForSingleObject", return_value=WAIT_TIMEOUT), \
             mock.patch("upgrade.ctypes.windll.kernel32.CloseHandle") as mock_close:
            with self.assertRaises(Exception):
                self.coord.run_uninstall_exe_elevated("C:\\App\\uninstall.exe", ["--silent"], timeout_ms=100)
        mock_close.assert_called_once_with(12345)  # 逾時也要記得收尾釋放控制代碼

    def test_raises_when_process_exit_code_is_nonzero(self):
        """真實抓到的問題（B6）：舊版 uninstall.exe 透過這條跨 UAC 路徑
        執行時，結束碼原本完全沒有被檢查——WaitForSingleObject 等到行程
        結束就直接視為成功，不管它實際上是不是真的執行成功。"""
        def fake_shell_execute(sei_ptr):
            sei_ptr.contents.hProcess = 12345
            return 1

        with mock.patch("upgrade.ctypes.windll.shell32.ShellExecuteExW", side_effect=fake_shell_execute), \
             mock.patch("upgrade.ctypes.windll.kernel32.WaitForSingleObject", return_value=0), \
             mock.patch("upgrade.ctypes.windll.kernel32.GetExitCodeProcess", side_effect=self._fake_get_exit_code(1)), \
             mock.patch("upgrade.ctypes.windll.kernel32.CloseHandle") as mock_close:
            with self.assertRaises(Exception):
                self.coord.run_uninstall_exe_elevated("C:\\App\\uninstall.exe", ["--silent"])
        mock_close.assert_called_once_with(12345)

    def test_raises_when_process_handle_is_null(self):
        """真實抓到的問題：SEE_MASK_NOCLOSEPROCESS 模式下，如果沒有真的
        產生一個行程（hProcess 是 NULL），WaitForSingleObject(NULL, ...)
        實際上會回傳 WAIT_FAILED，不是原本判斷式唯一認得的 WAIT_TIMEOUT，
        會被誤判成「等待成功」繼續往下跑，而不是明確的錯誤。"""
        def fake_shell_execute(sei_ptr):
            sei_ptr.contents.hProcess = None
            return 1

        with mock.patch("upgrade.ctypes.windll.shell32.ShellExecuteExW", side_effect=fake_shell_execute), \
             mock.patch("upgrade.ctypes.windll.kernel32.WaitForSingleObject") as mock_wait:
            with self.assertRaises(Exception):
                self.coord.run_uninstall_exe_elevated("C:\\App\\uninstall.exe", ["--silent"])
        mock_wait.assert_not_called()


class TestWaitForPathWritable(unittest.TestCase):
    """wait_for_path_writable()：更新覆蓋安裝後，舊版本 uninstall.exe
    背景延遲自我刪除不保證真的跑完，安裝目標路徑可能還卡在 Windows 的
    pending-delete 狀態。這裡驗證用短暫重試取代原本固定 time.sleep() 賭運氣的
    做法：遇到 PermissionError 要重試、真的可以寫入了要立刻停手、逾時也不拋例外。"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        shutil.rmtree(self.tmp_dir)  # 讓 os.makedirs() 有東西可以建立
        self.coord = upgrade.UpgradeCoordinator()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_returns_immediately_when_path_already_writable(self):
        with mock.patch("upgrade.time.sleep") as mock_sleep:
            self.coord.wait_for_path_writable(self.tmp_dir)
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

        with mock.patch("upgrade.os.makedirs", side_effect=flaky_makedirs), \
             mock.patch("upgrade.time.sleep") as mock_sleep:
            self.coord.wait_for_path_writable(self.tmp_dir, timeout=10, interval=0.5)

        self.assertEqual(call_count["n"], 3, "前兩次遇到 PermissionError 應該重試，第三次成功就停手")
        self.assertEqual(mock_sleep.call_count, 2)
        self.assertTrue(os.path.exists(self.tmp_dir))

    def test_gives_up_after_timeout_without_raising(self):
        with mock.patch(
            "upgrade.os.makedirs", side_effect=PermissionError("一直卡住"),
        ), mock.patch("upgrade.time.sleep"):
            self.coord.wait_for_path_writable(self.tmp_dir, timeout=0.01, interval=0.01)
        # 不拋例外，把失敗處理權交還給呼叫端（trigger_installation() 後續會再踢出真正的錯誤）


if __name__ == "__main__":
    unittest.main()
