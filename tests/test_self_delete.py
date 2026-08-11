"""self_delete.py 的測試（從 tests/test_uninstall.py 搬過來）。

拆出來的深模組：uninstall.exe 解除安裝完成後自我刪除的 .bat 產生 + 重試
邏輯，跟 --upgrade 旗標的排程判斷。原本這些測試就已經直接測
uninstall._schedule_self_delete()/_is_upgrade_call()/
_should_schedule_self_delete() 這幾個「事實上獨立」的函式，只是它們原本
活在 uninstall.py 裡；現在它們有了自己的檔案跟模組名稱，測試也跟著搬過來，
不用再透過 uninstall.py 這層間接引用。
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import self_delete


class TestUpgradeSelfDeleteGating(unittest.TestCase):
    """真實抓到的 bug：自我刪除是 fire-and-forget（背景、不等待的
    cmd.exe，先 ping 製造約 1 秒延遲才真正 del/rmdir）。更新覆蓋安裝時，
    installer_core.py 用 subprocess.run() 同步呼叫舊版 uninstall.exe，
    行程一結束就繼續複製新版本檔案——這時候背景那個延遲後才執行的
    rmdir /s /q 根本還沒發生，如果複製時間跨過那個延遲視窗，會把整個
    資料夾（含新複製好的檔案）一起砍掉，導致「安裝回報成功但檔案沒有
    複製完整」。修正：--upgrade 旗標讓舊版 uninstall.exe 完全不排這段
    背景指令。"""

    def test_is_upgrade_call_detects_flag(self):
        self.assertTrue(self_delete.is_upgrade_call(["uninstall.exe", "--silent", "--upgrade"]))

    def test_is_upgrade_call_false_without_flag(self):
        self.assertFalse(self_delete.is_upgrade_call(["uninstall.exe", "--silent"]))

    def test_self_delete_skipped_when_upgrade(self):
        self.assertFalse(self_delete._should_schedule(is_upgrade=True))

    def test_self_delete_scheduled_when_not_upgrade(self):
        self.assertTrue(self_delete._should_schedule(is_upgrade=False))


class TestScheduleIfNeeded(unittest.TestCase):
    """真實抓到的 bug（第一輪）：原本固定延遲約 1 秒就砍一次、不管成不
    成功——這在純 console 程式上大致成立，但 uninstall.exe 現在內嵌了
    WebView2 runtime，行程真正結束可能不只 1 秒，del/rmdir 失敗時又被
    靜默吞掉、不會重試，導致解除安裝完成後常常沒有真的把自己刪掉。

    真實抓到的 bug（第二輪）：`--noconsole` 編譯之後這支 exe 沒有主控台，
    stdin/stdout/stderr 是無效控制代碼，subprocess.Popen(shell=True) 沒有
    明確指定這三個會嘗試繼承、導致 CreateProcess 直接失敗、自我刪除完全
    沒被排上去。修正：明確指定 stdin/stdout/stderr=DEVNULL。

    真實抓到的 bug（第三輪，用「持有檔案控制代碼 5 秒後放開」實際重現才
    抓到）：第一輪改用的 `for /l %i in (...) do (...)` 重試迴圈，實測
    發現 cmd.exe 把整個迴圈主體當一次性解析的靜態區塊，即使檔案的鎖真的
    在中途放開了，同一個迴圈後續每一輪還是持續回報刪除失敗，20 次重試
    全部落空。改成寫一個暫存 `.bat`，用傳統 `:retry`/`goto retry` 標籤式
    重試（每次 goto 跳回都是重新解析那一行開始的內容，不是包在同一組
    括號裡的靜態區塊），實測鎖一放開就能立刻在下一輪重試成功。這裡直接
    讓 `schedule_if_needed()` 真的把 .bat 寫到磁碟（tempfile.gettempdir()，
    只是暫存檔，測試結束後清掉），檢查寫出來的內容而不是回去斷言真的
    執行整個 cmd 重試流程（那需要模擬真實的檔案鎖定情境）。
    """

    # 不帶 --upgrade，讓 schedule_if_needed() 內部的排程判斷放行，
    # 才能測到實際排程/寫 .bat 的行為本身。
    _NOT_UPGRADE_ARGV = ["uninstall.exe"]

    def _run_and_capture_bat(self, current_dir, exe_path, safe_to_remove_whole_dir):
        with mock.patch("self_delete.subprocess.Popen") as mock_popen:
            self_delete.schedule_if_needed(
                self._NOT_UPGRADE_ARGV, current_dir, exe_path, safe_to_remove_whole_dir
            )
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        bat_path = cmd.strip('"')
        self.assertTrue(os.path.exists(bat_path), f"預期 .bat 已寫入磁碟：{bat_path}")
        with open(bat_path, "r", encoding="mbcs") as f:
            content = f.read()
        os.remove(bat_path)
        return content, mock_popen

    def test_retries_delete_and_rmdir_when_safe_to_remove_whole_dir(self):
        content, mock_popen = self._run_and_capture_bat("C:\\App", "C:\\App\\uninstall.exe", True)
        self.assertIn(":retry", content)
        self.assertIn(":giveup", content)
        self.assertIn('del /f /q "C:\\App\\uninstall.exe"', content)
        self.assertIn('rmdir /s /q "C:\\App"', content)
        self.assertIn('del /f /q "%~f0"', content)
        self.assertTrue(mock_popen.call_args.kwargs.get("shell"))

    def test_retries_delete_without_rmdir_when_not_safe(self):
        content, _ = self._run_and_capture_bat("C:\\App", "C:\\App\\uninstall.exe", False)
        self.assertIn('del /f /q "C:\\App\\uninstall.exe"', content)
        self.assertNotIn("rmdir", content)

    def test_redirects_stdio_to_devnull_to_avoid_noconsole_invalid_handle(self):
        _, mock_popen = self._run_and_capture_bat("C:\\App", "C:\\App\\uninstall.exe", True)
        kwargs = mock_popen.call_args.kwargs
        self.assertEqual(kwargs.get("stdin"), self_delete.subprocess.DEVNULL)
        self.assertEqual(kwargs.get("stdout"), self_delete.subprocess.DEVNULL)
        self.assertEqual(kwargs.get("stderr"), self_delete.subprocess.DEVNULL)

    def test_popen_failure_is_swallowed(self):
        try:
            with mock.patch("self_delete.subprocess.Popen", side_effect=OSError("boom")):
                self_delete.schedule_if_needed(
                    self._NOT_UPGRADE_ARGV, "C:\\App", "C:\\App\\uninstall.exe", True
                )  # 不應該拋出
        finally:
            bat_path = os.path.join(
                tempfile.gettempdir(), f"_mswi_uninstall_cleanup_{os.getpid()}.bat"
            )
            if os.path.exists(bat_path):
                os.remove(bat_path)

    def test_skips_entirely_when_upgrade_call(self):
        """schedule_if_needed() 把「要不要排程」的前置判斷收進來——帶
        --upgrade 時完全不該去寫 .bat/呼叫 Popen。"""
        with mock.patch("self_delete.subprocess.Popen") as mock_popen:
            self_delete.schedule_if_needed(
                ["uninstall.exe", "--upgrade"], "C:\\App", "C:\\App\\uninstall.exe", True
            )
        mock_popen.assert_not_called()


class TestScheduleIfNeededNonAnsiPathFallback(unittest.TestCase):
    """F17：真實抓到的 bug——`.bat` 內容固定用系統目前的 ANSI 編碼
    （`mbcs`）寫入，安裝路徑如果含有這個編碼表示不了的字元（例如系統
    locale 跟安裝路徑語系不一致），`open(..., encoding="mbcs").write()`
    會丟 UnicodeEncodeError，原本整段被最外層 `except Exception: pass`
    吞掉，`uninstall.exe` 永遠不會被排程自我刪除、也完全沒有任何記錄。
    修法：改用 8.3 短路徑名稱（純 ASCII）重試一次，兩種結果都要記錄。"""

    _NOT_UPGRADE_ARGV = ["uninstall.exe"]

    def test_falls_back_to_short_path_when_mbcs_encoding_fails(self):
        calls = {"n": 0}

        def fake_write(bat_path, content):
            calls["n"] += 1
            if calls["n"] == 1:
                raise UnicodeEncodeError("mbcs", "\u65e5", 0, 1, "no mapping")
            with open(bat_path, "w", encoding="utf-8") as f:
                f.write(content)

        log_messages = []
        try:
            with mock.patch("self_delete._write_bat_file", side_effect=fake_write), \
                 mock.patch("self_delete._get_short_path", return_value="C:\\SHORT~1\\U.EXE"), \
                 mock.patch("self_delete.subprocess.Popen") as mock_popen:
                self_delete.schedule_if_needed(
                    self._NOT_UPGRADE_ARGV, "C:\\日本語\\App", "C:\\日本語\\App\\uninstall.exe", True,
                    log=log_messages.append,
                )
            mock_popen.assert_called_once()
            self.assertEqual(calls["n"], 2)
            self.assertTrue(any("短路徑" in m for m in log_messages), log_messages)
        finally:
            bat_path = os.path.join(
                tempfile.gettempdir(), f"_mswi_uninstall_cleanup_{os.getpid()}.bat"
            )
            if os.path.exists(bat_path):
                os.remove(bat_path)

    def test_gives_up_and_logs_when_short_path_unavailable(self):
        log_messages = []
        with mock.patch("self_delete._write_bat_file", side_effect=UnicodeEncodeError("mbcs", "\u65e5", 0, 1, "no mapping")), \
             mock.patch("self_delete._get_short_path", return_value=None), \
             mock.patch("self_delete.subprocess.Popen") as mock_popen:
            self_delete.schedule_if_needed(
                self._NOT_UPGRADE_ARGV, "C:\\日本語\\App", "C:\\日本語\\App\\uninstall.exe", True,
                log=log_messages.append,
            )
        mock_popen.assert_not_called()
        self.assertTrue(log_messages, "非 ANSI 路徑且短路徑也拿不到時，必須留下記錄，不能靜默放棄")

    def test_default_log_is_a_noop_when_not_provided(self):
        # 沒帶 log 參數（維持舊呼叫端相容）不應該讓函式整個炸掉。
        with mock.patch("self_delete._write_bat_file", side_effect=UnicodeEncodeError("mbcs", "\u65e5", 0, 1, "no mapping")), \
             mock.patch("self_delete._get_short_path", return_value=None), \
             mock.patch("self_delete.subprocess.Popen") as mock_popen:
            self_delete.schedule_if_needed(
                self._NOT_UPGRADE_ARGV, "C:\\日本語\\App", "C:\\日本語\\App\\uninstall.exe", True,
            )
        mock_popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
