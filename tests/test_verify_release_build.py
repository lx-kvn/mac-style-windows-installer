"""tools/verify_release_build.py 的測試：發布前對真實產物的煙霧測試。

這支工具回答的是「編出來的這顆安裝檔，在一台沒有開發環境的真實機器上，
裝得起來、跑得動、也移得乾淨嗎」。CI 結構上回答不了它：runner 是英文的、
行程本來就已提升、且每次都明確裝好所有相依套件（見 `CLAUDE.md`）。

這裡測的是判準與客體腳本的性質，不驅動虛擬機——真的跑一次要數分鐘，而
判準寫錯的後果是「驗證通過」這四個字失去意義，那才是要釘住的東西。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# vm_lease 由另一個 repo 提供，理由與跳過方式見 tests/test_vms.py 開頭。
try:
    import vm_lease  # noqa: F401
except ImportError as exc:  # pragma: no cover - 取決於執行機器裝了什麼
    raise unittest.SkipTest(
        "vm_lease 未安裝，跳過虛擬機驅動的測試（見 tests/test_vms.py 的說明）"
    ) from exc

from tools import verify_release_build as verify


def _installed(**overrides):
    """一份「一切正常」的安裝階段報告，各測試只覆蓋它關心的那一項。"""
    report = {
        "install_exit": "0",
        "install_dir_exists": "True",
        "main_exe_exists": "True",
        "cli_exe_exists": "True",
        "uninstall_entry": "True",
        "path_contains": "True",
    }
    report.update({k: str(v) for k, v in overrides.items()})
    return report


def _removed(**overrides):
    report = {
        "uninstall_exit": "0",
        "install_dir_gone": "True",
        "uninstall_entry_gone": "True",
        "path_cleaned": "True",
    }
    report.update({k: str(v) for k, v in overrides.items()})
    return report


class ParseReport(unittest.TestCase):
    def test_a_value_may_contain_an_equals_sign(self):
        """PATH 與憑證主體都含有等號，只切第一個。"""
        parsed = verify.parse_report("path_value=C:\\a;CN=x, O=y\n")
        self.assertEqual(parsed["path_value"], "C:\\a;CN=x, O=y")

    def test_blank_and_comment_lines_are_ignored(self):
        parsed = verify.parse_report("# 開頭註解\n\ninstall_exit=0\n")
        self.assertEqual(parsed, {"install_exit": "0"})

    def test_a_missing_key_is_simply_absent(self):
        """客體在中途死掉時報告會缺項。缺項要能被判準看見，不能變成預設值。"""
        self.assertNotIn("install_exit", verify.parse_report(""))


class EvaluateInstall(unittest.TestCase):
    def test_everything_present_passes(self):
        result = verify.evaluate_install(_installed())
        self.assertEqual(result.verdict, verify.PASS)

    def test_a_non_zero_exit_fails_even_when_the_files_landed(self):
        """檔案落地不等於安裝成功：一半裝完才失敗的情形，檔案也會在。"""
        result = verify.evaluate_install(_installed(install_exit="1"))
        self.assertEqual(result.verdict, verify.FAIL)
        self.assertIn("1", result.detail)

    def test_a_missing_install_directory_fails(self):
        result = verify.evaluate_install(_installed(install_dir_exists="False"))
        self.assertEqual(result.verdict, verify.FAIL)

    def test_a_missing_bundled_exe_fails(self):
        """兩顆 exe 各自檢查。真實抓到過的缺陷是打包清單漏列，症狀正是
        安裝成功、某一顆卻不在。"""
        for key in ("main_exe_exists", "cli_exe_exists"):
            result = verify.evaluate_install(_installed(**{key: "False"}))
            self.assertEqual(result.verdict, verify.FAIL, key)
            self.assertIn(key, result.detail)

    def test_a_missing_uninstall_entry_fails(self):
        result = verify.evaluate_install(_installed(uninstall_entry="False"))
        self.assertEqual(result.verdict, verify.FAIL)

    def test_path_not_updated_fails(self):
        result = verify.evaluate_install(_installed(path_contains="False"))
        self.assertEqual(result.verdict, verify.FAIL)

    def test_an_empty_report_is_inconclusive_not_a_pass(self):
        """客體完全沒有寫回報告時，不能判成失敗也不能判成通過——那代表這一
        輪根本沒有量到東西。把它判成失敗會讓人去找不存在的缺陷。"""
        result = verify.evaluate_install({})
        self.assertEqual(result.verdict, verify.INCONCLUSIVE)


class EvaluateCliRuns(unittest.TestCase):
    """裝起來的 CLI 真的跑得動，是這支工具最有價值的一項。

    exe 存在但一執行就 ModuleNotFoundError，是這個專案實際出過的事故形態，
    而它在打包機器上完全看不出來。
    """

    def test_a_clean_run_passes(self):
        result = verify.evaluate_cli_runs({"cli_exit": "0", "cli_output": "用法：..."})
        self.assertEqual(result.verdict, verify.PASS)

    def test_a_crash_fails(self):
        result = verify.evaluate_cli_runs(
            {"cli_exit": "1", "cli_output": "ModuleNotFoundError: winrt"})
        self.assertEqual(result.verdict, verify.FAIL)
        self.assertIn("ModuleNotFoundError", result.detail)

    def test_exit_zero_with_no_output_fails(self):
        """結束碼 0 但什麼都沒印，代表它沒有真的跑到印說明那一步。"""
        result = verify.evaluate_cli_runs({"cli_exit": "0", "cli_output": ""})
        self.assertEqual(result.verdict, verify.FAIL)

    def test_a_missing_report_is_inconclusive(self):
        self.assertEqual(verify.evaluate_cli_runs({}).verdict, verify.INCONCLUSIVE)


class EvaluateUninstall(unittest.TestCase):
    def test_a_clean_removal_passes(self):
        self.assertEqual(verify.evaluate_uninstall(_removed()).verdict, verify.PASS)

    def test_leftovers_fail(self):
        for key in ("install_dir_gone", "uninstall_entry_gone", "path_cleaned"):
            result = verify.evaluate_uninstall(_removed(**{key: "False"}))
            self.assertEqual(result.verdict, verify.FAIL, key)
            self.assertIn(key, result.detail)

    def test_a_non_zero_exit_fails(self):
        self.assertEqual(
            verify.evaluate_uninstall(_removed(uninstall_exit="2")).verdict,
            verify.FAIL)


class GuestScript(unittest.TestCase):
    def test_the_report_is_written_line_by_line(self):
        """報告要逐行附加，不能全部收在記憶體最後一次寫出。

        實際踩過：客體在中途卡住時，整份報告從來沒有被寫出來，結果與
        「腳本根本沒有執行」完全無法區分。逐行附加至少留下卡在哪一步。
        """
        script = verify.guest_script("Setup_App_v1.0.0.exe", "App", "mswi-gui.exe",
                                     "mswi-cli.exe")
        self.assertIn("Add-Content", script)
        self.assertNotIn("Set-Content -Value $report", script)

    def test_it_records_the_installer_exit_code(self):
        script = verify.guest_script("Setup_App_v1.0.0.exe", "App", "mswi-gui.exe",
                                     "mswi-cli.exe")
        self.assertIn("install_exit", script)

    def test_it_installs_silently(self):
        script = verify.guest_script("Setup_App_v1.0.0.exe", "App", "mswi-gui.exe",
                                     "mswi-cli.exe")
        self.assertIn("/S", script)

    def test_the_installer_path_is_quoted(self):
        """應用程式名稱可能含有空白，路徑未加引號時 PowerShell 會把它拆成
        兩個引數，而錯誤訊息是「找不到檔案」，不會提到引號。"""
        script = verify.guest_script("Setup_My App_v1.0.0.exe", "My App",
                                     "mswi-gui.exe", "mswi-cli.exe")
        self.assertIn("-FilePath 'C:\\Users\\User\\Setup_My App_v1.0.0.exe'", script)
        self.assertIn("'Programs\\My App'", script)


class RunOnAVirtualMachine(unittest.TestCase):
    """流程本身：先量安裝、再量移除，中途失敗仍要把已量到的帶回來。"""

    class FakeVm:
        def __init__(self, report_text):
            self.report_text = report_text
            self.events = []

        def copy_in(self, local, remote):
            self.events.append(("copy_in", os.path.basename(local), remote))

        def run_program(self, *args, **kwargs):
            self.events.append(("run", args[-1]))

        def copy_out(self, remote, local):
            self.events.append(("copy_out", remote))
            with open(local, "w", encoding="utf-8") as f:
                f.write(self.report_text)

        def stop(self):
            self.events.append(("stop",))

    def _run(self, report_text, tmp):
        vm = self.FakeVm(report_text)
        setup = os.path.join(tmp, "Setup_App_v1.0.0.exe")
        with open(setup, "w") as f:
            f.write("x")
        results = verify.run(vm, setup, app_name="App", work_dir=tmp)
        return vm, results

    def test_the_installer_is_copied_in_before_anything_runs(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            vm, _ = self._run("install_exit=0\n", tmp)
            kinds = [e[0] for e in vm.events]
            self.assertLess(kinds.index("copy_in"), kinds.index("run"))

    def test_every_stage_gets_a_result(self):
        import tempfile
        report = "\n".join(
            [f"{k}={v}" for k, v in _installed().items()]
            + [f"{k}={v}" for k, v in _removed().items()]
            + ["cli_exit=0", "cli_output=用法"])
        with tempfile.TemporaryDirectory() as tmp:
            _, results = self._run(report + "\n", tmp)
        self.assertEqual([r.verdict for r in results],
                         [verify.PASS, verify.PASS, verify.PASS])

    def test_the_guest_directory_follows_the_logged_in_account(self):
        """落腳目錄要跟客體實際登入的帳號一致。

        兩台機器的起始情境用不同帳號（win11 的標準使用者情境是 `User`，
        1809 的預設情境是 `Tester`）。寫死其中一個時，另一台會把檔案放進
        一個不存在的家目錄，而 vmrun 回報的是複製失敗，不會提到帳號。
        """
        import tempfile

        class WithAccount(self.FakeVm):
            class machine:
                user = "Tester"

        with tempfile.TemporaryDirectory() as tmp:
            vm = WithAccount("")
            setup = os.path.join(tmp, "Setup_App_v1.0.0.exe")
            with open(setup, "w") as f:
                f.write("x")
            verify.run(vm, setup, app_name="App", work_dir=tmp)

        destinations = [e[2] for e in vm.events if e[0] == "copy_in"]
        self.assertTrue(destinations)
        for destination in destinations:
            self.assertTrue(destination.startswith(r"C:\Users\Tester" + "\\"),
                            destination)

    def test_a_silent_guest_yields_inconclusive_rather_than_an_exception(self):
        """客體什麼都沒寫回來時，回報「量不到」，不是拋例外——例外會讓呼叫端
        看到一個與受測產物無關的堆疊。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            _, results = self._run("", tmp)
        self.assertTrue(all(r.verdict == verify.INCONCLUSIVE for r in results))


if __name__ == "__main__":
    unittest.main(verbosity=2)
