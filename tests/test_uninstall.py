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


class TestExplorerRestartHelpers(unittest.TestCase):
    """_process_image_name() / _kill_processes()：更新覆蓋安裝或解除安裝時
    用來釋放被鎖住的檔案。main() 本身涉及 MessageBoxW/is_admin()/sys.argv
    等 GUI/系統層面的東西，這個檔案原本就沒有整合測試 main()，這裡只測
    可以獨立驗證的這幾個 best-effort 函式本身。

    真實抓到的 bug：舊版 _kill_explorer() 寫死「一定是 explorer.exe 鎖住」，
    只涵蓋殼層擴充功能這一種情境。現在改用 restart_manager（包 Windows
    Restart Manager API）實際偵測是哪些進程鎖住了要刪除的檔案，
    _kill_processes() 逐一結束真正的鎖定者（不是無差別 taskkill /im
    explorer.exe）。

    真實抓到的另一個問題：原本結束掉的進程裡如果有 explorer.exe，會另外
    呼叫 subprocess.Popen(["explorer.exe"]) 主動重啟它。實測發現這個呼叫
    會跳出一個瀏覽視窗——只有『目前沒有任何 explorer.exe 在跑』時，執行
    explorer.exe 才會直接成為桌面/工作列（不開視窗）；跳出視窗代表呼叫
    當下 shell 其實已經被復原了，這一步已經是多餘、甚至會多跳出一個
    使用者沒有要求的視窗，所以拿掉了這個明確重啟的步驟。"""

    def test_process_image_name_parses_tasklist_csv_output(self):
        fake_output = '"explorer.exe","1234","Console","1","12,345 K"\r\n'
        with mock.patch("uninstall.subprocess.check_output", return_value=fake_output):
            result = un._process_image_name(1234)
        self.assertEqual(result, "explorer.exe")

    def test_process_image_name_swallows_failure(self):
        with mock.patch("uninstall.subprocess.check_output", side_effect=RuntimeError("模擬失敗")):
            result = un._process_image_name(1234)
        self.assertEqual(result, "")

    def test_kill_processes_taskkills_each_pid_and_returns_image_names(self):
        with mock.patch("uninstall.subprocess.run") as mock_run, \
             mock.patch("uninstall.subprocess.check_output", return_value='"explorer.exe","111",""\r\n'), \
             mock.patch("uninstall.time.sleep"):
            killed = un._kill_processes([(111, "Windows 檔案總管")])
        args, kwargs = mock_run.call_args
        self.assertEqual(args[0], ["taskkill", "/f", "/pid", "111"])
        self.assertEqual(killed, [(111, "explorer.exe")])

    def test_kill_processes_swallows_per_pid_failure_and_continues(self):
        with mock.patch("uninstall.subprocess.run", side_effect=RuntimeError("模擬失敗")), \
             mock.patch("uninstall.subprocess.check_output", return_value=""), \
             mock.patch("uninstall.time.sleep"):
            killed = un._kill_processes([(111, "app")])  # 不應該拋例外
        self.assertEqual(killed, [])

    def test_kill_processes_empty_list_does_not_sleep(self):
        with mock.patch("uninstall.subprocess.run") as mock_run, \
             mock.patch("uninstall.time.sleep") as mock_sleep:
            killed = un._kill_processes([])
        mock_run.assert_not_called()
        mock_sleep.assert_not_called()
        self.assertEqual(killed, [])


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
        並繼續解除安裝」時呼叫，寫法比照 installer_core.py 既有的
        close_running_main_exe()：taskkill /f、CREATE_NO_WINDOW、檢查
        returncode（不是呼叫沒拋例外就一律回傳 True）。"""
        api = self._make_api()
        with mock.patch("uninstall.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            result = api.close_running_main_exe()
        self.assertTrue(result)
        args, kwargs = mock_run.call_args
        self.assertEqual(args[0], ["taskkill", "/f", "/im", "app.exe"])

    def test_close_running_main_exe_returns_false_when_taskkill_reports_failure(self):
        api = self._make_api()
        with mock.patch("uninstall.subprocess.run") as mock_run:
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
        with mock.patch("uninstall.subprocess.run", side_effect=RuntimeError("模擬失敗")):
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
        with mock.patch("uninstall._perform_uninstall_steps", return_value=True) as mock_perform:
            result = api.run_uninstall(False)
        self.assertEqual(result, {"status": "success"})
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
        mock_schedule.assert_called_once_with(sys.argv, self.tmp_dir, sys.argv[0], True)
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


class TestCleanupEmptyDirs(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_removes_dir_when_empty(self):
        un._cleanup_empty_dirs(self.tmp_dir)
        self.assertFalse(os.path.exists(self.tmp_dir))

    def test_keeps_dir_when_files_remain(self):
        with open(os.path.join(self.tmp_dir, "keep.txt"), "w") as f:
            f.write("still here")
        un._cleanup_empty_dirs(self.tmp_dir)
        self.assertTrue(os.path.exists(self.tmp_dir))


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
