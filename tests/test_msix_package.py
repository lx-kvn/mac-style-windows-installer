"""msix_package.py 的測試：組裝套件目錄，並呼叫 makeappx 打包成 .msix。

拆成兩個階段，各自可獨立測試：

- `stage()` 只碰檔案系統，不呼叫任何外部工具——複製 app 內容、放圖示、
  寫清單、產生多語系資源來源檔。
- `pack()` 呼叫 makepri／makeappx。工具的檢索（`find_tool`）與子行程的
  執行（`run`）都是可注入的參數，比照 `file_assoc.py` 的 registry seam
  與 `builder._sign_executable()` 的作法，因此測試不需要真的有 SDK 工具。

真的用 makeappx 驗證產出這件事，由 CI 探針涵蓋（該 workflow 使用本模組與
`msix_manifest.py` 產生套件並實際部署）。
"""
import os
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import msix_manifest
import msix_package


class StageTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app_dir = os.path.join(self.tmp, "app")
        os.makedirs(os.path.join(self.app_dir, "sub"))
        self._write(os.path.join(self.app_dir, "main.exe"), b"MZ main")
        self._write(os.path.join(self.app_dir, "sub", "data.txt"), b"data")
        self.png = os.path.join(self.tmp, "icon.png")
        self._write(self.png, b"\x89PNG main")
        self.staging = os.path.join(self.tmp, "staging")

    def _write(self, path, content):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)

    def stage(self, **overrides):
        kwargs = {
            "app_dir": self.app_dir,
            "staging_dir": self.staging,
            "png_icon": self.png,
            "identity_name": "MyCompany.MyApp",
            "certificate_subject": "CN=My Company",
            "version": "1.2.3",
            "app_name": "My App",
            "publisher": "My Company",
            "main_exe": "main.exe",
        }
        kwargs.update(overrides)
        return msix_package.stage(**kwargs)

    def read(self, *parts):
        with open(os.path.join(self.staging, *parts), "rb") as f:
            return f.read()

    def exists(self, *parts):
        return os.path.exists(os.path.join(self.staging, *parts))


class StageContentTest(StageTestBase):
    def test_app_contents_are_copied_including_subfolders(self):
        self.stage()
        self.assertEqual(self.read("main.exe"), b"MZ main")
        self.assertEqual(self.read("sub", "data.txt"), b"data")

    def test_the_manifest_is_written_and_is_well_formed(self):
        self.stage()
        ET.fromstring(self.read("AppxManifest.xml").decode("utf-8"))

    def test_a_stale_staging_directory_is_cleared_first(self):
        os.makedirs(self.staging)
        self._write(os.path.join(self.staging, "leftover.txt"), b"old")
        self.stage()
        self.assertFalse(self.exists("leftover.txt"),
                         "上一次的殘留檔案會被打包進這一次的套件")

    def test_the_same_png_fills_all_three_icon_positions(self):
        """第五輪決議第一項：預設將既有的 png_icon 同一份用於三個位置。"""
        self.stage()
        for name in (msix_manifest.TILE_LOGO, msix_manifest.TASKBAR_LOGO,
                     msix_manifest.STORE_LOGO):
            self.assertEqual(self.read(name), b"\x89PNG main", name)

    def test_individual_icons_override_the_shared_one(self):
        tile = os.path.join(self.tmp, "tile_custom.png")
        self._write(tile, b"\x89PNG tile")
        self.stage(icons={"tile": tile})
        self.assertEqual(self.read(msix_manifest.TILE_LOGO), b"\x89PNG tile")
        self.assertEqual(self.read(msix_manifest.TASKBAR_LOGO), b"\x89PNG main")


class StageAssociationIconTest(StageTestBase):
    """ADR-0010：一副檔名一群組，圖示掛在群組上。"""

    def setUp(self):
        super().setUp()
        self.doc = os.path.join(self.tmp, "doc.png")
        self._write(self.doc, b"\x89PNG doc")
        self.alpha = os.path.join(self.tmp, "alpha.png")
        self._write(self.alpha, b"\x89PNG alpha")

    def _logos(self):
        root = ET.fromstring(self.read("AppxManifest.xml").decode("utf-8"))
        # 預設命名空間也要列進來，否則 Applications/Application 這幾層
        # 比對不到（清單的根元素宣告了預設命名空間）。
        ns = {
            "": "http://schemas.microsoft.com/appx/manifest/foundation/windows10",
            "uap": "http://schemas.microsoft.com/appx/manifest/uap/windows10",
        }
        groups = root.findall(
            "Applications/Application/Extensions/uap:Extension/uap:FileTypeAssociation", ns)
        return {g.get("Name"): (g.find("uap:Logo", ns).text if g.find("uap:Logo", ns) is not None else None)
                for g in groups}

    def test_the_shared_icon_is_copied_once_and_referenced_by_every_group(self):
        self.stage(file_associations=[".alpha", ".beta"], doc_icon=self.doc)
        self.assertEqual(self.read(msix_manifest.SHARED_ASSOCIATION_LOGO), b"\x89PNG doc")
        self.assertEqual(self._logos(), {
            "alpha": msix_manifest.SHARED_ASSOCIATION_LOGO,
            "beta": msix_manifest.SHARED_ASSOCIATION_LOGO,
        })

    def test_a_per_extension_icon_gets_its_own_file(self):
        self.stage(file_associations=[".alpha", ".beta"], doc_icon=self.doc,
                   doc_icons={".alpha": self.alpha})
        own = msix_manifest.association_logo_name(".alpha")
        self.assertEqual(self.read(own), b"\x89PNG alpha")
        self.assertEqual(self._logos(), {
            "alpha": own, "beta": msix_manifest.SHARED_ASSOCIATION_LOGO,
        })

    def test_every_referenced_icon_actually_exists_in_the_package(self):
        """清單指向一個不存在的檔案，是這一層最容易出的錯。"""
        self.stage(file_associations=[".alpha", ".beta"], doc_icon=self.doc,
                   doc_icons={".alpha": self.alpha})
        for name in self._logos().values():
            self.assertTrue(self.exists(name), f"清單指向 {name}，但套件裡沒有")

    def test_no_icons_means_no_logo_files_and_no_logo_elements(self):
        self.stage(file_associations=[".alpha"])
        self.assertEqual(self._logos(), {"alpha": None})
        self.assertFalse(self.exists(msix_manifest.SHARED_ASSOCIATION_LOGO))


