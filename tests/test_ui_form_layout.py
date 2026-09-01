"""配置精靈的版面結構：依賴順序排列，選用功能收進四個可收合的大區。

第十四輪定案決議第一至三、五、六項。

## 為什麼要排

原本的順序讓「安裝引擎」排在第 16 個區塊，而它決定了前面好幾組欄位有沒有
意義——安裝資料夾名稱、相依元件、個別檔案改裝在 MSIX 下全部失效或被擋。
使用者要填完八組欄位，才會遇到那個讓其中三組作廢的選項。

「數位簽章」原本排在最後一組，但在 MSIX 模式下它決定產出的是一顆安裝檔還是
一份 `.msix`——一個決定產出物形態的選項，放在整份表單的最底下。

## 攤開與收合的判準

不填就編不出來、或會改變「產出物是什麼」的攤開；不填只是少一個功能的收合。
編譯工作目錄殿後：它是這台機器的偏好，不描述要做出什麼產品。

這裡驗的是靜態的標記與程式碼結構。實際畫面另以截圖確認（CLAUDE.md 的介面
變更規定：不要沒看過畫面就宣告做完）。
"""
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_HTML = os.path.join(REPO_ROOT, "ui", "config.html")

SECTIONS = ("install_flow", "system_integration", "install_behavior", "publishing")

# 每一區裡應該有哪些區塊，以各自的 data-i18n 標籤代表。
SECTION_CONTENTS = {
    "install_flow": ["label_eula", "label_install_password"],
    "system_integration": ["label_file_assoc", "label_add_to_path",
                           "label_windows_service", "label_scheduled_task"],
    "install_behavior": ["label_dependencies", "label_custom_dependencies",
                         "label_pre_install_script", "label_post_install_script",
                         "label_local_appdata_files", "label_restore_point"],
    "publishing": ["label_enable_signing"],
}

# 攤開的區塊，依應有的先後順序。
EXPANDED_ORDER = [
    "label_install_engine",
    "label_app_name",
    "label_folder_name",
    "label_version",
    "label_publisher",
    "label_exe_name",
    "label_app_dir",
    "label_main_exe",
    "label_png_icon",
    "label_ico_icon",
    "label_install_location",
    "label_workspace_dir",
]


def _read():
    with open(CONFIG_HTML, "r", encoding="utf-8") as f:
        return f.read()


def _at(html, marker):
    """該標籤在文件中第一次出現的位置。"""
    return html.index(f'data-i18n="{marker}"')


class TheExpandedGroupsAreInDependencyOrder(unittest.TestCase):
    def setUp(self):
        self.html = _read()

    def test_every_expanded_group_is_present(self):
        for marker in EXPANDED_ORDER:
            self.assertIn(f'data-i18n="{marker}"', self.html, f"找不到 {marker}")

    def test_they_appear_in_the_declared_order(self):
        positions = [(m, _at(self.html, m)) for m in EXPANDED_ORDER]
        ordered = [m for m, _ in sorted(positions, key=lambda p: p[1])]
        self.assertEqual(ordered, EXPANDED_ORDER)

    def test_the_engine_comes_before_everything_it_decides(self):
        """引擎決定安裝資料夾名稱、安裝位置、相依元件等有沒有意義。排在
        它們後面，使用者要填完才會遇到那個讓它們作廢的選項。"""
        engine = _at(self.html, "label_install_engine")
        for decided in ("label_folder_name", "label_install_location",
                        "label_dependencies", "label_local_appdata_files",
                        "label_windows_service", "label_pre_install_script"):
            self.assertLess(engine, _at(self.html, decided),
                            f"引擎排在 {decided} 後面")

    def test_the_app_dir_comes_before_the_fields_populated_from_it(self):
        """主執行檔、PATH 執行檔、服務與排程的執行檔都是從內容資料夾裡
        列出來的下拉選單。"""
        app_dir = _at(self.html, "label_app_dir")
        for dependent in ("label_main_exe", "label_path_target_exe",
                          "label_service_exe", "label_task_exe"):
            if f'data-i18n="{dependent}"' in self.html:
                self.assertLess(app_dir, _at(self.html, dependent),
                                f"內容資料夾排在 {dependent} 後面")

    def test_the_workspace_directory_is_last_among_the_expanded_groups(self):
        """它是這台機器的偏好，不描述要做出什麼產品，而且設一次就不會再改。"""
        workspace = _at(self.html, "label_workspace_dir")
        for other in EXPANDED_ORDER:
            if other != "label_workspace_dir":
                self.assertLess(_at(self.html, other), workspace)


