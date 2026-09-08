"""`tools/verify_eula_gate.py` 的判準，以及拖曳工具新增的「先點一下」參數。

授權合約頁只出現在**互動安裝**——靜默安裝的既定行為就是跳過它（`/S` 視同
已經同意），因此它在任何自動化裡都沒有被碰過。而忘了帶合約時 `eula_texts`
為空，安裝檔會安靜地跳過那一頁、不報錯（`/released` 的步驟 6 就警告過這件
事，卻同時錯誤地宣稱步驟 8 驗得到它）。

驗的方式是「那一頁擋不擋得住」，三輪：

1. **對照組**——沒有合約的安裝檔，拖曳直接觸發安裝。這一輪證明拖曳這套
   機制在這個環境是有效的；它不通過的話，第二輪的「沒有裝起來」什麼都不能
   說明。
2. **合約擋住**——有合約的安裝檔，直接拖曳，不該有任何反應。
3. **同意之後可以裝**——同一顆，先點「同意並繼續」再拖曳。少了這一輪會有
   個漏洞：合約頁壞掉、按鈕點不動時，第二輪一樣會通過。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import drive_installer_gui as drive
from tools import verify_eula_gate as verify


def installed():
    return {"window_found": "True", "drag_sent": "True",
            "install_dir_exists": "True", "main_exe_exists": "True"}


def not_installed():
    return {"window_found": "True", "drag_sent": "True",
            "install_dir_exists": "False", "main_exe_exists": "False"}


class TheControlRoundProvesTheHarnessWorks(unittest.TestCase):
    def test_installing_is_a_pass(self):
        self.assertEqual(verify.evaluate_control(installed()).verdict, verify.PASS)

    def test_not_installing_is_a_failure(self):
        """對照組沒裝起來的話，問題出在拖曳或這台機器，不在合約頁。"""
        result = verify.evaluate_control(not_installed())
        self.assertEqual(result.verdict, verify.FAIL)

    def test_a_missing_window_is_inconclusive(self):
        report = installed()
        report["window_found"] = "False"
        self.assertEqual(verify.evaluate_control(report).verdict,
                         verify.INCONCLUSIVE)


class TheAgreementHasToBlockTheDrag(unittest.TestCase):
    def test_not_installing_is_a_pass(self):
        result = verify.evaluate_gate(not_installed(), control_passed=True)
        self.assertEqual(result.verdict, verify.PASS)

    def test_installing_anyway_is_a_failure(self):
        """拖曳直接裝起來了，代表畫面上根本沒有合約頁——正是「忘了帶
        `--config` 就安靜跳過」那個情形。"""
        result = verify.evaluate_gate(installed(), control_passed=True)
        self.assertEqual(result.verdict, verify.FAIL)
        self.assertIn("擋", result.detail)

    def test_it_is_inconclusive_when_the_control_did_not_pass(self):
        """對照組沒過的時候，「沒有裝起來」有可能只是拖曳在這台機器上沒作用。
        兩者在結果上長得一樣。"""
        result = verify.evaluate_gate(not_installed(), control_passed=False)
        self.assertEqual(result.verdict, verify.INCONCLUSIVE)

    def test_a_drag_that_never_happened_is_inconclusive(self):
        report = not_installed()
        report["drag_sent"] = "False"
        self.assertEqual(
            verify.evaluate_gate(report, control_passed=True).verdict,
            verify.INCONCLUSIVE)


class AcceptingHasToLetTheInstallThrough(unittest.TestCase):
    def _report(self, **overrides):
        base = installed()
        base["pre_click_sent"] = "True"
        base.update(overrides)
        return base

    def test_installing_after_accepting_is_a_pass(self):
        self.assertEqual(verify.evaluate_accept(self._report()).verdict,
                         verify.PASS)

    def test_still_not_installing_is_a_failure(self):
        """點了同意卻還是裝不起來——那顆按鈕沒有作用。少了這一輪，這種壞法
        會讓第二輪照樣通過。"""
        result = verify.evaluate_accept(
            self._report(install_dir_exists="False", main_exe_exists="False"))
        self.assertEqual(result.verdict, verify.FAIL)

    def test_a_click_that_never_happened_is_inconclusive(self):
        result = verify.evaluate_accept(self._report(pre_click_sent="False"))
        self.assertEqual(result.verdict, verify.INCONCLUSIVE)


class TheContractWithTheDragToolIsATwoTuple(unittest.TestCase):
    """`drive.run()` 回傳 `(報告, 它自己的判定)`。

    實際踩到（2026-09-09）：這裡當成只回傳報告，於是三輪都在虛擬機上跑完了
    之後，才在計算判定的第一行以 `'tuple' object has no attribute 'get'`
    收場——十分鐘的量測結果只留在磁碟上，沒有進到判定。

    它那個判定不能直接拿來用：它問的是「拖曳有沒有觸發安裝」，而這三輪裡
    有一輪的正確答案正好是「沒有」。
    """

    def test_run_returns_two_values(self):
        import inspect
        source = inspect.getsource(drive.run)
        returns = [line.strip() for line in source.splitlines()
                   if line.strip().startswith("return ")]
        self.assertTrue(returns, "drive.run() 沒有 return")
        self.assertTrue(all("," in line for line in returns),
                        f"drive.run() 的回傳不再是兩個值：{returns}")

    def test_the_gate_tool_unpacks_both(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "tools", "verify_eula_gate.py"),
                  encoding="utf-8") as f:
            source = f.read()
        self.assertIn("report, _its_own_verdict = drive.run(", source)


class TheDragToolCanClickFirst(unittest.TestCase):
    """合約頁的按鈕座標照既有的作法表示成「視窗矩形的比例」（ICON_AT /
    TARGET_AT 就是這樣）。送滑鼠事件的那段 C# 只留一份，在拖曳工具裡。
    """

    def test_no_click_block_by_default(self):
        script = drive.guest_script(r"C:\Setup.exe", "App")
        self.assertNotIn("pre_click_sent", script)

    def test_the_click_lands_at_the_given_fraction_of_the_window(self):
        script = drive.guest_script(r"C:\Setup.exe", "App",
                                    click_before_drag=(0.588, 0.779))
        self.assertIn("0.588", script)
        self.assertIn("0.779", script)
        self.assertIn("pre_click_sent", script)

    def test_the_click_happens_after_the_window_rect_is_known(self):
        """視窗還沒量到位置就點，點到的是桌面——而報告上會看起來像「點了
        但沒有反應」（拖曳那一段真的踩過同一件事）。"""
        script = drive.guest_script(r"C:\Setup.exe", "App",
                                    click_before_drag=(0.5, 0.5))
        self.assertLess(script.index("window_rect"), script.index("pre_click_sent"))

    def test_the_click_happens_before_the_drag(self):
        script = drive.guest_script(r"C:\Setup.exe", "App",
                                    click_before_drag=(0.5, 0.5))
        self.assertLess(script.index("pre_click_sent"), script.index("drag_sent"))

    def test_it_waits_after_clicking(self):
        """按下同意之後畫面要換頁，馬上拖曳會拖在還沒消失的合約頁上。"""
        script = drive.guest_script(r"C:\Setup.exe", "App",
                                    click_before_drag=(0.5, 0.5))
        between = script[script.index("pre_click_sent"):script.index("drag_sent")]
        self.assertIn("Start-Sleep", between)


if __name__ == "__main__":
    unittest.main(verbosity=2)
