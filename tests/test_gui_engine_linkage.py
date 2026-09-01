"""選定引擎之後，表單上的不相容欄位就地反應（第十四輪決議第四、十項）。

三種處置，依「這個設定在這個引擎下還有沒有意義」而不同：

- **會擋建置的**（第二、三類）——加警告樣式與一行原因，控制項**仍可操作**。
  停用它們會讓使用者無法取消自己的設定：他勾了「安裝為 Windows 服務」、切到
  MSIX、欄位停用但值還在，按下編譯仍被擋，卻沒辦法把它關掉，只能切回傳統
  引擎、取消勾選、再切回來。而且 ADR-0009 決定四要求一次列出全部不相容項，
  理由是下游要判斷「切換引擎划不划算」——靜默停用等於讓他看不到自己放棄了
  什麼。
- **不擋但無效的**（第四類）——真的停用。它沒有「要不要取消」這個問題。
- **安裝位置整組**——MSIX 下三個選項全部無效（自訂路徑是第三類、Program
  Files 是第二類、而「路徑」本身在 MSIX 下不存在），整組換成說明文字，比照
  安裝端第二輪決議第四項對拖曳目的地的處置。

## 前端不自己維護欄位清單

分類與提示文字都由後端提供（`install_engine.field_categories()` 與
`category_hint()`）。前端若自行維護一份，與後端分岔時的症狀是某個欄位悄悄
不再被標記——沒有任何東西會叫。
"""
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui_config
import install_engine

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_HTML = os.path.join(REPO_ROOT, "ui", "config.html")


def _html():
    with open(CONFIG_HTML, "r", encoding="utf-8") as f:
        return f.read()


class TheBackendExposesTheClassification(unittest.TestCase):
    def setUp(self):
        self.api = gui_config.ConfigAPI()

    def test_the_msix_engine_reports_every_incompatible_field(self):
        got = self.api.get_engine_field_categories("msix")
        self.assertEqual(set(got), set(install_engine.field_categories()))

    def test_the_traditional_engine_reports_nothing(self):
        """傳統引擎不受任何 MSIX 限制影響。"""
        self.assertEqual(self.api.get_engine_field_categories("traditional"), {})

    def test_each_entry_carries_its_category_and_a_hint(self):
        got = self.api.get_engine_field_categories("msix")
        for field, info in got.items():
            self.assertIn(info["category"],
                          (install_engine.UNSUPPORTED, install_engine.IMPOSSIBLE,
                           install_engine.MOOT), field)
            self.assertTrue(info["hint"].strip(), f"{field} 沒有提示文字")

    def test_the_hint_follows_the_requested_language(self):
        zh = self.api.get_engine_field_categories("msix", "zh-TW")
        en = self.api.get_engine_field_categories("msix", "en")
        field = next(iter(zh))
        self.assertNotEqual(zh[field]["hint"], en[field]["hint"])

    def test_an_unknown_engine_reports_nothing_instead_of_raising(self):
        """引擎值來自前端；為此拋例外會讓整個畫面停住。"""
        self.assertEqual(self.api.get_engine_field_categories("nonsense"), {})


class TheFrontEndMapsEveryFieldToTheForm(unittest.TestCase):
    """漂移防線：後端新增一個不相容欄位，前端沒有對應的 DOM 目標時，那個
    欄位就悄悄不再被標記。"""

    def setUp(self):
        self.html = _html()

    def test_there_is_a_field_to_element_map(self):
        self.assertIn("ENGINE_FIELD_TARGETS", self.html)

    def test_every_backend_field_has_a_target(self):
        block = re.search(r"const ENGINE_FIELD_TARGETS = \{(.*?)\n        \};",
                          self.html, re.S)
        self.assertIsNotNone(block, "找不到 ENGINE_FIELD_TARGETS")
        mapped = set(re.findall(r"^\s*(\w+):", block.group(1), re.M))
        missing = set(install_engine.field_categories()) - mapped
        self.assertEqual(missing, set(),
                         f"這些後端欄位在前端沒有對應的目標：{sorted(missing)}")

    def test_every_target_element_exists_in_the_page(self):
        block = re.search(r"const ENGINE_FIELD_TARGETS = \{(.*?)\n        \};",
                          self.html, re.S)
        for element_id in re.findall(r":\s*'([\w]+)'", block.group(1)):
            self.assertIn(f'id="{element_id}"', self.html,
                          f"對應到的元素 {element_id} 不存在")


