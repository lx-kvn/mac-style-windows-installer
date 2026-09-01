"""install_engine.py 的訊息改為可翻譯的 key（第十四輪決議第七至九項）。

## 為什麼要改

配置精靈是雙語的，而這些訊息原本只有繁體中文。第十四輪決議第四項要求在
使用者選定引擎的當下就地標出不相容的欄位——那些提示會出現在表單上，只有
中文的話英文介面會出現一塊沒被翻譯的區域。

## 為什麼翻譯表放在 Python 端

CLI 沒有前端可以問。訊息若只在 config.html 的 i18n 表裡，CLI 就沒有來源。

## 這裡釘住的是「不會漂移」

兩種語言各一份表、外加三份欄位分類清單，任一處增修而其他處沒跟上，症狀
都是「某個欄位在某個語言下沒有訊息」，而那不會有任何測試以外的地方會叫。
因此這裡不逐句斷言內容（那會變成把實作抄一遍），而是斷言三者的**鍵集合
必須相等**。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import install_engine


def settings(**overrides):
    data = {
        "dependencies": [], "custom_dependencies": [], "dependencies_min_version": {},
        "windows_service": {}, "scheduled_task": {}, "custom_install_dir": "",
        "pre_install_script": "", "post_install_script": "", "bundle_dependencies": [],
        "folder_name": "", "local_appdata_files": [], "no_admin_install": True,
    }
    data.update(overrides)
    return data


class TheTwoLanguagesCannotDrift(unittest.TestCase):
    def test_both_languages_are_declared(self):
        self.assertIn("zh-TW", install_engine.LANGUAGES)
        self.assertIn("en", install_engine.LANGUAGES)

    def test_every_language_has_exactly_the_same_keys(self):
        """少一把鍵的症狀是「某個欄位在某個語言下沒有訊息」，而那個欄位
        可能很少被用到，不會有人在正常使用中發現。"""
        tables = {lang: set(install_engine.MESSAGES[lang]) for lang in install_engine.LANGUAGES}
        reference = tables["zh-TW"]
        for lang, keys in tables.items():
            self.assertEqual(keys, reference,
                             f"{lang} 與 zh-TW 的鍵集合不同："
                             f"多了 {keys - reference}、少了 {reference - keys}")

    def test_no_message_is_empty(self):
        for lang in install_engine.LANGUAGES:
            for key, text in install_engine.MESSAGES[lang].items():
                self.assertTrue(text.strip(), f"{lang} 的 {key} 是空的")


class EveryClassifiedFieldHasAMessage(unittest.TestCase):
    """三份欄位清單與翻譯表之間的另一種漂移：新增一個不相容欄位卻忘了寫
    它的訊息，或反過來刪了欄位卻留下孤兒訊息。"""

    def test_every_incompatible_field_is_translatable(self):
        for field, category in install_engine.field_categories().items():
            for lang in install_engine.LANGUAGES:
                self.assertIsNotNone(
                    install_engine.field_message(field, lang),
                    f"{field}（{category}）在 {lang} 下沒有訊息")

    def test_every_category_has_a_short_hint(self):
        """就地提示只說類別、不說細節（細節留給編譯時的完整清單）。"""
        for category in (install_engine.UNSUPPORTED, install_engine.IMPOSSIBLE,
                         install_engine.MOOT):
            for lang in install_engine.LANGUAGES:
                hint = install_engine.category_hint(category, lang)
                self.assertTrue(hint and hint.strip(),
                                f"{category} 在 {lang} 下沒有簡短提示")

    def test_the_hints_are_actually_different_per_language(self):
        """兩種語言填一樣的字串會讓上面的鍵集合檢查通過，卻等於沒有翻譯。"""
        for category in (install_engine.UNSUPPORTED, install_engine.IMPOSSIBLE,
                         install_engine.MOOT):
            self.assertNotEqual(
                install_engine.category_hint(category, "zh-TW"),
                install_engine.category_hint(category, "en"),
                f"{category} 的兩種語言是同一串字")


class TheClassificationIsStatic(unittest.TestCase):
    """就地標記與「這份設定填了什麼」無關：它的用途是事前告知，使用者還沒
    填就該看得到這個欄位在這個模式下不能用。

    這與 check_settings() 是兩種不同的查詢——後者回答「這份設定裡有哪些
    違規」，只列出實際被填了的欄位。"""

    def test_it_lists_fields_that_are_not_set(self):
        categories = install_engine.field_categories()
        self.assertIn("windows_service", categories)
        self.assertIn("pre_install_script", categories)

    def test_it_covers_all_three_categories(self):
        found = set(install_engine.field_categories().values())
        self.assertEqual(found, {install_engine.UNSUPPORTED,
                                 install_engine.IMPOSSIBLE,
                                 install_engine.MOOT})

    def test_the_fourth_category_fields_are_marked_moot(self):
        categories = install_engine.field_categories()
        self.assertEqual(categories["folder_name"], install_engine.MOOT)
        self.assertEqual(categories["local_appdata_files"], install_engine.MOOT)


class FindingsCarryKeysNotSentences(unittest.TestCase):
    def test_a_finding_exposes_its_key(self):
        report = install_engine.check_settings(
            install_engine.MSIX, settings(pre_install_script="x.bat"))
        self.assertTrue(report.blocking)
        self.assertTrue(hasattr(report.blocking[0], "key"))

    def test_the_key_is_not_a_ready_made_sentence(self):
        """留著現成句子的話，呼叫端會直接印它，翻譯就永遠只做了一半。"""
        report = install_engine.check_settings(
            install_engine.MSIX, settings(pre_install_script="x.bat"))
        self.assertNotIn("MSIX", report.blocking[0].key)


class TheErrorMessageIsRendered(unittest.TestCase):
    def _report(self):
        return install_engine.check_settings(
            install_engine.MSIX,
            settings(pre_install_script="x.bat", windows_service={"service_name": "S"}))

    def test_the_default_language_is_traditional_chinese(self):
        """不帶語言參數時的行為要與改動前一致。"""
        text = self._report().error_message()
        self.assertIn("MSIX", text)
        self.assertIn("尚未支援", text)

    def test_english_renders_in_english(self):
        text = self._report().error_message("en")
        self.assertNotIn("尚未支援", text)
        self.assertIn("MSIX", text)

    def test_an_unknown_language_falls_back_instead_of_raising(self):
        """語言標籤來自系統設定或旗標，可能是任何值；為此中止建置沒有道理。"""
        text = self._report().error_message("fr-CA")
        self.assertTrue(text.strip())

    def test_both_categories_are_still_listed_separately(self):
        """第二類與第三類要求下游採取的行動不同（等待 vs 重新設計），
        混列會讓對方必須逐條判讀語氣才分得出來。"""
        for lang in install_engine.LANGUAGES:
            text = self._report().error_message(lang)
            self.assertGreaterEqual(text.count("\n\n"), 1, f"{lang} 沒有分段")

    def test_no_findings_still_gives_an_empty_string(self):
        report = install_engine.check_settings(install_engine.MSIX, settings())
        self.assertEqual(report.error_message("en"), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
