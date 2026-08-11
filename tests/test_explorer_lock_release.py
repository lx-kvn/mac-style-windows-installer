"""explorer_lock_release.py 的測試。

真實情境：檔案被 explorer.exe 鎖住時，直接 taskkill 整個殼層行程會讓
桌面/工作列全部重啟，而且 explorer.exe 被砍掉後幾乎瞬間會被 Windows
的 AutoRestartShell 機制自動復活——如果它剛好開著目標資料夾，會在
複製/覆寫作業還沒完成前就搶著把同一個檔案重新鎖住，看起來像是「殺不掉」。

這個模組提供分層的釋放策略：
  1. 先只關閉正在瀏覽目標路徑的檔案總管視窗（不動 explorer.exe 這個
     行程本身，桌面/工作列不受影響）。
  2. 關窗後如果 explorer.exe 仍然鎖著檔案，才暫時停用 AutoRestartShell、
     砍掉 explorer.exe（保證不會在檔案操作完成前自動復活搶鎖），並回傳
     一個狀態物件，讓呼叫端在檔案操作完成後呼叫 restore_after_lock_release()
     手動重啟 explorer.exe、恢復 AutoRestartShell。

跟 system_entries.py/restart_manager.py 一樣的 seam 慣例：registry 參數
預設是真正的 winreg，測試用 tests/_fakes.py 的 FakeWinReg 注入；
shell_factory/find_locking_processes 只給測試注入假物件用。
"""
import contextlib
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _fakes import FakeWinReg
import explorer_lock_release as elr


class _FakeWindow:
    def __init__(self, path, raises_on_quit=False):
        self.path = path
        self.raises_on_quit = raises_on_quit
        self.quit_called = False

    class _Document:
        class _Folder:
            class _Self:
                pass

    @property
    def Document(self):
        doc = mock.Mock()
        doc.Folder.Self.Path = self.path
        return doc

    def Quit(self):
        if self.raises_on_quit:
            raise RuntimeError("模擬 Quit() 失敗")
        self.quit_called = True


class _FakeShell:
    def __init__(self, windows):
        self._windows = windows

    def Windows(self):
        return self._windows


class TestCloseWindowsBrowsingPath(unittest.TestCase):
    def test_closes_window_browsing_exact_path(self):
        target = "C:\\Apps\\MyApp"
        matching = _FakeWindow(target)
        other = _FakeWindow("C:\\Apps\\OtherApp")
        shell = _FakeShell([matching, other])

        closed = elr.close_windows_browsing_path(target, shell_factory=lambda: shell)

        self.assertEqual(closed, 1)
        self.assertTrue(matching.quit_called)
        self.assertFalse(other.quit_called)

    def test_closes_window_browsing_subpath_case_insensitive(self):
        target = "C:\\Apps\\MyApp"
        matching = _FakeWindow("c:\\apps\\myapp\\sub\\folder")
        shell = _FakeShell([matching])

        closed = elr.close_windows_browsing_path(target, shell_factory=lambda: shell)

        self.assertEqual(closed, 1)
        self.assertTrue(matching.quit_called)

    def test_does_not_close_unrelated_sibling_path(self):
        target = "C:\\Apps\\MyApp"
        sibling = _FakeWindow("C:\\Apps\\MyAppOther")
        shell = _FakeShell([sibling])

        closed = elr.close_windows_browsing_path(target, shell_factory=lambda: shell)

        self.assertEqual(closed, 0)
        self.assertFalse(sibling.quit_called)

    def test_swallows_quit_failure_and_continues(self):
        target = "C:\\Apps\\MyApp"
        failing = _FakeWindow(target, raises_on_quit=True)
        ok = _FakeWindow(target)
        shell = _FakeShell([failing, ok])

        closed = elr.close_windows_browsing_path(target, shell_factory=lambda: shell)

        self.assertEqual(closed, 1)
        self.assertTrue(ok.quit_called)

    def test_returns_zero_when_shell_factory_raises(self):
        def _raise():
            raise RuntimeError("COM 不可用")

        closed = elr.close_windows_browsing_path("C:\\Apps\\MyApp", shell_factory=_raise)
        self.assertEqual(closed, 0)

    def test_returns_zero_when_no_path_given(self):
        self.assertEqual(elr.close_windows_browsing_path(None), 0)
        self.assertEqual(elr.close_windows_browsing_path(""), 0)


