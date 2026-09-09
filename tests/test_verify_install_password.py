"""`tools/verify_install_password.py` 的判準。

安裝密碼那一關只出現在**互動安裝**：靜默安裝走的是 `/PASSWORD=`，密碼由
命令列給，那一頁根本不會出現。也就是說畫面上那一關在此之前沒有被任何自動化
碰過——而它是這個專案唯一一個「輸入錯了就不該讓你繼續」的欄位。

## 四輪，以及為什麼一輪都不能少

| 輪次 | 材料 | 動作 | 預期 |
| --- | --- | --- | --- |
| 對照組 | 沒有密碼的安裝檔 | 直接拖曳 | 裝起來 |
| 擋住 | 有密碼的安裝檔 | 不輸入，直接拖曳 | **沒有**裝起來 |
| 錯的密碼 | 有密碼的安裝檔 | 輸入錯的再拖 | **沒有**裝起來 |
| 正確密碼 | 有密碼的安裝檔 | 輸入對的再拖 | 裝起來 |

- **對照組**證明拖曳這套機制在這台機器上有效。它不通過的話，「擋住」那一輪
  的「沒有裝起來」有可能只是拖曳沒作用。
- **錯的密碼**證明那個欄位真的在驗。少了這一輪，「正確密碼裝得起來」與
  「隨便打什麼都裝得起來」分不出來。
- **正確密碼**證明那一關不是壞掉。少了這一輪會留一個漏洞：頁面壞掉、按鈕
  點不動時，「擋住」與「錯的密碼」照樣通過。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import verify_install_password as verify


def installed():
    return {"window_found": "True", "drag_sent": "True",
            "install_dir_before": "False", "main_exe_before": "False",
            "install_dir_exists": "True", "main_exe_exists": "True"}


def blocked():
    report = installed()
    report["install_dir_exists"] = "False"
    report["main_exe_exists"] = "False"
    return report


def typed(report, ok=True):
    report["typed"] = "True"
    report["post_click_at"] = "300,300"
    return report


class TheControlRoundProvesTheHarnessWorks(unittest.TestCase):
    def test_installing_is_a_pass(self):
        self.assertEqual(verify.evaluate_control(installed()).verdict,
                         verify.PASS)

    def test_not_installing_is_a_failure(self):
        """對照組沒裝起來的話，問題出在拖曳或這台機器，不在密碼那一關。"""
        self.assertEqual(verify.evaluate_control(blocked()).verdict,
                         verify.FAIL)

    def test_a_missing_window_is_inconclusive(self):
        report = installed()
        report["window_found"] = "False"
        self.assertEqual(verify.evaluate_control(report).verdict,
                         verify.INCONCLUSIVE)

    def test_something_already_installed_is_inconclusive(self):
        """拖曳之前就裝著的話，「裝起來了」不是這一輪的功勞。"""
        report = installed()
        report["install_dir_before"] = "True"
        self.assertEqual(verify.evaluate_control(report).verdict,
                         verify.INCONCLUSIVE)


class TheGateHasToBlock(unittest.TestCase):
    def test_not_installing_is_a_pass(self):
        self.assertEqual(
            verify.evaluate_gate(blocked(), control_passed=True).verdict,
            verify.PASS)

    def test_installing_anyway_is_a_failure(self):
        """沒輸入密碼就裝起來了，代表那一關根本沒擋。"""
        self.assertEqual(
            verify.evaluate_gate(installed(), control_passed=True).verdict,
            verify.FAIL)

    def test_it_is_inconclusive_when_the_control_did_not_pass(self):
        self.assertEqual(
            verify.evaluate_gate(blocked(), control_passed=False).verdict,
            verify.INCONCLUSIVE)


class AWrongPasswordHasToBeRejected(unittest.TestCase):
    def test_not_installing_is_a_pass(self):
        self.assertEqual(
            verify.evaluate_wrong(typed(blocked()), control_passed=True).verdict,
            verify.PASS)

    def test_installing_anyway_is_a_failure(self):
        """打錯也裝得起來的話，那個欄位收下了任何東西——「正確密碼可以裝」
        因此什麼都證明不了。"""
        self.assertEqual(
            verify.evaluate_wrong(typed(installed()), control_passed=True).verdict,
            verify.FAIL)

    def test_never_typing_anything_is_inconclusive(self):
        """按鍵沒送出去的話，這一輪跟「不輸入直接拖」是同一輪。"""
        self.assertEqual(
            verify.evaluate_wrong(blocked(), control_passed=True).verdict,
            verify.INCONCLUSIVE)


class TheRightPasswordHasToLetItThrough(unittest.TestCase):
    def test_installing_is_a_pass(self):
        self.assertEqual(
            verify.evaluate_accept(typed(installed())).verdict, verify.PASS)

    def test_not_installing_is_a_failure(self):
        """輸入正確卻還是裝不起來——那一關壞了，而前兩輪會照樣通過。"""
        result = verify.evaluate_accept(typed(blocked()))
        self.assertEqual(result.verdict, verify.FAIL)

    def test_never_typing_anything_is_inconclusive(self):
        self.assertEqual(
            verify.evaluate_accept(installed()).verdict, verify.INCONCLUSIVE)


class TheEndpointsCameFromAMeasurement(unittest.TestCase):
    """兩個位置量自 2026-09-09 在 win11 上拍的那一頁截圖。

    整張 2558x1190 的實體像素畫面裡，視窗卡片是 x 33..762、y 33..508（與量
    「完成」那顆按鈕時同一組邊界，兩次是同一台機器同一個縮放）。輸入框的
    中心 (398.5, 287.5)、「確定」的中心 (455.5, 352.5)。
    """

    LEFT, WIDTH, TOP, HEIGHT = 33, 729, 33, 475

    def _fraction(self, px, py):
        return ((px - self.LEFT) / self.WIDTH, (py - self.TOP) / self.HEIGHT)

    def test_the_field_is_where_it_was_measured(self):
        x, y = self._fraction(398.5, 287.5)
        self.assertAlmostEqual(verify.FIELD_AT[0], x, delta=0.005)
        self.assertAlmostEqual(verify.FIELD_AT[1], y, delta=0.005)

    def test_the_submit_button_is_where_it_was_measured(self):
        x, y = self._fraction(455.5, 352.5)
        self.assertAlmostEqual(verify.SUBMIT_AT[0], x, delta=0.005)
        self.assertAlmostEqual(verify.SUBMIT_AT[1], y, delta=0.005)

    def test_the_two_are_not_the_same_spot(self):
        """兩者相同的話，「輸入」與「送出」按的是同一個地方，這一輪不會
        真的送出任何東西。"""
        self.assertNotEqual(verify.FIELD_AT, verify.SUBMIT_AT)


class TheRoundsUseDifferentPasswords(unittest.TestCase):
    def test_the_wrong_one_is_not_the_right_one(self):
        """兩輪打同一串的話，第三輪量到的是第四輪的結果。"""
        self.assertNotEqual(verify.WRONG_PASSWORD, verify.PROBE_PASSWORD)


if __name__ == "__main__":
    unittest.main(verbosity=2)
