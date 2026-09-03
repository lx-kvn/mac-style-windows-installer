"""所有訊息表的一致性：每個語言的鍵集合必須相同。

訊息表由各模組自己持有（見 `messages.py` 的說明），因此「兩種語言不會漂移」
這件事需要一個知道所有表的地方。那個地方是這裡，不是產品程式碼。

漂移的症狀是某個欄位在某個語言下顯示成中文、或顯示成鍵本身——而那不會有
任何測試以外的地方會叫。新增一個有訊息表的模組時，把它加進 `MODULES`。
"""
import importlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import messages

# 有訊息表的模組。加新模組時一併加進來——漏加的話那個模組的表不會被檢查。
MODULES = [
    "install_engine",
    "png_size",
    "cert_subject",
    "msix_settings",
    "packaging_core",
    "webview2_runtime",
]


def _tables():
    found = {}
    for name in MODULES:
        module = importlib.import_module(name)
        table = getattr(module, "MESSAGES", None)
        if table is not None:
            found[name] = table
    return found


class EveryTableIsConsistent(unittest.TestCase):
    def test_at_least_one_module_has_a_table(self):
        """全部都沒有的話，下面幾條會空跑而永遠通過。"""
        self.assertTrue(_tables(), "沒有任何模組有 MESSAGES 表")

    def test_no_language_is_missing_a_key(self):
        for name, table in _tables().items():
            gaps = messages.missing_keys(table)
            self.assertEqual(gaps, {}, f"{name}.MESSAGES 的鍵集合不一致：{gaps}")

    def test_every_table_covers_every_declared_language(self):
        for name, table in _tables().items():
            self.assertEqual(set(table), set(messages.LANGUAGES),
                             f"{name}.MESSAGES 的語言不是 {messages.LANGUAGES}")

    def test_no_message_is_empty(self):
        for name, table in _tables().items():
            for lang, entries in table.items():
                for key, text in entries.items():
                    self.assertTrue(str(text).strip(),
                                    f"{name}.MESSAGES[{lang}][{key}] 是空的")

    def test_the_two_languages_are_not_the_same_string(self):
        """兩種語言填同一串字會讓鍵集合檢查通過，卻等於沒有翻譯。

        少數例外（例如純粹的格式字串）以 IDENTICAL_ON_PURPOSE 列出，列出
        本身就是一次確認。
        """
        IDENTICAL_ON_PURPOSE = {
            # 分隔符不是句子，兩種語言剛好都用同一個符號時列在這裡。
        }
        for name, table in _tables().items():
            zh, en = table["zh-TW"], table["en"]
            for key in zh:
                if (name, key) in IDENTICAL_ON_PURPOSE:
                    continue
                self.assertNotEqual(
                    zh[key], en.get(key),
                    f"{name}.MESSAGES 的 {key} 兩種語言是同一串字")


class NoModuleKeepsItsOwnTranslateFunction(unittest.TestCase):
    """機制只有一份。`install_engine` 原本有自己的 translate()，四個模組都要
    雙語之後，再複製三份等於同一段邏輯有四個版本。"""

    def test_the_shared_one_is_used(self):
        import inspect
        for name in MODULES:
            module = importlib.import_module(name)
            if not hasattr(module, "MESSAGES"):
                continue
            source = inspect.getsource(module)
            self.assertNotIn("def translate(", source,
                             f"{name} 自己又定義了一份 translate()")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class ParameterNamesCannotCollideWithTheHelpersOwn(unittest.TestCase):
    """真實抓到的缺陷：`_t(key, lang, **params)` 的前兩個參數叫 `key` 與
    `lang`，而訊息的參數也可能叫這兩個名字——`custom_dep.insecure_url` 要帶
    的正是 `key=key`，於是 `TypeError: got multiple values for argument 'key'`。

    這是潛伏的陷阱：其他模組現在沒撞到只是碰巧沒有同名的參數。把前兩個參數
    改成僅限位置（`/`）之後，訊息的參數叫什麼都不會撞。
    """

    def test_every_helper_takes_its_first_two_arguments_positionally_only(self):
        import inspect
        for name in MODULES + ["messages"]:
            module = importlib.import_module(name)
            # `_t` 是模組自用的慣例名稱；`text` 用於訊息要給其他模組呼叫的
            # 情況（webview2_runtime 的對話框由三個進入點各自顯示），那種
            # 情況下底線開頭的名字會誤導。名稱不是這條測試的重點，僅限位置
            # 參數才是——找不到任何一個仍然算失敗。
            fn = (getattr(module, "_t", None)
                  or getattr(module, "text", None)
                  or getattr(module, "translate", None))
            self.assertIsNotNone(fn, f"{name} 沒有取訊息的函式")
            kinds = [p.kind for p in inspect.signature(fn).parameters.values()]
            self.assertIn(inspect.Parameter.POSITIONAL_ONLY, kinds,
                          f"{name} 的取訊息函式沒有僅限位置參數，"
                          f"訊息參數若與它同名就會撞")

    def test_a_message_parameter_named_key_actually_works(self):
        """直接驗行為，不只驗簽章。"""
        import packaging_core
        rendered = packaging_core._invalid("custom_dep.duplicate", "zh-TW", key="abc")
        self.assertIn("abc", rendered)