def _tasklist_output(image_name, pid):
    return f'"{image_name}","{pid}","Console","1","12,345 K"\r\n'


@contextlib.contextmanager
def _patch_successful_termination():
    """終止行程改成直接呼叫 Windows API（OpenProcessToken/AdjustTokenPrivileges
    啟用 SeDebugPrivilege，再 OpenProcess+TerminateProcess），取代原本外部
    呼叫 taskkill.exe——真實抓到的問題：taskkill.exe 預設不會啟用
    SeDebugPrivilege，即使呼叫端本身已經是系統管理員權限執行，對
    explorer.exe 這類跑在不同登入 session 的行程一樣會回報「存取被拒」
    而終止失敗；工作管理員能砍得掉正是因為它自己有啟用這個權限。這裡
    把整組 ctypes 呼叫都 mock 成成功，讓不是在測「終止本身有沒有成功」
    的測試不用逐一處理這些底層細節，回傳一個 name -> Mock 的 dict 方便
    個別斷言呼叫參數。"""
    with mock.patch("explorer_lock_release.ctypes.windll.advapi32.OpenProcessToken", return_value=1) as open_token, \
         mock.patch("explorer_lock_release.ctypes.windll.advapi32.LookupPrivilegeValueW", return_value=1) as lookup, \
         mock.patch("explorer_lock_release.ctypes.windll.advapi32.AdjustTokenPrivileges", return_value=1) as adjust, \
         mock.patch("explorer_lock_release.ctypes.windll.kernel32.OpenProcess", return_value=4321) as open_process, \
         mock.patch("explorer_lock_release.ctypes.windll.kernel32.TerminateProcess", return_value=1) as terminate, \
         mock.patch("explorer_lock_release.ctypes.windll.kernel32.CloseHandle", return_value=1) as close_handle:
        yield {
            "OpenProcessToken": open_token, "LookupPrivilegeValueW": lookup, "AdjustTokenPrivileges": adjust,
            "OpenProcess": open_process, "TerminateProcess": terminate, "CloseHandle": close_handle,
        }


class TestReleaseLockingProcesses(unittest.TestCase):
    def setUp(self):
        self.fake_reg = FakeWinReg()
        self.fake_reg.set_hkcu(elr._WINLOGON_KEY, {"AutoRestartShell": "1"})

    def test_kills_non_explorer_processes_without_touching_registry(self):
        processes = [{"pid": 111, "name": "某應用程式"}]
        with mock.patch("explorer_lock_release.subprocess.check_output",
                         return_value=_tasklist_output("myapp.exe", 111)), \
             _patch_successful_termination() as mock_kernel:
            state = elr.release_locking_processes(processes, registry=self.fake_reg)

        mock_kernel["OpenProcess"].assert_called_once_with(elr._PROCESS_TERMINATE, False, 111)
        mock_kernel["TerminateProcess"].assert_called_once_with(4321, 1)
        self.assertIsNone(state)
        self.assertEqual(self.fake_reg.hkcu(elr._WINLOGON_KEY)["AutoRestartShell"], "1")

    def test_closing_window_alone_resolves_lock_skips_forced_restart(self):
        processes = [{"pid": 222, "name": "Windows 檔案總管"}]
        with mock.patch("explorer_lock_release.close_windows_browsing_path", return_value=1) as mock_close, \
             _patch_successful_termination() as mock_kernel:
            state = elr.release_locking_processes(
                processes, path="C:\\Apps\\MyApp", registry=self.fake_reg,
                find_locking_processes=lambda paths: [],
            )

        self.assertEqual(mock_close.call_args[0], ("C:\\Apps\\MyApp",))
        self.assertEqual(mock_close.call_args[1]["shell_factory"], None)
        mock_kernel["OpenProcess"].assert_not_called()
        self.assertIsNone(state)
        self.assertEqual(self.fake_reg.hkcu(elr._WINLOGON_KEY)["AutoRestartShell"], "1")

    def test_explorer_still_locking_after_window_close_forces_shell_restart(self):
        """關窗之後仍鎖著，Restart Manager 優雅關閉這層也幫不上忙（session
        開不起來，模擬 Restart Manager 不可用/沒有可關閉的應用程式）時，
        才落到既有的強制關殼層那條路。"""
        processes = [{"pid": 222, "name": "Windows 檔案總管"}]
        unavailable_session = mock.Mock()
        unavailable_session.is_open = False
        with mock.patch("explorer_lock_release.close_windows_browsing_path", return_value=0), \
             mock.patch("explorer_lock_release.subprocess.check_output",
                         return_value=_tasklist_output("explorer.exe", 222)), \
             mock.patch("explorer_lock_release.restart_manager.RestartManagerSession",
                         return_value=unavailable_session), \
             _patch_successful_termination() as mock_kernel:
            state = elr.release_locking_processes(
                processes, path="C:\\Apps\\MyApp", registry=self.fake_reg,
                find_locking_processes=lambda paths: [(222, "Windows 檔案總管")],
            )

        self.assertEqual(self.fake_reg.hkcu(elr._WINLOGON_KEY)["AutoRestartShell"], "0")
        mock_kernel["OpenProcess"].assert_called_once_with(elr._PROCESS_TERMINATE, False, 222)
        mock_kernel["TerminateProcess"].assert_called_once_with(4321, 1)
        self.assertEqual(state, {"previous_auto_restart_shell": "1"})


