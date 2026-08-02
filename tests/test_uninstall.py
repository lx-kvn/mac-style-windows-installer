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
import shutil
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _fakes import FakeWinReg
import uninstall as un


class TestExplorerRestartHelpers(unittest.TestCase):
    """_restart_explorer() / _process_image_name() / _kill_processes()：
    更新覆蓋安裝或解除安裝時（打包時勾選 restart_explorer_on_update）用來
    釋放被鎖住的檔案。main() 本身涉及 MessageBoxW/is_admin()/sys.argv 等
    GUI/系統層面的東西，這個檔案原本就沒有整合測試 main()，這裡只測可以
    獨立驗證的這幾個 best-effort 函式本身。

    真實抓到的 bug：舊版 _kill_explorer() 寫死「一定是 explorer.exe 鎖住」，
    只涵蓋殼層擴充功能這一種情境。現在改用 restart_manager（包 Windows
    Restart Manager API）實際偵測是哪些進程鎖住了要刪除的檔案，
    _kill_processes() 逐一結束真正的鎖定者（不是無差別 taskkill /im
    explorer.exe），只有結束掉的進程剛好是 explorer.exe 才會自動重啟。"""

    def test_restart_explorer_launches_explorer_exe(self):
        with mock.patch("uninstall.subprocess.Popen") as mock_popen:
            un._restart_explorer()
        args, kwargs = mock_popen.call_args
        self.assertEqual(args[0], ["explorer.exe"])

    def test_restart_explorer_swallows_failure(self):
        with mock.patch("uninstall.subprocess.Popen", side_effect=RuntimeError("模擬失敗")):
            un._restart_explorer()  # 不應該拋例外

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


class TestConfirmKillLockingProcesses(unittest.TestCase):
    """_confirm_kill_locking_processes()：互動式解除安裝真的結束鎖定進程前，
    跳出確認對話框取得使用者同意——避免無預警強制關閉使用者正在用的其他
    程式。列出 restart_manager 實際偵測到的進程名稱，不是像舊版一律寫死
    「檔案總管」。"""

    def test_returns_true_when_user_clicks_yes(self):
        IDYES = 6
        with mock.patch("uninstall.ctypes.windll.user32.MessageBoxW", return_value=IDYES) as mock_box:
            result = un._confirm_kill_locking_processes("測試應用程式", [(111, "某個殼層擴充功能")])
        self.assertTrue(result)
        args, kwargs = mock_box.call_args
        self.assertIn("測試應用程式", args[1])
        self.assertIn("某個殼層擴充功能", args[1])

    def test_returns_false_when_user_clicks_no(self):
        IDNO = 7
        with mock.patch("uninstall.ctypes.windll.user32.MessageBoxW", return_value=IDNO):
            result = un._confirm_kill_locking_processes("測試應用程式", [(111, "某個殼層擴充功能")])
        self.assertFalse(result)

    def test_returns_false_without_prompting_when_no_processes_detected(self):
        with mock.patch("uninstall.ctypes.windll.user32.MessageBoxW") as mock_box:
            result = un._confirm_kill_locking_processes("測試應用程式", [])
        self.assertFalse(result)
        mock_box.assert_not_called()


class TestUpgradeSelfDeleteGating(unittest.TestCase):
    """真實抓到的 bug：main() 尾端的自我刪除是 fire-and-forget（背景、不等待
    的 cmd.exe，先 ping 製造約 1 秒延遲才真正 del/rmdir）。更新覆蓋安裝時，
    installer_core.py 用 subprocess.run() 同步呼叫舊版 uninstall.exe，行程
    一結束就繼續複製新版本檔案——這時候背景那個延遲後才執行的 rmdir /s /q
    根本還沒發生，如果複製時間跨過那個延遲視窗，會把整個資料夾（含新複製
    好的檔案）一起砍掉，導致「安裝回報成功但檔案沒有複製完整」。修正：
    --upgrade 旗標讓舊版 uninstall.exe 完全不排這段背景指令。"""

    def test_is_upgrade_call_detects_flag(self):
        self.assertTrue(un._is_upgrade_call(["uninstall.exe", "--silent", "--upgrade"]))

    def test_is_upgrade_call_false_without_flag(self):
        self.assertFalse(un._is_upgrade_call(["uninstall.exe", "--silent"]))

    def test_self_delete_skipped_when_upgrade(self):
        self.assertFalse(un._should_schedule_self_delete(is_upgrade=True))

    def test_self_delete_scheduled_when_not_upgrade(self):
        self.assertTrue(un._should_schedule_self_delete(is_upgrade=False))


class TestRemoveFromPath(unittest.TestCase):
    def setUp(self):
        self.fake_reg = FakeWinReg()
        # uninstall.py 在檔案最上面就 import winreg（不像 installer_core.py 是
        # 每個函式各自 local import），module 命名空間裡的 uninstall.winreg 早就
        # 綁定了真正的 winreg，事後 patch sys.modules 不會回溯生效，要直接換掉
        # uninstall 模組自己的屬性。
        self.patcher = mock.patch.object(un, "winreg", self.fake_reg)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def _path_key(self):
        return r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"

    def test_removes_only_matching_entry(self):
        self.fake_reg.set_hklm(self._path_key(), {"Path": "C:\\Windows;C:\\Apps\\MyApp;C:\\Other"})
        with mock.patch("uninstall.ctypes.windll.user32.SendMessageTimeoutW"):
            un.remove_from_path("C:\\Apps\\MyApp")
        self.assertEqual(self.fake_reg.hklm(self._path_key())["Path"], "C:\\Windows;C:\\Other")


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
