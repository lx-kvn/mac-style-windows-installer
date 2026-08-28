"""uninstall.py 的測試。

重點覆蓋 uninstall.py 檔案開頭註解記錄的那個真實 bug：清單式刪除（只刪
install_manifest.json 記錄的檔案，保留使用者事後自己在安裝目錄產生的東西）
最後卻用無差別的 rmdir 把整個資料夾砍光，讓前面的細心刪除形同虛設。
現在的正確行為是：清單刪完後，資料夾裡如果還有清單之外的項目就保留資料夾，
真的清空了才連資料夾一起刪。

登錄表操作一樣全程用 tests/_fakes.py 的假 winreg，不會動到真實登錄表。

檔案關聯（remove_file_associations）的登錄表操作已經收斂進 file_assoc.py
的 unregister()，對應測試搬到 tests/test_file_assoc.py，這裡不再重複。
"""
import os
import sys
import json
import shutil
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _fakes import FakeWinReg
import uninstall as un


class TestModuleLevelWindowDefaultsToNone(unittest.TestCase):
    """真實抓到的 bug：跟 installer_core.py 同一個問題——`window` 這個
    模組層級全域變數原本從未被初始化過，_report_progress() 原本能正常
    運作純粹是意外（NameError 被自己的 try/except Exception 吞掉）。
    改用 progress_report.report_progress() 收斂重複程式碼後才暴露出來。"""

    def test_module_level_window_defaults_to_none(self):
        self.assertIsNone(un.window)


class TestPerformUninstallStepsReportsFailures(unittest.TestCase):
    """真實抓到的問題（F20）：登錄表項目/捷徑移除失敗原本完全被忽略——
    remove_registry_entry() 的回傳值只在成功時被拿來 log，失敗時連
    log 都沒有；remove_shortcut() 的回傳值整個被丟棄，不管實際上有沒有
    刪掉都無條件 log 成功。互動式解除安裝可以在 PATH/捷徑/登錄表項目
    全部沒清乾淨的情況下，還是讓使用者看到一個綠色的「解除安裝完成」
    畫面。_perform_uninstall_steps() 現在額外回傳一份失敗項目清單，讓
    呼叫端有機會如實回報「完成，但有 N 項沒清乾淨」，而不是照單全收。
    """

    def _make_ctx(self, current_dir, manifest=None, no_admin_install=False):
        return {
            "app_name": "MyApp",
            "manifest": manifest or {},
            "current_dir": current_dir,
            "no_admin_install": no_admin_install,
        }

    def setUp(self):
        self.current_dir = tempfile.mkdtemp()
        self.self_name = "uninstall.exe"
        with open(os.path.join(self.current_dir, self.self_name), "w") as f:
            f.write("fake")
        self.argv_patcher = mock.patch.object(un.sys, "argv", [os.path.join(self.current_dir, self.self_name)])
        self.argv_patcher.start()

    def tearDown(self):
        self.argv_patcher.stop()
        shutil.rmtree(self.current_dir, ignore_errors=True)

    def test_returns_tuple_with_empty_failures_on_full_success(self):
        ctx = self._make_ctx(self.current_dir, manifest={"files": []})
        with mock.patch("uninstall.remove_registry_entry", return_value=True):
            result = un._perform_uninstall_steps(ctx, [], False, log=lambda m: None)
        self.assertEqual(result, (True, []))

    def test_registry_entry_removal_failure_is_collected(self):
        ctx = self._make_ctx(self.current_dir, manifest={"files": []})
        with mock.patch("uninstall.remove_registry_entry", return_value=False):
            safe_to_remove, failures = un._perform_uninstall_steps(ctx, [], False, log=lambda m: None)
        self.assertTrue(any("登錄表" in f for f in failures))

    def test_shortcut_removal_failure_is_collected(self):
        ctx = self._make_ctx(
            self.current_dir, manifest={"files": [], "start_menu_shortcut": True},
        )
        with mock.patch("uninstall.remove_registry_entry", return_value=True), \
             mock.patch("uninstall.remove_shortcut", return_value=False):
            safe_to_remove, failures = un._perform_uninstall_steps(ctx, [], False, log=lambda m: None)
        self.assertTrue(any("捷徑" in f for f in failures))

    def test_file_association_removal_failure_is_collected(self):
        """F01/F02：這一步原本無條件記錄成功——`file_assoc.unregister()` 不
        回傳值，呼叫端沒有東西可以判斷。同一個函式裡六個移除步驟只有四個
        會被記錄為失敗，檔案關聯與 PATH 兩步是例外。"""
        ctx = self._make_ctx(
            self.current_dir, manifest={"files": [], "file_associations": [".foo"]},
        )
        with mock.patch("uninstall.remove_registry_entry", return_value=True), \
             mock.patch("uninstall.file_assoc.unregister", return_value=False):
            safe_to_remove, failures = un._perform_uninstall_steps(ctx, [], False, log=lambda m: None)
        self.assertTrue(any("檔案關聯" in f for f in failures))

    def test_file_association_removal_success_is_not_collected(self):
        ctx = self._make_ctx(
            self.current_dir, manifest={"files": [], "file_associations": [".foo"]},
        )
        with mock.patch("uninstall.remove_registry_entry", return_value=True), \
             mock.patch("uninstall.file_assoc.unregister", return_value=True):
            safe_to_remove, failures = un._perform_uninstall_steps(ctx, [], False, log=lambda m: None)
        self.assertEqual(failures, [])

    def test_path_removal_failure_is_collected(self):
        ctx = self._make_ctx(
            self.current_dir, manifest={"files": [], "path_added": True},
        )
        with mock.patch("uninstall.remove_registry_entry", return_value=True), \
             mock.patch("uninstall.remove_from_path", return_value=False):
            safe_to_remove, failures = un._perform_uninstall_steps(ctx, [], False, log=lambda m: None)
        self.assertTrue(any("PATH" in f for f in failures))

    def test_path_removal_success_is_not_collected(self):
        ctx = self._make_ctx(
            self.current_dir, manifest={"files": [], "path_added": True},
        )
        with mock.patch("uninstall.remove_registry_entry", return_value=True), \
             mock.patch("uninstall.remove_from_path", return_value=True):
            safe_to_remove, failures = un._perform_uninstall_steps(ctx, [], False, log=lambda m: None)
        self.assertEqual(failures, [])


