"""progress_report.py 的測試。

拆出來的深模組：把「算好百分比/訊息、推到 pywebview 視窗前端某個
window.<callback> 全域函式」這個原語收斂成一個函式。installer_core.py
（安裝主流程/相依元件安裝）跟 uninstall.py 各自都有一份幾乎一樣的
_report_progress()，只差前端呼叫的 callback 名稱不同，這裡收斂成單一
實作，呼叫端只傳 callback 名稱進來。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import progress_report


class TestReportProgress(unittest.TestCase):
    def test_calls_window_evaluate_js_with_callback_name_percent_and_message(self):
        window = mock.Mock()
        progress_report.report_progress(window, "updateInstallProgress", 42, "安裝中...")
        window.evaluate_js.assert_called_once()
        (call_arg,), _ = window.evaluate_js.call_args
        self.assertIn("window.updateInstallProgress(42,", call_arg)
        self.assertIn("安裝中...", call_arg)

    def test_message_is_json_encoded_so_special_characters_are_safe(self):
        window = mock.Mock()
        progress_report.report_progress(window, "updateUninstallProgress", 5, 'a "quote" and \\ backslash')
        (call_arg,), _ = window.evaluate_js.call_args
        self.assertIn('\\"quote\\"', call_arg)

    def test_none_window_is_a_silent_no_op(self):
        # window 還沒建立好（例如太早呼叫）時不能拋例外，直接什麼都不做。
        progress_report.report_progress(None, "updateInstallProgress", 1, "訊息")

    def test_evaluate_js_exception_is_swallowed(self):
        window = mock.Mock()
        window.evaluate_js.side_effect = RuntimeError("視窗已關閉")
        # 不應該往外拋——呼叫端（安裝/解除安裝主流程）不該因為前端視窗
        # 剛好在這個時間點已經關閉就整個流程跟著中斷。
        progress_report.report_progress(window, "updateInstallProgress", 1, "訊息")


if __name__ == "__main__":
    unittest.main()
