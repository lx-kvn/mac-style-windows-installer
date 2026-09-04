"""file_extension.py 的測試：副檔名的正規化、驗證，以及各處要用的名字。

對應稽核 D2。修正前，副檔名這個概念的規則散在四個地方各自實作：

- `packaging_core` 解析清單（補上開頭的點、轉小寫）——驗證只到這裡為止
- `file_assoc.prog_id()` 推出登錄表的 ProgID
- `builder.py` 推出傳統引擎內嵌圖示的檔名 `doc_icon_<副檔名>.ico`
- `msix_manifest.association_group_name()`／`association_logo_name()` 推出
  套件清單的關聯群組名與套件內的圖示檔名

四處都沒有檢查字元集，而 `msix_manifest.association_group_name()` 的註釋寫著
「字元集的檢查留在驗證階段」——專案裡沒有那個階段。實測結果：帶空白、非
ASCII、引號、超長的副檔名一路通到 `makeappx`，錯誤訊息不指向副檔名欄位；
帶 `..\\` 的副檔名會讓圖示被複製到組裝目錄之外。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import file_extension


class NormalizeTest(unittest.TestCase):
    """正規化只做形狀，不做判斷——判斷是 validate() 的事。"""

    def test_a_bare_name_gains_a_leading_dot(self):
        self.assertEqual(file_extension.normalize("txt"), ".txt")

    def test_an_existing_leading_dot_is_kept_not_doubled(self):
        self.assertEqual(file_extension.normalize(".txt"), ".txt")

    def test_case_is_folded_down(self):
        """關聯群組名必須全小寫（Microsoft 對 Name 屬性的規定），登錄表的
        比對也不分大小寫，因此一律轉小寫。"""
        self.assertEqual(file_extension.normalize(".TXT"), ".txt")

    def test_surrounding_whitespace_is_dropped(self):
        self.assertEqual(file_extension.normalize("  txt  "), ".txt")

    def test_an_empty_value_stays_empty_rather_than_becoming_a_lone_dot(self):
        """空字串補上點會變成 `.`，那是個看起來合法、實際無意義的副檔名。"""
        self.assertEqual(file_extension.normalize(""), "")
        self.assertEqual(file_extension.normalize("   "), "")


class ValidateTest(unittest.TestCase):
    """通過回傳 None，否則回傳一則可以直接顯示的訊息。"""

    def test_an_ordinary_extension_passes(self):
        self.assertIsNone(file_extension.validate(".txt"))

    def test_digits_hyphens_underscores_and_interior_dots_pass(self):
        """`.tar.gz`、`.my-type`、`.my_type`、`.mp3` 都是真實會出現的形式。"""
        for ext in (".tar.gz", ".my-type", ".my_type", ".mp3", ".7z"):
            self.assertIsNone(file_extension.validate(ext), ext)

    def test_a_space_is_rejected(self):
        """Microsoft 對關聯群組名的規定是「全小寫、不含空白」。"""
        self.assertIsNotNone(file_extension.validate(".my ext"))

    def test_non_ascii_is_rejected(self):
        self.assertIsNotNone(file_extension.validate(".中文"))

    def test_a_quote_is_rejected(self):
        self.assertIsNotNone(file_extension.validate('.a"b'))

    def test_a_path_separator_is_rejected(self):
        """這個字串會變成套件目錄裡的檔名，穿越出去就寫到組裝目錄之外。"""
        self.assertIsNotNone(file_extension.validate(".a/b"))
        self.assertIsNotNone(file_extension.validate(".a\\b"))

    def test_a_parent_directory_reference_is_rejected(self):
        self.assertIsNotNone(file_extension.validate("..\\..\\evil"))

    def test_over_the_length_limit_is_rejected(self):
        """關聯群組名的上限是 64 個字元（Microsoft 的規定）。"""
        self.assertIsNone(file_extension.validate("." + "x" * 64))
        self.assertIsNotNone(file_extension.validate("." + "x" * 65))

    def test_a_lone_dot_is_rejected(self):
        self.assertIsNotNone(file_extension.validate("."))
        self.assertIsNotNone(file_extension.validate(".."))

    def test_an_empty_value_is_rejected(self):
        self.assertIsNotNone(file_extension.validate(""))

    def test_the_message_names_the_extension_it_is_talking_about(self):
        """一次填好幾個副檔名時，訊息不指名等於要使用者自己逐一比對。"""
        self.assertIn("my ext", file_extension.validate(".my ext"))

    def test_the_message_follows_the_requested_language(self):
        zh = file_extension.validate(".my ext", lang="zh-TW")
        en = file_extension.validate(".my ext", lang="en")
        self.assertNotEqual(zh, en)


class ParseListTest(unittest.TestCase):
    """`"txt, .abc，xyz"` 這種使用者輸入的解析。全形逗號也認得——中文輸入法
    下打出全形逗號是常態。"""

    def test_it_splits_normalizes_and_returns_no_error(self):
        got, error = file_extension.parse_list("txt, .ABC，xyz")
        self.assertEqual(got, [".txt", ".abc", ".xyz"])
        self.assertIsNone(error)

    def test_empty_items_are_dropped_not_turned_into_lone_dots(self):
        got, error = file_extension.parse_list("txt,,  ,abc")
        self.assertEqual(got, [".txt", ".abc"])
        self.assertIsNone(error)

    def test_an_empty_input_gives_an_empty_list(self):
        got, error = file_extension.parse_list("")
        self.assertEqual(got, [])
        self.assertIsNone(error)

    def test_one_bad_item_fails_the_whole_list(self):
        got, error = file_extension.parse_list("txt, my ext")
        self.assertIsNone(got)
        self.assertIsNotNone(error)

    def test_duplicates_collapse_keeping_the_first_position(self):
        """同一個副檔名寫兩次會產生兩個同名的關聯群組，套件清單因此無效。"""
        got, error = file_extension.parse_list("txt, .TXT, abc")
        self.assertEqual(got, [".txt", ".abc"])
        self.assertIsNone(error)


class DerivedNamesTest(unittest.TestCase):
    """四個推導出來的名字集中在這裡，因此不可能各自漂移。"""

    def test_prog_id_keeps_the_existing_convention(self):
        """對外契約：既有安裝寫進登錄表的就是這個字串，改了解除安裝會清不掉。"""
        self.assertEqual(file_extension.prog_id(".locked"), "AppFilelocked")

    def test_prog_id_matches_what_file_assoc_exposes(self):
        """`file_assoc.prog_id()` 是 CONTEXT.md 記載的對齊點，兩者必須相同。"""
        import file_assoc
        for ext in (".locked", ".tar.gz", ".txt"):
            self.assertEqual(file_assoc.prog_id(ext), file_extension.prog_id(ext))

    def test_the_msix_group_name_drops_the_dot(self):
        self.assertEqual(file_extension.msix_group(".locked"), "locked")

    def test_the_msix_group_name_of_a_multi_part_extension_keeps_interior_dots(self):
        self.assertEqual(file_extension.msix_group(".tar.gz"), "tar.gz")

    def test_the_msix_logo_name_is_derived_from_the_group_name(self):
        self.assertEqual(file_extension.msix_logo_name(".locked"), "doc_locked.png")

    def test_the_traditional_icon_name_keeps_the_existing_convention(self):
        """既有的安裝檔內嵌的就是這個檔名，安裝端也認這個名字。"""
        self.assertEqual(file_extension.traditional_icon_name(".locked"),
                         "doc_icon_locked.ico")

    def test_every_derived_name_refuses_an_unvalidated_extension(self):
        """推導函式是最後一道防線：驗證漏掉時，不該安靜地產出一個會被當成
        路徑使用的字串。"""
        for derive in (file_extension.msix_group,
                       file_extension.msix_logo_name,
                       file_extension.traditional_icon_name,
                       file_extension.prog_id):
            with self.assertRaises(file_extension.InvalidExtension,
                                   msg=derive.__name__):
                derive("..\\..\\evil")


if __name__ == "__main__":
    unittest.main()
