"""restart_manager.py 的測試。

包裝 Windows Restart Manager API（Rstrtmgr.dll），取代 uninstall.py 舊版
「一律假設鎖住檔案的是 explorer.exe」的寫死做法，改成實際偵測是哪些進程
持有指定檔案的控制代碼。真正的 Rstrtmgr.dll 呼叫沒辦法在測試環境重現
（需要真的有檔案被鎖定），這裡用一個假的 DLL 物件（提供跟真實 API 一樣
的四個方法）注入進 find_locking_processes()，驗證兩階段 RmGetList
（先問需要多大的 buffer，再拿完整清單）、pid 去重、以及各種失敗路徑的
best-effort 容錯（不拋例外、回傳空清單）。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import restart_manager as rm


class _FakeRstrtmgr:
    """模擬 Rstrtmgr.dll 四個函式的行為，讓 find_locking_processes() 的流程
    邏輯可以在沒有真實鎖定檔案的情況下被驗證。"""

    def __init__(self, processes=None, start_result=0, register_result=0, get_list_result=0,
                 shutdown_result=0, restart_result=0):
        self._processes = processes or []
        self._start_result = start_result
        self._register_result = register_result
        self._get_list_result = get_list_result
        self._shutdown_result = shutdown_result
        self._restart_result = restart_result
        self.registered_filenames = None
        self.ended = False
        self.shutdown_calls = []
        self.restart_calls = 0

    def RmStartSession(self, session_handle_ref, flags, session_key):
        if self._start_result == 0:
            session_handle_ref._obj.value = 1
        return self._start_result

    def RmRegisterResources(self, session_handle, n_files, filenames, n_apps, apps, n_svcs, svcs):
        self.registered_filenames = list(filenames) if filenames else []
        return self._register_result

    def RmGetList(self, session_handle, needed_ref, count_ref, array, reboot_reasons_ref):
        if self._get_list_result != 0:
            return self._get_list_result
        if array is None:
            needed_ref._obj.value = len(self._processes)
            return rm.ERROR_MORE_DATA if self._processes else rm.ERROR_SUCCESS
        for i, (pid, name) in enumerate(self._processes):
            array[i].Process.dwProcessId = pid
            array[i].strAppName = name
        count_ref._obj.value = len(self._processes)
        return rm.ERROR_SUCCESS

    def RmEndSession(self, session_handle):
        self.ended = True
        return 0

    def RmShutdown(self, session_handle, flags, fn_status):
        self.shutdown_calls.append(flags)
        return self._shutdown_result

    def RmRestart(self, session_handle, flags, fn_status):
        self.restart_calls += 1
        return self._restart_result


class TestFindLockingProcesses(unittest.TestCase):
    def test_empty_file_list_returns_empty_without_calling_api(self):
        fake = _FakeRstrtmgr(processes=[(111, "應用程式")])
        result = rm.find_locking_processes([], rm_dll=fake)
        self.assertEqual(result, [])
        self.assertIsNone(fake.registered_filenames)

    def test_returns_processes_found_by_two_phase_rm_get_list(self):
        fake = _FakeRstrtmgr(processes=[(111, "Windows 檔案總管"), (222, "某個應用程式")])
        result = rm.find_locking_processes([r"C:\app\locked.dll"], rm_dll=fake)
        self.assertEqual(result, [(111, "Windows 檔案總管"), (222, "某個應用程式")])
        self.assertEqual(fake.registered_filenames, [r"C:\app\locked.dll"])
        self.assertTrue(fake.ended)

    def test_no_locking_processes_returns_empty_list(self):
        fake = _FakeRstrtmgr(processes=[])
        result = rm.find_locking_processes([r"C:\app\free.dll"], rm_dll=fake)
        self.assertEqual(result, [])

    def test_dedupes_by_pid(self):
        fake = _FakeRstrtmgr(processes=[(111, "App A"), (111, "App A")])
        result = rm.find_locking_processes([r"C:\app\locked.dll"], rm_dll=fake)
        self.assertEqual(result, [(111, "App A")])

    def test_start_session_failure_returns_empty_list(self):
        fake = _FakeRstrtmgr(processes=[(111, "App")], start_result=5)
        result = rm.find_locking_processes([r"C:\app\locked.dll"], rm_dll=fake)
        self.assertEqual(result, [])

    def test_register_resources_failure_returns_empty_list(self):
        fake = _FakeRstrtmgr(processes=[(111, "App")], register_result=5)
        result = rm.find_locking_processes([r"C:\app\locked.dll"], rm_dll=fake)
        self.assertEqual(result, [])

    def test_get_list_failure_returns_empty_list(self):
        fake = _FakeRstrtmgr(processes=[(111, "App")], get_list_result=5)
        result = rm.find_locking_processes([r"C:\app\locked.dll"], rm_dll=fake)
        self.assertEqual(result, [])

    def test_dll_load_failure_returns_empty_list_without_raising(self):
        with mock.patch("restart_manager._load_rstrtmgr", side_effect=OSError("找不到 DLL")):
            result = rm.find_locking_processes([r"C:\app\locked.dll"])
        self.assertEqual(result, [])

    def test_unexpected_exception_during_session_is_swallowed(self):
        class _BrokenDll:
            def RmStartSession(self, *a, **kw):
                raise RuntimeError("模擬失敗")

        result = rm.find_locking_processes([r"C:\app\locked.dll"], rm_dll=_BrokenDll())
        self.assertEqual(result, [])


class TestRestartManagerSession(unittest.TestCase):
    """RestartManagerSession：跨步驟持有 session（跟 find_locking_processes()
    「開 session 用完即關」的單函式生命週期不同），讓呼叫端可以先查鎖定
    清單、再對支援 Restart Manager 的應用程式呼叫 shutdown() 請它優雅
    關閉，檔案操作完成後 restart() 把它啟動回來，最後 close()。"""

    def test_open_session_registers_resources_and_is_open(self):
        fake = _FakeRstrtmgr(processes=[(111, "App")])
        session = rm.RestartManagerSession([r"C:\app\locked.dll"], rm_dll=fake)
        self.assertTrue(session.is_open)
        self.assertEqual(fake.registered_filenames, [r"C:\app\locked.dll"])
        session.close()

    def test_start_session_failure_leaves_session_closed(self):
        fake = _FakeRstrtmgr(start_result=5)
        session = rm.RestartManagerSession([r"C:\app\locked.dll"], rm_dll=fake)
        self.assertFalse(session.is_open)

    def test_list_locking_processes_reuses_open_session(self):
        fake = _FakeRstrtmgr(processes=[(111, "Windows 檔案總管")])
        session = rm.RestartManagerSession([r"C:\app\locked.dll"], rm_dll=fake)
        result = session.list_locking_processes()
        self.assertEqual(result, [(111, "Windows 檔案總管")])
        session.close()

    def test_list_locking_processes_on_closed_session_returns_empty(self):
        fake = _FakeRstrtmgr(start_result=5)
        session = rm.RestartManagerSession([r"C:\app\locked.dll"], rm_dll=fake)
        self.assertEqual(session.list_locking_processes(), [])

    def test_shutdown_calls_rm_shutdown_and_reports_success(self):
        fake = _FakeRstrtmgr()
        session = rm.RestartManagerSession([r"C:\app\locked.dll"], rm_dll=fake)
        self.assertTrue(session.shutdown())
        self.assertEqual(fake.shutdown_calls, [0])
        session.close()

    def test_shutdown_force_passes_force_shutdown_flag(self):
        fake = _FakeRstrtmgr()
        session = rm.RestartManagerSession([r"C:\app\locked.dll"], rm_dll=fake)
        session.shutdown(force=True)
        self.assertEqual(fake.shutdown_calls, [rm._RM_FORCE_SHUTDOWN])
        session.close()

    def test_shutdown_failure_reports_false(self):
        fake = _FakeRstrtmgr(shutdown_result=5)
        session = rm.RestartManagerSession([r"C:\app\locked.dll"], rm_dll=fake)
        self.assertFalse(session.shutdown())

    def test_shutdown_on_closed_session_returns_false_without_calling_dll(self):
        fake = _FakeRstrtmgr(start_result=5)
        session = rm.RestartManagerSession([r"C:\app\locked.dll"], rm_dll=fake)
        self.assertFalse(session.shutdown())
        self.assertEqual(fake.shutdown_calls, [])

    def test_restart_calls_rm_restart_and_reports_success(self):
        fake = _FakeRstrtmgr()
        session = rm.RestartManagerSession([r"C:\app\locked.dll"], rm_dll=fake)
        self.assertTrue(session.restart())
        self.assertEqual(fake.restart_calls, 1)
        session.close()

    def test_restart_failure_reports_false(self):
        fake = _FakeRstrtmgr(restart_result=5)
        session = rm.RestartManagerSession([r"C:\app\locked.dll"], rm_dll=fake)
        self.assertFalse(session.restart())

    def test_close_ends_session_and_is_idempotent(self):
        fake = _FakeRstrtmgr()
        session = rm.RestartManagerSession([r"C:\app\locked.dll"], rm_dll=fake)
        session.close()
        self.assertTrue(fake.ended)
        self.assertFalse(session.is_open)
        fake.ended = False
        session.close()  # 再呼叫一次不該再去呼叫 RmEndSession
        self.assertFalse(fake.ended)

    def test_context_manager_closes_session_on_exit(self):
        fake = _FakeRstrtmgr()
        with rm.RestartManagerSession([r"C:\app\locked.dll"], rm_dll=fake) as session:
            self.assertTrue(session.is_open)
        self.assertTrue(fake.ended)

    def test_context_manager_closes_session_even_when_body_raises(self):
        fake = _FakeRstrtmgr()
        with self.assertRaises(RuntimeError):
            with rm.RestartManagerSession([r"C:\app\locked.dll"], rm_dll=fake):
                raise RuntimeError("模擬呼叫端例外")
        self.assertTrue(fake.ended)

    def test_dll_load_failure_leaves_session_closed_without_raising(self):
        with mock.patch("restart_manager._load_rstrtmgr", side_effect=OSError("找不到 DLL")):
            session = rm.RestartManagerSession([r"C:\app\locked.dll"])
        self.assertFalse(session.is_open)


if __name__ == "__main__":
    unittest.main()
