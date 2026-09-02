"""windows_service.py 的測試：Windows 服務建立/移除原語（sc.exe 包裝）。

全程 mock subprocess.run，不會真的呼叫 sc.exe（要系統管理員權限，也不該
依賴這台開發機的服務控制管理員狀態）。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _fakes
import windows_service


class TestCreateService(unittest.TestCase):
    def test_builds_sc_create_command_with_quoted_binpath_and_start_type(self):
        """真實抓到的 bug（unquoted service path，CWE-428）：binPath 的值
        必須加上引號。sc.exe 本身不會幫呼叫端加——即使命令列傳遞時
        subprocess/CRT 因為值裡有空白而自動加了一層引號，那只是讓 sc.exe
        的 argv 解析器把它當成一個參數，sc.exe 寫進登錄表 ImagePath 的內容
        仍然是「拿掉那層引號之後」的原始字串。沒有明確加上字面上的引號
        字元，Service Control Manager 之後解析 ImagePath 時會依序嘗試每個
        以空白分隔的前綴當可執行檔（例如先試 C:\\Program.exe）。

        這裡刻意用預設安裝路徑常見的、含空白的 Program Files 路徑當測試
        資料——原本的測試用了一個不含空白的路徑，完全看不出這個問題。
        """
        with mock.patch("windows_service.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            result = windows_service.create_service("MyService", r"C:\Program Files\MyApp\service.exe")

        self.assertTrue(result)
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[:3], ["sc.exe", "create", "MyService"])
        self.assertIn("binPath=", cmd)
        self.assertEqual(cmd[cmd.index("binPath=") + 1], r'"C:\Program Files\MyApp\service.exe"')
        self.assertIn("start=", cmd)
        self.assertEqual(cmd[cmd.index("start=") + 1], "auto")

    def test_custom_start_type_is_passed_through(self):
        with mock.patch("windows_service.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            windows_service.create_service("MyService", "svc.exe", start_type="demand")

        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[cmd.index("start=") + 1], "demand")

    def test_display_name_appends_displayname_flag(self):
        with mock.patch("windows_service.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            windows_service.create_service("MyService", "svc.exe", display_name="My Friendly Service")

        cmd = mock_run.call_args[0][0]
        self.assertIn("DisplayName=", cmd)
        self.assertEqual(cmd[cmd.index("DisplayName=") + 1], "My Friendly Service")

    def test_no_display_name_omits_displayname_flag(self):
        with mock.patch("windows_service.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            windows_service.create_service("MyService", "svc.exe")

        cmd = mock_run.call_args[0][0]
        self.assertNotIn("DisplayName=", cmd)

    def test_uses_create_no_window_flag(self):
        with mock.patch("windows_service.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            windows_service.create_service("MyService", "svc.exe")

        self.assertIn("creationflags", mock_run.call_args.kwargs)

    def test_nonzero_returncode_is_reported_as_failure(self):
        with mock.patch("windows_service.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=1)
            result = windows_service.create_service("MyService", "svc.exe")

        self.assertFalse(result)

    def test_exception_is_swallowed_and_reported_as_failure(self):
        with mock.patch("windows_service.subprocess.run", side_effect=OSError("boom")):
            result = windows_service.create_service("MyService", "svc.exe")

        self.assertFalse(result)


def _query_result(returncode, state_code=None):
    """模擬 `sc query <name>` 的回傳結果。真實輸出格式（節錄）：

        SERVICE_NAME: MyService
                TYPE               : 10  WIN32_OWN_PROCESS
                STATE              : 4  RUNNING
                ...

    服務不存在時 sc.exe 回傳非 0 exit code（真實情境是 1060）。
    """
    if returncode != 0:
        return mock.Mock(returncode=returncode, stdout="", stderr="[SC] ... 1060:\n")
    stdout = (
        "SERVICE_NAME: MyService\n"
        "        TYPE               : 10  WIN32_OWN_PROCESS\n"
        f"        STATE              : {state_code}  SOMESTATE\n"
        "        WIN32_EXIT_CODE    : 0  (0x0)\n"
    )
    return mock.Mock(returncode=0, stdout=stdout, stderr="")


class _ScRunner:
    """依 sc.exe 子命令（query/stop/delete）分流回傳值的假 subprocess.run。

    每個子命令對應一份「依序消耗」的回應佇列，佇列只剩一個時最後一個值
    會被重複回傳（跟 tests/test_bits_download.py 的 _FakeJob.GetState()
    同一種模式），讓測試不用精確算好呼叫次數。
    """

    def __init__(self, responses):
        self.responses = {k: list(v) for k, v in responses.items()}
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        sub = cmd[1]
        queue = self.responses.get(sub)
        if not queue:
            return mock.Mock(returncode=0, stdout="", stderr="")
        return queue.pop(0) if len(queue) > 1 else queue[0]

    @property
    def subcommands(self):
        return [c[1] for c in self.calls]


class TestRemoveServiceLifecycle(unittest.TestCase):
    """真實抓到的 bug（B1/F4）：`sc delete` 對一個仍在執行中的服務會回傳
    0，只是把它標記成 DELETE_PENDING，不是真的移除——舊版 remove_service()
    只看 delete 的 returncode，導致解除安裝端誤報「已移除 Windows 服務」，
    服務其實還在，指向的執行檔也還被鎖著刪不掉。這裡驗證新版的完整生命週期：
    查詢目前狀態 -> 執行中先 stop 並輪詢確認真的停止 -> delete -> 再查詢一次
    確認真的消失了，不是相信任何單一呼叫的 returncode。
    """

    def test_stopped_service_is_deleted_without_calling_stop(self):
        runner = _ScRunner({
            "query": [_query_result(0, state_code=1), _query_result(1060)],
            "delete": [mock.Mock(returncode=0)],
        })
        with mock.patch("windows_service.subprocess.run", side_effect=runner):
            result = windows_service.remove_service("MyService")

        self.assertTrue(result)
        self.assertEqual(runner.subcommands, ["query", "delete", "query"])

    def test_running_service_is_stopped_polled_then_deleted_and_verified_gone(self):
        runner = _ScRunner({
            "query": [_query_result(0, 4), _query_result(0, 1), _query_result(1060)],
            "stop": [mock.Mock(returncode=0)],
            "delete": [mock.Mock(returncode=0)],
        })
        with mock.patch("windows_service.subprocess.run", side_effect=runner), \
             mock.patch("windows_service.time.sleep"):
            result = windows_service.remove_service("MyService")

        self.assertTrue(result)
        self.assertEqual(runner.subcommands, ["query", "stop", "query", "delete", "query"])

    def test_nonexistent_service_short_circuits_to_success(self):
        runner = _ScRunner({"query": [_query_result(1060)]})
        with mock.patch("windows_service.subprocess.run", side_effect=runner):
            result = windows_service.remove_service("Ghost")

        self.assertTrue(result)
        self.assertEqual(runner.subcommands, ["query"])

    def test_delete_returns_0_but_service_still_present_is_reported_as_failure(self):
        """核心案例：sc delete 的 returncode 是 0（呼叫本身沒出錯），但
        delete 之後再查一次，服務仍然存在（例如卡在 DELETE_PENDING）——
        必須回傳 False，不能照單全收 delete 的 returncode。"""
        runner = _ScRunner({
            "query": [_query_result(0, 1), _query_result(0, 3)],
            "delete": [mock.Mock(returncode=0)],
        })
        with mock.patch("windows_service.subprocess.run", side_effect=runner):
            result = windows_service.remove_service("MyService")

        self.assertFalse(result)

    def test_sc_delete_call_exception_is_swallowed_and_reported_as_failure(self):
        def fake_run(cmd, **kwargs):
            if cmd[1] == "query":
                return _query_result(0, state_code=1)
            raise OSError("boom")

        with mock.patch("windows_service.subprocess.run", side_effect=fake_run):
            result = windows_service.remove_service("MyService")

        self.assertFalse(result)

    def test_stop_poll_gives_up_after_timeout_but_still_attempts_delete(self):
        """服務卡在 STOP_PENDING、遲遲不進 STOPPED 狀態時，輪詢要有時間
        上限（不能無限等），逾時後仍然嘗試 delete（真的刪不掉會被最後的
        verify 查詢抓到，回傳 False），不能整個卡死。"""
        runner = _ScRunner({
            "query": [_query_result(0, 4)] + [_query_result(0, 3)] * 100 + [_query_result(0, 3)],
            "stop": [mock.Mock(returncode=0)],
            "delete": [mock.Mock(returncode=0)],
        })
        fake_now = [0.0]

        def fake_monotonic():
            fake_now[0] += 1.0
            return fake_now[0]

        with mock.patch("windows_service.subprocess.run", side_effect=runner), \
             mock.patch("windows_service.time.sleep"), \
             mock.patch("windows_service.time.monotonic", side_effect=fake_monotonic):
            result = windows_service.remove_service("MyService", stop_timeout=10)

        self.assertFalse(result)
        self.assertIn("delete", runner.subcommands)


class SubprocessOutputDecodingTest(unittest.TestCase):
    """子行程輸出的解碼方式（見 tests/_fakes.py 的解碼探針說明）。

    這些測試真的起一個子行程，讓它輸出一段在系統地區編碼下無法解碼的位元組，
    再檢查受測函式最後拿到什麼——驗證的是「輸出有沒有被完整取回」，不是實作
    傳了哪些參數。
    """

    def test_the_service_state_is_still_parsed_when_output_is_not_decodable(self):
        script = _fakes.decode_probe_script(
            ascii_text="SERVICE_NAME: MySvc\n        STATE              : 4  RUNNING\n")
        with mock.patch("windows_service.subprocess.run",
                        side_effect=_fakes.decode_probe_run(script)):
            exists, state = windows_service._query_service_state("MySvc")
        self.assertTrue(exists)
        self.assertEqual(state, 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
