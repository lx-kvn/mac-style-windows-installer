"""`tools/provision_build_env.py` 的判準。

那支工具把 Python 與 `requirements.txt` 的每一項離線裝進虛擬機，因為那台
機器沒有 Python 也沒有網路。這裡釘住兩件事：

1. **「裝好了」的判準**——`pip install` 印出 `Successfully installed` 只
   說明檔案落地了，不代表匯入得起來。客體會逐一跑一次匯入並把輸出原樣寫
   回來，判準看的是那些輸出。

2. **清單只有一份**——要裝的東西以 `requirements.txt` 為準。工具裡另外抄
   一份的話，兩份會各自演化，而症狀是某一輪驗證莫名其妙失敗、或者更糟：
   通過了，但通過的原因與本機不同（`tests/test_dependency_manifest.py`
   對 CI 的工作流程做同一件事，理由相同）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import provision_build_env as provision

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _interactive_calls(module_name):
    """那支工具裡有幾個呼叫傳了 `interactive=True`。

    數的是語法樹上的呼叫，不是原始碼裡的字串——模組說明裡本來就寫著
    「不以 `interactive=True` 執行這一段」，用搜字串的話會被自己的註解騙。
    """
    import ast

    with open(os.path.join(REPO, "tools", module_name),
              encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == "interactive":
                count += 1
    return count


def good_report():
    """2026-09-12 在 Windows 11 25H2 上實際量到的成功回報。"""
    report = {
        "wheels": "24",
        "python_setup_exit": "0",
        "python_exists": "True",
        "python_version": "3.13.11",
        "pip_tail": "Successfully installed altgraph-0.17.5 ...",
        "pyinstaller_exe": "True",
        "done": "True",
    }
    for name, _code in provision._IMPORTS:
        report["has_" + name] = "yes"
    report["has_pyinstaller"] = "6.22.2"
    return report


class ReadyMeansEveryPackageImports(unittest.TestCase):
    def test_a_complete_environment_is_ready(self):
        self.assertEqual(provision.is_ready(good_report()), [])

    def test_a_package_that_fails_to_import_is_reported(self):
        """客體把匯入的輸出原樣寫回來，失敗時那一格是 Python 的錯誤訊息。"""
        report = good_report()
        report["has_pywebview"] = (
            "Traceback (most recent call last): ModuleNotFoundError: "
            "No module named 'webview'")
        self.assertIn("pywebview", provision.is_ready(report))

    def test_pip_saying_it_succeeded_is_not_enough(self):
        """只看 pip 那行的話，這份回報會被判成到齊——而 winrt 其實匯不進去。"""
        report = good_report()
        report["has_winrt"] = "ImportError: DLL load failed"
        missing = provision.is_ready(report)
        self.assertIn("winrt", missing)
        self.assertIn("Successfully installed", report["pip_tail"])

    def test_a_missing_interpreter_is_reported(self):
        report = good_report()
        report["python_exists"] = "False"
        self.assertIn("Python 本身", provision.is_ready(report))

    def test_the_pyinstaller_command_is_checked_separately(self):
        """編譯時呼叫的是那支指令，不是模組——模組在、指令不在也不算到齊。"""
        report = good_report()
        report["pyinstaller_exe"] = "False"
        missing = provision.is_ready(report)
        self.assertIn("pyinstaller 指令", missing)
        self.assertEqual(report["has_pyinstaller"], "6.22.2")

    def test_an_empty_report_is_not_ready(self):
        """誘餌：什麼都沒有時一定要判成缺——全部回傳空清單的實作會在這裡紅。"""
        self.assertNotEqual(provision.is_ready({}), [])


class TheReportIsReadBackFaithfully(unittest.TestCase):
    def test_keys_and_values_survive_the_round_trip(self):
        text = "\ufeff# 客體建置環境\npython_exists=True\npip_tail=a=b=c\n"
        report = provision.parse_report(text)
        self.assertEqual(report["python_exists"], "True")
        # 值裡面本來就會有等號（pip 的輸出），只切第一個。
        self.assertEqual(report["pip_tail"], "a=b=c")
        self.assertNotIn("# 客體建置環境", report)


class TheGuestScriptFollowsRequirements(unittest.TestCase):
    def test_it_installs_from_requirements_not_its_own_list(self):
        self.assertIn("-r \"$dir\\requirements.txt\"", provision.guest_script())

    def test_every_checked_package_is_in_requirements(self):
        with open(os.path.join(REPO, "requirements.txt"),
                  encoding="utf-8") as handle:
            text = handle.read().lower()
        for name, _code in provision._IMPORTS:
            # winrt 在清單裡是五個 winrt-* 套件，名稱前綴相同。
            self.assertIn(name, text, name + " 不在 requirements.txt 裡")

    def test_a_package_that_is_not_required_would_be_caught(self):
        """誘餌：上面那條檢查抓得到不存在的名字嗎？抓不到的話它永遠會過。"""
        with open(os.path.join(REPO, "requirements.txt"),
                  encoding="utf-8") as handle:
            text = handle.read().lower()
        self.assertNotIn("pyqt5", text)

    def test_it_does_not_run_on_the_interactive_desktop(self):
        """裝 Python 要寫 C:\\Python313，在使用者桌面那個工作階段會彈出權限
        詢問窗而整段卡死（實測等了十四分鐘）。這條釘住那個修正。"""
        self.assertEqual(_interactive_calls("provision_build_env.py"), 0)

    def test_that_check_can_fail(self):
        """誘餌：同一條檢查套在真的會用到桌面的那支工具上應該數得出來。數不
        出來的話，上面那個 0 有可能只是這段程式碼永遠回傳的東西。"""
        self.assertGreater(_interactive_calls("verify_config_wizard.py"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
