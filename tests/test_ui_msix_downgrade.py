"""安裝端在部署之前要先問過降版（ADR-0015 決定二）。

webview 的前端沒有辦法在 Python 呼叫中途回答問題，因此問題一定要在觸發安裝
之前問完——形狀比照既有的覆蓋安裝提示（`check_existing_install()` 先問、
使用者同意才呼叫 `trigger_installation()`）。

手法比照 `test_ui_warning_display.py`：靜態解析 HTML。這個專案沒有 JavaScript
測試執行環境。
"""
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_HTML = os.path.join(REPO_ROOT, "ui", "index.html")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


class TheInstallerAsksBeforeDowngrading(unittest.TestCase):
    def setUp(self):
        self.html = _read(INDEX_HTML)

    def test_the_check_is_called(self):
        self.assertIn("check_msix_existing_package", self.html)

    def test_the_check_happens_before_the_install_is_triggered(self):
        """問題放在觸發之後就問不到了——那時 Python 已經在跑安裝。

        比的是真正的呼叫位置，不是第一次出現的位置：檔案開頭的說明區塊就
        提到過 `trigger_installation()`，拿它比會得到一個與程式碼無關的結論。
        """
        check = self.html.index("pywebview.api.check_msix_existing_package")
        trigger = self.html.index("pywebview.api.trigger_installation")
        self.assertLess(check, trigger,
                        "偵測寫在觸發安裝之後，那個順序問不到使用者")

    def test_consent_reaches_the_backend(self):
        """後端不會自己決定要不要降版，它只轉呈前端問到的答案。"""
        call = re.search(r"pywebview\.api\.trigger_installation\((.*?)\);",
                         self.html, re.S)
        self.assertIsNotNone(call, "找不到觸發安裝的呼叫")
        self.assertIn("msixDowngradeAllowed", call.group(1),
                      "使用者的答案沒有被帶到後端")

    def test_the_three_outcomes_are_distinguished(self):
        """none／downgrade／coexist 三種的處置不同：第一種什麼都不做，
        第二種要問，第三種只告知。"""
        for action in ("downgrade", "coexist"):
            self.assertIn(f"'{action}'", self.html,
                          f"沒有處理 {action} 這種結果")

    def test_the_coexist_case_is_told_not_asked(self):
        """發行者不同的那份套件有可能屬於另一個開發者，工具不代為判定。
        因此那是告知，不是一個要使用者回答「要不要移除」的問題。"""
        self.assertIn("msix_coexist_title", self.html)

    def test_the_downgrade_prompt_exists_in_both_languages(self):
        """訊息表少一種語言時，那個語言會顯示成 undefined。"""
        for key in ("msix_downgrade_title", "msix_coexist_title"):
            occurrences = re.findall(rf"{key}:\s*\"", self.html)
            self.assertGreaterEqual(len(occurrences), 2, f"{key} 只有一種語言")

    def test_declining_does_not_start_the_install(self):
        """使用者說不要的時候，安裝不能照樣跑下去。"""
        block = re.search(r"async function checkMsixExistingPackage[\s\S]*?\n        \}\n",
                          self.html)
        self.assertIsNotNone(block, "找不到 checkMsixExistingPackage()")
        self.assertRegex(block.group(0), r"return false|return;",
                         "沒有在使用者拒絕時中止")


if __name__ == "__main__":
    unittest.main()
