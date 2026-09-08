"""`msix_all_users.py`：主行程這一端怎麼取得全機器範圍，以及取不到時說什麼。

依 [ADR-0013](docs/adr/0013-msix-all-users-scope-is-an-opt-in-field.md) 與
`/grill-with-docs`（2026-09-08）的決定：

- **決定三**：`Setup.exe` 本體維持未提權，佈建交由提權的子行程執行；那個
  子行程就是 `Setup.exe` 自己帶一個內部旗標再跑一次（第一題）。
- **決定四／第二題**：直接跳 UAC，不預先詢問。取消、拒絕、帳號無法提權
  三者走同一條路——降級為當前使用者範圍，並在完成畫面說明實際發生的事。
- **第三題**：提權成功但佈建失敗不中止，退回當前使用者範圍，語氣改為警示。
- **第六題**：靜默安裝不主動跳 UAC。行程本身已提權就直接佈建，否則降級並
  把發生的事寫進紀錄，結束碼仍是 0。
- **第九題**：說明走既有的 `warnings` 陣列，互動與靜默兩邊一次到位。

**降級不是失敗。** 這個模組回報的 `ok` 只說「有沒有裝成全機器」，安裝本身
的成敗由後續的註冊決定。把降級當成安裝失敗，會讓一次其實可用的安裝變成
使用者手上什麼都沒有。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import elevate
import msix_all_users
import msix_provision


def launcher(status=elevate.OK, exit_code=0, detail=""):
    calls = []

    def launch(exe, args, timeout_ms=None):
        calls.append({"exe": exe, "args": list(args), "timeout_ms": timeout_ms})
        return elevate.Launch(status, exit_code, detail)

    launch.calls = calls
    return launch


def provision(package_path, digest, identity_name, publisher="", **kwargs):
    return msix_provision.EXIT_OK, ""


class Base(unittest.TestCase):
    def run_it(self, **overrides):
        options = dict(
            package_path="C:\\Temp\\app.msix",
            setup_exe="C:\\Temp\\Setup.exe",
            identity_name="MyApp",
            publisher="CN=Demo",
            digest="a" * 64,
            elevated=False,
            silent=False,
            launch=launcher(),
            run_in_process=provision,
            report_path=lambda: "C:\\Temp\\report.txt",
            read_report=lambda path: "",
        )
        options.update(overrides)
        return msix_all_users.provision_all_users(**options)


class TheChildIsSetupExeItself(Base):
    """第一題：不另外編一顆輔助 exe，也不呼叫 PowerShell。"""

    def test_it_launches_the_same_executable(self):
        launch = launcher()
        self.run_it(launch=launch)
        self.assertEqual(launch.calls[0]["exe"], "C:\\Temp\\Setup.exe")

    def test_it_passes_the_package_the_digest_and_the_report_path(self):
        launch = launcher()
        self.run_it(launch=launch)
        args = launch.calls[0]["args"]
        self.assertIn(msix_all_users.FLAG_PACKAGE + "C:\\Temp\\app.msix", args)
        self.assertIn(msix_all_users.FLAG_DIGEST + "a" * 64, args)
        self.assertIn(msix_all_users.FLAG_REPORT + "C:\\Temp\\report.txt", args)

    def test_it_gives_the_child_a_generous_timeout(self):
        """子行程是一顆 onefile 的安裝檔，啟動時要先把自己解壓一次——GB 級的
        安裝檔在慢速磁碟上那一段就要好幾分鐘。"""
        launch = launcher()
        self.run_it(launch=launch)
        self.assertGreaterEqual(launch.calls[0]["timeout_ms"], 600000)

    def test_a_zero_exit_code_means_it_worked(self):
        result = self.run_it()
        self.assertTrue(result.ok)
        self.assertIsNone(result.warning)


class DecliningTheUacPrompt(Base):
    """決定四：使用者的選擇，平靜地說明，不是故障。"""

    def test_it_downgrades(self):
        result = self.run_it(launch=launcher(status=elevate.DECLINED))
        self.assertFalse(result.ok)

    def test_the_wording_says_what_actually_happened(self):
        result = self.run_it(launch=launcher(status=elevate.DECLINED))
        self.assertIn("系統管理員權限", result.warning)
        self.assertIn("其他使用者", result.warning)

    def test_it_does_not_call_it_a_failure(self):
        """使用者按下取消不是壞掉。用「失敗」形容他自己的決定，會讓他去找
        一個不存在的故障。"""
        result = self.run_it(launch=launcher(status=elevate.DECLINED))
        self.assertNotIn("失敗", result.warning)

    def test_the_english_wording_says_the_same_things(self):
        result = self.run_it(launch=launcher(status=elevate.DECLINED), lang="en")
        self.assertIn("administrator", result.warning.lower())
        self.assertIn("other user", result.warning.lower())


class WhenElevationItselfGoesWrong(Base):
    """帳號無法提權、或啟動就失敗——同樣降級，但語氣是故障。"""

    def test_a_failed_launch_downgrades(self):
        result = self.run_it(
            launch=launcher(status=elevate.FAILED, detail="錯誤碼 1260"))
        self.assertFalse(result.ok)
        self.assertIn("1260", result.warning)

    def test_a_timeout_downgrades_too(self):
        result = self.run_it(launch=launcher(status=elevate.TIMEOUT, detail="等待逾時。"))
        self.assertFalse(result.ok)
        self.assertIn("其他使用者", result.warning)

    def test_a_raising_launcher_does_not_take_the_installation_down(self):
        """提權那一段爆掉時，使用者要的東西其實還拿得到——接下來的註冊會
        照常進行。讓例外往上跑等於把整次安裝賠進去。"""
        def boom(exe, args, timeout_ms=None):
            raise OSError("shell32 沒回應")

        result = self.run_it(launch=boom)
        self.assertFalse(result.ok)
        self.assertIn("shell32 沒回應", result.warning)


class WhenTheChildFails(Base):
    """第三題：不中止，退回當前使用者範圍，完成畫面用警示樣式說明。"""

    def test_a_nonzero_exit_code_downgrades(self):
        result = self.run_it(
            launch=launcher(exit_code=msix_provision.EXIT_PROVISION_FAILED))
        self.assertFalse(result.ok)

    def test_the_reason_from_the_child_reaches_the_warning(self):
        """系統給的 `error_text` 是完整且已在地化的說明。子行程沒有主控台，
        那段文字要靠報告檔才回得來。"""
        result = self.run_it(
            launch=launcher(exit_code=msix_provision.EXIT_PROVISION_FAILED),
            read_report=lambda path: "錯誤 0x80073CF9：安裝失敗。")
        self.assertIn("0x80073CF9", result.warning)

    def test_it_reads_the_report_the_child_was_told_to_write(self):
        seen = []
        self.run_it(launch=launcher(exit_code=msix_provision.EXIT_STAGE_FAILED),
                    read_report=lambda path: seen.append(path) or "")
        self.assertEqual(seen, ["C:\\Temp\\report.txt"])

    def test_an_empty_report_still_produces_a_usable_warning(self):
        """報告寫不出來的情形是預期內的（子行程那一端不為此崩潰）。此時
        至少要說出結束碼，否則使用者手上完全沒有可以查的東西。"""
        result = self.run_it(launch=launcher(exit_code=99),
                             read_report=lambda path: "")
        self.assertIn("99", result.warning)

    def test_the_report_file_is_cleaned_up(self):
        removed = []
        self.run_it(launch=launcher(exit_code=99), read_report=lambda path: "",
                    remove_report=removed.append)
        self.assertEqual(removed, ["C:\\Temp\\report.txt"])

    def test_the_report_file_is_cleaned_up_on_success_too(self):
        removed = []
        self.run_it(remove_report=removed.append)
        self.assertEqual(removed, ["C:\\Temp\\report.txt"])


class WhenTheProcessIsAlreadyElevated(Base):
    """已經提權就不必再跳一次 UAC——那個視窗只會問一個已經有答案的問題。"""

    def test_it_does_the_work_in_process(self):
        launch = launcher()
        done = []
        self.run_it(elevated=True, launch=launch,
                    run_in_process=lambda *a, **k: done.append(a) or (0, ""))
        self.assertEqual(launch.calls, [])
        self.assertEqual(len(done), 1)

    def test_it_passes_the_identity_through(self):
        seen = {}

        def run(package_path, digest, identity_name, publisher="", **kwargs):
            seen.update(package_path=package_path, digest=digest,
                        identity_name=identity_name, publisher=publisher)
            return msix_provision.EXIT_OK, ""

        self.run_it(elevated=True, run_in_process=run)
        self.assertEqual(seen["identity_name"], "MyApp")
        self.assertEqual(seen["publisher"], "CN=Demo")
        self.assertEqual(seen["package_path"], "C:\\Temp\\app.msix")

    def test_a_failure_in_process_reports_its_own_message(self):
        result = self.run_it(
            elevated=True,
            run_in_process=lambda *a, **k: (msix_provision.EXIT_PROVISION_FAILED,
                                            "登記給所有使用者時失敗：磁碟已滿"))
        self.assertFalse(result.ok)
        self.assertIn("磁碟已滿", result.warning)


class SilentInstallationNeverRaisesUac(Base):
    """第六題：跳 UAC 等於違背呼叫端「不要詢問」的前提，那個視窗會讓無人
    值守的部署卡在那裡直到逾時。"""

    def test_it_does_not_launch_anything(self):
        launch = launcher()
        self.run_it(silent=True, launch=launch)
        self.assertEqual(launch.calls, [])

    def test_it_downgrades_and_says_why(self):
        result = self.run_it(silent=True)
        self.assertFalse(result.ok)
        self.assertIn("系統管理員", result.warning)

    def test_the_wording_tells_the_operator_how_to_get_what_they_asked_for(self):
        """讀這一則的是寫部署腳本的人，不是坐在電腦前的使用者。他能做的事
        （以系統管理員身分執行）要講出來。"""
        result = self.run_it(silent=True)
        self.assertIn("以系統管理員身分執行", result.warning)

    def test_an_already_elevated_silent_run_provisions_without_asking(self):
        """部署腳本多半本來就以管理員身分執行——那種情況下行程已經是提權
        的，根本不用跳。"""
        launch = launcher()
        result = self.run_it(silent=True, elevated=True, launch=launch)
        self.assertTrue(result.ok)
        self.assertEqual(launch.calls, [])


class TheSuccessPathSaysNothingAlarming(Base):
    """第九題：`warnings` 在畫面上是警示樣式。成功時放一則進去，等於用紅色
    的字說一件沒有出錯的事。"""

    def test_no_warning_on_success(self):
        self.assertIsNone(self.run_it().warning)

    def test_the_caveat_goes_to_the_log_instead(self):
        """其他使用者要先啟動才會完成註冊——這件事仍然要留下紀錄，只是它
        不屬於警示。"""
        lines = []
        self.run_it(log=lines.append)
        self.assertTrue(any("啟動" in line for line in lines))


class ParsingTheInternalFlag(unittest.TestCase):
    """子行程靠這幾個旗標知道自己這一趟是來佈建的。"""

    def test_a_normal_command_line_is_not_provisioning(self):
        self.assertIsNone(msix_all_users.parse_arguments(["/S", "/LOG=a.txt"]))

    def test_it_reads_all_three_values(self):
        parsed = msix_all_users.parse_arguments([
            msix_all_users.FLAG_PACKAGE + "C:\\a\\app.msix",
            msix_all_users.FLAG_DIGEST + "b" * 64,
            msix_all_users.FLAG_REPORT + "C:\\a\\r.txt",
        ])
        self.assertEqual(parsed["package_path"], "C:\\a\\app.msix")
        self.assertEqual(parsed["digest"], "b" * 64)
        self.assertEqual(parsed["report_path"], "C:\\a\\r.txt")

    def test_quotes_around_a_path_with_spaces_are_stripped(self):
        parsed = msix_all_users.parse_arguments(
            ['%s"C:\\Program Files\\app.msix"' % msix_all_users.FLAG_PACKAGE])
        self.assertEqual(parsed["package_path"], "C:\\Program Files\\app.msix")

    def test_the_flag_is_matched_regardless_of_letter_case(self):
        parsed = msix_all_users.parse_arguments(["/provision=C:\\a\\app.msix"])
        self.assertEqual(parsed["package_path"], "C:\\a\\app.msix")

    def test_a_report_path_is_optional(self):
        """報告是為了把系統的說明送回主行程，不是佈建的前提。"""
        parsed = msix_all_users.parse_arguments(
            [msix_all_users.FLAG_PACKAGE + "C:\\a\\app.msix"])
        self.assertEqual(parsed["report_path"], "")

    def test_the_digest_flag_alone_is_not_provisioning_mode(self):
        """沒有套件就沒有要佈建的東西。只認雜湊旗標會讓一個打錯的命令列走
        進佈建模式，然後以一個看不懂的錯誤結束。"""
        self.assertIsNone(
            msix_all_users.parse_arguments([msix_all_users.FLAG_DIGEST + "b" * 64]))


class BuildingTheChildsCommandLine(unittest.TestCase):
    def test_it_round_trips_through_the_parser(self):
        args = msix_all_users.build_arguments(
            "C:\\Program Files\\app.msix", "c" * 64, "C:\\Temp\\r.txt")
        parsed = msix_all_users.parse_arguments(args)
        self.assertEqual(parsed["package_path"], "C:\\Program Files\\app.msix")
        self.assertEqual(parsed["digest"], "c" * 64)
        self.assertEqual(parsed["report_path"], "C:\\Temp\\r.txt")


class RunningAsTheChild(unittest.TestCase):
    """子行程那一趟：做完佈建、把說明寫進報告檔、以結束碼回報結果。"""

    def test_it_returns_the_exit_code_from_the_provisioning(self):
        with mock.patch.object(msix_provision, "run_provisioning",
                               return_value=(msix_provision.EXIT_OK, "")):
            code = msix_all_users.run_as_child(
                {"package_path": "C:\\a.msix", "digest": "d" * 64,
                 "report_path": ""}, identity_name="MyApp")
        self.assertEqual(code, msix_provision.EXIT_OK)

    def test_it_writes_the_message_to_the_report(self):
        written = {}
        with mock.patch.object(
                msix_provision, "run_provisioning",
                return_value=(msix_provision.EXIT_PROVISION_FAILED, "系統拒絕了。")):
            msix_all_users.run_as_child(
                {"package_path": "C:\\a.msix", "digest": "d" * 64,
                 "report_path": "C:\\r.txt"}, identity_name="MyApp",
                write_report=lambda path, message: written.update(
                    path=path, message=message))
        self.assertEqual(written["path"], "C:\\r.txt")
        self.assertIn("系統拒絕了。", written["message"])

    def test_an_unexpected_exception_still_produces_an_exit_code(self):
        """子行程崩潰時主行程只看得到一個結束碼。讓例外直接把它帶走，主行程
        拿到的會是一個沒有意義的數字。"""
        with mock.patch.object(msix_provision, "run_provisioning",
                               side_effect=RuntimeError("爆了")):
            code = msix_all_users.run_as_child(
                {"package_path": "C:\\a.msix", "digest": "d" * 64,
                 "report_path": ""}, identity_name="MyApp")
        self.assertNotEqual(code, msix_provision.EXIT_OK)


if __name__ == "__main__":
    unittest.main(verbosity=2)
