"""MSIX 模式的拖曳目的地：換圖示、不可點選、路徑文字改成說明文字。

對應第二輪決議第四項。MSIX 的安裝位置由系統決定，而現有的目的地是可點選的
資料夾圖示，點下去會開啟資料夾選擇對話框——那個行為在 MSIX 模式下沒有對應
的語意，留著等於讓使用者以為自己選得了位置。

保留資料夾造型而非改用其他圖形，是因為「目的地固定、不可選擇」正是 macOS
DMG 的原始形式：使用者把應用程式拖進固定的「應用程式」資料夾，沒有位置
選擇。現行可點選的設計是本專案在原始形式之外的延伸，移除它在視覺語彙上是
回歸而非退化。

這裡驗的是靜態的標記與程式碼結構。實際畫面另以 .claude/skills/run-installer-gui
截圖確認（CLAUDE.md 的介面變更規定：不要沒看過畫面就宣告做完）。
"""
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_HTML = os.path.join(REPO_ROOT, "ui", "index.html")
UI_DIR = os.path.join(REPO_ROOT, "ui")


def _read():
    with open(INDEX_HTML, "r", encoding="utf-8") as f:
        return f.read()


class TestTheAlternateIconExists(unittest.TestCase):
    def test_the_msix_folder_icon_is_in_the_repository(self):
        self.assertTrue(os.path.isfile(os.path.join(UI_DIR, "windows_folder_icon.png")))

    def test_the_page_references_it(self):
        self.assertIn("windows_folder_icon.png", _read())


class TestTheDestinationIsSwitchedByEngine(unittest.TestCase):
    def setUp(self):
        self.content = _read()

    def test_the_front_end_asks_the_backend_which_engine_this_is(self):
        self.assertIn("get_install_engine", self.content)

    def test_there_is_a_function_that_applies_the_msix_destination(self):
        self.assertIsNotNone(
            re.search(r"function\s+applyMsixDestination\s*\(", self.content),
            "找不到套用 MSIX 目的地外觀的函式",
        )

    def test_the_icon_source_is_swapped(self):
        body = re.search(r"function\s+applyMsixDestination\s*\(.*?\n        \}",
                         self.content, re.DOTALL)
        self.assertIsNotNone(body)
        self.assertIn("windows_folder_icon.png", body.group(0))


class TestTheDestinationIsNotClickable(unittest.TestCase):
    def setUp(self):
        self.content = _read()
        self.body = re.search(r"function\s+applyMsixDestination\s*\(.*?\n        \}",
                              self.content, re.DOTALL).group(0)

    def test_the_button_role_is_removed(self):
        """留著 role="button" 會讓螢幕閱讀器繼續宣告它是可按的。"""
        self.assertIn("removeAttribute('role')", self.body)

    def test_it_is_taken_out_of_the_tab_order(self):
        self.assertIn("removeAttribute('tabindex')", self.body)

    def test_it_is_marked_disabled_so_the_existing_styles_apply(self):
        """既有 CSS 已經有 #drop-target[aria-disabled="true"] 的規則
        （游標不變手指、按下去不縮放），設這個屬性就直接沿用，不必新增樣式。"""
        self.assertIn("aria-disabled", self.body)

    def test_the_existing_disabled_styles_are_still_there(self):
        self.assertIn('#drop-target[aria-disabled="true"] .icon-wrapper', self.content)

    def test_the_click_handler_refuses_in_msix_mode(self):
        """守門要放在函式裡而不是只解掉 click 監聽：鍵盤（Enter／空白鍵）
        走的是同一個函式，只處理 click 的話鍵盤那條路徑仍然打得開對話框。
        既有的 installState 守門就是為了同一個理由放在那裡的。"""
        body = re.search(r"async function\s+chooseInstallFolder\s*\(.*?\n        \}",
                         self.content, re.DOTALL).group(0)
        self.assertIn("installEngine", body)


class TestThePathTextBecomesExplanatory(unittest.TestCase):
    def setUp(self):
        self.content = _read()

    def test_both_languages_have_the_explanatory_string(self):
        """i18n 表漏一個語言的話，那個語言的使用者會看到原始的 key 名稱。"""
        self.assertEqual(
            self.content.count("msix_destination_hint:"), 2,
            "說明文字的翻譯不是剛好兩份（中英各一）",
        )

    def test_the_hint_does_not_promise_a_path(self):
        """第二輪決議第四項：不顯示系統實際的套件路徑，那對使用者不具意義。"""
        for match in re.finditer(r'msix_destination_hint:\s*"([^"]*)"', self.content):
            text = match.group(1)
            self.assertNotIn("C:\\", text)
            self.assertNotIn("Program Files", text)

    def test_the_hint_is_applied_in_the_msix_branch(self):
        body = re.search(r"function\s+applyMsixDestination\s*\(.*?\n        \}",
                         self.content, re.DOTALL).group(0)
        self.assertIn("msix_destination_hint", body)

    def test_no_path_tooltip_is_left_behind(self):
        """一般模式會把完整路徑放進 title 屬性當工具提示；MSIX 模式沒有
        路徑可放，留著上一次設定的值會變成一個對不上的提示。"""
        body = re.search(r"function\s+applyMsixDestination\s*\(.*?\n        \}",
                         self.content, re.DOTALL).group(0)
        self.assertIn("removeAttribute('title')", body)


if __name__ == "__main__":
    unittest.main()
