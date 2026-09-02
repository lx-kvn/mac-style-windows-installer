"""messages.py：共用的訊息翻譯機制。

`install_engine.py` 先前自己有一套 `translate()` 與語言常數（第十四輪決議
第七項）。四個模組都要支援雙語之後，再複製三份等於同一段邏輯有四個版本，
其中三份日後不會被修到。

## 為什麼不是一張集中的大表

訊息留在使用它的模組裡：`png_size` 的訊息只有 `png_size` 知道什麼時候該說、
說的是哪一件事。集中成一張表之後，改動一則訊息要跳到另一個檔案，而「這則
訊息還有沒有人在用」也不再看得出來。

這裡提供的是**機制**（查表、語言退回、參數代入），表由各模組自己持有。
跨模組的一致性由 `tests/test_message_tables.py` 統一檢查——那件事需要一個
知道所有表的地方，但那個地方是測試，不是產品程式碼。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import messages


TABLE = {
    "zh-TW": {
        "greeting": "你好",
        "with_param": "檔案「{name}」不存在。",
    },
    "en": {
        "greeting": "Hello",
        "with_param": "The file \"{name}\" does not exist.",
    },
}


class TheBasicLookup(unittest.TestCase):
    def test_it_returns_the_requested_language(self):
        self.assertEqual(messages.translate(TABLE, "greeting", "en"), "Hello")
        self.assertEqual(messages.translate(TABLE, "greeting", "zh-TW"), "你好")

    def test_the_default_language_is_traditional_chinese(self):
        """不帶語言參數時的行為要與訊息 key 化之前一致。"""
        self.assertEqual(messages.translate(TABLE, "greeting"), "你好")

    def test_parameters_are_substituted(self):
        text = messages.translate(TABLE, "with_param", "en", name="a.txt")
        self.assertIn("a.txt", text)


class ItNeverRaisesForABadInput(unittest.TestCase):
    """語言標籤來自系統設定或命令列旗標，鍵來自呼叫端。為了顯示層的問題
    中止建置沒有道理——使用者要的是安裝檔，不是一堂錯誤處理課。"""

    def test_an_unknown_language_falls_back_to_the_default(self):
        self.assertEqual(messages.translate(TABLE, "greeting", "fr-CA"), "你好")

    def test_a_language_that_lacks_one_key_falls_back_for_that_key(self):
        partial = {"zh-TW": {"a": "甲", "b": "乙"}, "en": {"a": "A"}}
        self.assertEqual(messages.translate(partial, "b", "en"), "乙")

    def test_an_unknown_key_returns_the_key_itself(self):
        """回傳鍵本身而不是空字串：畫面上出現一串看得懂的識別字，比出現
        一片空白更容易查出是哪裡漏了。"""
        self.assertEqual(messages.translate(TABLE, "no_such_key", "en"), "no_such_key")

    def test_a_missing_parameter_does_not_raise(self):
        """訊息裡的佔位符與呼叫端給的參數對不上時，寧可顯示原始的佔位符，
        也不要讓整個驗證流程因為一個字串而中斷。"""
        text = messages.translate(TABLE, "with_param", "en")
        self.assertIn("{name}", text)


class TheLanguageSet(unittest.TestCase):
    def test_both_languages_are_declared(self):
        self.assertEqual(set(messages.LANGUAGES), {"zh-TW", "en"})

    def test_the_default_is_one_of_them(self):
        self.assertIn(messages.DEFAULT_LANGUAGE, messages.LANGUAGES)


class TheDriftCheck(unittest.TestCase):
    """供測試使用的工具函式：找出一張表在各語言之間缺了哪些鍵。"""

    def test_a_consistent_table_reports_nothing(self):
        self.assertEqual(messages.missing_keys(TABLE), {})

    def test_it_names_the_language_and_the_keys(self):
        partial = {"zh-TW": {"a": "甲", "b": "乙"}, "en": {"a": "A"}}
        self.assertEqual(messages.missing_keys(partial), {"en": {"b"}})

    def test_an_extra_key_in_one_language_is_also_reported(self):
        """多出來的鍵同樣是漂移：那則訊息在另一個語言下不存在。"""
        extra = {"zh-TW": {"a": "甲"}, "en": {"a": "A", "c": "C"}}
        self.assertEqual(messages.missing_keys(extra), {"zh-TW": {"c"}})


if __name__ == "__main__":
    unittest.main(verbosity=2)
