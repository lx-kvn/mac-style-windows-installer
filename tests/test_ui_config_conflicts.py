"""F09 的 GUI 半邊：「免管理員權限安裝」與需要管理員權限的選項互斥。

`packaging_core.validate_and_build_pack_data()` 已經會把這個矛盾組合擋在
打包階段（見 tests/test_packaging_core.py 的 TestNoAdminInstallConflicts），
但那是「按下編譯之後才跳錯誤」。GUI 端「安裝位置」三選一就在同一頁，
Windows 服務與系統還原點的設定欄位在同一頁下方——使用者可以把兩個都打開、
一路填完，直到按下編譯才被拒絕。

這裡釘住兩件事：
  1. 「這次是不是免權限安裝」只在一個地方計算。原本的 no_admin_install
     推導寫在送出資料的那一段裡，如果畫面上的停用邏輯另外複製一份判斷，
     兩份就會變成要手動對齊的協定——這個專案已經因為這種形態漏改過好幾次
     （見 file_assoc.py 的模組說明）。
  2. 免權限安裝時，Windows 服務與系統還原點兩個選項會被停用。

跟 test_js_api_contract.py／test_ui_accessibility.py 同樣的手法：靜態解析
HTML。這個專案沒有 JavaScript 測試執行環境，靜態解析是唯一能把這條規則
釘住的方式。
"""
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_HTML = os.path.join(REPO_ROOT, "ui", "config.html")


def _read():
    with open(CONFIG_HTML, "r", encoding="utf-8") as f:
        return f.read()


class TestNoAdminInstallIsComputedInOnePlace(unittest.TestCase):
    def setUp(self):
        self.content = _read()

    def test_has_a_single_helper_for_the_no_admin_decision(self):
        self.assertIn("function isNoAdminInstall()", self.content)

    def test_the_submitted_payload_uses_the_helper(self):
        match = re.search(r"no_admin_install:\s*([^\n,]+)", self.content)
        self.assertIsNotNone(match, "找不到送出資料裡的 no_admin_install 欄位")
        self.assertIn("isNoAdminInstall()", match.group(1))

    def test_the_raw_radio_comparison_appears_only_inside_the_helper(self):
        """`getInstallLocation() === 'local_appdata'` 這個判斷只該出現在
        helper 裡，出現第二次就代表又多了一份要手動對齊的複本。"""
        occurrences = self.content.count("getInstallLocation() === 'local_appdata'")
        self.assertEqual(
            occurrences, 1,
            f"免權限安裝的判斷條件出現了 {occurrences} 次，應該只在 isNoAdminInstall() 裡出現一次",
        )


class TestAdminOnlyOptionsReactToInstallLocation(unittest.TestCase):
    def setUp(self):
        self.content = _read()

    def test_has_a_function_that_syncs_the_admin_only_options(self):
        self.assertIn("function updateAdminOnlyOptions()", self.content)

    def test_it_disables_both_admin_only_controls(self):
        match = re.search(
            r"function updateAdminOnlyOptions\(\) \{(.*?)\n        \}",
            self.content, re.DOTALL,
        )
        self.assertIsNotNone(match, "找不到 updateAdminOnlyOptions()")
        body = match.group(1)
        self.assertIn("need_windows_service", body)
        self.assertIn("create_restore_point", body)
        self.assertIn("isNoAdminInstall()", body)
        self.assertIn("disabled", body)

    def test_install_location_change_triggers_the_sync(self):
        match = re.search(
            r"function onInstallLocationChange\(\) \{(.*?)\n        \}",
            self.content, re.DOTALL,
        )
        self.assertIsNotNone(match, "找不到 onInstallLocationChange()")
        self.assertIn("updateAdminOnlyOptions()", match.group(1))

    def test_the_custom_path_admin_checkbox_also_triggers_the_sync(self):
        """自訂路徑那顆「這個路徑需要系統管理員權限」勾選框同樣參與
        isNoAdminInstall() 的判斷，勾選狀態改變時也要重新同步，不然使用者
        勾了它、服務選項卻還停用著。"""
        tag = re.search(
            r'<input[^>]*id="custom_install_requires_admin"[^>]*>', self.content,
        )
        self.assertIsNotNone(tag, "找不到 custom_install_requires_admin 的元素")
        self.assertIn("updateAdminOnlyOptions()", tag.group(0))

    def test_the_explanatory_note_exists_in_both_languages(self):
        for lang in ("zh-TW", "en"):
            block = re.search(
                r'"' + re.escape(lang) + r'"\s*:\s*\{(.*?)\n            \},?\n',
                self.content, re.DOTALL,
            )
            self.assertIsNotNone(block, f"找不到 {lang} 的 I18N 區塊")
            self.assertIn("note_admin_only_disabled", block.group(1))


if __name__ == "__main__":
    unittest.main()
