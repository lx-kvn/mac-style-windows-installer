"""真實抓到的 bug（在另一個使用這個工具的專案裡發現）：`mswi-cli`/`mswi-gui`
編出來的 `Setup_XXX.exe` 一執行就跳出
`ModuleNotFoundError: No module named 'system_entries'`。

根本原因：`installer_core.py`/`uninstall.py` 實際 import 的專案內部深模組
（`file_assoc.py`/`install_scope.py`/`self_delete.py`/`system_entries.py`
這幾個），跟「這個打包工具的 frozen exe 要怎麼把這些檔案一起帶著走」的兩處
清單——`packaging_core.ensure_workspace_files()`（執行期把內嵌資源解壓回
工作目錄）跟 `build_config_tool.py`（編譯 mswi-gui.exe/mswi-cli.exe 時的
`--add-data`）——是三份要手動保持同步的清單，只要新增一個深模組卻忘記
更新其中一份，frozen exe 執行到那一步就會找不到模組，而 .py 直接執行
（開發環境）完全不會踩到這個問題，很容易在合入前沒發現。

這裡不是把「目前應該要有哪些模組」寫死成一份新的期望清單（那只是把同一種
會過期的手動清單又多開一份），而是直接用 ast 解析 installer_core.py／
uninstall.py 檔案最上層實際的 import 陳述式，反推出「這兩支 entry point
真正依賴哪些專案內部模組」，拿這個結果去對照 packaging_core.py／
build_config_tool.py 目前登記的清單有沒有漏掉——這樣以後不管是誰、
什麼時候，只要在 installer_core.py/uninstall.py 加了新的 `import xxx`
卻忘記讓兩邊清單同步更新，這個測試就會紅燈，不需要每次有人手動記得。
"""
import ast
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import packaging_core
import build_config_tool


def _local_module_imports(py_file_path):
    """解析 py_file_path 檔案最上層的 import 陳述式，回傳其中『對應到專案
    根目錄某個 .py 檔案』的模組名稱（不含 .py），排除標準庫/第三方套件
    （webview/ctypes/subprocess 之類的，repo 根目錄下沒有同名 .py 檔案）。
    """
    with open(py_file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=py_file_path)

    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                names.add(node.module.split(".")[0])

    return {name for name in names if os.path.exists(os.path.join(REPO_ROOT, f"{name}.py"))}


class TestPackagingCoreCoversEveryLocalImport(unittest.TestCase):
    """packaging_core.ensure_workspace_files() 實際會複製到工作目錄的檔案
    清單，要涵蓋 installer_core.py/uninstall.py 真正 import 的每一個
    專案內部模組，不然 frozen exe 執行到編譯這兩支 entry point 那一步時
    會找不到模組。"""

    def test_installer_core_local_imports_are_all_packaged(self):
        needed = _local_module_imports(os.path.join(REPO_ROOT, "installer_core.py"))
        packaged = {name[:-3] for name in packaging_core.SHARED_DEEP_MODULES}
        missing = needed - packaged
        self.assertEqual(
            missing, set(),
            f"installer_core.py import 了這些模組，但 packaging_core.SHARED_DEEP_MODULES 沒有列到：{missing}",
        )

    def test_uninstall_local_imports_are_all_packaged(self):
        needed = _local_module_imports(os.path.join(REPO_ROOT, "uninstall.py"))
        packaged = {name[:-3] for name in packaging_core.SHARED_DEEP_MODULES}
        missing = needed - packaged
        self.assertEqual(
            missing, set(),
            f"uninstall.py import 了這些模組，但 packaging_core.SHARED_DEEP_MODULES 沒有列到：{missing}",
        )


class TestBuildConfigToolCoversEveryLocalImport(unittest.TestCase):
    """build_config_tool.py 編譯 mswi-gui.exe/mswi-cli.exe 時的 --add-data
    清單，同樣要涵蓋 installer_core.py/uninstall.py 真正 import 的每一個
    專案內部模組——這兩支 exe 執行時要靠 ensure_workspace_files() 把這些
    模組解壓回工作目錄，前提是這些模組本身有先被內嵌進 exe 裡。"""

    def test_shared_add_data_covers_installer_core_imports(self):
        needed = _local_module_imports(os.path.join(REPO_ROOT, "installer_core.py"))
        packaged = {name[:-3] for name in build_config_tool._SHARED_ADD_DATA}
        missing = needed - packaged
        self.assertEqual(missing, set(), f"缺少於 build_config_tool._SHARED_ADD_DATA：{missing}")

    def test_shared_add_data_covers_uninstall_imports(self):
        needed = _local_module_imports(os.path.join(REPO_ROOT, "uninstall.py"))
        packaged = {name[:-3] for name in build_config_tool._SHARED_ADD_DATA}
        missing = needed - packaged
        self.assertEqual(missing, set(), f"缺少於 build_config_tool._SHARED_ADD_DATA：{missing}")

    def test_required_files_covers_installer_core_imports(self):
        needed = _local_module_imports(os.path.join(REPO_ROOT, "installer_core.py"))
        packaged = {os.path.basename(label)[:-3] for label, _ in build_config_tool._REQUIRED_FILES if label.endswith(".py")}
        missing = needed - packaged
        self.assertEqual(missing, set(), f"缺少於 build_config_tool._REQUIRED_FILES：{missing}")


if __name__ == "__main__":
    unittest.main()