class TestPerformUninstallStepsReportsDeletionFailures(unittest.TestCase):
    """F03：清單式刪除失敗的檔案原本只寫進紀錄檔的警告行，不併入
    failures，所以不會出現在使用者看到的結果裡。更糟的是接下來判斷「能不能
    整個移除資料夾」時，剩餘項目一律被描述成「可能是使用者自行產生的資料」
    ——剛才刪除失敗的檔案正好也會出現在那份剩餘清單裡，使用者看到的訊息
    與實際原因相反。
    """

    def _make_ctx(self, current_dir, manifest=None):
        return {
            "app_name": "MyApp",
            "manifest": manifest or {},
            "current_dir": current_dir,
            "no_admin_install": False,
        }

    def setUp(self):
        self.current_dir = tempfile.mkdtemp()
        self.self_name = "uninstall.exe"
        with open(os.path.join(self.current_dir, self.self_name), "w") as f:
            f.write("fake")
        self.argv_patcher = mock.patch.object(un.sys, "argv", [os.path.join(self.current_dir, self.self_name)])
        self.argv_patcher.start()

    def tearDown(self):
        self.argv_patcher.stop()
        shutil.rmtree(self.current_dir, ignore_errors=True)

    def _run(self, manifest):
        logs = []
        ctx = self._make_ctx(self.current_dir, manifest=manifest)
        with mock.patch("uninstall.remove_registry_entry", return_value=True):
            safe_to_remove, failures = un._perform_uninstall_steps(ctx, [], False, log=logs.append)
        return safe_to_remove, failures, logs

    def test_file_deletion_failure_is_collected(self):
        with open(os.path.join(self.current_dir, "locked.dll"), "w") as f:
            f.write("被鎖住")
        with mock.patch("uninstall.os.remove", side_effect=PermissionError("檔案被鎖住")):
            _safe, failures, _logs = self._run({"files": ["locked.dll"]})
        self.assertTrue(any("locked.dll" in f for f in failures))

    def test_leftover_from_failed_deletion_is_not_described_as_user_data(self):
        """刪除失敗留下的檔案不能跟使用者自行產生的資料共用同一句措辭。"""
        with open(os.path.join(self.current_dir, "locked.dll"), "w") as f:
            f.write("被鎖住")
        with mock.patch("uninstall.os.remove", side_effect=PermissionError("檔案被鎖住")):
            _safe, _failures, logs = self._run({"files": ["locked.dll"]})
        text = "\n".join(logs)
        self.assertIn("locked.dll", text)
        stuck_lines = [line for line in logs if "locked.dll" in line and "保留資料夾" in line]
        self.assertTrue(stuck_lines, f"應該有一行說明殘留原因，實際紀錄：{logs}")
        self.assertFalse(
            any("使用者自行產生" in line for line in stuck_lines),
            "刪除失敗的檔案被描述成使用者自行產生的資料，訊息與事實相反",
        )

    def test_user_data_leftover_keeps_its_own_wording(self):
        """真的是使用者自行產生的資料時，原本那句措辭要維持不變。"""
        with open(os.path.join(self.current_dir, "app.exe"), "w") as f:
            f.write("app")
        with open(os.path.join(self.current_dir, "user_data.txt"), "w") as f:
            f.write("使用者自己的資料")
        safe_to_remove, failures, logs = self._run({"files": ["app.exe"]})
        self.assertFalse(safe_to_remove)
        self.assertEqual(failures, [])
        self.assertTrue(any("使用者自行產生" in line and "user_data.txt" in line for line in logs))

    def test_local_appdata_leftovers_are_also_reported(self):
        """F03 附帶問題：剩餘項目檢查原本只涵蓋安裝目錄，
        `%LOCALAPPDATA%\\Programs\\<folder_name>` 底下刪剩什麼完全沒有
        對應檢查——那個目錄同樣是安裝流程建立的，殘留同樣該讓使用者知道。
        """
        alt_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, alt_dir, True)
        with open(os.path.join(alt_dir, "cli.exe"), "w") as f:
            f.write("copied")
        with open(os.path.join(alt_dir, "user_cache.bin"), "w") as f:
            f.write("使用者自己的資料")
        _safe, _failures, logs = self._run({
            "files": ["cli.exe"],
            "local_appdata_files": ["cli.exe"],
            "local_appdata_dir": alt_dir,
        })
        self.assertTrue(
            any("user_cache.bin" in line for line in logs),
            f"local_appdata 目錄的殘留項目應該也被回報，實際紀錄：{logs}",
        )


