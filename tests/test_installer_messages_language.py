"""安裝流程送到畫面上的字要跟著介面語言走。

真實抓到（2026-09-11，`tools/verify_ui_language.py` 在真機上跑出來的）：英文
系統上完成畫面的標題是 `Installation Complete`，內文卻是一句中文「安裝成功」。
成因是 `installer_core` 回給畫面的訊息全部寫死中文，而前端只在它與標題一字
不差時才不顯示（`messageBeyondTitle()`）——中文系統上兩者剛好一樣，因此這個
缺陷在中文機器上完全看不出來，而 CI 沒有互動桌面也看不到。

同一個成因底下，安裝過程的進度文字與所有失敗訊息也都是中文。

**這一份的每一條都指定語言，不靠機器偵測到的那一個。** 靠機器的話，同一條
測試在繁體中文的開發機與英文的 runner 上量到的是不同的東西，而其中一邊會紅
得莫名其妙（v0.16.0 就是這樣：本機 1654 項全綠、CI 全紅）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import installer_core
import messages
from _fakes import make_installer_api


class _FakeWinError(OSError):
    def __init__(self, winerror):
        super().__init__("boom")
        self.winerror = winerror


def api_in(lang):
    api = make_installer_api()
    api.ui_language = lang
    return api


class TheTableCoversBothLanguages(unittest.TestCase):
    def test_no_key_is_missing_from_either_language(self):
        self.assertEqual(messages.missing_keys(installer_core.MESSAGES), {})

    def test_the_success_message_matches_the_frontend_title_word_for_word(self):
        """前端只在兩者一字不差時才不顯示內文。差一個字，英文使用者就會在
        完成畫面上看到一句多餘的話——這正是這次抓到的那個缺陷。

        對照的是 `ui/index.html` 裡那張翻譯表，不是這裡另外抄一份常數：
        抄一份的話兩邊各改各的，測試仍然是綠的。
        """
        html = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "ui", "index.html")
        with open(html, encoding="utf-8") as handle:
            text = handle.read()
        for lang, quoted in (("zh-TW", '"zh-TW"'), ("en", '"en"')):
            start = text.index(quoted + ": {")
            chunk = text[start:start + 4000]
            marker = "install_success_title: \""
            title = chunk[chunk.index(marker) + len(marker):]
            title = title[:title.index('"')]
            self.assertEqual(installer_core._t("install_success", lang), title,
                             f"{lang} 的成功訊息與前端標題對不上")


class TheMessagesActuallyChangeLanguage(unittest.TestCase):
    """每一條都比對兩種語言**不相同**。

    只斷言英文那一邊有某個英文字的話，查表整個壞掉、兩種語言都回退到中文時
    仍然可能通過（回退的結果裡也可能湊巧含有那個字）。要求兩者相異才排除得掉
    那種情形。
    """

    KEYS = ("install_in_progress", "no_payload", "empty_payload",
            "install_success", "progress_installing", "progress_done",
            "os_write_protect", "os_access_denied", "os_permission")

    def test_each_one_reads_differently_in_the_two_languages(self):
        for key in self.KEYS:
            self.assertNotEqual(installer_core._t(key, "zh-TW"),
                                installer_core._t(key, "en"), key)

    def test_the_english_side_has_no_chinese_left_in_it(self):
        for key, text in installer_core.MESSAGES["en"].items():
            has_han = any("\u4e00" <= ch <= "\u9fff" for ch in text)
            self.assertFalse(has_han, f"en 的 {key} 裡還有中文：{text}")


class TheOsErrorDescriptionFollowsTheLanguage(unittest.TestCase):
    """那幾句是安裝失敗時唯一告訴使用者「為什麼」的東西。"""

    def test_write_protect_is_english_on_an_english_system(self):
        message = api_in("en")._describe_install_os_error(
            _FakeWinError(19), r"D:\file.dll")
        self.assertIn("read-only", message)
        self.assertNotIn("唯讀", message)

    def test_access_denied_is_english_on_an_english_system(self):
        message = api_in("en")._describe_install_os_error(
            _FakeWinError(5), r"C:\app\file.dll")
        self.assertIn("administrator", message)
        self.assertNotIn("系統管理員", message)

    def test_the_same_case_is_chinese_on_a_chinese_system(self):
        """對照：同一個情形在中文那一邊要是中文。少了它，上面兩條在一個
        「什麼都回傳英文」的壞掉實作下也會通過。"""
        message = api_in("zh-TW")._describe_install_os_error(
            _FakeWinError(19), r"D:\file.dll")
        self.assertIn("唯讀", message)

    def test_the_generic_fallback_follows_the_language_too(self):
        english = api_in("en")._describe_install_os_error(OSError("disk on fire"))
        self.assertIn("Installation failed", english)
        chinese = api_in("zh-TW")._describe_install_os_error(OSError("硬碟燒了"))
        self.assertIn("安裝失敗", chinese)


class TheDiskShortfallDetailFollowsTheLanguage(unittest.TestCase):
    """那句訊息裡的明細原本也是中文拼出來的，連頓號都是。"""

    def test_the_separator_is_not_the_chinese_one_in_english(self):
        self.assertEqual(installer_core._t("list_separator", "zh-TW"), "、")
        self.assertNotEqual(installer_core._t("list_separator", "en"), "、")

    def test_the_item_reads_as_english(self):
        item = installer_core._t("disk_shortfall_item", "en",
                                 drive="E:", required="700 KB", free="200 KB")
        self.assertIn("700 KB", item)
        self.assertIn("200 KB", item)
        self.assertNotIn("需要", item)


if __name__ == "__main__":
    unittest.main(verbosity=2)
