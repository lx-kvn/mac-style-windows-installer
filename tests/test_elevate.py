"""`elevate.py`：以系統管理員權限啟動一支程式並等待它結束。

這段 ctypes 原本只存在於 `upgrade.py` 的更新覆蓋流程裡（`ShellExecuteExW`
加上 `runas` 動詞）。MSIX 的全機器佈建需要同一件事，而它需要的東西多一項：

**要分得出「使用者按了取消」與「啟動失敗」**。ADR-0013 決定四把前者視為
使用者的選擇（平靜地降級為當前使用者範圍），後者則是故障，兩者在畫面上的
語氣不同。`upgrade.py` 現行的作法是兩者都拋出同一個例外、訊息裡寫「使用者
可能取消了」——「可能」二字正是這裡要消掉的東西。

判斷依據是 `ShellExecuteExW` 失敗時系統設定的錯誤碼 `ERROR_CANCELLED`
(1223)。這是 UAC 提示被拒絕時的固定回報值。
"""
import ctypes
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import elevate


def fake_shell32(ok=1, hprocess=12345):
    shell32 = mock.Mock()

    def shell_execute(sei_ptr):
        sei_ptr.contents.hProcess = hprocess
        return ok

    shell32.ShellExecuteExW.side_effect = shell_execute
    return shell32


def fake_kernel32(wait_result=0, exit_code=0, last_error=0):
    kernel32 = mock.Mock()
    kernel32.WaitForSingleObject.return_value = wait_result
    kernel32.GetLastError.return_value = last_error

    def get_exit_code(handle, exit_code_ptr):
        exit_code_ptr.contents.value = exit_code
        return 1

    kernel32.GetExitCodeProcess.side_effect = get_exit_code
    return kernel32


class TheProcessRunsToCompletion(unittest.TestCase):
    def test_a_zero_exit_code_is_reported_as_ok(self):
        result = elevate.run_elevated_and_wait(
            "C:\\App\\child.exe", ["/FLAG"],
            shell32=fake_shell32(), kernel32=fake_kernel32())
        self.assertEqual(result.status, elevate.OK)
        self.assertEqual(result.exit_code, 0)

    def test_a_nonzero_exit_code_is_still_ok_but_carries_the_code(self):
        """「跑完了」與「跑成功了」是兩件事。結束碼的意義由呼叫端定義——
        佈建的子行程用不同的非零值表達不同的失敗，這一層不該替它判斷。"""
        result = elevate.run_elevated_and_wait(
            "C:\\App\\child.exe", [],
            shell32=fake_shell32(), kernel32=fake_kernel32(exit_code=7))
        self.assertEqual(result.status, elevate.OK)
        self.assertEqual(result.exit_code, 7)

    def test_the_handle_is_always_closed(self):
        kernel32 = fake_kernel32()
        elevate.run_elevated_and_wait("C:\\App\\child.exe", [],
                                      shell32=fake_shell32(), kernel32=kernel32)
        kernel32.CloseHandle.assert_called_once_with(12345)

    def test_the_arguments_are_quoted_when_they_contain_spaces(self):
        shell32 = fake_shell32()
        elevate.run_elevated_and_wait(
            "C:\\App\\child.exe", ["/P=C:\\Program Files\\a.msix", "/Q"],
            shell32=shell32, kernel32=fake_kernel32())
        sei = shell32.ShellExecuteExW.call_args.args[0].contents
        self.assertIn('"/P=C:\\Program Files\\a.msix"', sei.lpParameters)
        self.assertIn("/Q", sei.lpParameters)

    def test_it_asks_for_elevation(self):
        """動詞是 `runas`。少了它，子行程會直接以目前（未提權）的權杖啟動，
        佈建會在系統那一端被拒——而不是跳出 UAC。"""
        shell32 = fake_shell32()
        elevate.run_elevated_and_wait("C:\\App\\child.exe", [],
                                      shell32=shell32, kernel32=fake_kernel32())
        sei = shell32.ShellExecuteExW.call_args.args[0].contents
        self.assertEqual(sei.lpVerb, "runas")
        self.assertEqual(sei.lpFile, "C:\\App\\child.exe")


class DecliningTheUacPromptIsNotAFailure(unittest.TestCase):
    """ADR-0013 決定四：拒絕提權是使用者的選擇，要與故障分開。"""

    def test_error_cancelled_is_reported_as_declined(self):
        result = elevate.run_elevated_and_wait(
            "C:\\App\\child.exe", [],
            shell32=fake_shell32(ok=0),
            kernel32=fake_kernel32(last_error=elevate.ERROR_CANCELLED))
        self.assertEqual(result.status, elevate.DECLINED)

    def test_any_other_launch_error_is_a_failure(self):
        result = elevate.run_elevated_and_wait(
            "C:\\App\\child.exe", [],
            shell32=fake_shell32(ok=0), kernel32=fake_kernel32(last_error=2))
        self.assertEqual(result.status, elevate.FAILED)
        self.assertIn("2", result.detail)

    def test_an_unreadable_last_error_is_a_failure_not_a_decline(self):
        """錯誤碼讀不出來時當成故障。反過來猜「大概是使用者取消」會把一個
        真正的故障說成使用者的選擇，而那正是這個模組要分開的兩件事。"""
        kernel32 = fake_kernel32()
        kernel32.GetLastError.return_value = object()
        result = elevate.run_elevated_and_wait(
            "C:\\App\\child.exe", [], shell32=fake_shell32(ok=0), kernel32=kernel32)
        self.assertEqual(result.status, elevate.FAILED)