class TheThreeTreatmentsAreDistinct(unittest.TestCase):
    def setUp(self):
        self.html = _html()

    def test_there_is_a_function_that_applies_them(self):
        self.assertRegex(self.html, r"function\s+applyEngineLinkage\s*\(")

    def test_it_runs_when_the_engine_changes(self):
        block = re.search(r"function\s+onEngineChange\s*\([^)]*\)\s*\{(.*?)\n        \}",
                          self.html, re.S)
        self.assertIn("applyEngineLinkage", block.group(1))

    def test_blocking_fields_are_warned_not_disabled(self):
        """停用會讓使用者無法取消自己的設定。"""
        self.assertIn("engine-warned", self.html)
        body = re.search(r"function\s+applyEngineLinkage[\s\S]*?\n        \}\n", self.html)
        self.assertIsNotNone(body)
        self.assertRegex(body.group(0), r"MOOT|moot",
                         "沒有依類別分開處置，等於三類一視同仁")

    def test_the_moot_category_is_actually_disabled(self):
        self.assertIn("engine-disabled", self.html)

    def test_disabling_sets_the_disabled_property_not_only_a_style(self):
        """真實抓到的缺陷：原本只加了 `pointer-events: none` 的樣式，那擋得住
        滑鼠、擋不住鍵盤——使用者仍然可以用 Tab 移進那個欄位並打字，只是看
        起來是灰的。實測確認欄位仍可 focus、仍可輸入。

        灰掉的欄位仍然照送它的值（顯示狀態不等於資料狀態，GUI 與 CLI 對同一
        份設定要編出一樣的東西），因此這裡用 readOnly／視覺停用，不用會讓
        欄位從送出資料中消失的原生 disabled 屬性——那會讓兩邊分岔。
        """
        body = re.search(r"function\s+applyEngineLinkage[\s\S]*?\n        \}\n", self.html)
        self.assertIsNotNone(body)
        self.assertRegex(body.group(0), r"setGroupInert|readOnly|tabIndex",
                         "只靠 CSS 停用，鍵盤仍然進得去")

    def test_there_is_a_function_that_makes_a_group_keyboard_inert(self):
        self.assertRegex(self.html, r"function\s+setGroupInert\s*\(")

    def test_it_is_undone_when_switching_back(self):
        body = re.search(r"function\s+clearEngineLinkage[\s\S]*?\n        \}\n", self.html)
        self.assertIsNotNone(body)
        self.assertIn("setGroupInert", body.group(0))

    def test_the_two_styles_are_defined(self):
        for css_class in ("engine-warned", "engine-disabled"):
            self.assertRegex(self.html, rf"\.{css_class}\s*[,{{]",
                             f"{css_class} 沒有樣式定義")


class TheInstallLocationGroupIsReplaced(unittest.TestCase):
    """MSIX 下三個選項全部無效，且「路徑」本身不存在。"""

    def setUp(self):
        self.html = _html()

    def test_there_is_an_explanatory_replacement(self):
        self.assertIn('id="install_location_msix_note"', self.html)

    def test_it_starts_hidden(self):
        m = re.search(r'<div id="install_location_msix_note"[^>]*>', self.html)
        self.assertIn("display: none", m.group(0))

    def test_the_choices_are_hidden_in_msix_mode(self):
        self.assertIn('id="install_location_choices"', self.html)

    def test_both_languages_explain_it(self):
        zh = self.html.index("label_enable_signing:")
        en = self.html.index("label_enable_signing:", zh + 1)
        for block, name in ((self.html[zh:en], "繁中"), (self.html[en:], "英文")):
            self.assertIn("note_install_location_msix:", block, f"{name}缺少說明")

    def test_the_note_does_not_promise_a_path(self):
        m = re.search(r'note_install_location_msix:\s*"([^"]*)"', self.html)
        self.assertIsNotNone(m)
        self.assertNotIn("Program Files", m.group(1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
