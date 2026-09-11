"""`packaging_core.check_build_environment()` 判斷「有沒有 Python」的方式。

真實抓到（2026-09-12，在一台乾淨的 Windows 11 上跑配置精靈）：那台機器**完全
沒有安裝 Python**，環境檢查那一頁卻顯示「✓ Python 直譯器」。

成因：原本只判斷 `shutil.which("python")` 找不找得到那個名字，而 Windows 預設
就把微軟商店的**捷徑替身**（`%LOCALAPPDATA%\\Microsoft\\WindowsApps\\python.exe`）
放在 PATH 上——名字永遠找得到。執行它只會印出一段「請到商店安裝」的訊息。

後果不只是顯示錯：那一頁建議的 `pip install pyinstaller pywebview pywin32`
在同一台機器上一定失敗（pip 要有 Python 才存在），而使用者看到 Python 那格是
綠的，不會往那個方向查。同一個對話框下面就寫著「如果連 Python 都還沒裝，請先
前往官網下載」——那句提示被上面的 ✓ 打臉。

判斷方式因此改成**真的執行它一次**。那段程式碼本來就會起子行程去問
`webview`／`pywin32`，機制是現成的。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import packaging_core


class _Result:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


STORE_STUB_MESSAGE = (
    "Python was not found; run without arguments to install from the "
    "Microsoft Store, or disable this shortcut from Settings > Apps > "
    "Advanced app settings > App execution aliases."
)


class TheStoreStubIsNotAPythonInterpreter(unittest.TestCase):
    """名字找得到不等於執行得起來。"""

    def _check(self, which_result, run_result):
        with mock.patch.object(packaging_core.shutil, "which",
                               side_effect=lambda name: which_result.get(name)):
            with mock.patch.object(packaging_core.subprocess, "run",
                                   return_value=run_result):
                return packaging_core.check_build_environment()

    def test_a_stub_that_prints_the_store_message_is_not_python(self):
        """這正是那台乾淨機器上的情形：路徑在、跑起來卻什麼都不是。"""
        result = self._check(
            {"python": r"C:\Users\T\AppData\Local\Microsoft\WindowsApps\python.exe"},
            _Result(stdout=STORE_STUB_MESSAGE, returncode=9009))
        self.assertFalse(result["python_found"])

    def test_a_real_interpreter_is_python(self):
        """對照組：真的直譯器跑得出那個記號。少了這一條，上面那條在一個
        「永遠回傳沒有」的壞掉實作下也會通過。"""
        result = self._check(
            {"python": r"C:\Python313\python.exe"},
            _Result(stdout="PYTHON_OK\nWEBVIEW_OK\n"))
        self.assertTrue(result["python_found"])
        self.assertTrue(result["webview_found"])

    def test_nothing_on_the_path_is_not_python(self):
        result = self._check({}, _Result(stdout=""))
        self.assertFalse(result["python_found"])
        self.assertEqual(result["python_path"], "")

    def test_an_interpreter_that_cannot_be_run_is_not_python(self):
        """跑起來就爆掉的也不算——那種情形下後面的編譯一定失敗。"""
        with mock.patch.object(packaging_core.shutil, "which",
                               side_effect=lambda name: (
                                   r"C:\bad\python.exe" if name == "python" else None)):
            with mock.patch.object(packaging_core.subprocess, "run",
                                   side_effect=OSError("壞掉了")):
                result = packaging_core.check_build_environment()
        self.assertFalse(result["python_found"])

    def test_the_path_is_still_reported_for_diagnosis(self):
        """判成「不是 Python」時仍然要說出找到的是哪一個檔案——不然使用者
        只看到一個否定，無從得知問題出在那個商店替身上。"""
        stub = r"C:\Users\T\AppData\Local\Microsoft\WindowsApps\python.exe"
        result = self._check({"python": stub},
                             _Result(stdout=STORE_STUB_MESSAGE, returncode=9009))
        self.assertEqual(result["python_path"], stub)


class TheReadyFlagFollowsPython(unittest.TestCase):
    def test_nothing_is_ready_without_a_real_interpreter(self):
        """Python 都不是真的時，不該有任何「可以編譯了」的結論。"""
        with mock.patch.object(packaging_core.shutil, "which",
                               side_effect=lambda name: (
                                   r"C:\stub\python.exe" if name in ("python", "pyinstaller")
                                   else None)):
            with mock.patch.object(packaging_core.subprocess, "run",
                                   return_value=_Result(stdout=STORE_STUB_MESSAGE,
                                                        returncode=9009)):
                result = packaging_core.check_build_environment()
        self.assertFalse(result["ready"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