class TestReleaseLockingProcessesRestartManagerLayer(unittest.TestCase):
    """三層釋放策略的第二層：關窗之後仍鎖著時，先試著用 Restart Manager
    請支援它的應用程式優雅關閉（不是砍行程），成功解開鎖就不用再進到
    「強制關殼層」那條路；沒解開才落到既有的強制關殼層邏輯。"""

    def setUp(self):
        self.fake_reg = FakeWinReg()
        self.fake_reg.set_hkcu(elr._WINLOGON_KEY, {"AutoRestartShell": "1"})

    def _make_session(self, is_open=True, shutdown_result=True):
        session = mock.Mock()
        session.is_open = is_open
        session.shutdown.return_value = shutdown_result
        return session

    def test_rm_shutdown_resolves_lock_skips_forced_shell_restart(self):
        processes = [{"pid": 222, "name": "Windows 檔案總管"}]
        session = self._make_session()
        find_calls = []

        def fake_find(paths):
            find_calls.append(list(paths))
            # 第一次（關窗後）還鎖著；RM shutdown 之後第二次查已經解開。
            return [(222, "Windows 檔案總管")] if len(find_calls) == 1 else []

        with mock.patch("explorer_lock_release.close_windows_browsing_path", return_value=0), \
             mock.patch("explorer_lock_release.restart_manager.RestartManagerSession", return_value=session), \
             _patch_successful_termination() as mock_kernel:
            state = elr.release_locking_processes(
                processes, path="C:\\Apps\\MyApp", registry=self.fake_reg,
                find_locking_processes=fake_find,
            )

        session.shutdown.assert_called_once()
        session.restart.assert_called_once()
        session.close.assert_called_once()
        mock_kernel["OpenProcess"].assert_not_called()
        self.assertIsNone(state)
        self.assertEqual(self.fake_reg.hkcu(elr._WINLOGON_KEY)["AutoRestartShell"], "1")

    def test_rm_shutdown_does_not_resolve_lock_falls_back_to_force_kill(self):
        processes = [{"pid": 222, "name": "Windows 檔案總管"}]
        session = self._make_session()

        with mock.patch("explorer_lock_release.close_windows_browsing_path", return_value=0), \
             mock.patch("explorer_lock_release.restart_manager.RestartManagerSession", return_value=session), \
             mock.patch("explorer_lock_release.subprocess.check_output",
                         return_value=_tasklist_output("explorer.exe", 222)), \
             _patch_successful_termination() as mock_kernel:
            state = elr.release_locking_processes(
                processes, path="C:\\Apps\\MyApp", registry=self.fake_reg,
                find_locking_processes=lambda paths: [(222, "Windows 檔案總管")],
            )

        session.shutdown.assert_called_once()
        # RM 這層沒解開鎖，剩下的行程還是要交給既有的強制關殼層邏輯處理。
        mock_kernel["TerminateProcess"].assert_called_once_with(4321, 1)
        session.close.assert_called_once()
        self.assertEqual(state, {"previous_auto_restart_shell": "1"})

    def test_rm_shutdown_call_failure_falls_back_to_force_kill(self):
        processes = [{"pid": 222, "name": "Windows 檔案總管"}]
        session = self._make_session(shutdown_result=False)

        with mock.patch("explorer_lock_release.close_windows_browsing_path", return_value=0), \
             mock.patch("explorer_lock_release.restart_manager.RestartManagerSession", return_value=session), \
             mock.patch("explorer_lock_release.subprocess.check_output",
                         return_value=_tasklist_output("explorer.exe", 222)), \
             _patch_successful_termination() as mock_kernel:
            state = elr.release_locking_processes(
                processes, path="C:\\Apps\\MyApp", registry=self.fake_reg,
                find_locking_processes=lambda paths: [(222, "Windows 檔案總管")],
            )

        mock_kernel["TerminateProcess"].assert_called_once_with(4321, 1)
        self.assertEqual(state, {"previous_auto_restart_shell": "1"})

    def test_no_path_skips_restart_manager_layer_entirely(self):
        """沒有 path 就不知道要對哪個路徑開 Restart Manager session，這層
        直接略過，維持原本「沒有 path 就直接分類終止」的行為。"""
        processes = [{"pid": 222, "name": "Windows 檔案總管"}]
        with mock.patch("explorer_lock_release.restart_manager.RestartManagerSession") as mock_session_cls, \
             mock.patch("explorer_lock_release.subprocess.check_output",
                         return_value=_tasklist_output("explorer.exe", 222)), \
             _patch_successful_termination():
            elr.release_locking_processes(processes, registry=self.fake_reg)

        mock_session_cls.assert_not_called()

    def test_explorer_locking_without_path_forces_shell_restart(self):
        processes = [{"pid": 222, "name": "Windows 檔案總管"}]
        with mock.patch("explorer_lock_release.subprocess.check_output",
                         return_value=_tasklist_output("explorer.exe", 222)), \
             _patch_successful_termination() as mock_kernel:
            state = elr.release_locking_processes(processes, registry=self.fake_reg)

        self.assertEqual(self.fake_reg.hkcu(elr._WINLOGON_KEY)["AutoRestartShell"], "0")
        mock_kernel["TerminateProcess"].assert_called_once_with(4321, 1)
        self.assertEqual(state, {"previous_auto_restart_shell": "1"})

    def test_missing_registry_value_falls_back_to_default_on_restore_state(self):
        empty_reg = FakeWinReg()
        processes = [{"pid": 222, "name": "Windows 檔案總管"}]
        with mock.patch("explorer_lock_release.subprocess.check_output",
                         return_value=_tasklist_output("explorer.exe", 222)), \
             _patch_successful_termination():
            state = elr.release_locking_processes(processes, registry=empty_reg)

        self.assertEqual(state, {"previous_auto_restart_shell": None})


