"""F01：後端回傳的警告清單必須真的有出口。

安裝端 `installer_core.py` 的成功結果帶著 `warnings`（Windows 服務／排程
工作／系統還原點／安裝後置腳本／捷徑建立失敗），解除安裝端 `uninstall.py`
的成功結果帶著 `warnings`（六個移除步驟各自的失敗）。兩端的前端原本都只
判斷 `status === 'success'` 就切到完成畫面，`warnings` 從來沒有被讀取——
後端把資料收集完整了，卻沒有任何一條路徑通到使用者眼前，該情境的實際行為
與收集這份清單之前完全相同。

`uninstall.py` 的說明文字明確記載這份清單存在的理由是「讓使用者不會在
PATH／捷徑／登錄表項目全部沒清乾淨的情況下，還是看到一個解除安裝完成的
畫面」——沒有前端出口，這個理由並未成立。

跟 `test_js_api_contract.py`／`test_ui_accessibility.py` 同樣的手法：靜態
解析 HTML，斷言這條「後端欄位 → 前端畫面」的接線真的存在。這個專案沒有
JavaScript 測試執行環境，靜態解析是唯一能把這條跨端契約釘住的方式；它抓
不到樣式問題，那部分依 `CLAUDE.md` 的 UI 檢查清單以實際截圖確認。
"""
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_HTML = os.path.join(REPO_ROOT, "ui", "index.html")
UNINSTALL_HTML = os.path.join(REPO_ROOT, "ui", "uninstall.html")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _i18n_block(content, lang):
    """取出 I18N 表裡某個語言的區塊原文，用來確認新增的字串兩種語言都有。"""
    match = re.search(
        r'"' + re.escape(lang) + r'"\s*:\s*\{(.*?)\n\s*\},?\n',
        content, re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"找不到 {lang} 的 I18N 區塊")
    return match.group(1)


class TestInstallSuccessScreenShowsWarnings(unittest.TestCase):
    def setUp(self):
        self.content = _read(INDEX_HTML)

    def test_has_a_container_for_the_warning_list(self):
        self.assertIn('id="installWarnings"', self.content)

    def test_success_branch_reads_warnings_from_the_result(self):
        """安裝成功那條分支必須真的讀 result.warnings，而不是只判斷 status。"""
        match = re.search(
            r"async function attemptInstall\(.*?\n            if \(result\.status === 'success'\) \{(.*?)\n            \} else if",
            self.content, re.DOTALL,
        )
        self.assertIsNotNone(match, "找不到 attemptInstall() 的成功分支")
        self.assertIn("warnings", match.group(1))

    def test_warning_strings_exist_in_both_languages(self):
        for lang in ("zh-TW", "en"):
            self.assertIn(
                "install_warnings_title", _i18n_block(self.content, lang),
                f"{lang} 缺少警告清單標題的翻譯",
            )


class TestUninstallDoneScreenShowsWarnings(unittest.TestCase):
    def setUp(self):
        self.content = _read(UNINSTALL_HTML)

    def test_has_a_container_for_the_warning_list(self):
        self.assertIn('id="uninstallWarnings"', self.content)

    def test_both_success_call_sites_go_through_the_same_renderer(self):
        """解除安裝有兩個呼叫 run_uninstall() 的地方——鎖定程序分支
        （startUninstall）與反悔倒數分支（startUninstallWithGrace）。兩個
        都要接上，只接一個等於使用者走另一條路時仍然看不到警告。這裡要求
        兩邊走同一個渲染函式，而不是各自複製一份判斷——兩份手動對齊的
        流程正是這個專案已經漏改過好幾次的形態。
        """
        success_branches = re.findall(
            r"if \(result\.status === 'success'\) \{(.*?)\} else \{",
            self.content, re.DOTALL,
        )
        self.assertEqual(
            len(success_branches), 2,
            f"預期兩個 run_uninstall() 成功分支，實際找到 {len(success_branches)} 個",
        )
        for branch in success_branches:
            self.assertIn("showUninstallDone(result)", branch)

    def test_the_renderer_reads_warnings_from_the_result(self):
        match = re.search(
            r"function showUninstallDone\(result\) \{(.*?)\n        \}",
            self.content, re.DOTALL,
        )
        self.assertIsNotNone(match, "找不到 showUninstallDone()")
        self.assertIn("warnings", match.group(1))

    def test_warning_strings_exist_in_both_languages(self):
        for lang in ("zh-TW", "en"):
            self.assertIn(
                "uninstall_warnings_title", _i18n_block(self.content, lang),
                f"{lang} 缺少警告清單標題的翻譯",
            )


if __name__ == "__main__":
    unittest.main()