class StageLocalizedTest(StageTestBase):
    def test_resource_sources_are_written_for_each_language(self):
        self.stage(display_names={"zh-TW": "我的應用程式", "en-US": "My App"},
                   default_language="zh-TW")
        for lang in ("zh-TW", "en-US"):
            self.assertTrue(self.exists("strings", lang, "Resources.resw"), lang)

    def test_no_display_names_means_no_strings_folder(self):
        self.stage()
        self.assertFalse(self.exists("strings"))


class FakeRun:
    """記錄呼叫並回傳成功；比照 tests/test_builder.py 的假 subprocess.run。"""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.calls = []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        return type("R", (), {"returncode": self.returncode,
                              "stdout": self.stdout, "stderr": self.stderr})()


def fake_find_tool(name):
    return msix_package.ToolPath(f"C:\\tools\\{name}")


class PackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.staging = os.path.join(self.tmp, "staging")
        os.makedirs(self.staging)
        with open(os.path.join(self.staging, "AppxManifest.xml"), "w") as f:
            f.write("<Package/>")
        self.output = os.path.join(self.tmp, "out.msix")

    def pack(self, run=None, **kwargs):
        run = run or FakeRun()
        msix_package.pack(self.staging, self.output,
                          find_tool=fake_find_tool, run=run, **kwargs)
        return run

    def test_makeappx_is_invoked_with_the_staging_directory_and_output(self):
        run = self.pack()
        command = run.calls[-1]
        self.assertIn("makeappx.exe", command[0])
        self.assertIn("pack", command)
        self.assertIn(self.staging, command)
        self.assertIn(self.output, command)

    def test_makepri_is_not_invoked_without_a_strings_folder(self):
        run = self.pack()
        self.assertEqual(len(run.calls), 1)

    def test_makepri_runs_before_makeappx_when_strings_are_present(self):
        os.makedirs(os.path.join(self.staging, "strings", "en-US"))
        run = self.pack()
        tools = [os.path.basename(call[0]) for call in run.calls]
        self.assertEqual(tools[-1], "makeappx.exe")
        self.assertIn("makepri.exe", tools)
        self.assertLess(tools.index("makepri.exe"), tools.index("makeappx.exe"))

    def test_the_priconfig_is_removed_before_packing(self):
        """priconfig.xml 是 makepri 的中間產物，留在目錄裡會被一起打包進去。"""
        os.makedirs(os.path.join(self.staging, "strings", "en-US"))
        real_run = FakeRun()

        def run(cmd, **kwargs):
            # 模擬 makepri createconfig 真的產生了設定檔
            if "createconfig" in cmd:
                for i, part in enumerate(cmd):
                    if part == "/cf":
                        with open(cmd[i + 1], "w") as config:
                            config.write("<config/>")
            return real_run(cmd, **kwargs)

        msix_package.pack(self.staging, self.output, find_tool=fake_find_tool, run=run)
        self.assertFalse(os.path.exists(os.path.join(self.staging, "priconfig.xml")))

    def test_a_failing_makeappx_raises_with_its_output(self):
        run = FakeRun(returncode=1, stdout="error: 0x80080204 something")
        with self.assertRaises(Exception) as ctx:
            msix_package.pack(self.staging, self.output,
                              find_tool=fake_find_tool, run=run)
        self.assertIn("0x80080204", str(ctx.exception))

    def test_a_failing_makepri_raises_rather_than_packing_anyway(self):
        """資源檔沒編成功就打包，產出的套件顯示名稱會是 ms-resource: 原始
        字串，而那個錯誤要到裝好之後才看得到。"""
        os.makedirs(os.path.join(self.staging, "strings", "en-US"))
        run = FakeRun(returncode=1, stdout="makepri 掛了")
        with self.assertRaises(Exception):
            msix_package.pack(self.staging, self.output,
                              find_tool=fake_find_tool, run=run)
        self.assertNotIn("makeappx.exe", [os.path.basename(c[0]) for c in run.calls])

    def test_the_source_of_each_tool_is_reported(self):
        """docs/adr/0008 決定五末段：建置過程須輸出本次實際採用的來源與版本。"""
        messages = []
        self.pack(log=messages.append)
        self.assertTrue(any("makeappx" in m for m in messages))


if __name__ == "__main__":
    unittest.main()