class TestTerminateProcess(unittest.TestCase):
    """_terminate_process()：直接呼叫 Windows API 終止行程（先啟用
    SeDebugPrivilege，再 OpenProcess+TerminateProcess），取代原本外部呼叫
    taskkill.exe——實測發現對 explorer.exe 這類跑在不同登入 session 的
    行程，taskkill.exe 即使在提權執行的呼叫端底下仍會回報「存取被拒」
    而終止失敗，改成跟工作管理員一樣直接呼叫 API 才能真正砍得掉。"""

    def test_enables_debug_privilege_before_terminating(self):
        messages = []
        with mock.patch("explorer_lock_release.ctypes.windll.advapi32.OpenProcessToken",
                         return_value=1) as mock_open_token, \
             mock.patch("explorer_lock_release.ctypes.windll.advapi32.LookupPrivilegeValueW",
                         return_value=1) as mock_lookup, \
             mock.patch("explorer_lock_release.ctypes.windll.advapi32.AdjustTokenPrivileges",
                         return_value=1) as mock_adjust, \
             mock.patch("explorer_lock_release.ctypes.windll.kernel32.OpenProcess", return_value=4321), \
             mock.patch("explorer_lock_release.ctypes.windll.kernel32.TerminateProcess", return_value=1), \
             mock.patch("explorer_lock_release.ctypes.windll.kernel32.CloseHandle", return_value=1):
            elr._terminate_process(222, log=messages.append)

        mock_open_token.assert_called_once()
        mock_lookup.assert_called_once()
        mock_adjust.assert_called_once()

    def test_logs_success_when_terminate_process_succeeds(self):
        messages = []
        with _patch_successful_termination():
            elr._terminate_process(222, log=messages.append)
        self.assertTrue(any("成功" in m and "222" in m for m in messages))

    def test_logs_failure_when_open_process_fails(self):
        messages = []
        with mock.patch("explorer_lock_release.ctypes.windll.advapi32.OpenProcessToken", return_value=1), \
             mock.patch("explorer_lock_release.ctypes.windll.advapi32.LookupPrivilegeValueW", return_value=1), \
             mock.patch("explorer_lock_release.ctypes.windll.advapi32.AdjustTokenPrivileges", return_value=1), \
             mock.patch("explorer_lock_release.ctypes.windll.kernel32.OpenProcess", return_value=0), \
             mock.patch("explorer_lock_release.ctypes.windll.kernel32.CloseHandle") as mock_close:
            elr._terminate_process(222, log=messages.append)

        self.assertTrue(any("OpenProcess" in m and "222" in m for m in messages))
        # SeDebugPrivilege 那一步關 token 控制代碼是預期行為；但拿不到
        # process 的有效控制代碼時，不該多關一個不存在的 process 控制代碼。
        self.assertNotIn(mock.call(4321), mock_close.call_args_list)

    def test_logs_failure_when_terminate_process_fails(self):
        messages = []
        with mock.patch("explorer_lock_release.ctypes.windll.advapi32.OpenProcessToken", return_value=1), \
             mock.patch("explorer_lock_release.ctypes.windll.advapi32.LookupPrivilegeValueW", return_value=1), \
             mock.patch("explorer_lock_release.ctypes.windll.advapi32.AdjustTokenPrivileges", return_value=1), \
             mock.patch("explorer_lock_release.ctypes.windll.kernel32.OpenProcess", return_value=4321), \
             mock.patch("explorer_lock_release.ctypes.windll.kernel32.TerminateProcess", return_value=0), \
             mock.patch("explorer_lock_release.ctypes.windll.kernel32.CloseHandle") as mock_close:
            elr._terminate_process(222, log=messages.append)

        self.assertTrue(any("TerminateProcess" in m and "222" in m for m in messages))
        self.assertIn(mock.call(4321), mock_close.call_args_list)  # 就算終止失敗也要記得釋放控制代碼

    def test_swallows_unexpected_exception(self):
        with mock.patch("explorer_lock_release.ctypes.windll.advapi32.OpenProcessToken",
                         side_effect=RuntimeError("模擬失敗")):
            elr._terminate_process(222)  # 不應該拋例外


