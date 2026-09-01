"""配置精靈的引擎選擇器與 msix 欄位（第十三輪決議第三項）。

MSIX 若停在 CLI，會是這個工具第一項 GUI 與 CLI 不對等的功能——清查過現況，
兩邊的欄位目前一一對應，沒有任何一方獨有的東西。這裡加的就是補上那個缺口
所需的表單欄位。

MSIX 專屬欄位只在選了 MSIX 引擎時出現。傳統引擎完全不看它們（後端亦然，
見 `install_engine.py`），一直顯示等於要每個使用者都先搞懂一個他用不到的
概念。

這裡驗的是靜態的標記與程式碼結構。實際畫面另以 .claude/skills 截圖確認
（CLAUDE.md 的介面變更規定：不要沒看過畫面就宣告做完）。
"""
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_HTML = os.path.join(REPO_ROOT, "ui", "config.html")


def _read():
    with open(CONFIG_HTML, "r", encoding="utf-8") as f:
        return f.read()


class TheEngineSelectorExists(unittest.TestCase):
    def setUp(self):
        self.html = _read()

    def test_both_engines_can_be_chosen(self):
        for value in ("traditional", "msix"):
            self.assertIn(f'value="{value}"', self.html,
                          f"沒有可以選 {value} 引擎的控制項")

    def test_they_are_one_radio_group(self):
        """兩種引擎是二選一（第二輪決議第一項），不是兩個各自獨立的開關。"""
        group = re.findall(r'name="install_engine"', self.html)
        self.assertGreaterEqual(len(group), 2, "引擎選項不在同一組單選裡")

    def test_the_traditional_engine_is_preselected(self):
        """沒填即為 traditional，既有的設定檔與既有的使用者習慣都不受影響。"""
        m = re.search(r'<input[^>]*id="engine_traditional"[^>]*>', self.html)
        self.assertIsNotNone(m, "找不到傳統引擎的選項")
        self.assertIn("checked", m.group(0))

    def test_changing_the_engine_runs_a_handler(self):
        self.assertIn("onEngineChange()", self.html)
        self.assertRegex(self.html, r"function\s+onEngineChange\s*\(")


class TheMsixFieldsExist(unittest.TestCase):
    def setUp(self):
        self.html = _read()

    def test_the_identity_name_field_is_present(self):
        self.assertIn('id="msix_identity_name"', self.html)

    def test_the_certificate_subject_field_is_present(self):
        self.assertIn('id="msix_certificate_subject"', self.html)

    def test_the_minimum_windows_version_field_is_present(self):
        self.assertIn('id="msix_min_windows_version"', self.html)

    def test_the_three_icon_overrides_are_present(self):
        """三個宣告位置的最小邊長各自不同（150／44／50），因此是三個獨立的
        覆蓋欄位，不是一個。"""
        for position in ("tile", "taskbar", "store"):
            self.assertIn(f'id="msix_icon_{position}"', self.html,
                          f"缺少 {position} 的圖示覆蓋欄位")

    def test_the_identity_name_warns_that_it_cannot_be_changed(self):
        """一經發布即不可變更（docs/adr/0007）：改了系統會當成另一個不相關
        的應用程式並存安裝，而使用者不會收到任何警告。"""
        self.assertRegex(self.html, r"不可變更|不能變更|一經發布")

    def test_the_msix_section_starts_hidden(self):
        m = re.search(r'<div id="msix_section"[^>]*>', self.html)
        self.assertIsNotNone(m, "找不到 msix 欄位區塊")
        self.assertIn("display: none", m.group(0),
                      "預設是傳統引擎，msix 區塊不該一開始就顯示")


class TheFormSendsTheEngineAndTheMsixBlock(unittest.TestCase):
    def setUp(self):
        self.html = _read()

    def test_the_engine_is_sent(self):
        self.assertRegex(self.html, r"install_engine\s*:")

    def test_the_msix_block_is_sent(self):
        self.assertRegex(self.html, r"\bmsix\s*:")

    def test_the_msix_block_carries_the_identity_name(self):
        self.assertRegex(self.html, r"identity_name\s*:")

    def test_the_msix_block_carries_the_icon_overrides(self):
        self.assertRegex(self.html, r"icons\s*:")


class BothLanguagesAreTranslated(unittest.TestCase):
    """新欄位若只有中文，英文介面會出現一塊沒被翻譯的區域。"""

    def setUp(self):
        self.html = _read()

    def _language_tables(self):
        """回傳 (zh 區塊, en 區塊)。兩張表都是內嵌在頁面裡的物件實字。"""
        zh = self.html.index("label_enable_signing:")
        en = self.html.index("label_enable_signing:", zh + 1)
        return self.html[zh:en], self.html[en:]

    def test_every_new_key_exists_in_both_tables(self):
        zh_block, en_block = self._language_tables()
        keys = [
            "label_install_engine", "label_engine_traditional", "label_engine_msix",
            "note_install_engine", "label_msix_identity_name",
            "label_msix_certificate_subject", "label_msix_min_windows_version",
            "note_msix",
        ]
        for key in keys:
            self.assertIn(f"{key}:", zh_block, f"繁中翻譯表缺少 {key}")
            self.assertIn(f"{key}:", en_block, f"英文翻譯表缺少 {key}")


class TheNoteExplainsTheTwoPaths(unittest.TestCase):
    """憑證在不在本機決定按下「編譯」之後會發生什麼事。使用者按下去之前
    就該知道，不是按了才從結果訊息推。"""

    def setUp(self):
        self.html = _read()

    def test_it_says_a_local_certificate_finishes_in_one_go(self):
        self.assertRegex(self.html, r"note_msix\s*:")
        note = re.search(r'note_msix:\s*"([^"]*)"', self.html)
        self.assertIsNotNone(note)
        self.assertIn("憑證", note.group(1))

    def test_it_mentions_stopping_at_the_package(self):
        note = re.search(r'note_msix:\s*"([^"]*)"', self.html)
        self.assertIn(".msix", note.group(1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
