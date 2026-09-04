"""稽核 D1：密碼關卡對「安裝檔內部不一致」要有自己的出口。

`installer_core.verify_install_password()` 現在對「設定檔說有密碼保護，但
安裝檔裡沒有加密內容」這種情形拋 `MissingEncryptedPayloadError`，而不是
回傳 False——回傳 False 等同告訴使用者「密碼錯了」，他會一直重試一件不可能
成功的事。

前端若不接住這個例外，`await` 會直接拒絕，按鈕按下去毫無反應：使用者看到
的是一個沒有任何回饋的畫面，跟 `webview2_runtime.py` 開頭記載的「停在載入
中」是同一種最不利的症狀（空白至少看得出壞了）。

手法比照 `test_ui_warning_display.py`／`test_js_api_contract.py`：靜態解析
HTML，斷言這條接線存在。這個專案沒有 JavaScript 測試執行環境。
"""
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_HTML = os.path.join(REPO_ROOT, "ui", "index.html")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _submit_password_body(html):
    match = re.search(r"async function submitPassword\(\)\s*\{(.*?)\n        \}",
                      html, re.S)
    assert match is not None, "找不到 submitPassword()"
    return match.group(1)


class ThePasswordGateHandlesAnUnusableInstaller(unittest.TestCase):
    def setUp(self):
        self.html = _read(INDEX_HTML)

    def test_the_call_is_wrapped_so_a_rejection_does_not_kill_the_button(self):
        body = _submit_password_body(self.html)
        self.assertIn("verify_install_password", body)
        self.assertRegex(body, r"try\s*\{",
                         "verify_install_password 的呼叫沒有被接住，"
                         "例外會讓按鈕毫無反應")
        self.assertRegex(body, r"catch\s*\(")

    def test_there_is_a_message_distinct_from_the_wrong_password_one(self):
        """兩者是不同的事：一個重試會成功，一個永遠不會。"""
        self.assertIn("password_unusable_error", self.html)
        self.assertIn("password_wrong_error", self.html)

    def test_the_distinct_message_exists_in_both_languages(self):
        """訊息表少一種語言時，那個語言會顯示成 undefined。"""
        occurrences = re.findall(r"password_unusable_error:\s*\"", self.html)
        self.assertGreaterEqual(len(occurrences), 2,
                                "password_unusable_error 只有一種語言")

    def test_the_unusable_case_does_not_invite_a_retry(self):
        """輸入框不該被清空並重新聚焦——那是「再試一次」的邀請。"""
        body = _submit_password_body(self.html)
        catch_block = re.search(r"catch\s*\([^)]*\)\s*\{(.*?)\n            \}",
                                body, re.S)
        self.assertIsNotNone(catch_block, "找不到 catch 區塊")
        self.assertNotIn("input.select()", catch_block.group(1))
        self.assertIn("return", catch_block.group(1),
                      "catch 之後沒有中止，會繼續走密碼正確的那條路")


if __name__ == "__main__":
    unittest.main()
