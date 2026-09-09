"""`tools/verify_uninstall_drag.py` 的判準。

解除安裝的唯一觸發點是把應用程式圖示拖到垃圾桶上（依 ADR-0002 決定四，不
另外提供「跳過拖曳」的按鈕）。靜默移除（`--silent`）走的是另一條路，CI 用
的也是那一條——因此**那個手勢本身**在此之前完全沒有被自動化碰過。

判準的形狀與安裝那一端相反：安裝要看「檔案出現」，這裡要看「檔案不見了」。
「不見了」這種斷言特別容易永遠成立，因此兩件事一定要成立才算數：

1. **前後量出不同的值**——同一個 `Test-Path` 在拖曳前後各量一次。前面那次
   不是 True 的話，這一輪判為無法判定：一台從來沒裝過的機器上，「不見了」
   本來就是真的。
2. **誘餌**——先把圖示拖到視窗裡的空白處。同樣送出完整的滑鼠事件、同樣的
   起點，只有落點不同，而東西應該還在。這一輪沒過的話，正式那一輪的「不見
   了」說明不了是垃圾桶造成的——任何一種「拖了就移除」的壞法都長得一樣。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import drive_installer_gui as drive
from tools import verify_uninstall_drag as verify


def still_there():
    return {"window_found": "True", "drag_sent": "True",
            "install_dir_before": "True", "main_exe_before": "True",
            "install_dir_exists": "True", "main_exe_exists": "True"}


def removed():
    """拖到垃圾桶之後，實際量到的樣子。

    `install_dir_exists` 仍然是 True 不是缺陷：解除安裝程式自己就住在那個
    目錄裡，整個目錄由 `self_delete` 排出來的背景指令刪掉，而那段指令要等
    使用者在完成畫面按下「完成」才會排（見 `uninstall.py` 的
    `finish_and_exit()`）。2026-09-09 第一輪實測就是量到這個中間狀態。
    """
    report = still_there()
    report["main_exe_exists"] = "False"
    report["result_screen"] = "True"
    report["finish_click_at"] = "300,300"
    report["install_dir_after_finish"] = "False"
    report["main_exe_after_finish"] = "False"
    return report


class TheDecoyRoundProvesTheDropTargetMatters(unittest.TestCase):
    def test_dragging_into_empty_space_leaves_it_installed(self):
        self.assertEqual(verify.evaluate_decoy(still_there()).verdict, verify.PASS)

    def test_being_removed_by_a_drag_to_nowhere_is_a_failure(self):
        """落點沒有作用的話，正式那一輪的「不見了」就不是垃圾桶的功勞。"""
        result = verify.evaluate_decoy(removed())
        self.assertEqual(result.verdict, verify.FAIL)

    def test_nothing_installed_beforehand_is_inconclusive(self):
        report = still_there()
        report["install_dir_before"] = "False"
        self.assertEqual(verify.evaluate_decoy(report).verdict,
                         verify.INCONCLUSIVE)

    def test_a_window_that_never_appeared_is_inconclusive(self):
        report = still_there()
        report["window_found"] = "False"
        self.assertEqual(verify.evaluate_decoy(report).verdict,
                         verify.INCONCLUSIVE)


class DraggingToTheTrashHasToRemoveIt(unittest.TestCase):
    def test_the_directory_disappearing_is_a_pass(self):
        result = verify.evaluate_removal(removed(), decoy_passed=True)
        self.assertEqual(result.verdict, verify.PASS)

    def test_nothing_being_removed_at_all_is_a_failure(self):
        result = verify.evaluate_removal(still_there(), decoy_passed=True)
        self.assertEqual(result.verdict, verify.FAIL)

    def test_the_folder_surviving_the_finish_button_is_a_failure(self):
        """檔案清掉了、按了「完成」，整個目錄卻還在——自我刪除沒有發生。"""
        report = removed()
        report["install_dir_after_finish"] = "True"
        result = verify.evaluate_removal(report, decoy_passed=True)
        self.assertEqual(result.verdict, verify.FAIL)

    def test_the_intermediate_state_is_not_treated_as_a_failure(self):
        """拖完的當下目錄還在是既定行為，不是缺陷。"""
        report = removed()
        self.assertEqual(report["install_dir_exists"], "True")
        self.assertEqual(verify.evaluate_removal(report, True).verdict,
                         verify.PASS)

    def test_never_reaching_the_finish_button_is_inconclusive(self):
        """完成畫面沒按到的話，自我刪除本來就不會發生。"""
        report = removed()
        del report["install_dir_after_finish"]
        del report["finish_click_at"]
        self.assertEqual(
            verify.evaluate_removal(report, decoy_passed=True).verdict,
            verify.INCONCLUSIVE)

    def test_it_is_inconclusive_when_the_decoy_round_did_not_pass(self):
        """誘餌沒過時，這裡的「不見了」與「拖到哪裡都會移除」分不出來。"""
        result = verify.evaluate_removal(removed(), decoy_passed=False)
        self.assertEqual(result.verdict, verify.INCONCLUSIVE)

    def test_nothing_installed_beforehand_is_inconclusive(self):
        """前面那次不是 True 的話，「不見了」本來就成立，什麼都沒驗到。"""
        report = removed()
        report["install_dir_before"] = "False"
        report["main_exe_before"] = "False"
        result = verify.evaluate_removal(report, decoy_passed=True)
        self.assertEqual(result.verdict, verify.INCONCLUSIVE)

    def test_the_mouse_events_not_going_out_is_inconclusive(self):
        report = removed()
        report["drag_sent"] = "False"
        self.assertEqual(
            verify.evaluate_removal(report, decoy_passed=True).verdict,
            verify.INCONCLUSIVE)

    def test_an_empty_report_is_inconclusive(self):
        self.assertEqual(verify.evaluate_removal({}, decoy_passed=True).verdict,
                         verify.INCONCLUSIVE)


class TheEndpointsCameFromAMeasurementWithAControl(unittest.TestCase):
    """兩個端點由截圖換算而來，換算方式先在安裝那一端驗過。

    `docs/screenshots/` 底下的兩張截圖同樣是 600x420 的無邊框視窗、同樣的
    取景（實測兩張的視窗卡片都是 x 53..678、y 36..441）。把同一套換算套回
    安裝那張，會重現出 `drive.ICON_AT`／`TARGET_AT` 這組獨立量到的值——那就是
    這組座標的對照組。誤差容許 0.005，實測是 0.001 以內。
    """

    CARD_LEFT, CARD_WIDTH = 52.0, 626.0
    CARD_TOP, CARD_HEIGHT = 36.0, 406.0

    def _fraction(self, px, py):
        return ((px - self.CARD_LEFT) / self.CARD_WIDTH,
                (py - self.CARD_TOP) / self.CARD_HEIGHT)

    def test_the_conversion_reproduces_the_install_side_values(self):
        # 安裝那張截圖上量到的圖示與目的地中心（像素）。
        icon = self._fraction(213.0, 211.0)
        target = self._fraction(519.0, 210.0)
        self.assertAlmostEqual(icon[0], drive.ICON_AT[0], delta=0.005)
        self.assertAlmostEqual(icon[1], drive.ICON_AT[1], delta=0.005)
        self.assertAlmostEqual(target[0], drive.TARGET_AT[0], delta=0.005)

    def test_the_uninstall_endpoints_match_that_conversion(self):
        icon = self._fraction(231.5, 215.5)
        trash = self._fraction(500.5, 215.5)
        self.assertAlmostEqual(verify.ICON_AT[0], icon[0], delta=0.005)
        self.assertAlmostEqual(verify.ICON_AT[1], icon[1], delta=0.005)
        self.assertAlmostEqual(verify.TRASH_AT[0], trash[0], delta=0.005)

    def test_the_finish_button_sits_where_it_was_measured(self):
        """「完成」的位置量自 2026-09-09 那一輪自己拍的截圖：整張 2558x1190
        的實體像素畫面裡，視窗卡片是 x 33..762、y 33..508，按鈕中心
        (398, 350.5)。"""
        self.assertAlmostEqual(verify.DONE_AT[0], (398.0 - 33) / 729, delta=0.005)
        self.assertAlmostEqual(verify.DONE_AT[1], (350.5 - 33) / 475, delta=0.005)

    def test_the_decoy_endpoint_is_not_on_either_icon(self):
        """空白處要離兩個圖示夠遠，否則誘餌其實還是拖到了垃圾桶上。

        圖示與垃圾桶各約佔視窗寬度的六分之一，落點與它們的中心至少要差
        0.12（約 72 像素）才確定落在外面。
        """
        for named in (verify.ICON_AT, verify.TRASH_AT):
            gap = abs(verify.NOWHERE_AT[0] - named[0]) + \
                abs(verify.NOWHERE_AT[1] - named[1])
            self.assertGreater(gap, 0.12)


class TheWindowTitleIsTheUninstallerNotTheInstaller(unittest.TestCase):
    def test_it_looks_for_the_uninstall_window(self):
        self.assertEqual(verify.WINDOW_TITLE, "解除安裝")
        self.assertNotEqual(verify.WINDOW_TITLE, drive.WINDOW_TITLE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
