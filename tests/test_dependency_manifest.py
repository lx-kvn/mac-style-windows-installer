"""相依套件宣告的單一真實來源：`requirements.txt`。

真實踩到的缺陷（2026-09-03）：MSIX 引擎的安裝檔需要 `winrt-*` 綁定套件，
而這五個套件只出現在 `.github/workflows/test-packaging-options.yml` 的「安裝
相依」步驟裡。版本庫沒有任何相依宣告檔，本機開發者因此沒有任何管道會知道
要裝它們——打包出來的 Setup.exe 少了那些模組，一執行就中止，而錯誤要等到
終端使用者手上才出現。

這裡鎖住的性質是「工作流程不自己列套件名稱」。工作流程各自列一份的代價
不是多打幾個字，而是那幾份清單會各自演化：其中一份加了套件、其他幾份沒
跟上時，症狀是某一個工作流程莫名其妙失敗，或者更糟——通過了，但通過的
原因與本機不同。
"""
import io
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIREMENTS = os.path.join(REPO_ROOT, "requirements.txt")
WORKFLOW_DIR = os.path.join(REPO_ROOT, ".github", "workflows")

# 安裝端呼叫 Windows 的套件部署介面所需的綁定套件（見 msix_deploy.py）。
# 這份清單是外部事實（pypi 上的套件名稱），不是本專案自己的常數。
MSIX_BINDING_PACKAGES = [
    "winrt-runtime",
    "winrt-Windows.Management.Deployment",
    "winrt-Windows.Foundation",
    "winrt-Windows.Foundation.Collections",
    "winrt-Windows.ApplicationModel",
]


def _requirement_lines():
    with io.open(REQUIREMENTS, encoding="utf-8") as f:
        return [line.strip() for line in f
                if line.strip() and not line.strip().startswith("#")]


def _workflow_files():
    return [os.path.join(WORKFLOW_DIR, name)
            for name in sorted(os.listdir(WORKFLOW_DIR))
            if name.endswith((".yml", ".yaml"))]


class TheManifestExists(unittest.TestCase):
    def test_requirements_txt_is_in_the_repository_root(self):
        self.assertTrue(os.path.exists(REQUIREMENTS),
                        "找不到 requirements.txt；本機開發者沒有任何管道知道要裝哪些套件")

    def test_it_declares_the_build_tooling(self):
        """安裝檔是由 pyinstaller 子行程編出來的，而 installer_core.py 匯入
        webview——這兩者缺一，打包流程在分析階段就會失敗。"""
        joined = " ".join(_requirement_lines()).lower()
        self.assertIn("pyinstaller", joined)
        self.assertIn("pywebview", joined)


class TheMsixBindingsAreDeclaredAndPinned(unittest.TestCase):
    """`winrt-*` 的版本要固定。PyInstaller 是靜態分析，版本組合換掉可能導致
    打包出來的 exe 少帶模組，而那個後果同樣要到終端使用者手上才顯現。"""

    def test_every_binding_package_is_listed(self):
        listed = {re.split(r"[=<>!~\[]", line)[0].strip().lower()
                  for line in _requirement_lines()}
        for package in MSIX_BINDING_PACKAGES:
            self.assertIn(package.lower(), listed,
                          f"requirements.txt 沒有列出 {package}")

    def test_every_binding_package_is_pinned_to_an_exact_version(self):
        for line in _requirement_lines():
            name = re.split(r"[=<>!~\[]", line)[0].strip().lower()
            if name in {p.lower() for p in MSIX_BINDING_PACKAGES}:
                self.assertRegex(line, r"==\s*\d",
                                 f"{line} 沒有鎖定確切版本")


class WorkflowsInstallFromTheManifest(unittest.TestCase):
    """工作流程不自己列套件名稱，一律經由 requirements.txt。"""

    def test_no_workflow_names_packages_on_a_pip_install_line(self):
        offenders = []
        for path in _workflow_files():
            with io.open(path, encoding="utf-8") as f:
                for number, line in enumerate(f, start=1):
                    stripped = line.strip()
                    if not re.match(r"^(python -m )?pip install\b", stripped):
                        continue
                    # 允許兩種形式：升級 pip 本身，以及從清單檔安裝。
                    if "--upgrade pip" in stripped or "-r requirements.txt" in stripped:
                        continue
                    offenders.append(f"{os.path.basename(path)}:{number}: {stripped}")
        self.assertEqual(offenders, [], "工作流程直接列了套件名稱：\n" + "\n".join(offenders))

    def test_at_least_one_workflow_installs_from_the_manifest(self):
        """上一條在所有 pip install 都被刪掉時也會通過，這一條補住那個缺口。"""
        found = False
        for path in _workflow_files():
            with io.open(path, encoding="utf-8") as f:
                if "-r requirements.txt" in f.read():
                    found = True
        self.assertTrue(found, "沒有任何工作流程從 requirements.txt 安裝相依")


if __name__ == "__main__":
    unittest.main(verbosity=2)
