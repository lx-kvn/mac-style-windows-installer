"""`ui/` 底下的資源有沒有被漏掉在打包清單裡。

跟 `tests/test_shared_module_packaging.py` 同一個動機，對象換成前端資源。
那份測試用 `ast` 解析 `installer_core.py`／`uninstall.py` 實際的 import、
反推「這兩支 entry point 真正依賴哪些專案內部模組」，拿結果去比對
`packaging_core.py`／`build_config_tool.py` 登記的清單有沒有漏掉——這樣
不管是誰、什麼時候新增了一個模組卻忘記更新清單，都會自動紅燈。

**那個機制完全不涵蓋 `ui/` 底下的資源。** 三份 HTML 引用到的圖示、SVG、
以及將來可能抽出來的共用 `.js`，漏登記不會有任何測試轉紅，而 frozen exe
（mswi-gui.exe／mswi-cli.exe）執行時才會發現檔案不在——`.py` 直接執行完全
不會踩到，因為工作目錄本來就是原始碼目錄，什麼都找得到。這正是當初導致
`ModuleNotFoundError` 的同一種情境。

這份測試補上那個缺口：解析三份 HTML 實際引用到的本地相對路徑，斷言它們
都確實存在、而且該登記的地方都登記了。
"""
import os
import re
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import build_config_tool
import packaging_core

UI_DIR = os.path.join(REPO_ROOT, "ui")
HTML_FILES = ("config.html", "index.html", "uninstall.html")

# 引用本地檔案的屬性。`href` 一併看，之後如果改用外部樣式表也會被涵蓋。
_REFERENCE_ATTRS = ("src", "href")


def _referenced_assets(html_name):
    """回傳這份 HTML 引用到的本地相對路徑（排除 http(s):／data:／# 這類
    不是本地檔案的目標）。"""
    with open(os.path.join(UI_DIR, html_name), "r", encoding="utf-8") as f:
        content = f.read()

    found = set()
    for attr in _REFERENCE_ATTRS:
        for value in re.findall(attr + r'="([^"]+)"', content):
            value = value.strip()
            if not value:
                continue
            if value.startswith(("http:", "https:", "data:", "#", "mailto:", "//")):
                continue
            found.add(value)
    return found


class TestReferencedAssetsExist(unittest.TestCase):
    """HTML 引用到的檔案要真的在 ui/ 底下。漏掉的話安裝畫面會少一張圖，
    而且不會有任何錯誤訊息（規格文件記錄過的真實 bug：安裝視窗右側資料夾
    圖示消失）。"""

    def test_every_referenced_asset_exists_or_is_generated_at_build_time(self):
        """例外只有一種：打包當下才產生的資源（`app_icon.png` 是
        `builder.build_all()` 把開發者選的 PNG 複製過去的），它預期在版本庫
        裡缺席，所以要從這個檢查裡排除——但必須是**明確宣告**的排除，
        不是「找不到就算了」。"""
        missing = []
        for html_name in HTML_FILES:
            for asset in _referenced_assets(html_name):
                if asset in packaging_core.BUILD_GENERATED_UI_ASSETS:
                    continue
                if not os.path.exists(os.path.join(UI_DIR, asset)):
                    missing.append(f"{html_name} -> {asset}")
        self.assertEqual(missing, [], f"HTML 引用到不存在的檔案：{missing}")

    def test_the_build_generated_list_is_actually_referenced(self):
        """反向檢查：宣告成「建置期產生」的資源，要真的有 HTML 在引用它。
        沒有的話代表這份清單過期了，留著一個誰都不用的例外。"""
        referenced = set()
        for html_name in HTML_FILES:
            referenced |= _referenced_assets(html_name)
        unused = [
            name for name in packaging_core.BUILD_GENERATED_UI_ASSETS
            if name not in referenced
        ]
        self.assertEqual(unused, [], f"沒有任何 HTML 引用這些宣告過的建置期資源：{unused}")


class TestUiAssetsAreRegisteredForPackaging(unittest.TestCase):
    """`ui/` 底下的每一個檔案，都必須在兩份清單裡各自有明確的歸屬。"""

    def _ui_files(self):
        return {
            name for name in os.listdir(UI_DIR)
            if os.path.isfile(os.path.join(UI_DIR, name))
        }

    def test_every_implementation_file_is_registered_in_the_prerequisite_check(self):
        """`build_config_tool._REQUIRED_FILES` 是編譯兩顆工具 exe 之前的
        存在性檢查。介面實作（HTML 與 JS）缺一份就編不出可用的工具，必須在
        檢查清單裡；漏登記的話會編出一顆跑起來才發現東西不對的 exe——共用的
        拖曳實作缺了尤其陰險，畫面照樣畫得出來，只是圖示完全拖不動。

        判斷依據是副檔名（實作 vs 靜態資源），不是一份寫死的檔名清單——
        那正是 ensure_workspace_files() 原本踩過的坑。"""
        registered = {label for label, _path in build_config_tool._REQUIRED_FILES}
        missing = [
            f"ui/{name}" for name in self._ui_files()
            if name.endswith((".html", ".js")) and f"ui/{name}" not in registered
        ]
        self.assertEqual(
            missing, [],
            f"這些介面實作沒有登記在 build_config_tool._REQUIRED_FILES：{missing}",
        )

    def test_every_ui_file_has_a_declared_overwrite_policy(self):
        """`packaging_core.ensure_workspace_files()` 對 `ui/` 底下的檔案
        只有兩種處置：使用者可自訂的資源（缺少時才補），或介面實作（一律
        覆蓋）。規則本身是白名單反轉，所以不會有「漏掉就不被複製」的情況，
        但每個檔案屬於哪一邊應該是想清楚的、不是預設落到某一邊。

        這裡把可自訂清單釘住：清單裡列的檔案要真的存在（避免清單本身過期，
        列著一個早就刪掉的檔名，讓人以為那是可自訂的）。
        """
        ui_files = self._ui_files()
        stale = [name for name in packaging_core.USER_CUSTOMIZABLE_UI_ASSETS if name not in ui_files]
        self.assertEqual(
            stale, [],
            f"可自訂資源清單裡列了 ui/ 底下不存在的檔案：{stale}",
        )

    def test_html_files_are_not_in_the_customizable_list(self):
        """介面實作絕對不能被當成使用者可自訂的資源——那會讓工作目錄裡的
        舊版永遠不被更新，是這個函式說明文字裡以「【重要】」標記過的缺陷。"""
        wrong = [
            name for name in packaging_core.USER_CUSTOMIZABLE_UI_ASSETS
            if name.endswith((".html", ".js"))
        ]
        self.assertEqual(
            wrong, [],
            f"介面實作被列進可自訂資源清單，工作目錄裡的舊版會靜默生效：{wrong}",
        )


if __name__ == "__main__":
    unittest.main()