class TestReleaseLockingProcessesLogging(unittest.TestCase):
    """release_locking_processes() 目前完全沒有留下任何紀錄，實測發現
    砍 explorer.exe 沒有效果時完全無從得知是「根本沒被判斷成 explorer.exe」
    還是「taskkill 真的執行了但沒有用」——加上可注入的 log(msg) callback，
    把「這個 pid 解析出來的真正執行檔名稱」「分類成 explorer/其他」
    「taskkill 有沒有成功（看 returncode，不是呼叫沒拋例外就算數）」都
    記下來，才能在下次重現問題時看得到具體是哪一步壞掉。"""

    def setUp(self):
        self.fake_reg = FakeWinReg()
        self.fake_reg.set_hkcu(elr._WINLOGON_KEY, {"AutoRestartShell": "1"})
        self.messages = []

    def _log(self, msg):
        self.messages.append(msg)

    def test_logs_resolved_image_name_and_classification(self):
        processes = [{"pid": 222, "name": "Windows 檔案總管"}]
        with mock.patch("explorer_lock_release.subprocess.check_output",
                         return_value=_tasklist_output("explorer.exe", 222)), \
             _patch_successful_termination():
            elr.release_locking_processes(processes, registry=self.fake_reg, log=self._log)

        joined = "\n".join(self.messages)
        self.assertIn("222", joined)
        self.assertIn("explorer.exe", joined)

    def test_logs_when_image_name_resolution_fails(self):
        processes = [{"pid": 222, "name": "Windows 檔案總管"}]
        with mock.patch("explorer_lock_release.subprocess.check_output",
                         side_effect=RuntimeError("模擬 tasklist 失敗")), \
             _patch_successful_termination():
            elr.release_locking_processes(processes, registry=self.fake_reg, log=self._log)

        joined = "\n".join(self.messages)
        self.assertIn("222", joined)
        # 解析不出真正的執行檔名稱時要留下痕跡，不能悄悄當成「不是
        # explorer.exe」處理掉、卻完全沒留下任何線索。
        self.assertTrue(any("無法解析" in m or "空字串" in m or "查無" in m for m in self.messages))

    def test_logs_terminate_process_failure_reason(self):
        """真實抓到的問題（實測重現）：改成直接呼叫 TerminateProcess 之前，
        原本 taskkill 的 stdout/stderr 都導到 DEVNULL，就算知道失敗了也
        完全看不到 Windows 給的實際原因（例如「拒絕存取」）；改用
        TerminateProcess 之後一樣要把失敗原因（哪個 API 呼叫失敗）記下來，
        不能只留一句「失敗」讓人猜。"""
        processes = [{"pid": 222, "name": "Windows 檔案總管"}]
        with mock.patch("explorer_lock_release.subprocess.check_output",
                         return_value=_tasklist_output("explorer.exe", 222)), \
             mock.patch("explorer_lock_release.ctypes.windll.advapi32.OpenProcessToken", return_value=1), \
             mock.patch("explorer_lock_release.ctypes.windll.advapi32.LookupPrivilegeValueW", return_value=1), \
             mock.patch("explorer_lock_release.ctypes.windll.advapi32.AdjustTokenPrivileges", return_value=1), \
             mock.patch("explorer_lock_release.ctypes.windll.kernel32.OpenProcess", return_value=4321), \
             mock.patch("explorer_lock_release.ctypes.windll.kernel32.TerminateProcess", return_value=0), \
             mock.patch("explorer_lock_release.ctypes.windll.kernel32.CloseHandle"):
            elr.release_locking_processes(processes, registry=self.fake_reg, log=self._log)

        joined = "\n".join(self.messages)
        self.assertIn("222", joined)
        self.assertIn("TerminateProcess", joined)

    def test_log_defaults_to_no_op_without_raising(self):
        processes = [{"pid": 222, "name": "Windows 檔案總管"}]
        with mock.patch("explorer_lock_release.subprocess.check_output",
                         return_value=_tasklist_output("explorer.exe", 222)), \
             _patch_successful_termination():
            elr.release_locking_processes(processes, registry=self.fake_reg)  # 不應該拋例外


