"""`tools/verify_msix_dialogs.py` 的判準。

MSIX 引擎有兩個只在互動安裝才會走到的分支，兩個都沒有被任何自動化碰過：

- **降版詢問**——這台電腦上的版本比較新時要先問過。它與傳統引擎的降版不同：
  MSIX 降版會把應用程式的資料一併清掉，因此那一頁的訊息要說出這件事，而
  「問過」本身是不能省的一步（見 `msix_install._downgrade_question()`）。
  靜默安裝不問、直接做，走的是另一條路。
- **傳統→MSIX 遷移**——中途改採 MSIX 的專案，其既有使用者都處於「已安裝
  傳統模式版本」的狀態，那一份要在交付系統部署之前先被移除
  （見 `msix_install` 檔頭的第二輪決議第九項）。

## 四輪

| 輪次 | 前置狀態 | 動作 | 預期 |
| --- | --- | --- | --- |
| 對照組 | 乾淨 | 裝 1.0.0 | 裝起來，**沒有**那一頁 |
| 取消 | 已裝 2.0.0 | 裝 1.0.0，按「取消」 | 版本仍是 2.0.0 |
| 繼續 | 已裝 2.0.0 | 裝 1.0.0，按「仍要繼續安裝」 | 版本變成 1.0.0 |
| 遷移 | 已裝傳統版 | 裝 MSIX | 傳統那份不見了，套件裝上了 |

對照組不是多餘的：少了它，「取消」那一輪的「版本沒變」有可能只是這顆安裝檔
在這台機器上根本裝不起來。而「繼續」那一輪證明那一頁不是壞掉——只有「取消」
的話，一個點不動的按鈕會讓它照樣通過。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import verify_msix_dialogs as verify


def base(**overrides):
    report = {"window_found": "True", "drag_sent": "True", "page_ready": "True"}
    report.update(overrides)
    return report


class TheControlRoundProvesTheInstallerWorks(unittest.TestCase):
    def test_a_clean_install_with_no_question_passes(self):
        report = base(package_version_before="", package_version_after="1.0.0")
        self.assertEqual(verify.evaluate_control(report).verdict, verify.PASS)

    def test_not_installing_is_a_failure(self):
        report = base(package_version_before="", package_version_after="")
        self.assertEqual(verify.evaluate_control(report).verdict, verify.FAIL)

    def test_asking_about_a_downgrade_on_a_clean_machine_is_a_failure(self):
        """機器上什麼都沒有，卻問了降版——那一頁的觸發條件是錯的。"""
        report = base(package_version_before="", package_version_after="1.0.0",
                      before_drag_found="True", before_drag_pressed="True")
        self.assertEqual(verify.evaluate_control(report).verdict, verify.FAIL)

    def test_something_already_installed_is_inconclusive(self):
        report = base(package_version_before="2.0.0",
                      package_version_after="1.0.0")
        self.assertEqual(verify.evaluate_control(report).verdict,
                         verify.INCONCLUSIVE)


class CancellingTheDowngradeHasToLeaveItAlone(unittest.TestCase):
    def _report(self, **kw):
        fields = {"package_version_before": "2.0.0",
                  "before_drag_found": "True", "before_drag_pressed": "True"}
        fields.update(kw)
        return base(**fields)

    def test_the_version_staying_put_is_a_pass(self):
        result = verify.evaluate_cancel(self._report(package_version_after="2.0.0"),
                                        control_passed=True)
        self.assertEqual(result.verdict, verify.PASS)

    def test_being_downgraded_anyway_is_a_failure(self):
        result = verify.evaluate_cancel(self._report(package_version_after="1.0.0"),
                                        control_passed=True)
        self.assertEqual(result.verdict, verify.FAIL)

    def test_the_question_never_appearing_is_a_failure(self):
        """沒問就沒降版的話，「版本沒變」不是那一頁的功勞。"""
        report = base(package_version_before="2.0.0",
                      package_version_after="2.0.0",
                      before_drag_found="False",
                      before_drag_seen="APP | 建立桌面捷徑")
        self.assertEqual(
            verify.evaluate_cancel(report, control_passed=True).verdict,
            verify.FAIL)

    def test_nothing_installed_beforehand_is_inconclusive(self):
        result = verify.evaluate_cancel(
            self._report(package_version_before="", package_version_after=""),
            control_passed=True)
        self.assertEqual(result.verdict, verify.INCONCLUSIVE)

    def test_it_is_inconclusive_when_the_control_did_not_pass(self):
        result = verify.evaluate_cancel(self._report(package_version_after="2.0.0"),
                                        control_passed=False)
        self.assertEqual(result.verdict, verify.INCONCLUSIVE)


class ContinuingHasToActuallyDowngrade(unittest.TestCase):
    def _report(self, **kw):
        fields = {"package_version_before": "2.0.0",
                  "before_drag_found": "True", "before_drag_pressed": "True"}
        fields.update(kw)
        return base(**fields)

    def test_the_version_going_backwards_is_a_pass(self):
        self.assertEqual(
            verify.evaluate_continue(self._report(package_version_after="1.0.0")).verdict,
            verify.PASS)

    def test_the_version_not_moving_is_a_failure(self):
        """按了「仍要繼續」卻什麼都沒發生——那顆按鈕點不動，而「取消」那一輪
        會照樣通過。"""
        self.assertEqual(
            verify.evaluate_continue(self._report(package_version_after="2.0.0")).verdict,
            verify.FAIL)

    def test_the_question_never_appearing_is_a_failure(self):
        report = base(package_version_before="2.0.0",
                      package_version_after="1.0.0",
                      before_drag_found="False",
                      before_drag_seen="APP | 建立桌面捷徑")
        self.assertEqual(verify.evaluate_continue(report).verdict, verify.FAIL)


class TheLegacyInstallHasToBeRemovedFirst(unittest.TestCase):
    def _report(self, **kw):
        fields = {"package_version_before": "", "legacy_dir_before": "True",
                  "before_drag_found": "True", "before_drag_pressed": "True"}
        fields.update(kw)
        return base(**fields)

    def test_the_old_folder_going_away_is_a_pass(self):
        report = self._report(package_version_after="1.0.0",
                              legacy_dir_exists="False")
        self.assertEqual(verify.evaluate_migration(report).verdict, verify.PASS)

    def test_the_old_folder_surviving_is_a_failure(self):
        report = self._report(package_version_after="1.0.0",
                              legacy_dir_exists="True")
        self.assertEqual(verify.evaluate_migration(report).verdict, verify.FAIL)

    def test_the_package_not_being_installed_is_a_failure(self):
        report = self._report(package_version_after="", legacy_dir_exists="False")
        self.assertEqual(verify.evaluate_migration(report).verdict, verify.FAIL)

    def test_no_legacy_install_beforehand_is_inconclusive(self):
        """前置狀態沒建立起來的話，「舊的不見了」本來就成立。"""
        report = base(package_version_before="", legacy_dir_before="False",
                      package_version_after="1.0.0", legacy_dir_exists="False")
        self.assertEqual(verify.evaluate_migration(report).verdict,
                         verify.INCONCLUSIVE)

    def test_the_existing_install_never_being_noticed_is_a_failure(self):
        """畫面上沒出現更新詢問——既有的那一份根本沒被偵測到。"""
        report = self._report(package_version_after="1.0.0",
                              legacy_dir_exists="False",
                              before_drag_found="False",
                      before_drag_seen="APP | 建立桌面捷徑")
        self.assertEqual(verify.evaluate_migration(report).verdict, verify.FAIL)


class TheButtonsAreNamedNotPositioned(unittest.TestCase):
    """對話框上的按鈕按**名字**叫。

    第一版按座標，點得到、卻分不出「按到了但沒作用」與「根本沒按到那
    一顏」——2026-09-09 那一輪就卡在這個分不出來。
    """

    def test_the_names_come_from_the_interface(self):
        self.assertEqual(verify.CANCEL_BUTTON, "取消")
        self.assertEqual(verify.CONTINUE_BUTTON, "仍要繼續安裝")

    def test_cancel_and_continue_differ(self):
        """兩者相同的話，兩輪量的是同一件事。"""
        self.assertNotEqual(verify.CANCEL_BUTTON, verify.CONTINUE_BUTTON)

    def test_a_button_that_was_not_there_is_inconclusive(self):
        """按不到與按了沒作用要分得出來——兩者的處置完全不同。"""
        report = base(package_version_before="2.0.0",
                      package_version_after="2.0.0",
                      before_drag_found="True", before_drag_pressed="False",
                      before_drag_focus_error="焦點移不過去")
        result = verify.evaluate_continue(report)
        self.assertEqual(result.verdict, verify.INCONCLUSIVE)
        self.assertIn("焦點移不過去", result.detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
