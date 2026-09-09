"""`tools/verify_ui_language.py` 的判準。

安裝精靈的介面語言不給使用者選，由 `installer_core` 依系統語言自動偵測
（`lang_detect.detect_system_language()`）。CI 的 runner 是英文的，但它沒有
互動桌面，跑不起那個畫面；兩台虛擬機裡有畫面的那台是繁體中文。也就是說
「英文系統上介面是不是英文」從來沒有被任何地方驗過。

## 兩輪互為對照

| 輪次 | 系統語言 | 畫面上應該有 | 不應該有 |
| --- | --- | --- | --- |
| 中文 | zh-TW（那台的預設） | 安裝目的地、建立桌面捷徑 | Install Location |
| 英文 | en-US（`Set-Culture` 改過） | Install Location、Create a desktop shortcut | 安裝目的地 |

兩輪缺一不可，而且理由不是「多驗一點」：**「畫面上沒有中文」在一個讀不到
任何文字的情況下無條件成立**。同一輪同時要求「有另一種語言的字」才排除得掉
那種情形，而另一輪證明這套讀法在這台機器上讀得到東西。

畫面上的字經由輔助使用介面讀回來（見 `drive_installer_gui` 的 `dump_text`），
讀到的是使用者眼睛看到的那幾個字，不是程式碼裡的常數。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import verify_ui_language as verify


def screen(text):
    return {"window_found": "True", "window_text": text}


ZH_SCREEN = "APP | 安裝目的地 | C:\\Users\\Tester\\AppData | 建立桌面捷徑"
EN_SCREEN = "APP | Install Location | C:\\Users\\Tester\\AppData | Create a desktop shortcut"


class TheChineseRoundIsTheControl(unittest.TestCase):
    def test_chinese_text_passes(self):
        self.assertEqual(verify.evaluate_chinese(screen(ZH_SCREEN)).verdict,
                         verify.PASS)

    def test_english_text_on_a_chinese_system_is_a_failure(self):
        self.assertEqual(verify.evaluate_chinese(screen(EN_SCREEN)).verdict,
                         verify.FAIL)

    def test_reading_nothing_at_all_is_inconclusive(self):
        """一個字都讀不到時，「沒有英文」無條件成立——那不是通過。"""
        self.assertEqual(verify.evaluate_chinese(screen("")).verdict,
                         verify.INCONCLUSIVE)

    def test_a_window_that_never_appeared_is_inconclusive(self):
        report = screen(ZH_SCREEN)
        report["window_found"] = "False"
        self.assertEqual(verify.evaluate_chinese(report).verdict,
                         verify.INCONCLUSIVE)


class TheEnglishRoundIsWhatIsActuallyBeingAsked(unittest.TestCase):
    def test_english_text_passes(self):
        self.assertEqual(
            verify.evaluate_english(screen(EN_SCREEN), control_passed=True).verdict,
            verify.PASS)

    def test_chinese_text_on_an_english_system_is_a_failure(self):
        """系統是英文的，介面卻還是中文——這正是這一輪要抓的東西。"""
        self.assertEqual(
            verify.evaluate_english(screen(ZH_SCREEN), control_passed=True).verdict,
            verify.FAIL)

    def test_reading_nothing_at_all_is_inconclusive(self):
        self.assertEqual(
            verify.evaluate_english(screen(""), control_passed=True).verdict,
            verify.INCONCLUSIVE)

    def test_it_is_inconclusive_when_the_control_did_not_pass(self):
        """中文那一輪沒過的話，這套讀法在這台機器上讀不讀得到東西還沒定論。"""
        self.assertEqual(
            verify.evaluate_english(screen(EN_SCREEN), control_passed=False).verdict,
            verify.INCONCLUSIVE)

    def test_the_locale_actually_changed(self):
        """系統語言沒真的換掉的話，這一輪量的還是中文那一輪。"""
        report = screen(EN_SCREEN)
        report["locale"] = "zh-TW"
        self.assertEqual(
            verify.evaluate_english(report, control_passed=True).verdict,
            verify.INCONCLUSIVE)


class TheResultScreenIsCheckedForLeakage(unittest.TestCase):
    """完成畫面上也不能混進另一種語言的字。

    這一條抓到過真的缺陷：2026-09-09 的英文那一輪，完成畫面的標題是
    `Installation Complete`，內文卻是一句中文「安裝成功」。成因是
    `installer_core` 回給畫面的訊息寫死中文，而前端只有在它與標題一字不差
    時才不顯示——中文系統上剛好一樣，所以那個缺陷在中文機器上看不出來。
    下面用的就是那一輪實際讀回來的字串。
    """

    LEAKED = ("DMG Installer | App | UninstallProbe | "
              "Folder Install Location Installation complete! | Folder | "
              "Create a desktop shortcut | Installation Complete | 安裝成功 | "
              "Launch the app after installation | Done")
    CLEAN = LEAKED.replace(" | 安裝成功", "")

    def _report(self, after):
        return {"window_found": "True",
                "window_text": "Install Location | Create a desktop shortcut",
                "window_text_after": after}

    def test_a_chinese_line_on_an_english_result_screen_is_a_failure(self):
        result = verify.evaluate_english(self._report(self.LEAKED),
                                         control_passed=True)
        self.assertEqual(result.verdict, verify.FAIL)
        self.assertIn("安裝成功", result.detail)

    def test_the_same_screen_without_it_passes(self):
        self.assertEqual(
            verify.evaluate_english(self._report(self.CLEAN),
                                    control_passed=True).verdict,
            verify.PASS)


class TheExpectedStringsComeFromTheInterface(unittest.TestCase):
    """比對的字串取自 `ui/index.html` 的翻譯表，兩邊互相是對方的誘餌。"""

    def test_the_two_languages_do_not_share_any_expected_string(self):
        self.assertFalse(set(verify.ZH_MUST_HAVE) & set(verify.EN_MUST_HAVE))

    def test_each_side_looks_for_the_other_as_well(self):
        self.assertTrue(set(verify.ZH_MUST_NOT_HAVE) <= set(verify.EN_MUST_HAVE))
        self.assertTrue(set(verify.EN_MUST_NOT_HAVE) <= set(verify.ZH_MUST_HAVE))


if __name__ == "__main__":
    unittest.main(verbosity=2)
