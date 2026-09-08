"""`tools/verify_upgrade_install.py` 的判準與客體腳本的性質。

更新覆蓋安裝是**唯一會刪除使用者既有檔案**的路徑：備份舊資料夾 → 呼叫舊版
的解除安裝程式移除 → 裝新版 → 失敗時把備份復原。它在任何真實機器上都沒有
被跑過，而 `upgrade.py` 的說明裡記著三輪真實抓到的 bug，其中兩輪的症狀是
「時好時壞」與「回報成功但檔案沒有複製完整」——都不是會當場報錯的那種。

## 為什麼一定要在未提權的桌面上跑

`upgrade.py` 有一道安全防護：舊版的登錄表項目在 HKCU、而目前這個行程已經
提權時，它拒絕代為執行那支舊版解除安裝程式（來源不受信任 + 已提權 = 任意
程式碼執行）。**CI 的 runner 本來就是提權的**，因此 `no_admin_install=true`
的升級路徑在 CI 上必定走進那個拒絕分支，測不到真正要測的東西。而
`no_admin_install=true` 正是這個專案自己的安裝檔所用的設定。

判準寫在這裡、驅動虛擬機的部分不寫測試：真的跑一輪要好幾分鐘。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import verify_upgrade_install as verify

APP = "MswiUpgradeProbe"


class TheseTestsHaveToRunOnACleanMachine(unittest.TestCase):
    """與 `test_verify_msix_all_users.py` 同一個理由：判準必須在一台沒有
    `vm_lease` 的機器上也跑得起來，否則 CI 上整份跳過，等於沒有保障。"""

    def test_the_tool_does_not_import_vms_at_module_level(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "tools", "verify_upgrade_install.py"),
                  encoding="utf-8") as f:
            source = f.read()
        top_level = [line for line in source.splitlines()
                     if line.startswith("from tools import vms")
                     or line.startswith("import tools.vms")]
        self.assertEqual(top_level, [],
                         "vms 回到了最上層，這份測試在 CI 上會整份跳不起來")


def report(**overrides):
    """一輪成功的升級長什麼樣。各個測試只覆寫它要談的那一項。"""
    base = {
        "first_exit": "0",
        "first_marker_v1": "True",
        "first_version": "1.0.0",
        "second_exit": "0",
        "second_marker_v1": "False",
        "second_marker_v2": "True",
        "second_version": "1.1.0",
        "entry_count": "1",
        "backup_leftovers": "",
        "path_occurrences": "1",
        "uninstall_exit": "0",
        "install_dir_gone": "True",
        "entry_count_after": "0",
        "path_occurrences_after": "0",
        "backup_probe_finds_decoy": "True",
    }
    base.update(overrides)
    return base


class TheFirstInstallHasToActuallyHappen(unittest.TestCase):
    """第二輪要驗的是「覆蓋」，前提是真的有東西可以覆蓋。第一次就沒裝成的話
    第二次其實是一次乾淨安裝——而它會全部通過，看起來像升級沒問題。"""

    def test_a_failed_first_install_is_inconclusive_not_a_pass(self):
        result = verify.evaluate_upgrade(report(first_exit="1"))
        self.assertEqual(result.verdict, verify.INCONCLUSIVE)

    def test_a_missing_first_marker_is_inconclusive(self):
        result = verify.evaluate_upgrade(report(first_marker_v1="False"))
        self.assertEqual(result.verdict, verify.INCONCLUSIVE)

    def test_the_happy_path_passes(self):
        self.assertEqual(verify.evaluate_upgrade(report()).verdict, verify.PASS)


class TheOldVersionHasToBeGone(unittest.TestCase):
    """舊版的檔案還在，代表新版是「疊上去」而不是「換掉」。兩個版本的檔案
    混在同一個資料夾裡，而使用者看到的是一次成功的安裝。"""

    def test_a_leftover_old_file_is_a_failure(self):
        result = verify.evaluate_upgrade(report(second_marker_v1="True"))
        self.assertEqual(result.verdict, verify.FAIL)
        self.assertIn("舊版", result.detail)

    def test_a_missing_new_file_is_a_failure(self):
        """`upgrade.py` 真實抓過的 bug：舊版的背景自我刪除指令在新版複製到
        一半時觸發，把整個資料夾連同剛複製好的檔案一起砍掉——而安裝回報
        成功。"""
        result = verify.evaluate_upgrade(report(second_marker_v2="False"))
        self.assertEqual(result.verdict, verify.FAIL)


class TheRegistryHasToTellTheTruth(unittest.TestCase):
    def test_a_stale_version_is_a_failure(self):
        result = verify.evaluate_upgrade(report(second_version="1.0.0"))
        self.assertEqual(result.verdict, verify.FAIL)
        self.assertIn("1.0.0", result.detail)

    def test_two_uninstall_entries_is_a_failure(self):
        """兩筆同名項目會讓系統的應用程式清單出現重複，而其中一筆指向一個
        已經不存在的目錄。"""
        result = verify.evaluate_upgrade(report(entry_count="2"))
        self.assertEqual(result.verdict, verify.FAIL)

    def test_no_uninstall_entry_at_all_is_a_failure(self):
        result = verify.evaluate_upgrade(report(entry_count="0"))
        self.assertEqual(result.verdict, verify.FAIL)


class NothingMayBeLeftBehind(unittest.TestCase):
    def test_a_leftover_backup_folder_is_a_failure(self):
        """備份是升級途中的中繼物，成功之後應該被丟棄。留著的話使用者的磁碟
        上多出一份完整的舊安裝，而沒有任何介面提到它。"""
        result = verify.evaluate_upgrade(
            report(backup_leftovers="C:\\Users\\Tester\\AppData\\Local\\Temp\\mswi_backup_x"))
        self.assertEqual(result.verdict, verify.FAIL)
        self.assertIn("mswi_backup_x", result.detail)

    def test_a_backup_check_that_cannot_see_a_decoy_is_inconclusive(self):
        """「沒有找到備份殘留」跟「搜尋條件寫錯了、什麼都找不到」在結果上長得
        一樣——而後者曾經真的發生（條件要求名字含應用程式名稱，但備份資料夾
        的名字裡沒有）。客體因此會在一個刻意放好的誘餌上先跑一次同一段搜尋；
        連誘餌都找不到的話，這一項的「乾淨」不能採信。
        """
        result = verify.evaluate_upgrade(report(backup_probe_finds_decoy="False"))
        self.assertEqual(result.verdict, verify.INCONCLUSIVE)
        self.assertIn("誘餌", result.detail)

    def test_a_duplicated_path_entry_is_a_failure(self):
        """升級把安裝目錄又加了一次。PATH 會隨著每次升級愈來愈長，而症狀要
        很久以後才顯現。"""
        result = verify.evaluate_upgrade(report(path_occurrences="2"))
        self.assertEqual(result.verdict, verify.FAIL)

    def test_the_path_disappearing_is_also_a_failure(self):
        result = verify.evaluate_upgrade(report(path_occurrences="0"))
        self.assertEqual(result.verdict, verify.FAIL)


class ItHasToStillBeRemovable(unittest.TestCase):
    """升級之後還解除安裝得掉——升級留下的是「新版本」還是「新版本加上一堆
    舊版本的殘骸」，這一步才問得出來。"""

    def test_a_failing_uninstall_is_a_failure(self):
        result = verify.evaluate_uninstall_after_upgrade(report(uninstall_exit="1"))
        self.assertEqual(result.verdict, verify.FAIL)

    def test_a_leftover_install_dir_is_a_failure(self):
        result = verify.evaluate_uninstall_after_upgrade(
            report(install_dir_gone="False"))
        self.assertEqual(result.verdict, verify.FAIL)

    def test_a_leftover_registry_entry_is_a_failure(self):
        result = verify.evaluate_uninstall_after_upgrade(report(entry_count_after="1"))
        self.assertEqual(result.verdict, verify.FAIL)

    def test_a_leftover_path_entry_is_a_failure(self):
        result = verify.evaluate_uninstall_after_upgrade(
            report(path_occurrences_after="1"))
        self.assertEqual(result.verdict, verify.FAIL)

    def test_the_happy_path_passes(self):
        self.assertEqual(
            verify.evaluate_uninstall_after_upgrade(report()).verdict, verify.PASS)

    def test_a_missing_step_is_inconclusive(self):
        self.assertEqual(
            verify.evaluate_uninstall_after_upgrade({}).verdict,
            verify.INCONCLUSIVE)


class TheCountsHaveToBeAbleToChange(unittest.TestCase):
    """量同一件事兩次（升級之後、解除安裝之後），是這一輪唯一能證明那幾條
    表達式不是死的方式。

    `entry_count` 在升級之後應該是 1、解除安裝之後應該是 0；`path_occurrences`
    同理。兩次都拿到同一個數字的話，那個數字有可能只是「這段 PowerShell 永遠
    回傳的東西」——備份那一項就是這樣被抓到的（條件寫錯，永遠是空的）。
    """

    def test_the_uninstall_round_measures_the_same_two_things_again(self):
        script = verify.uninstall_script(APP)
        self.assertIn("entry_count_after", script)
        self.assertIn("path_occurrences_after", script)

    def test_it_reuses_the_same_helpers(self):
        """換一段不同的程式碼去量，證明的就只是那一段的行為。"""
        script = verify.uninstall_script(APP)
        self.assertIn("EntryCount", script)

    def test_the_backup_search_is_tried_on_a_decoy_first(self):
        """同一段搜尋先在一個刻意建立的資料夾上跑一次，證明它找得到東西。
        誘餌用完要刪掉，否則它自己會變成下一輪的「殘留」。"""
        script = verify.upgrade_script(r"C:\a.exe", APP, "v1.txt", "v2.txt")
        self.assertIn("backup_probe_finds_decoy", script)
        self.assertIn("Remove-Item", script.split("backup_probe_finds_decoy", 1)[1])


class TheRefusalPathIsItsOwnAnswer(unittest.TestCase):
    """`upgrade.py` 的安全防護：舊版項目在 HKCU、而行程已提權時拒絕代為
    執行。這一輪跑在未提權的桌面上，因此**不該**走到那個分支——走到了就
    代表這一輪量的是另一件事，而不是升級本身。"""

    def test_hitting_the_refusal_is_inconclusive_not_a_failure(self):
        result = verify.evaluate_upgrade(
            report(second_exit="1", second_marker_v2="False",
                   second_log="偵測到舊版本的登錄表項目位於使用者層級（HKCU），"
                              "但目前安裝程式正以系統管理員權限執行。"))
        self.assertEqual(result.verdict, verify.INCONCLUSIVE)
        self.assertIn("提權", result.detail)


class TheGuestScriptsHaveToBeShapedRight(unittest.TestCase):
    def setUp(self):
        self.first = verify.first_install_script(
            r"C:\Users\Tester\Setup_v1.exe", APP, "v1.txt")
        self.second = verify.upgrade_script(
            r"C:\Users\Tester\Setup_v2.exe", APP, "v1.txt", "v2.txt")

    def test_it_reads_files_as_utf8(self):
        """安裝檔的紀錄是 UTF-8，而 Windows PowerShell 5.1 的 Get-Content
        預設用系統的 ANSI 字碼頁——在中文機器上讀回來是亂碼（實際踩過）。"""
        self.assertIn("Get-Content", self.second)
        self.assertIn("-Encoding UTF8", self.second.split("Get-Content", 1)[1])

    def test_the_upgrade_round_looks_for_the_old_marker(self):
        """「舊版檔案有沒有消失」是這一輪最核心的一項，而它只有在明確去找
        那個檔案時才問得到。"""
        self.assertIn("second_marker_v1", self.second)
        self.assertIn("v1.txt", self.second)

    def test_it_counts_uninstall_entries_in_both_hives(self):
        """舊版可能登記在另一個 hive（`check_existing` 就是為此兩邊都查）。
        只數一邊會把「兩筆重複」讀成「一筆正常」。"""
        self.assertIn("HKCU:", self.second)
        self.assertIn("HKLM:", self.second)

    def test_it_looks_for_leftover_backup_folders(self):
        self.assertIn("backup_leftovers", self.second)

    def test_it_looks_for_the_name_the_backup_actually_has(self):
        """實際踩到（2026-09-09，第一次跑就全綠時去對實作才發現）：備份資料夾
        叫 `mswi_upgrade_backup_<pid>`，**不含應用程式名稱**（見
        `upgrade.backup()`）。第一版這裡要求名字同時含 'backup' 與應用程式名，
        那個條件永遠不成立——那條斷言因此從來驗不到東西，而它會一直是綠的。

        這正是這個專案記過的事故形態：測試寫的是「描述實作實際做了什麼」的
        猜測，而不是真的去對過那個名字。
        """
        self.assertIn("mswi_upgrade_backup", self.second)

    def test_the_installer_log_comes_back(self):
        """失敗時要分得出是哪一種失敗——尤其是那個提權拒絕分支，它的訊息
        是唯一的線索。"""
        self.assertIn("second_log", self.second)

    def test_both_rounds_append_to_the_same_report(self):
        for script in (self.first, self.second):
            self.assertIn("-Append", script)

    def test_the_silent_flag_is_used(self):
        """互動安裝停在等人按的畫面上，兩邊會互等到逾時。"""
        for script in (self.first, self.second):
            self.assertIn("/S", script)


if __name__ == "__main__":
    unittest.main(verbosity=2)