class TestPerformUninstallStepsLockRelease(unittest.TestCase):
    """_perform_uninstall_steps()：需要結束鎖定檔案的程式時，實際的釋放
    邏輯（先關瀏覽視窗、不夠才暫停 AutoRestartShell 強制關殼層）收在
    explorer_lock_release.py，這裡只驗證整合面——有沒有把 current_dir
    當 path 帶進去、有沒有在檔案刪除步驟做完（不管成功或失敗）之後補呼叫
    restore_after_lock_release()。

    真實抓到的 bug：舊版 _kill_explorer() 寫死「一定是 explorer.exe 鎖住」，
    只涵蓋殼層擴充功能這一種情境；後來改用 restart_manager 實際偵測，但
    砍 explorer.exe 這個殼層行程本身還是會讓桌面/工作列全部重啟，而且
    Windows 的 AutoRestartShell 機制會讓它幾乎瞬間自動復活、在檔案操作
    完成前搶著重新鎖住同一個檔案——這是 explorer_lock_release.py 現在
    處理的問題，見該模組的說明。"""

    def _make_ctx(self, current_dir, manifest=None):
        return {
            "app_name": "MyApp",
            "manifest": manifest or {},
            "current_dir": current_dir,
            "no_admin_install": False,
        }

    def setUp(self):
        self.current_dir = tempfile.mkdtemp()
        self.self_name = "uninstall.exe"
        with open(os.path.join(self.current_dir, self.self_name), "w") as f:
            f.write("fake")
        self.argv_patcher = mock.patch.object(un.sys, "argv", [os.path.join(self.current_dir, self.self_name)])
        self.argv_patcher.start()

    def tearDown(self):
        self.argv_patcher.stop()
        shutil.rmtree(self.current_dir, ignore_errors=True)

    def test_passes_current_dir_as_path_and_restores_after_success(self):
        ctx = self._make_ctx(self.current_dir, manifest={"files": []})
        locking_processes = [(111, "Windows 檔案總管")]
        with mock.patch(
            "uninstall.explorer_lock_release.release_locking_processes", return_value=None,
        ) as mock_release, mock.patch(
            "uninstall.explorer_lock_release.restore_after_lock_release",
        ) as mock_restore:
            un._perform_uninstall_steps(ctx, locking_processes, True, log=lambda m: None)

        args, kwargs = mock_release.call_args
        self.assertEqual(args, ([{"pid": 111, "name": "Windows 檔案總管"}],))
        self.assertEqual(kwargs["path"], self.current_dir)
        mock_restore.assert_called_once_with(None)

    def test_does_not_call_release_when_kill_not_requested(self):
        ctx = self._make_ctx(self.current_dir, manifest={"files": []})
        with mock.patch(
            "uninstall.explorer_lock_release.release_locking_processes",
        ) as mock_release, mock.patch(
            "uninstall.explorer_lock_release.restore_after_lock_release",
        ) as mock_restore:
            un._perform_uninstall_steps(ctx, [], True, log=lambda m: None)

        mock_release.assert_not_called()
        mock_restore.assert_called_once_with(None)

    def test_restores_even_when_delete_step_raises(self):
        """刪除清單裡逐一刪檔那段本身有 try/except，個別檔案刪除失敗不會
        往外拋（只記警告 log）；這裡改用清單刪完後「看資料夾裡還剩什麼」
        那一步（沒有包 try/except）製造一個真的會往外拋的未預期例外，
        驗證 finally 還是會補呼叫 restore_after_lock_release()。"""
        ctx = self._make_ctx(self.current_dir, manifest={"files": []})
        locking_processes = [(222, "Windows 檔案總管")]
        fake_state = {"previous_auto_restart_shell": "1"}
        with mock.patch(
            "uninstall.explorer_lock_release.release_locking_processes", return_value=fake_state,
        ), mock.patch(
            "uninstall.explorer_lock_release.restore_after_lock_release",
        ) as mock_restore, mock.patch(
            "uninstall.os.listdir", side_effect=RuntimeError("模擬未預期例外"),
        ):
            with self.assertRaises(RuntimeError):
                un._perform_uninstall_steps(ctx, locking_processes, True, log=lambda m: None)

        mock_restore.assert_called_once_with(fake_state)


