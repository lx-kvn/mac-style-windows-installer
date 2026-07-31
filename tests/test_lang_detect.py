"""lang_detect.py 的 detect_system_language() 測試。

用 mock 頂替 ctypes.windll.kernel32.GetUserDefaultLocaleName，模擬不同的
系統語言回報結果，不需要真的在對應語言的 Windows 環境下才能測試。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lang_detect


def make_fake_get_locale_name(locale_tag):
    """回傳一個假的 GetUserDefaultLocaleName，把 locale_tag 寫進呼叫端傳入的
    buffer，模擬 Windows API 實際的「透過輸出參數回傳字串」的呼叫慣例。
    """
    def fake(buf, size):
        buf.value = locale_tag
        return 1
    return fake


class TestDetectSystemLanguage(unittest.TestCase):
    def test_exact_match_returned_as_is(self):
        with mock.patch(
            "lang_detect.ctypes.windll.kernel32.GetUserDefaultLocaleName",
            side_effect=make_fake_get_locale_name("zh-TW"), create=True,
        ):
            self.assertEqual(lang_detect.detect_system_language(["zh-TW", "en"], "zh-TW"), "zh-TW")

    def test_primary_language_prefix_match(self):
        with mock.patch(
            "lang_detect.ctypes.windll.kernel32.GetUserDefaultLocaleName",
            side_effect=make_fake_get_locale_name("en-US"), create=True,
        ):
            self.assertEqual(lang_detect.detect_system_language(["zh-TW", "en"], "zh-TW"), "en")

    def test_unsupported_language_falls_back_to_default(self):
        with mock.patch(
            "lang_detect.ctypes.windll.kernel32.GetUserDefaultLocaleName",
            side_effect=make_fake_get_locale_name("ja-JP"), create=True,
        ):
            self.assertEqual(lang_detect.detect_system_language(["zh-TW", "en"], "zh-TW"), "zh-TW")

    def test_api_failure_falls_back_to_default(self):
        with mock.patch(
            "lang_detect.ctypes.windll.kernel32.GetUserDefaultLocaleName",
            side_effect=OSError("boom"), create=True,
        ):
            self.assertEqual(lang_detect.detect_system_language(["zh-TW", "en"], "zh-TW"), "zh-TW")

    def test_api_returns_zero_falls_back_to_default(self):
        def fake_returns_zero(buf, size):
            return 0

        with mock.patch(
            "lang_detect.ctypes.windll.kernel32.GetUserDefaultLocaleName",
            side_effect=fake_returns_zero, create=True,
        ):
            self.assertEqual(lang_detect.detect_system_language(["zh-TW", "en"], "en"), "en")


if __name__ == "__main__":
    unittest.main()
