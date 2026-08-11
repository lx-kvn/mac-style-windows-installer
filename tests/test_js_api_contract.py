"""A6（架構稽核）：pywebview 的 js_api 介面，實際上就是「這個 class 所有
沒有底線開頭的 public method」——這是 pywebview 本身的實作細節，不是
`ConfigAPI`/`InstallerAPI`/`UninstallerAPI` 這幾個 class 自己明確宣告的
介面。JS 端（`ui/*.html`）呼叫 `pywebview.api.xxx(...)` 用的方法名稱，
跟 Python 端這個 class 定義的方法名稱，完全靠人工保持同步——改名/刪除
Python 這邊的方法完全不會有任何錯誤或警告，只有使用者實際點擊那個功能
按鈕時，才會在瀏覽器主控台看到「undefined is not a function」，這種
錯誤在開發環境用滑鼠點過一輪才測得到，不會被任何自動化測試攔下來。

跟 F16（`test_shared_module_packaging.py`）同樣的模式：靜態解析 JS 端
實際呼叫了哪些 `pywebview.api.*` 方法、Python 端這個 class 定義了哪些
public method，互相比對，抓出「JS 呼叫了 Python 沒有的方法」這種會直接
讓對應功能整個壞掉的 drift。
"""
import ast
import os
import re
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)


def _class_public_methods(py_file_path, class_name):
    """回傳 py_file_path 裡 class_name 這個 class 定義的所有 public method
    名稱（不含底線開頭的內部方法）。pywebview 會把這些全部自動暴露給
    js_api，這就是 JS 端實際能呼叫到的方法全集。"""
    with open(py_file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=py_file_path)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                n.name for n in node.body
                if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")
            }
    raise AssertionError(f"{py_file_path} 裡找不到 class {class_name}")


def _js_api_calls(html_file_path):
    """回傳 html_file_path 裡所有 `pywebview.api.xxx(...)` 呼叫用到的方法
    名稱。"""
    with open(html_file_path, "r", encoding="utf-8") as f:
        content = f.read()
    return set(re.findall(r"pywebview\.api\.([a-zA-Z_][a-zA-Z0-9_]*)", content))


class TestJsApiContractMatchesActualCalls(unittest.TestCase):
    """三組 GUI 進入點各自的 js_api class 跟對應 HTML 檔案的呼叫要互相
    對得上——這裡只斷言「JS 呼叫了但 Python 沒有定義」這個方向會直接讓
    使用者點按鈕時整個功能壞掉的情境，不斷言反方向（Python 定義了但 JS
    沒呼叫，可能是內部方法之間互相呼叫，例如 __init__ 呼叫 load_config()，
    這種公開但不是給 JS 呼叫的方法是合理的既有寫法，不是這裡要抓的問題）。
    """

    def test_config_html_only_calls_methods_that_exist_on_config_api(self):
        methods = _class_public_methods(os.path.join(REPO_ROOT, "gui_config.py"), "ConfigAPI")
        calls = _js_api_calls(os.path.join(REPO_ROOT, "ui", "config.html"))
        missing = calls - methods
        self.assertEqual(
            missing, set(),
            f"ui/config.html 呼叫了 gui_config.ConfigAPI 沒有定義的方法：{missing}",
        )

    def test_index_html_only_calls_methods_that_exist_on_installer_api(self):
        methods = _class_public_methods(os.path.join(REPO_ROOT, "installer_core.py"), "InstallerAPI")
        calls = _js_api_calls(os.path.join(REPO_ROOT, "ui", "index.html"))
        missing = calls - methods
        self.assertEqual(
            missing, set(),
            f"ui/index.html 呼叫了 installer_core.InstallerAPI 沒有定義的方法：{missing}",
        )

    def test_uninstall_html_only_calls_methods_that_exist_on_uninstaller_api(self):
        methods = _class_public_methods(os.path.join(REPO_ROOT, "uninstall.py"), "UninstallerAPI")
        calls = _js_api_calls(os.path.join(REPO_ROOT, "ui", "uninstall.html"))
        missing = calls - methods
        self.assertEqual(
            missing, set(),
            f"ui/uninstall.html 呼叫了 uninstall.UninstallerAPI 沒有定義的方法：{missing}",
        )


class TestJsApiContractHelpersDetectDrift(unittest.TestCase):
    """證明上面兩個輔助函式真的抓得到 drift，不是恆為綠燈的假測試——用
    一組刻意兜出來的暫存檔案，其中 HTML 呼叫了一個 Python class 沒有
    定義的方法名稱。"""

    def test_detects_a_js_call_with_no_matching_python_method(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            py_path = os.path.join(tmp_dir, "fake_api.py")
            with open(py_path, "w", encoding="utf-8") as f:
                f.write(
                    "class FakeAPI:\n"
                    "    def real_method(self):\n"
                    "        pass\n"
                )
            html_path = os.path.join(tmp_dir, "fake.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write("<script>pywebview.api.real_method(); pywebview.api.renamed_method();</script>")

            methods = _class_public_methods(py_path, "FakeAPI")
            calls = _js_api_calls(html_path)
            missing = calls - methods
            self.assertEqual(missing, {"renamed_method"})


if __name__ == "__main__":
    unittest.main()
