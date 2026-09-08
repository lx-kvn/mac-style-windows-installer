"""`tools/verify_msix_all_users.py` 的判準與客體腳本的性質。

真的跑一輪要在虛擬機上花好幾分鐘，因此驅動虛擬機的那一段不進測試；這裡釘住
的是「拿到客體回報之後怎麼判定」，以及客體腳本裡幾件錯了不會報錯、只會讓
量測悄悄變成另一件事的性質。

判定的形狀沿用 `tools/verify_release_build.py`：`PASS`／`FAIL`／
`INCONCLUSIVE`，其中第三種與第二種不同——「量不到」不是「量到失敗」。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import verify_msix_all_users as verify

IDENTITY = "MswiProbe.ScopeProbe"
VERSION = "1.2.0.0"


class TheseTestsHaveToRunOnACleanMachine(unittest.TestCase):
    """真實踩到（2026-09-08 的 CI 紅燈）：這支工具原本在最上層 `from tools
    import vms`，而 `tools/vms.py` 要求 `vm_lease`——那個套件由另一個 repo
    提供，只裝在開發機上。於是本檔在 CI 上連匯入都失敗，整份 31 項一項都沒
    跑到，而那些正是「拿到客體回報之後怎麼判定」的全部保障。

    判準寫錯的後果是拿到一個看起來有結論、實際上不成立的答案，因此它必須在
    一台乾淨的機器上被驗過。驅動虛擬機的那幾個函式仍然只在開發機上跑得起來，
    它們把 `vms` 留在函式內部匯入。

    以靜態方式確認：同一個行程裡沒辦法真的重現「模組尚未被匯入過」的狀態。
    """

    def test_the_tool_does_not_import_vms_at_module_level(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "tools", "verify_msix_all_users.py"),
                  encoding="utf-8") as f:
            source = f.read()
        top_level = [line for line in source.splitlines()
                     if line.startswith("from tools import vms")
                     or line.startswith("import tools.vms")]
        self.assertEqual(top_level, [],
                         "vms 回到了最上層，這份測試在 CI 上會整份跳不起來")


class ParsingWhatTheGuestWroteBack(unittest.TestCase):
    def test_it_reads_key_value_lines(self):
        report = verify.parse_report("child_exit=0\nstate_after=provisioned:1.2.0.0\n")
        self.assertEqual(report["child_exit"], "0")

    def test_a_value_may_contain_equals_signs(self):
        """客體寫回的值裡有等號是常態（狀態字串、命令列）。只切第一個。"""
        report = verify.parse_report("cmd=/PROVISION=C:\\a.msix\n")
        self.assertEqual(report["cmd"], "/PROVISION=C:\\a.msix")

    def test_the_byte_order_mark_does_not_become_part_of_the_first_key(self):
        """Windows PowerShell 5.1 建立檔案時會寫入 UTF-8 BOM。不剝掉的話第一個
        鍵會變成 `\\ufeffchild_exit`，而查表只會查不到——看起來像客體沒有回報
        那一步（實際踩過）。"""
        report = verify.parse_report("\ufeffchild_exit=0\n")
        self.assertIn("child_exit", report)


class TheElevatedChildActuallyProvisions(unittest.TestCase):
    """第一件要驗的事：那個內部旗標啟動的子行程，真的把套件登記給了整台機器。

    單元測試把系統那一端全部換成替身，因此「`provision_package_for_all_users_async`
    這條路在真的機器上走不走得通」在那裡驗不到。
    """

    def test_exit_zero_and_a_provisioning_record_is_a_pass(self):
        result = verify.evaluate_child_provisions({
            "good_before": "none",
            "good_exit": "0",
            "good_after": "provisioned:" + VERSION,
        })
        self.assertEqual(result.verdict, verify.PASS)

    def test_a_nonzero_exit_is_a_failure_and_the_reason_is_carried_over(self):
        result = verify.evaluate_child_provisions({
            "good_before": "none",
            "good_exit": "15",
            "good_after": "none",
            "good_report": "登記給所有使用者時失敗：系統拒絕了這個要求。",
        })
        self.assertEqual(result.verdict, verify.FAIL)
        self.assertIn("系統拒絕了這個要求。", result.detail)

    def test_exit_zero_without_a_provisioning_record_is_a_failure(self):
        """回報成功卻沒有登記到，正是這個專案最怕的那一種結果——安裝檔說裝好
        了，實際上什麼都沒發生。"""
        result = verify.evaluate_child_provisions({
            "good_before": "none", "good_exit": "0", "good_after": "none",
        })
        self.assertEqual(result.verdict, verify.FAIL)

    def test_a_missing_step_is_inconclusive_not_a_failure(self):
        result = verify.evaluate_child_provisions({"good_exit": "0"})
        self.assertEqual(result.verdict, verify.INCONCLUSIVE)

    def test_a_dirty_starting_state_is_inconclusive(self):
        """機器上已經有佈建紀錄時，「事後有紀錄」證明不了是這一次留下的。"""
        result = verify.evaluate_child_provisions({
            "good_before": "provisioned:1.1.0.0",
            "good_exit": "0",
            "good_after": "provisioned:" + VERSION,
        })
        self.assertEqual(result.verdict, verify.INCONCLUSIVE)


class TheDigestGuardRefusesAForeignPackage(unittest.TestCase):
    """那個旗標任何人都能打，而它會以提權身分佈建參數指定的套件。雜湊不符就
    拒絕，是這條路徑唯一的關卡。"""

    def test_the_documented_exit_code_and_no_provisioning_is_a_pass(self):
        result = verify.evaluate_digest_guard({
            "bad_before": "none", "bad_exit": "11", "bad_after": "none",
        })
        self.assertEqual(result.verdict, verify.PASS)

    def test_provisioning_anyway_is_a_failure(self):
        """比對不符卻還是裝上去了——這個測試存在的全部理由。"""
        result = verify.evaluate_digest_guard({
            "bad_before": "none", "bad_exit": "0",
            "bad_after": "provisioned:" + VERSION,
        })
        self.assertEqual(result.verdict, verify.FAIL)

    def test_a_different_nonzero_exit_code_is_a_failure(self):
        """拒絕了，但拒絕的理由不是雜湊——例如根本沒讀到檔案。那樣的話這一關
        沒有被驗到，而結果看起來一樣。"""
        result = verify.evaluate_digest_guard({
            "bad_before": "none", "bad_exit": "10", "bad_after": "none",
        })
        self.assertEqual(result.verdict, verify.FAIL)

    def test_a_missing_step_is_inconclusive(self):
        self.assertEqual(verify.evaluate_digest_guard({}).verdict,
                         verify.INCONCLUSIVE)


class SilentInstallationWithoutAdminRights(unittest.TestCase):
    """第六題：不跳 UAC、降級、寫進紀錄、結束碼仍是 0。"""

    def test_the_expected_shape_is_a_pass(self):
        result = verify.evaluate_silent_downgrade({
            "silent_exit": "0",
            "silent_state": "user:" + VERSION + ":Ok",
            "silent_provisioned": "none",
            "silent_log": "[警告] 這個安裝檔設定為安裝給這台電腦上的所有使用者，"
                          "但靜默安裝不會跳出權限要求...只安裝給目前這位使用者。",
        })
        self.assertEqual(result.verdict, verify.PASS)

    def test_a_nonzero_exit_code_is_a_failure(self):
        """降級之後的安裝是成功且可用的。回非零等於讓部署腳本把一次成功當成
        失敗處理。"""
        result = verify.evaluate_silent_downgrade({
            "silent_exit": "1",
            "silent_state": "user:" + VERSION + ":Ok",
            "silent_provisioned": "none",
            "silent_log": "只安裝給目前這位使用者",
        })
        self.assertEqual(result.verdict, verify.FAIL)

    def test_provisioning_without_admin_rights_would_be_a_failure(self):
        """未提權竟然登記成了整台機器，代表權限判斷是錯的。"""
        result = verify.evaluate_silent_downgrade({
            "silent_exit": "0",
            "silent_state": "user:" + VERSION + ":Ok",
            "silent_provisioned": "provisioned:" + VERSION,
            "silent_log": "只安裝給目前這位使用者",
        })
        self.assertEqual(result.verdict, verify.FAIL)

    def test_a_log_that_does_not_mention_it_is_a_failure(self):
        """紀錄檔是無人值守情境下這件事唯一的出口。沒寫進去，等於降級這件事
        無人知悉——那正是 ADR-0009 決定三否決降級的理由。"""
        result = verify.evaluate_silent_downgrade({
            "silent_exit": "0",
            "silent_state": "user:" + VERSION + ":Ok",
            "silent_provisioned": "none",
            "silent_log": "[成功] 安裝成功。",
        })
        self.assertEqual(result.verdict, verify.FAIL)

    def test_the_application_not_being_installed_at_all_is_a_failure(self):
        result = verify.evaluate_silent_downgrade({
            "silent_exit": "0", "silent_state": "none",
            "silent_provisioned": "none", "silent_log": "只安裝給目前這位使用者",
        })
        self.assertEqual(result.verdict, verify.FAIL)


class SilentInstallationAsAnAdministrator(unittest.TestCase):
    """已提權就直接佈建，不跳 UAC。

    這一輪同時量到一件既有的事：提權的行程替當前使用者註冊會被系統拒
    （ADR-0013 背景第五項），因此整個安裝的結束碼是非零的。那不是這個功能
    造成的，但它決定了「以管理員身分跑靜默安裝」實際上會得到什麼，因此照實
    記錄，不併進判定。
    """

    def test_a_provisioning_record_is_what_this_round_is_about(self):
        result = verify.evaluate_silent_elevated({
            "elevated_exit": "1",
            "elevated_provisioned": "provisioned:" + VERSION,
        })
        self.assertEqual(result.verdict, verify.PASS)

    def test_the_exit_code_is_reported_but_does_not_decide_it(self):
        result = verify.evaluate_silent_elevated({
            "elevated_exit": "1",
            "elevated_provisioned": "provisioned:" + VERSION,
        })
        self.assertIn("1", result.detail)

    def test_no_provisioning_record_is_a_failure(self):
        result = verify.evaluate_silent_elevated({
            "elevated_exit": "0", "elevated_provisioned": "none",
        })
        self.assertEqual(result.verdict, verify.FAIL)

    def test_a_missing_step_is_inconclusive(self):
        self.assertEqual(verify.evaluate_silent_elevated({}).verdict,
                         verify.INCONCLUSIVE)


class TheGuestScriptsHaveToBeShapedRight(unittest.TestCase):
    """幾件錯了不會報錯、只會讓量測悄悄變成另一件事的性質。"""

    def setUp(self):
        self.child = verify.child_round_script(
            r"C:\Users\Public\Setup.exe", r"C:\Users\Public\probe.msix",
            "a" * 64, IDENTITY)

    def test_the_bad_digest_run_comes_first(self):
        """順序反過來的話，比對不符那一次是在「已經有佈建紀錄」的機器上跑的，
        而它的判準正是「事後沒有佈建紀錄」——那個判準會無條件通過。這與上一輪
        量測踩到的陷阱同一種（腳本順序讓失敗回的是另一個錯誤碼）。"""
        self.assertLess(self.child.index("bad_exit"), self.child.index("good_exit"))

    def test_every_step_records_the_state_before_it(self):
        """上一輪的結果之所以無法採信，就是因為沒有記錄前置狀態。"""
        for key in ("bad_before", "good_before"):
            self.assertIn(key, self.child)

    def test_the_deliberately_wrong_digest_is_not_the_real_one(self):
        self.assertIn("0" * 64, self.child)

    def test_it_waits_for_the_child_and_reads_its_exit_code(self):
        """`Start-Process` 不加 `-Wait` 會立刻回來，之後量到的狀態是佈建還沒
        做完時的狀態——而那看起來就像佈建失敗。"""
        self.assertIn("-Wait", self.child)
        self.assertIn("ExitCode", self.child)

    def test_it_reads_the_report_the_child_wrote(self):
        """子行程沒有主控台。系統給的那段說明只有透過報告檔才回得來。"""
        self.assertIn("good_report", self.child)

    def test_the_silent_round_asks_for_a_log_file(self):
        """降級的說明在靜默模式下只有這一個出口。"""
        script = verify.silent_round_script(r"C:\Users\Public\Setup.exe", IDENTITY,
                                            r"C:\Users\Public\silent.log")
        self.assertIn("/LOG=", script)
        self.assertIn("silent_log", script)

    def test_the_silent_round_does_not_ask_for_the_provisioning_record(self):
        """`Get-AppxProvisionedPackage -Online` 需要管理員權限，而這一輪整個
        重點就是它跑在未提權的工作階段裡。在這裡問只會拿到一個看起來像
        「沒有佈建」的錯誤——與真正的答案難以區分。"""
        script = verify.silent_round_script(r"C:\Users\Public\Setup.exe", IDENTITY,
                                            r"C:\Users\Public\silent.log")
        self.assertNotIn("Get-AppxProvisionedPackage", script)

    def test_the_provisioning_record_is_checked_by_an_elevated_follow_up(self):
        script = verify.silent_check_script(IDENTITY)
        self.assertIn("Get-AppxProvisionedPackage", script)
        self.assertIn("silent_provisioned", script)

    def test_files_written_by_the_product_are_read_as_utf8(self):
        """實際踩到：安裝檔把紀錄寫成 UTF-8，而 Windows PowerShell 5.1 的
        `Get-Content` 預設以系統的 ANSI 字碼頁解碼（這台機器是 cp950）。少了
        `-Encoding UTF8`，讀回來的是一整段亂碼，而判定要找的是一句中文——
        於是一次其實正確的行為被判成失敗，理由與產品無關。

        這一類錯誤在英文機器上不會現形，CI 因此也驗不到。
        """
        for script in (self.child,
                       verify.silent_round_script("a", IDENTITY, "b"),
                       verify.elevated_round_script("a", IDENTITY, "b")):
            self.assertIn("Get-Content", script)
            self.assertIn("-Encoding UTF8", script.split("Get-Content", 1)[1])

    def test_every_script_appends_rather_than_replaces_the_report(self):
        """一輪裡有提權與未提權兩支腳本，各自寫同一份報告。用覆寫的話，後跑的
        那一支會把前一支的結果清掉，而缺步驟的判定是「無法判定」——看起來像
        機器有問題，不像腳本互相覆蓋。"""
        for script in (self.child,
                       verify.silent_round_script("a", IDENTITY, "b"),
                       verify.silent_check_script(IDENTITY),
                       verify.elevated_round_script("a", IDENTITY, "b")):
            self.assertIn("-Append", script)


if __name__ == "__main__":
    unittest.main(verbosity=2)
