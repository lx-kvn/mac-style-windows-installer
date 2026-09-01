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
        # 範圍限定在 submitForm 組出來的那包資料裡。原本是搜尋整份檔案的第一個
        # no_admin_install:，那個寫法假設「檔案裡只會有一處」——引擎連動的
        # 欄位對應表出現之後就不成立了，而它抓到的是那張表、不是送出的資料。
        payload = re.search(r"const data = \{(.*?)\n            \};",
                            self.content, re.S)
        self.assertIsNotNone(payload, "找不到 submitForm 組出來的資料")
        match = re.search(r"no_admin_install:\s*([^\n,]+)", payload.group(1))
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


class TestInstallPasswordBlock(unittest.TestCase):
    """安裝密碼保護的欄位（F14 的 GUI 部分，設計見 docs/adr/0004）。

    這個區塊有兩種填法，只有一種能寫進設定檔。畫面上最容易在後續修改中
    走偏的是那條界線：只要有人把直接輸入的密碼塞回送出的表單資料裡，設定檔
    就跟著能寫明文密碼了，而且不會有任何地方報錯。這裡把那條界線釘住。
    """

    def setUp(self):
        self.content = _read()

    def test_has_the_enable_checkbox_and_section(self):
        self.assertIn('id="need_install_password"', self.content)
        self.assertIn('id="install_password_section"', self.content)

    def test_has_both_input_modes(self):
        self.assertIn('id="install_password_mode_inline"', self.content)
        self.assertIn('id="install_password_mode_env"', self.content)
        self.assertIn('id="install_password"', self.content)
        self.assertIn('id="install_password_env"', self.content)

    def test_inline_mode_is_the_default(self):
        tag = re.search(r'<input[^>]*id="install_password_mode_inline"[^>]*>', self.content)
        self.assertIsNotNone(tag, "找不到「直接輸入密碼」那個選項")
        self.assertIn("checked", tag.group(0))

    def test_the_password_input_is_masked(self):
        tag = re.search(r'<input[^>]*id="install_password"[^>]*>', self.content)
        self.assertIsNotNone(tag, "找不到密碼輸入框")
        self.assertIn('type="password"', tag.group(0))

    def test_has_a_reveal_toggle(self):
        """工具無法驗證使用者有沒有打錯，打錯的後果是產出一顆沒人打得開的
        安裝檔。看得到自己打了什麼是唯一的緩解措施。"""
        self.assertIn('id="install_password_reveal"', self.content)

    def test_the_password_is_sent_as_a_separate_argument_not_a_form_field(self):
        """密碼必須以 start_pack() 的第二個參數送出，不能變成表單資料裡的
        一個欄位——那包資料的欄位集合就是設定檔的格式。"""
        call = re.search(r"pywebview\.api\.start_pack\(([^)]*)\)", self.content)
        self.assertIsNotNone(call, "找不到 start_pack 的呼叫")
        self.assertIn(",", call.group(1), "start_pack 應該收兩個參數")

        payload = re.search(
            r"async function submitForm\(\).*?const data = \{(.*?)\n            \};",
            self.content, re.DOTALL,
        )
        self.assertIsNotNone(payload, "找不到送出的表單資料")
        # 比對整個欄位名（行首起算），不能只找子字串——`need_install_password:`
        # 跟 `install_password_env:` 都包含 `install_password`，兩個都是應該
        # 存在的欄位。
        self.assertIsNone(
            re.search(r"\n\s*install_password:", payload.group(1)),
            "密碼不該是表單資料的一個欄位——那等於讓設定檔也能寫明文密碼",
        )
        self.assertIn(
            "need_install_password:", payload.group(1),
            "勾選框的狀態要送出去，後端才能分辨「沒啟用」跟「啟用了但沒填」",
        )

    def test_strings_exist_in_both_languages(self):
        for lang in ("zh-TW", "en"):
            block = re.search(
                r'"' + re.escape(lang) + r'"\s*:\s*\{(.*?)\n            \},?\n',
                self.content, re.DOTALL,
            )
            self.assertIsNotNone(block, f"找不到 {lang} 的 I18N 區塊")
            for key in ("label_install_password", "note_install_password"):
                self.assertIn(key, block.group(1), f"{lang} 缺少 {key}")

    def test_the_note_states_what_this_is_not_for(self):
        """CONTEXT.md 把這個功能定位成存取控制，明確不是防範暴力破解的資安
        機制。畫面上要講出來，不然「有密碼＝很安全」這個直覺沒有任何地方
        會被修正，定位就只活在文件裡。"""
        note = re.search(r'note_install_password:\s*"(.*?)",\n', self.content, re.DOTALL)
        self.assertIsNotNone(note, "找不到中文說明文字")
        self.assertIn("破解", note.group(1))

    def test_the_help_modal_covers_it(self):
        """說明彈窗是拿來回答「這欄到底是幹嘛的」，新功能不進去，那份清單
        自己就變成會過期的東西。這裡只認彈窗內文，不是整份檔案任一處。"""
        start = self.content.find("HELP_MODAL_BODY")
        end = self.content.find("const I18N", start)
        self.assertNotEqual(start, -1, "找不到說明彈窗內文")
        body = self.content[start:end]
        self.assertIn("密碼", body)


if __name__ == "__main__":
    unittest.main()