class TheFourSectionsExist(unittest.TestCase):
    def setUp(self):
        self.html = _read()

    def test_each_section_has_a_container(self):
        for name in SECTIONS:
            self.assertIn(f'id="section_{name}"', self.html, f"缺少 {name} 區")

    def test_each_section_starts_collapsed(self):
        for name in SECTIONS:
            m = re.search(rf'<div id="section_{name}"[^>]*>', self.html)
            self.assertIsNotNone(m, name)
            self.assertIn("display: none", m.group(0),
                          f"{name} 區預設就是展開的，等於沒有收合")

    def test_each_section_has_a_clickable_header(self):
        for name in SECTIONS:
            self.assertIn(f"toggleSection('{name}')", self.html,
                          f"{name} 區沒有可點的標題")

    def test_a_toggle_function_exists(self):
        self.assertRegex(self.html, r"function\s+toggleSection\s*\(")


class EachOptionalGroupIsInItsSection(unittest.TestCase):
    def setUp(self):
        self.html = _read()

    def _bounds(self, name):
        start = self.html.index(f'id="section_{name}"')
        others = [self.html.index(f'id="section_{o}"') for o in SECTIONS
                  if self.html.index(f'id="section_{o}"') > start]
        return start, min(others) if others else len(self.html)

    def test_every_group_sits_inside_the_right_section(self):
        for name, markers in SECTION_CONTENTS.items():
            start, end = self._bounds(name)
            for marker in markers:
                pos = _at(self.html, marker)
                self.assertTrue(start < pos < end,
                                f"{marker} 不在 section_{name} 裡")


class SectionsOpenThemselvesWhenTheyShould(unittest.TestCase):
    """收合區裡的設定會真的進到安裝檔。使用者看不到它已經被設定了，是這個
    版面最容易出的問題。"""

    def setUp(self):
        self.html = _read()

    def test_there_is_a_function_that_decides_this(self):
        self.assertRegex(self.html, r"function\s+applySectionAutoExpand\s*\(")

    def test_it_is_run_when_the_engine_changes(self):
        """MSIX 下的「發布」區要展開：那個模式下簽章決定產出的是安裝檔還是
        .msix，不再只是選填。"""
        block = re.search(r"function\s+onEngineChange\s*\([^)]*\)\s*\{(.*?)\n        \}",
                          self.html, re.S)
        self.assertIsNotNone(block, "找不到 onEngineChange")
        self.assertIn("applySectionAutoExpand", block.group(1))

    def test_it_is_run_when_validation_blocks_the_build(self):
        """錯誤是用彈窗顯示的，使用者關掉彈窗還要自己找是哪一格——那一格
        如果還在收起來的區裡，他根本看不到。"""
        self.assertRegex(self.html, r"showErrorModal[\s\S]{0,400}?applySectionAutoExpand"
                                    r"|applySectionAutoExpand[\s\S]{0,400}?showErrorModal")


class EveryLabelHasFallbackText(unittest.TestCase):
    """真實抓到的缺陷：四個區的標題原本寫成 `<span data-i18n="..."></span>`，
    等 i18n 執行時才填入文字。但這個檔案裡每一個其他標籤都有內建的中文——
    i18n 初始化失敗或還沒跑的話，畫面上就是四條沒有標題的空白橫條。

    `data-i18n-html`（說明彈窗的長篇內容）不在此列，那個本來就是整段替換。
    """

    def test_no_translatable_element_is_left_empty(self):
        html = _read()
        empty = re.findall(r'<(\w+)[^>]*\sdata-i18n="([a-z_]+)"[^>]*>\s*</\1>', html)
        self.assertEqual(empty, [],
                         f"這些元素沒有內建文字，i18n 沒跑就會是空白：{empty}")


class BothLanguagesHaveTheSectionTitles(unittest.TestCase):
    def setUp(self):
        self.html = _read()

    def _tables(self):
        zh = self.html.index("label_enable_signing:")
        en = self.html.index("label_enable_signing:", zh + 1)
        return self.html[zh:en], self.html[en:]

    def test_every_section_title_is_translated(self):
        zh_block, en_block = self._tables()
        for name in SECTIONS:
            key = f"label_section_{name}"
            self.assertIn(f"{key}:", zh_block, f"繁中缺少 {key}")
            self.assertIn(f"{key}:", en_block, f"英文缺少 {key}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
