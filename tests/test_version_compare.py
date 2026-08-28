"""版本字串解析/比較的測試（version_compare.py 的 parse_version / compare_versions）。

這兩個函式同時被 installer_core.py 的「覆蓋安裝偵測」（check_existing_install）
跟 dependency_install.py 的「相依元件版本檢查」共用，錯了會導致「明明是更新
卻被當成降級」之類的誤判。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import version_compare as vc


class TestParseVersion(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(vc.parse_version("1.10.2"), (1, 10, 2))

    def test_missing_parts_default_to_zero_when_compared(self):
        self.assertEqual(vc.parse_version("1.2"), (1, 2))

    def test_non_numeric_suffix_is_stripped(self):
        """"1.0.0-beta" 這種帶後綴的版本號，只取數字部分，不應該讓 int() 炸掉。"""
        self.assertEqual(vc.parse_version("1.0.0-beta"), (1, 0, 0))

    def test_empty_segment_defaults_to_zero(self):
        self.assertEqual(vc.parse_version("1..2"), (1, 0, 2))

    def test_rc_suffix_digit_is_not_absorbed_into_the_preceding_segment(self):
        """真實抓到的 bug：原本的實作把整段裡「所有」數字字元濾出來串接，
        "1.0.0-rc2" 最後一段是 "0-rc2"，濾出的數字是 '0' 跟 '2'，串起來變成
        02 -> 2，導致解析成 (1, 0, 2)——跟 "1.0.2" 完全一樣，"-rc2" 這個
        後綴反而讓版本號變大。應該只取每一段「開頭連續的數字」，遇到第一個
        非數字字元就停止，後面的後綴整段忽略。"""
        self.assertEqual(vc.parse_version("1.0.0-rc2"), (1, 0, 0))

    def test_non_numeric_prefix_letter_also_stops_digit_run(self):
        self.assertEqual(vc.parse_version("1.0b3"), (1, 0))


class TestCompareVersions(unittest.TestCase):
    def test_numeric_comparison_not_lexicographic(self):
        """純字串比較會誤判 "1.10.0" < "1.2.0"（因為 '1' < '2'），
        數字比較才會正確得出 1.10.0 > 1.2.0。"""
        self.assertEqual(vc.compare_versions("1.10.0", "1.2.0"), 1)

    def test_equal_versions(self):
        self.assertEqual(vc.compare_versions("1.0.0", "1.0.0"), 0)

    def test_different_length_versions(self):
        """"1.0" 跟 "1.0.0" 語意上是同一個版本，長度不同時要補零再比，不能直接判不相等。"""
        self.assertEqual(vc.compare_versions("1.0", "1.0.0"), 0)

    def test_older_version(self):
        self.assertEqual(vc.compare_versions("1.0.0", "2.0.0"), -1)

    def test_prerelease_version_is_older_than_the_release_it_precedes(self):
        """真實抓到的 bug：數字部分解析錯誤（見上面 TestParseVersion）導致
        "1.0.0-rc2" 被判斷成比 "1.0.0" 新，方向完全反了——使用者從 1.0.0
        「降級」回一個候選版時，會被系統告知這是「升級」。數字部分相等時，
        帶有版次後綴（"-"）的版本應該被視為比沒有後綴的正式版舊。"""
        self.assertEqual(vc.compare_versions("1.0.0-rc2", "1.0.0"), -1)

    def test_release_is_newer_than_its_own_prerelease(self):
        self.assertEqual(vc.compare_versions("1.0.0", "1.0.0-rc2"), 1)


class TestComparingTwoPrereleases(unittest.TestCase):
    """F13：數字段相同、兩邊都有後綴時原本一律回傳 0，`1.0.0-rc1` 升級到
    `1.0.0-rc2` 會被判定成「版本完全一致」的重新安裝。

    這個情境原本踩不到，因為 `version_info._parse_version_tuple()` 讓帶
    後綴的版本號根本無法打包產出；ADR-0003 放寬版本號格式之後（稽核第三輪
    已實作）就會立刻浮現，所以這是必做項而不是選配。

    比較規則依 ADR-0003：後綴以字串逐字比較（ASCII 順序）。不引入
    semantic versioning 對 alpha/beta/rc 的語意排序——後綴是自由文字，
    無法保證使用者只用這三個詞。
    """

    def test_later_rc_is_newer(self):
        self.assertEqual(vc.compare_versions("1.0.0-rc2", "1.0.0-rc1"), 1)
        self.assertEqual(vc.compare_versions("1.0.0-rc1", "1.0.0-rc2"), -1)

    def test_identical_prereleases_are_equal(self):
        self.assertEqual(vc.compare_versions("1.0.0-rc1", "1.0.0-rc1"), 0)

    def test_beta_sorts_before_rc_by_ascii_order(self):
        self.assertEqual(vc.compare_versions("1.0.0-beta", "1.0.0-rc1"), -1)

    def test_numeric_segments_still_win_over_the_suffix(self):
        """後綴只有在數字段完全相同時才拿出來比——`1.0.1-rc1` 比
        `1.0.0-rc9` 新，不能因為後綴字串較小就判成舊的。"""
        self.assertEqual(vc.compare_versions("1.0.1-rc1", "1.0.0-rc9"), 1)

    def test_double_digit_rc_sorts_by_ascii_not_by_number(self):
        """已知限制（ADR-0003）：後綴採 ASCII 逐字順序，`1.0.0-rc10` 會被
        判定為早於 `1.0.0-rc9`（'1' < '9'）。需要兩位數 rc 編號的使用者要
        自行補零成 rc09。這裡把這個限制釘成測試，讓它是一個有記錄的取捨，
        而不是哪天有人以為修好了就默默改掉。"""
        self.assertEqual(vc.compare_versions("1.0.0-rc10", "1.0.0-rc9"), -1)


class TestPrereleaseSuffix(unittest.TestCase):
    def test_returns_the_text_after_the_first_hyphen(self):
        self.assertEqual(vc.prerelease_suffix("1.0.0-rc1"), "rc1")

    def test_returns_empty_string_without_a_suffix(self):
        self.assertEqual(vc.prerelease_suffix("1.0.0"), "")

    def test_keeps_everything_after_the_first_hyphen(self):
        """後綴本身含連字號時整段保留，不再切一次——後綴是自由文字。"""
        self.assertEqual(vc.prerelease_suffix("1.0.0-rc1-hotfix"), "rc1-hotfix")


if __name__ == "__main__":
    unittest.main(verbosity=2)