class ThingsThatGoWrongAfterTheLaunch(unittest.TestCase):
    def test_a_null_process_handle_is_a_failure(self):
        """`SEE_MASK_NOCLOSEPROCESS` 之下沒有真的產生行程時 `hProcess` 是
        NULL，而 `WaitForSingleObject(NULL, ...)` 回傳的是 WAIT_FAILED——
        不是逾時，會被誤判成等待成功（`upgrade.py` 真實抓過的問題）。"""
        kernel32 = fake_kernel32()
        result = elevate.run_elevated_and_wait(
            "C:\\App\\child.exe", [],
            shell32=fake_shell32(hprocess=None), kernel32=kernel32)
        self.assertEqual(result.status, elevate.FAILED)
        kernel32.WaitForSingleObject.assert_not_called()

    def test_a_timeout_is_its_own_status(self):
        result = elevate.run_elevated_and_wait(
            "C:\\App\\child.exe", [], timeout_ms=100,
            shell32=fake_shell32(),
            kernel32=fake_kernel32(wait_result=elevate.WAIT_TIMEOUT))
        self.assertEqual(result.status, elevate.TIMEOUT)

    def test_the_handle_is_closed_even_on_timeout(self):
        kernel32 = fake_kernel32(wait_result=elevate.WAIT_TIMEOUT)
        elevate.run_elevated_and_wait("C:\\App\\child.exe", [], timeout_ms=100,
                                      shell32=fake_shell32(), kernel32=kernel32)
        kernel32.CloseHandle.assert_called_once_with(12345)

    def test_the_timeout_is_passed_through(self):
        kernel32 = fake_kernel32()
        elevate.run_elevated_and_wait("C:\\App\\child.exe", [], timeout_ms=4321,
                                      shell32=fake_shell32(), kernel32=kernel32)
        self.assertEqual(kernel32.WaitForSingleObject.call_args.args[1], 4321)


class TheWindowStaysHidden(unittest.TestCase):
    """子行程沒有介面，跳出來的視窗只會是一個空的黑框。"""

    def test_show_command_is_hide(self):
        shell32 = fake_shell32()
        elevate.run_elevated_and_wait("C:\\App\\child.exe", [],
                                      shell32=shell32, kernel32=fake_kernel32())
        self.assertEqual(shell32.ShellExecuteExW.call_args.args[0].contents.nShow,
                         elevate.SW_HIDE)


class FallingBackToTheRealApi(unittest.TestCase):
    def test_omitting_the_seams_uses_ctypes_windll(self):
        with mock.patch("elevate.ctypes.windll.shell32.ShellExecuteExW",
                        return_value=0), \
             mock.patch("elevate.ctypes.windll.kernel32.GetLastError",
                        return_value=elevate.ERROR_CANCELLED):
            result = elevate.run_elevated_and_wait("C:\\App\\child.exe", [])
        self.assertEqual(result.status, elevate.DECLINED)


class AskingWhetherWeAreAlreadyElevated(unittest.TestCase):
    def test_it_reports_true_when_the_api_says_so(self):
        shell32 = mock.Mock()
        shell32.IsUserAnAdmin.return_value = 1
        self.assertTrue(elevate.is_elevated(shell32=shell32))

    def test_it_reports_false_when_the_api_says_so(self):
        shell32 = mock.Mock()
        shell32.IsUserAnAdmin.return_value = 0
        self.assertFalse(elevate.is_elevated(shell32=shell32))

    def test_a_failing_api_is_treated_as_not_elevated(self):
        """答不出來時當成沒有提權：後果是多跳一次 UAC，而反過來猜錯的後果
        是佈建在系統那一端被拒，錯誤訊息與真正的成因無關。"""
        shell32 = mock.Mock()
        shell32.IsUserAnAdmin.side_effect = OSError("boom")
        self.assertFalse(elevate.is_elevated(shell32=shell32))


class TheStructureMatchesTheOneItReplaces(unittest.TestCase):
    """`upgrade.py` 的那一份是實機驗證過的，欄位順序與 `cbSize` 錯了不會
    報錯，只會讓系統讀到別的東西。"""

    def test_the_size_field_is_set_to_the_structure_size(self):
        shell32 = fake_shell32()
        elevate.run_elevated_and_wait("C:\\App\\child.exe", [],
                                      shell32=shell32, kernel32=fake_kernel32())
        sei = shell32.ShellExecuteExW.call_args.args[0].contents
        self.assertEqual(sei.cbSize, ctypes.sizeof(elevate.SHELLEXECUTEINFOW))

    def test_the_process_handle_is_requested(self):
        shell32 = fake_shell32()
        elevate.run_elevated_and_wait("C:\\App\\child.exe", [],
                                      shell32=shell32, kernel32=fake_kernel32())
        sei = shell32.ShellExecuteExW.call_args.args[0].contents
        self.assertTrue(sei.fMask & elevate.SEE_MASK_NOCLOSEPROCESS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
