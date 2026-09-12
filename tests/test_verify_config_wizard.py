"""`tools/verify_config_wizard.py` 的判準。

配置精靈是這個專案唯一沒有被任何自動化碰過的介面。CLI（`builder_cli.py`）
走的是另一條路，CI 用的也是那一條——也就是說「使用者實際會用的那個畫面，
填完表單按下編譯，會不會產出一顆安裝檔」在此之前完全沒有驗過。

判準分三段，各自的形狀不同：

1. **環境檢查**——同一輪裡開兩次配置精靈，第一次不補 PATH、第二次補上。
   前者應該跳出「缺少編譯安裝檔所需的環境」的遮罩，後者不應該。兩次量到
   同一個值的話這一段判為無法判定：那代表這條檢查與環境無關，它說什麼都
   不算數。這一對也是這個檔案裡唯一能證明「環境檢查有辦法失敗」的證據。

2. **填表單**——每一格都讀回來才算填進去了。`SetValue` 沒有拋例外不等於
   網頁那邊真的收到；2026-09-12 實測過一種情形是例外沒拋、值也沒進去。

3. **產出安裝檔**——同一個 `Test-Path` 在按下編譯之前與之後各量一次。
   之前那次不是 False 的話判為無法判定：檔案本來就在的話，「產出來了」
   不需要任何人動手就成立。

真的跑一輪要二十分鐘（含把 Python 與二十二個套件離線裝進客體），因此驅動
虛擬機的部分不進測試，只釘住判準。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import verify_config_wizard as verify


def full_run():
    """一次完整成功的回報（2026-09-12 在 Windows 11 25H2 上實際量到的）。"""
    return {
        "env_modal_without_path": "True",
        "env_modal_with_path": "False",
        "app_name_readback": "DemoApp",
        "folder_name_readback": "DemoApp",
        "version_readback": "1.0.0",
        "publisher_readback": "VerifyBot",
        "exe_name_readback": "Setup_DemoApp",
        "folder_dialog_closed": "True",
        "page_still_says_none": "False",
        "main_exe_before": "請選擇...",
        "main_exe_after": "app.exe",
        "page_still_says_no_file": "False",
        "setup_exists_before": "False",
        "setup_exists": "True",
        "setup_size": "39192063",
    }


class TheEnvironmentCheckNeedsBothHalves(unittest.TestCase):
    """「環境齊全時不跳遮罩」單獨一次量測說明不了任何事。"""

    def test_shown_without_path_and_hidden_with_it_passes(self):
        self.assertEqual(
            verify.evaluate_environment(full_run()).verdict, verify.PASS)

    def test_never_shown_at_all_is_inconclusive(self):
        """兩次都沒跳：這條檢查與環境無關，它的「通過」不算數。"""
        report = full_run()
        report["env_modal_without_path"] = "False"
        result = verify.evaluate_environment(report)
        self.assertEqual(result.verdict, verify.INCONCLUSIVE)

    def test_still_shown_with_a_complete_environment_fails(self):
        report = full_run()
        report["env_modal_with_path"] = "True"
        self.assertEqual(
            verify.evaluate_environment(report).verdict, verify.FAIL)

    def test_a_missing_half_is_inconclusive(self):
        report = full_run()
        del report["env_modal_without_path"]
        self.assertEqual(
            verify.evaluate_environment(report).verdict, verify.INCONCLUSIVE)


class TheFormIsOnlyFilledIfItReadsBack(unittest.TestCase):
    def test_every_field_reading_back_passes(self):
        self.assertEqual(verify.evaluate_form(full_run()).verdict, verify.PASS)

    def test_a_text_field_that_stayed_empty_fails(self):
        report = full_run()
        report["publisher_readback"] = ""
        result = verify.evaluate_form(report)
        self.assertEqual(result.verdict, verify.FAIL)
        self.assertIn("發行者", result.detail)

    def test_the_folder_display_still_saying_none_fails(self):
        """對話框關掉了不等於路徑進去了。證據是頁面上那行字換掉了。"""
        report = full_run()
        report["page_still_says_none"] = "True"
        self.assertEqual(verify.evaluate_form(report).verdict, verify.FAIL)

    def test_the_icon_display_still_saying_no_file_fails(self):
        report = full_run()
        report["page_still_says_no_file"] = "True"
        self.assertEqual(verify.evaluate_form(report).verdict, verify.FAIL)

    def test_a_main_exe_that_never_changed_fails(self):
        """前後同一個值代表那個下拉沒被動到——而它是必填。"""
        report = full_run()
        report["main_exe_after"] = report["main_exe_before"]
        result = verify.evaluate_form(report)
        self.assertEqual(result.verdict, verify.FAIL)
        self.assertIn("主要執行檔", result.detail)

    def test_an_unopened_folder_dialog_is_inconclusive(self):
        report = full_run()
        report["folder_dialog_closed"] = "False"
        self.assertEqual(verify.evaluate_form(report).verdict,
                         verify.INCONCLUSIVE)


class TheInstallerMustAppearWhereItWasNotBefore(unittest.TestCase):
    def test_appearing_after_the_build_passes(self):
        self.assertEqual(verify.evaluate_build(full_run()).verdict, verify.PASS)

    def test_already_being_there_beforehand_is_inconclusive(self):
        report = full_run()
        report["setup_exists_before"] = "True"
        result = verify.evaluate_build(report)
        self.assertEqual(result.verdict, verify.INCONCLUSIVE)

    def test_nothing_produced_fails(self):
        report = full_run()
        report["setup_exists"] = "False"
        self.assertEqual(verify.evaluate_build(report).verdict, verify.FAIL)

    def test_a_suspiciously_small_file_fails(self):
        """存在不等於是一顆安裝檔。實測的那顆是 39 MB；幾 KB 的東西比較像
        中途寫壞的殘檔，那種情形下「產出來了」會是個假的綠燈。"""
        report = full_run()
        report["setup_size"] = "4096"
        self.assertEqual(verify.evaluate_build(report).verdict, verify.FAIL)

    def test_the_form_not_being_filled_makes_the_build_inconclusive(self):
        """表單沒填完就按編譯，得到的是一個提醒的彈窗。那種情形下沒有產物
        不代表編譯壞了。"""
        report = full_run()
        report["setup_exists"] = "False"
        report["page_still_says_no_file"] = "True"
        self.assertEqual(verify.evaluate_build(report).verdict,
                         verify.INCONCLUSIVE)


class TheFieldsMatchTheActualForm(unittest.TestCase):
    """欄位靠 placeholder 指名——那些字是從 `ui/config.html` 來的，改了那邊
    這裡就會找不到元素，而症狀是「填不進去」，看不出成因。"""

    def test_every_placeholder_appears_in_the_form(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "ui", "config.html"),
                  encoding="utf-8") as handle:
            html = handle.read()
        for field in verify.TEXT_FIELDS:
            self.assertIn(field.placeholder, html,
                          f"{field.label} 的 placeholder 不在 ui/config.html 裡")

    def test_a_placeholder_that_is_not_there_is_detected(self):
        """誘餌：確認上面那條檢查抓得到不存在的字串。抓不到的話它永遠會過。"""
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "ui", "config.html"),
                  encoding="utf-8") as handle:
            html = handle.read()
        self.assertNotIn("例如: 這個字串不在表單裡", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
