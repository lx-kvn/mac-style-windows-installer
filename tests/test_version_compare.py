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


if __name__ == "__main__":
    unittest.main(verbosity=2)