class TestPerformUninstallStepsWindowsService(unittest.TestCase):
    """manifest 裡有 windows_service_name 才呼叫 windows_service.remove_service()
    移除對應的 Windows 服務，沒有這個欄位（或空字串）完全不呼叫。"""

    def _make_ctx(self, current_dir, manifest=None):
        return {
            "app_name": "MyApp",
            "manifest": manifest or {},
            "current_dir": current_dir,
            "no_admin_install": False,
        }

    def setUp(self):
        self.current_dir = tempfile.mkdtemp()
        self.self_name = "uninstall.exe"
        with open(os.path.join(self.current_dir, self.self_name), "w") as f:
            f.write("fake")
        self.argv_patcher = mock.patch.object(un.sys, "argv", [os.path.join(self.current_dir, self.self_name)])
        self.argv_patcher.start()

    def tearDown(self):
        self.argv_patcher.stop()
        shutil.rmtree(self.current_dir, ignore_errors=True)

    def test_removes_service_when_manifest_has_service_name(self):
        ctx = self._make_ctx(self.current_dir, manifest={"files": [], "windows_service_name": "MySvc"})
        with mock.patch("uninstall.windows_service.remove_service", return_value=True) as mock_remove:
            un._perform_uninstall_steps(ctx, [], False, log=lambda m: None)

        mock_remove.assert_called_once_with("MySvc")

    def test_does_not_call_remove_service_when_manifest_has_no_service(self):
        ctx = self._make_ctx(self.current_dir, manifest={"files": []})
        with mock.patch("uninstall.windows_service.remove_service") as mock_remove:
            un._perform_uninstall_steps(ctx, [], False, log=lambda m: None)

        mock_remove.assert_not_called()

    def test_does_not_call_remove_service_when_service_name_is_empty_string(self):
        ctx = self._make_ctx(self.current_dir, manifest={"files": [], "windows_service_name": ""})
        with mock.patch("uninstall.windows_service.remove_service") as mock_remove:
            un._perform_uninstall_steps(ctx, [], False, log=lambda m: None)

        mock_remove.assert_not_called()


class TestPerformUninstallStepsFileAssociations(unittest.TestCase):
    """真實抓到的 bug：file_assoc.unregister() 補上 no_admin_install 參數
    （見 file_assoc.py/tests/test_file_assoc.py）之後，這裡的呼叫端一直
    沒有跟著把 ctx["no_admin_install"] 傳進去——預設值 False 剛好跟
    unregister() 原本唯一支援的 HKLM 行為一致，所以沒有立刻爆炸，但
    no_admin_install=True 裝出來的安裝（檔案關聯寫在 HKCU）解除安裝時
    會去清 HKLM，清不到真正寫入的那份，殘留關聯永遠留著。"""

    def _make_ctx(self, current_dir, manifest=None, no_admin_install=False):
        return {
            "app_name": "MyApp",
            "manifest": manifest or {},
            "current_dir": current_dir,
            "no_admin_install": no_admin_install,
        }

    def setUp(self):
        self.current_dir = tempfile.mkdtemp()
        self.self_name = "uninstall.exe"
        with open(os.path.join(self.current_dir, self.self_name), "w") as f:
            f.write("fake")
        self.argv_patcher = mock.patch.object(un.sys, "argv", [os.path.join(self.current_dir, self.self_name)])
        self.argv_patcher.start()

    def tearDown(self):
        self.argv_patcher.stop()
        shutil.rmtree(self.current_dir, ignore_errors=True)

    def test_passes_no_admin_install_true_through_to_unregister(self):
        ctx = self._make_ctx(
            self.current_dir, manifest={"files": [], "file_associations": [".foo"]}, no_admin_install=True,
        )
        with mock.patch("uninstall.file_assoc.unregister") as mock_unregister:
            un._perform_uninstall_steps(ctx, [], False, log=lambda m: None)

        mock_unregister.assert_called_once_with([".foo"], no_admin_install=True)

    def test_passes_no_admin_install_false_through_to_unregister(self):
        ctx = self._make_ctx(
            self.current_dir, manifest={"files": [], "file_associations": [".foo"]}, no_admin_install=False,
        )
        with mock.patch("uninstall.file_assoc.unregister") as mock_unregister:
            un._perform_uninstall_steps(ctx, [], False, log=lambda m: None)

        mock_unregister.assert_called_once_with([".foo"], no_admin_install=False)


class TestPerformUninstallStepsScheduledTask(unittest.TestCase):
    """manifest 裡有 scheduled_task_name 才呼叫
    scheduled_task.remove_scheduled_task() 移除對應的排程工作，沒有這個
    欄位（或空字串）完全不呼叫。"""

    def _make_ctx(self, current_dir, manifest=None):
        return {
            "app_name": "MyApp",
            "manifest": manifest or {},
            "current_dir": current_dir,
            "no_admin_install": False,
        }

    def setUp(self):
        self.current_dir = tempfile.mkdtemp()
        self.self_name = "uninstall.exe"
        with open(os.path.join(self.current_dir, self.self_name), "w") as f:
            f.write("fake")
        self.argv_patcher = mock.patch.object(un.sys, "argv", [os.path.join(self.current_dir, self.self_name)])
        self.argv_patcher.start()

    def tearDown(self):
        self.argv_patcher.stop()
        shutil.rmtree(self.current_dir, ignore_errors=True)

    def test_removes_task_when_manifest_has_task_name(self):
        ctx = self._make_ctx(self.current_dir, manifest={"files": [], "scheduled_task_name": "MyTask"})
        with mock.patch("uninstall.scheduled_task.remove_scheduled_task", return_value=True) as mock_remove:
            un._perform_uninstall_steps(ctx, [], False, log=lambda m: None)

        mock_remove.assert_called_once_with("MyTask")

    def test_does_not_call_remove_task_when_manifest_has_no_task(self):
        ctx = self._make_ctx(self.current_dir, manifest={"files": []})
        with mock.patch("uninstall.scheduled_task.remove_scheduled_task") as mock_remove:
            un._perform_uninstall_steps(ctx, [], False, log=lambda m: None)

        mock_remove.assert_not_called()

    def test_does_not_call_remove_task_when_task_name_is_empty_string(self):
        ctx = self._make_ctx(self.current_dir, manifest={"files": [], "scheduled_task_name": ""})
        with mock.patch("uninstall.scheduled_task.remove_scheduled_task") as mock_remove:
            un._perform_uninstall_steps(ctx, [], False, log=lambda m: None)

        mock_remove.assert_not_called()