class TestRestoreAfterLockRelease(unittest.TestCase):
    def setUp(self):
        self.fake_reg = FakeWinReg()

    def test_none_state_does_nothing(self):
        with mock.patch("explorer_lock_release.subprocess.Popen") as mock_popen:
            elr.restore_after_lock_release(None, registry=self.fake_reg)
        mock_popen.assert_not_called()
        self.assertIsNone(self.fake_reg.hkcu(elr._WINLOGON_KEY))

    def test_relaunches_explorer_and_restores_previous_value(self):
        with mock.patch("explorer_lock_release.subprocess.Popen") as mock_popen:
            elr.restore_after_lock_release(
                {"previous_auto_restart_shell": "1"}, registry=self.fake_reg,
            )
        mock_popen.assert_called_once()
        self.assertEqual(mock_popen.call_args[0][0], ["explorer.exe"])
        self.assertEqual(self.fake_reg.hkcu(elr._WINLOGON_KEY)["AutoRestartShell"], "1")

    def test_missing_previous_value_restores_default(self):
        with mock.patch("explorer_lock_release.subprocess.Popen"):
            elr.restore_after_lock_release(
                {"previous_auto_restart_shell": None}, registry=self.fake_reg,
            )
        self.assertEqual(self.fake_reg.hkcu(elr._WINLOGON_KEY)["AutoRestartShell"], "1")

    def test_swallows_popen_failure(self):
        with mock.patch("explorer_lock_release.subprocess.Popen", side_effect=RuntimeError("模擬失敗")):
            elr.restore_after_lock_release(
                {"previous_auto_restart_shell": "1"}, registry=self.fake_reg,
            )  # 不應該拋例外
        self.assertEqual(self.fake_reg.hkcu(elr._WINLOGON_KEY)["AutoRestartShell"], "1")


if __name__ == "__main__":
    unittest.main()
