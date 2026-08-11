"""版本字串解析/比較的測試（installer_core.py 的 _parse_version / _compare_versions）。

這兩個函式同時被「覆蓋安裝偵測」（check_existing_install）跟未來 backlog 的
「相依元件版本檢查」共用，錯了會導致「明明是更新卻被當成降級」之類的誤判。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import installer_core as ic


class TestParseVersion(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(ic._parse_version("1.10.2"), (1, 10, 2))

    def test_missing_parts_default_to_zero_when_compared(self):
        self.assertEqual(ic._parse_version("1.2"), (1, 2))

    def test_non_numeric_suffix_is_stripped(self):
        """"1.0.0-beta" 這種帶後綴的版本號，只取數字部分，不應該讓 int() 炸掉。"""
        self.assertEqual(ic._parse_version("1.0.0-beta"), (1, 0, 0))

    def test_empty_segment_defaults_to_zero(self):
        self.assertEqual(ic._parse_version("1..2"), (1, 0, 2))

    def test_rc_suffix_digit_is_not_absorbed_into_the_preceding_segment(self):
        """真實抓到的 bug：原本的實作把整段裡「所有」數字字元濾出來串接，
        "1.0.0-rc2" 最後一段是 "0-rc2"，濾出的數字是 '0' 跟 '2'，串起來變成
        02 -> 2，導致解析成 (1, 0, 2)——跟 "1.0.2" 完全一樣，"-rc2" 這個
        後綴反而讓版本號變大。應該只取每一段「開頭連續的數字」，遇到第一個
        非數字字元就停止，後面的後綴整段忽略。"""
        self.assertEqual(ic._parse_version("1.0.0-rc2"), (1, 0, 0))

    def test_non_numeric_prefix_letter_also_stops_digit_run(self):
        self.assertEqual(ic._parse_version("1.0b3"), (1, 0))


class TestCompareVersions(unittest.TestCase):
    def test_numeric_comparison_not_lexicographic(self):
        """純字串比較會誤判 "1.10.0" < "1.2.0"（因為 '1' < '2'），
        數字比較才會正確得出 1.10.0 > 1.2.0。"""
        self.assertEqual(ic._compare_versions("1.10.0", "1.2.0"), 1)

    def test_equal_versions(self):
        self.assertEqual(ic._compare_versions("1.0.0", "1.0.0"), 0)

    def test_different_length_versions(self):
        """"1.0" 跟 "1.0.0" 語意上是同一個版本，長度不同時要補零再比，不能直接判不相等。"""
        self.assertEqual(ic._compare_versions("1.0", "1.0.0"), 0)

    def test_older_version(self):
        self.assertEqual(ic._compare_versions("1.0.0", "2.0.0"), -1)

    def test_prerelease_version_is_older_than_the_release_it_precedes(self):
        """真實抓到的 bug：數字部分解析錯誤（見上面 TestParseVersion）導致
        "1.0.0-rc2" 被判斷成比 "1.0.0" 新，方向完全反了——使用者從 1.0.0
        「降級」回一個候選版時，會被系統告知這是「升級」。數字部分相等時，
        帶有版次後綴（"-"）的版本應該被視為比沒有後綴的正式版舊。"""
        self.assertEqual(ic._compare_versions("1.0.0-rc2", "1.0.0"), -1)

    def test_release_is_newer_than_its_own_prerelease(self):
        self.assertEqual(ic._compare_versions("1.0.0", "1.0.0-rc2"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