class TestWantsLockRelease(unittest.TestCase):
    """_wants_lock_release()：真實抓到的 bug——更新覆蓋安裝呼叫的是舊版本
    的 uninstall.exe，它是否要偵測鎖定進程原本只看自己那份（可能過期的）
    install_manifest.json，跟使用者這次重新打包的新設定是兩回事，導致行為
    時好時壞。修正後 --restart-explorer 命令列旗標（由新版本明確傳入）要能
    覆蓋掉 manifest 裡的舊設定。

    另一個真實抓到的 bug：這個函式原本互動式（沒帶 --silent）解除安裝一律
    回傳 False，導致手動解除安裝永遠不會釋放被鎖住的檔案。現在改成不分
    互動或無人值守，只看設定本身；互動情境是否要先跟使用者確認，改由
    main() 呼叫 _confirm_kill_locking_processes() 另外把關，這裡不再接收
    silent 參數。"""

    def test_cli_flag_overrides_manifest_when_manifest_says_false(self):
        result = un._wants_lock_release(
            manifest={"restart_explorer_on_update": False}, argv=["uninstall.exe", "--silent", "--restart-explorer"],
        )
        self.assertTrue(result)

    def test_manifest_used_as_fallback_when_no_cli_flag(self):
        result = un._wants_lock_release(
            manifest={"restart_explorer_on_update": True}, argv=["uninstall.exe", "--silent"],
        )
        self.assertTrue(result)

    def test_false_when_neither_cli_flag_nor_manifest_set(self):
        result = un._wants_lock_release(
            manifest={"restart_explorer_on_update": False}, argv=["uninstall.exe", "--silent"],
        )
        self.assertFalse(result)

    def test_true_for_interactive_uninstall_when_manifest_set(self):
        """互動式解除安裝（沒帶 --silent）現在也套用同一個設定——是否要先
        跟使用者確認是 main() 的事，這個函式只回答「設定上想不想要」。"""
        result = un._wants_lock_release(
            manifest={"restart_explorer_on_update": True}, argv=["uninstall.exe"],
        )
        self.assertTrue(result)


