"""`tools/verify_eula_fallback.py` 的判準。

授權合約可以有多種語言，選哪一份的回退順序寫在 `installer_core.get_eula_text()`：
系統語言完全對到 → 打包時指定的預設語言 → 字典裡第一筆 → 空字串。前兩段
在真機上從來沒有被驗過——`verify_eula_gate` 驗的是「那一頁擋不擋得住」，
與「顯示的是哪一份」是兩件事。

## 兩輪

| 輪次 | 合約表 | 預期畫面上出現 |
| --- | --- | --- |
| 對照組 | 含 zh-TW | 中文那一份 |
| 回退 | 只有 ja 與 en，預設語言指定 en | 英文那一份 |

回退那一輪的合約表把 `ja` 排在 `en` 前面：這樣「回退到字典第一筆」與「回退到
指定的預設語言」會得到不同的答案，判準才分得出走的是哪一條。兩者排同一個
順序的話，那一輪不管實作走哪一條都會通過。

對照組不是多餘的：它證明「畫面上讀得到合約內容」這件事在這台機器上成立，
否則回退那一輪的「沒有出現日文那份」在一個什麼都讀不到的情況下無條件為真。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import verify_eula_fallback as verify


def screen(*marks):
    return {"window_found": "True",
            "window_text": "APP | " + " | ".join(marks) + " | 同意並繼續"}


class TheControlRoundShowsTheSystemLanguage(unittest.TestCase):
    def test_the_chinese_copy_passes(self):
        self.assertEqual(verify.evaluate_control(screen(verify.MARK_ZH)).verdict,
                         verify.PASS)

    def test_another_language_showing_up_is_a_failure(self):
        """系統語言明明對得到，卻顯示了別的語言。"""
        self.assertEqual(verify.evaluate_control(screen(verify.MARK_EN)).verdict,
                         verify.FAIL)

    def test_reading_nothing_is_inconclusive(self):
        report = {"window_found": "True", "window_text": ""}
        self.assertEqual(verify.evaluate_control(report).verdict,
                         verify.INCONCLUSIVE)

    def test_a_window_that_never_appeared_is_inconclusive(self):
        report = screen(verify.MARK_ZH)
        report["window_found"] = "False"
        self.assertEqual(verify.evaluate_control(report).verdict,
                         verify.INCONCLUSIVE)


class TheFallbackRoundIsWhatIsActuallyBeingAsked(unittest.TestCase):
    def test_the_declared_default_language_passes(self):
        result = verify.evaluate_fallback(screen(verify.MARK_EN),
                                          control_passed=True)
        self.assertEqual(result.verdict, verify.PASS)

    def test_falling_back_to_the_first_entry_instead_is_a_failure(self):
        """字典第一筆是 ja。出現它就代表沒有走「指定的預設語言」那一條。"""
        result = verify.evaluate_fallback(screen(verify.MARK_JA),
                                          control_passed=True)
        self.assertEqual(result.verdict, verify.FAIL)
        self.assertIn(verify.MARK_JA, result.detail)

    def test_showing_neither_is_a_failure(self):
        """一份都沒顯示——那一頁整個被跳過了，而那正是打包時忘記設定的後果。"""
        result = verify.evaluate_fallback(screen("同意並繼續"),
                                          control_passed=True)
        self.assertEqual(result.verdict, verify.FAIL)

    def test_it_is_inconclusive_when_the_control_did_not_pass(self):
        result = verify.evaluate_fallback(screen(verify.MARK_EN),
                                          control_passed=False)
        self.assertEqual(result.verdict, verify.INCONCLUSIVE)


class TheMarksAreDistinguishable(unittest.TestCase):
    def test_no_mark_contains_another(self):
        """一個標記包含另一個的話，比對會同時命中兩者。"""
        marks = (verify.MARK_ZH, verify.MARK_EN, verify.MARK_JA)
        for one in marks:
            for other in marks:
                if one is not other:
                    self.assertNotIn(one, other)


if __name__ == "__main__":
    unittest.main(verbosity=2)