class TestComputeLockingProcesses(unittest.TestCase):
    """_compute_locking_processes()：取代原本的 _confirm_kill_locking_processes()
    （原生 MessageBoxW 確認對話框）——確認邏輯現在搬進 ui/uninstall.html +
    UninstallerAPI.get_locking_process_names()，這裡只負責偵測，不負責問。
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _ctx(self, manifest):
        return {"current_dir": self.tmp_dir, "manifest": manifest}

    def test_returns_empty_when_lock_release_not_wanted(self):
        with mock.patch("uninstall.restart_manager.find_locking_processes") as mock_find:
            result = un._compute_locking_processes(self._ctx({}), ["uninstall.exe"])
        self.assertEqual(result, [])
        mock_find.assert_not_called()

    def test_delegates_to_restart_manager_when_lock_release_wanted(self):
        manifest = {"restart_explorer_on_update": True, "files": ["app.exe"]}
        with mock.patch("uninstall.restart_manager.find_locking_processes", return_value=[(111, "某個殼層擴充功能")]) as mock_find:
            result = un._compute_locking_processes(self._ctx(manifest), ["uninstall.exe"])
        self.assertEqual(result, [(111, "某個殼層擴充功能")])
        mock_find.assert_called_once()

    def test_falls_back_to_directory_scan_without_manifest_files(self):
        with open(os.path.join(self.tmp_dir, "leftover.dll"), "w") as f:
            f.write("x")
        manifest = {"restart_explorer_on_update": True}
        with mock.patch("uninstall.restart_manager.find_locking_processes", return_value=[]) as mock_find:
            un._compute_locking_processes(self._ctx(manifest), ["uninstall.exe"])
        candidate_paths = mock_find.call_args[0][0]
        self.assertIn(os.path.join(self.tmp_dir, "leftover.dll"), candidate_paths)


class TestUninstallerAPI(unittest.TestCase):
    """UninstallerAPI：互動式解除安裝的 pywebview JS API，取代原本一路線性
    執行到底、靠原生 MessageBoxW 中斷的 main()。"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.fake_reg = FakeWinReg()
        self.patcher = mock.patch.object(un, "winreg", self.fake_reg)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make_api(self, **manifest_overrides):
        ctx = {
            "current_dir": self.tmp_dir,
            "manifest": manifest_overrides,
            "app_name": "測試應用程式",
            "main_exe": "app.exe",
            "no_admin_install": False,
        }
        return un.UninstallerAPI(ctx)

    def test_check_main_exe_running_delegates(self):
        api = self._make_api()
        with mock.patch("uninstall.is_process_running", return_value=True) as mock_running:
            self.assertTrue(api.check_main_exe_running())
        mock_running.assert_called_once_with("app.exe")

    def test_close_running_main_exe_calls_taskkill_with_main_exe_basename(self):
        """使用者在解除安裝端『偵測到程式正在執行』畫面按下「關閉應用程式
        並繼續解除安裝」時呼叫，跟 installer_core.py 同樣情境共用
        system_entries.kill_process_by_name()（見 test_system_entries.py
        的 TestKillProcessByName），這裡只驗證有正確委派、傳對 main_exe。"""
        api = self._make_api()
        with mock.patch("system_entries.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            result = api.close_running_main_exe()
        self.assertTrue(result)
        args, kwargs = mock_run.call_args
        self.assertEqual(args[0], ["taskkill", "/f", "/im", "app.exe"])

    def test_close_running_main_exe_returns_false_when_taskkill_reports_failure(self):
        api = self._make_api()
        with mock.patch("system_entries.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 128
            self.assertFalse(api.close_running_main_exe())

    def test_close_running_main_exe_returns_false_without_main_exe(self):
        ctx = {
            "current_dir": self.tmp_dir, "manifest": {}, "app_name": "測試應用程式",
            "main_exe": "", "no_admin_install": False,
        }
        api = un.UninstallerAPI(ctx)
        self.assertFalse(api.close_running_main_exe())

    def test_close_running_main_exe_swallows_failure(self):
        api = self._make_api()
        with mock.patch("system_entries.subprocess.run", side_effect=RuntimeError("模擬失敗")):
            self.assertFalse(api.close_running_main_exe())

    def test_get_locking_process_names_caches_and_dedupes(self):
        api = self._make_api(restart_explorer_on_update=True, files=["app.exe"])
        with mock.patch("uninstall.restart_manager.find_locking_processes", return_value=[(1, "A"), (2, "A"), (3, "B")]):
            names = api.get_locking_process_names()
        self.assertEqual(names, ["A", "B"])
        self.assertEqual(api._locking_processes, [(1, "A"), (2, "A"), (3, "B")])

    def test_get_locking_process_names_empty_when_not_wanted(self):
        api = self._make_api()
        names = api.get_locking_process_names()
        self.assertEqual(names, [])

    def test_run_uninstall_success_updates_safe_to_remove_flag(self):
        api = self._make_api()
        with mock.patch("uninstall._perform_uninstall_steps", return_value=(True, [])) as mock_perform:
            result = api.run_uninstall(False)
        self.assertEqual(result, {"status": "success", "warnings": []})
        self.assertTrue(api._safe_to_remove_whole_dir)
        mock_perform.assert_called_once()

    def test_run_uninstall_exception_returns_error(self):
        api = self._make_api()
        with mock.patch("uninstall._perform_uninstall_steps", side_effect=RuntimeError("boom")):
            result = api.run_uninstall(False)
        self.assertEqual(result["status"], "error")
        self.assertIn("boom", result["message"])

    def test_finish_and_exit_writes_log_schedules_delete_and_hard_exits(self):
        """真實抓到的 bug（第一輪）：改用 os._exit() 而不是 window.destroy()
        是因為 WinForms/WebView2 的訊息迴圈不保證乾脆返回，行程沒真的結束，
        exe 檔案就一直被鎖住、自我刪除永遠不會成功。這裡驗證 os._exit(0)
        真的被呼叫（mock 掉，不然測試行程自己會被砍掉）。

        真實抓到的 bug（第二輪）：os._exit(0) 讓行程立刻終止沒錯，但
        WebView2 是硬體加速合成畫面，行程終止後 Windows 桌面合成器不一定
        馬上回收視窗殘留畫面，使用者會感覺「按下去卡住一兩秒才消失」。
        修法：先呼叫 window.hide()（輕量的 WinForms Form.Hide()）讓視窗
        立刻從畫面上消失，這裡驗證它在 os._exit() 之前被呼叫。"""
        api = self._make_api()
        api._safe_to_remove_whole_dir = True
        fake_window = mock.Mock()
        with mock.patch.object(un, "window", fake_window, create=True), \
             mock.patch("uninstall._write_uninstall_log") as mock_log, \
             mock.patch.object(un.self_delete, "schedule_if_needed") as mock_schedule, \
             mock.patch("uninstall.os._exit") as mock_exit:
            api.finish_and_exit()
        fake_window.hide.assert_called_once()
        mock_log.assert_called_once()
        mock_schedule.assert_called_once_with(
            sys.argv, self.tmp_dir, sys.argv[0], True, log=api._log_lines.append
        )
        mock_exit.assert_called_once_with(0)

    def test_cancel_hard_exits_without_deleting(self):
        api = self._make_api()
        fake_window = mock.Mock()
        with mock.patch.object(un, "window", fake_window, create=True), \
             mock.patch.object(un.self_delete, "schedule_if_needed") as mock_schedule, \
             mock.patch("uninstall.os._exit") as mock_exit:
            api.cancel()
        fake_window.hide.assert_called_once()
        mock_schedule.assert_not_called()
        mock_exit.assert_called_once_with(0)


class TestLoadUninstallContext(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.exe_path = os.path.join(self.tmp_dir, "uninstall.exe")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_defaults_when_no_manifest(self):
        ctx = un._load_uninstall_context([self.exe_path])
        self.assertEqual(ctx["app_name"], "DefaultApp")
        self.assertEqual(ctx["main_exe"], "")
        self.assertFalse(ctx["no_admin_install"])
        self.assertEqual(ctx["current_dir"], self.tmp_dir)

    def test_reads_manifest_fields(self):
        with open(os.path.join(self.tmp_dir, "install_manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"app_name": "MyApp", "main_exe": "app.exe", "no_admin_install": True}, f)
        ctx = un._load_uninstall_context([self.exe_path])
        self.assertEqual(ctx["app_name"], "MyApp")
        self.assertEqual(ctx["main_exe"], "app.exe")
        self.assertTrue(ctx["no_admin_install"])

    def test_falls_back_to_config_app_name_when_manifest_missing_it(self):
        with open(os.path.join(self.tmp_dir, "installer_config.json"), "w", encoding="utf-8") as f:
            json.dump({"app_name": "FromConfig"}, f)
        ctx = un._load_uninstall_context([self.exe_path])
        self.assertEqual(ctx["app_name"], "FromConfig")

    def test_corrupt_manifest_does_not_crash(self):
        """真實抓到的 bug：install_manifest.json 的 json.load() 原本沒有
        try/except（緊接著六行之後讀 installer_config.json 那段反而有），
        一份損毀的 manifest（例如安裝途中斷電、寫到一半的檔案）會讓
        JSONDecodeError 直接往外拋出 main()——這支 exe 是 --noconsole
        編譯的，使用者會看到完全沒有任何反應，看起來像是「按了解除安裝
        什麼事都沒發生」，而不是明確的錯誤訊息。損毀時應該退回跟「完全
        沒有 manifest」一樣的預設值，讓後續流程盡量繼續。"""
        with open(os.path.join(self.tmp_dir, "install_manifest.json"), "w", encoding="utf-8") as f:
            f.write("{ 這不是合法的 JSON")
        ctx = un._load_uninstall_context([self.exe_path])
        self.assertEqual(ctx["app_name"], "DefaultApp")
        self.assertEqual(ctx["manifest"], {})

    def test_manifest_that_is_a_json_array_does_not_crash(self):
        """有效 JSON、但不是物件（例如 `[]`）：manifest.get(...) 會直接
        AttributeError，同樣要當成「沒有可用的 manifest」處理。"""
        with open(os.path.join(self.tmp_dir, "install_manifest.json"), "w", encoding="utf-8") as f:
            json.dump([], f)
        ctx = un._load_uninstall_context([self.exe_path])
        self.assertEqual(ctx["app_name"], "DefaultApp")
        self.assertEqual(ctx["manifest"], {})


## 自我刪除（.bat 產生＋重試邏輯 + --upgrade 旗標判斷）已經拆到
## self_delete.py，對應測試搬到 tests/test_self_delete.py，這裡不再重複。


## 登錄表項目/捷徑/PATH 的實際移除邏輯（含 no_admin_install 的 HKCU/HKLM
## 判斷）已經拆到 system_entries.py，對應測試搬到
## tests/test_system_entries.py。這裡的 remove_registry_entry()/
## remove_shortcut()/remove_from_path() 只是薄薄一層委派，不再重複測試。


class TestCliLogPath(unittest.TestCase):
    def test_no_flag_returns_none(self):
        self.assertIsNone(un._cli_log_path(["uninstall.exe"]))

    def test_parses_log_flag(self):
        self.assertEqual(
            un._cli_log_path(["uninstall.exe", "/LOG=D:\\logs\\uninstall.txt"]),
            "D:\\logs\\uninstall.txt",
        )

    def test_case_insensitive(self):
        self.assertEqual(un._cli_log_path(["uninstall.exe", "/log=D:\\x.txt"]), "D:\\x.txt")


class TestPathRemovalTarget(unittest.TestCase):
    def test_uses_path_directory_when_present(self):
        manifest = {"install_path": "C:\\Apps\\MyApp", "path_directory": "C:\\Apps\\MyApp\\tools"}
        self.assertEqual(un._path_removal_target(manifest, "C:\\Apps\\MyApp"), "C:\\Apps\\MyApp\\tools")

    def test_falls_back_to_install_path_when_path_directory_missing(self):
        """舊版本安裝寫入的 manifest 沒有 path_directory 這個欄位，要退回
        install_path，維持原本「整個安裝目錄」的行為。"""
        manifest = {"install_path": "C:\\Apps\\MyApp"}
        self.assertEqual(un._path_removal_target(manifest, "C:\\fallback"), "C:\\Apps\\MyApp")

    def test_falls_back_to_current_dir_when_both_missing(self):
        self.assertEqual(un._path_removal_target({}, "C:\\fallback"), "C:\\fallback")


class TestLocalAppdataResolver(unittest.TestCase):
    def test_matches_listed_file_regardless_of_slash_direction(self):
        manifest = {"local_appdata_files": ["tools/cli.exe"]}
        is_local = un._local_appdata_resolver(manifest)
        self.assertTrue(is_local("tools\\cli.exe"))
        self.assertTrue(is_local("tools/cli.exe"))

    def test_unlisted_file_is_not_local_appdata(self):
        manifest = {"local_appdata_files": ["cli.exe"]}
        is_local = un._local_appdata_resolver(manifest)
        self.assertFalse(is_local("gui.exe"))

    def test_empty_or_missing_field_matches_nothing(self):
        is_local = un._local_appdata_resolver({})
        self.assertFalse(is_local("cli.exe"))


# _cleanup_empty_dirs 已收斂進 system_entries.cleanup_empty_dirs()，
# 對應測試搬到 tests/test_system_entries.py 的 TestCleanupEmptyDirs。


class TestManifestDeletionRoutesLocalAppdataFiles(unittest.TestCase):
    """對應 installer_core.py 的 local_appdata_files：安裝時被指定改裝到
    %LOCALAPPDATA%\\Programs\\<folder_name> 的檔案，解除安裝要從那個目錄
    刪，不是從安裝目錄（current_dir）刪。"""

    def setUp(self):
        self.install_dir = tempfile.mkdtemp()
        self.alt_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.install_dir, ignore_errors=True)
        shutil.rmtree(self.alt_dir, ignore_errors=True)

    def test_deletes_from_local_appdata_dir_not_install_dir(self):
        with open(os.path.join(self.alt_dir, "cli.exe"), "w") as f:
            f.write("copied")
        with open(os.path.join(self.install_dir, "gui.exe"), "w") as f:
            f.write("copied")
        manifest = {"local_appdata_files": ["cli.exe"], "local_appdata_dir": self.alt_dir}
        files_to_remove = ["gui.exe", "cli.exe"]
        self_name = "uninstall.exe"

        is_local_appdata_file = un._local_appdata_resolver(manifest)
        local_appdata_dir = manifest.get("local_appdata_dir") or ""
        for rel in files_to_remove:
            if os.path.basename(rel) == self_name:
                continue
            base_dir = local_appdata_dir if (local_appdata_dir and is_local_appdata_file(rel)) else self.install_dir
            item_path = os.path.join(base_dir, rel)
            if os.path.exists(item_path):
                os.remove(item_path)

        self.assertFalse(os.path.exists(os.path.join(self.alt_dir, "cli.exe")))
        self.assertFalse(os.path.exists(os.path.join(self.install_dir, "gui.exe")))


class TestUninstallManifestDrivenDeletion(unittest.TestCase):
    """對應 uninstall.py 檔案頭部記錄的那個真實 bug：不能『清單式刪除做得很仔細，
    最後卻無差別 rmdir 整個資料夾』。這裡直接重現 main() 裡那段判斷
    safe_to_remove_whole_dir 的邏輯，不呼叫真正的 main()（會牽扯到 MessageBox、
    自我刪除 subprocess 等一堆 GUI/系統層面的東西，不適合單元測試）。
    """

    def setUp(self):
        self.install_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.install_dir, ignore_errors=True)

    def _run_manifest_deletion(self, files_to_remove, self_name="uninstall.exe"):
        """複製 uninstall.py main() 第 212-245 行那段清單式刪除邏輯，
        回傳 safe_to_remove_whole_dir 這個關鍵旗標。
        """
        current_dir = self.install_dir
        for rel in files_to_remove:
            if os.path.basename(rel) == self_name:
                continue
            item_path = os.path.join(current_dir, rel)
            if os.path.exists(item_path):
                os.remove(item_path)

        for root, dirs, files in os.walk(current_dir, topdown=False):
            for d in dirs:
                dpath = os.path.join(root, d)
                try:
                    if not os.listdir(dpath):
                        os.rmdir(dpath)
                except Exception:
                    pass

        remaining = [item for item in os.listdir(current_dir) if item != self_name]
        return not remaining

    def test_user_added_file_prevents_whole_dir_removal(self):
        """使用者在安裝目錄裡自己多放了一個檔案（不在 install_manifest.json 的
        files 清單內），解除安裝完清單內的東西之後，資料夾不該被整個刪掉，
        使用者的檔案也不該被動到。"""
        with open(os.path.join(self.install_dir, "app.exe"), "w") as f:
            f.write("app")
        with open(os.path.join(self.install_dir, "uninstall.exe"), "w") as f:
            f.write("self")
        with open(os.path.join(self.install_dir, "user_data.txt"), "w") as f:
            f.write("使用者自己產生的資料")

        safe_to_remove_whole_dir = self._run_manifest_deletion(["app.exe", "uninstall.exe"])

        self.assertFalse(safe_to_remove_whole_dir, "資料夾裡還有清單之外的檔案，不該被判定成可以整個刪除")
        self.assertTrue(os.path.exists(os.path.join(self.install_dir, "user_data.txt")), "使用者的檔案不該被清單式刪除動到")

    def test_fully_listed_install_allows_whole_dir_removal(self):
        """清單內的東西刪完之後，資料夾裡除了 uninstall.exe 自己以外空無一物，
        這種情況才可以連資料夾一起刪掉。"""
        with open(os.path.join(self.install_dir, "app.exe"), "w") as f:
            f.write("app")
        with open(os.path.join(self.install_dir, "uninstall.exe"), "w") as f:
            f.write("self")

        safe_to_remove_whole_dir = self._run_manifest_deletion(["app.exe", "uninstall.exe"])

        self.assertTrue(safe_to_remove_whole_dir)

    def test_nested_subdirectory_from_manifest_is_pruned(self):
        os.makedirs(os.path.join(self.install_dir, "assets"))
        with open(os.path.join(self.install_dir, "assets", "logo.png"), "w") as f:
            f.write("logo")
        with open(os.path.join(self.install_dir, "uninstall.exe"), "w") as f:
            f.write("self")

        safe_to_remove_whole_dir = self._run_manifest_deletion(["assets/logo.png", "uninstall.exe"])

        self.assertFalse(os.path.exists(os.path.join(self.install_dir, "assets")), "清空的子目錄應該被清掉")
        self.assertTrue(safe_to_remove_whole_dir)


if __name__ == "__main__":
    unittest.main(verbosity=2)
