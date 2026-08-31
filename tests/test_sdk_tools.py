"""sdk_tools.py 的測試：SDK 工具（makeappx／signtool）的定位與取得。

對應 docs/adr/0008-sdk-build-tools-are-fetched-on-explicit-request-only.md
的五項決定，以及 docs/proposals/MSIX輸出規劃.md 第二輪決議第十三項
（既有的 signtool 檢索邏輯改用同一套）。

這份測試不進行任何真實網路存取，也不依賴這台機器上是否安裝 Windows SDK：
下載動作與環境變數都是注入進去的參數（見 sdk_tools.find_tool() 的
environ／settings 參數與 fetch_tools() 的 download 參數），比照
file_assoc.py 的 registry seam。
"""
import hashlib
import io
import os
import sys
import tempfile
import shutil
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sdk_tools


def make_tool(dir_path, name):
    """在指定目錄底下造一個假的工具執行檔，回傳它的路徑。"""
    os.makedirs(dir_path, exist_ok=True)
    path = os.path.join(dir_path, name)
    with open(path, "wb") as f:
        f.write(b"MZ FAKE")
    return path


def make_bin_layout(root, toolset_version, arch, tool_names):
    """造出 NuGet 套件／Windows SDK 共通的 bin/<版本>/<架構>/ 目錄結構。"""
    target = os.path.join(root, "bin", toolset_version, arch)
    for name in tool_names:
        make_tool(target, name)
    return target


def make_fake_nupkg_bytes(toolset_version="10.0.26100.0"):
    """造一份形狀跟真實 Microsoft.Windows.SDK.BuildTools 一致的 zip。

    真實套件的內部目錄版本號（10.0.26100.0）與套件本身的版本號
    （10.0.26100.4948）並不相同，這份假資料刻意重現這個差異，避免實作
    寫成「拿套件版本號去拼內部路徑」。
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for arch in ("x64", "x86", "arm64"):
            for name in ("makeappx.exe", "signtool.exe"):
                z.writestr(f"bin/{toolset_version}/{arch}/{name}", b"MZ FAKE")
        z.writestr("Microsoft.Windows.SDK.BuildTools.nuspec", b"<package/>")
    return buf.getvalue()


def fake_downloader(payload, recorder=None, corrupt=False):
    """回傳一個假的下載函式，介面與 builder._download_file() 相同。

    payload 一律當成「通過雜湊驗證的內容」寫出去——測試造的假 zip 當然
    算不出寫死在 sdk_tools 裡的那個真實雜湊，若在這裡真的比對，驗到的
    會是這份替身自己編的資料，不是待測程式的行為。真正要驗的「有沒有把
    寫死的雜湊傳下去」由 recorder 記錄的參數負責。

    corrupt=True 時重現真實 _download_file() 在雜湊不符時的行為：刪掉已
    寫入的檔案再往外拋。
    """
    def download(url, dest_path, timeout=60, expected_sha256=None):
        if recorder is not None:
            recorder.append({"url": url, "dest_path": dest_path, "expected_sha256": expected_sha256})
        with open(dest_path, "wb") as f:
            f.write(payload)
        if corrupt:
            os.remove(dest_path)
            raise Exception(f"完整性驗證失敗（sha256 不符）：預期 {expected_sha256}。")
    return download


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def sub(self, *parts):
        path = os.path.join(self.tmp, *parts)
        os.makedirs(path, exist_ok=True)
        return path


class PinnedPackageTest(unittest.TestCase):
    """ADR-0008 決定二：版本與 SHA-256 皆寫死。

    這裡驗的是「這兩個常數與下載網址彼此一致」這個外部契約，不是複述
    常數自己的值——後者驗了等於沒驗。
    """

    def test_sha256_is_a_real_digest(self):
        self.assertRegex(sdk_tools.PACKAGE_SHA256, r"^[0-9a-f]{64}$")

    def test_download_url_points_at_the_pinned_version_on_nuget_org(self):
        url = sdk_tools.PACKAGE_URL
        self.assertTrue(url.startswith("https://api.nuget.org/"), url)
        self.assertIn(sdk_tools.PACKAGE_VERSION, url)
        self.assertIn(sdk_tools.PACKAGE_ID.lower(), url)

    def test_required_tools_cover_both_msix_and_signing(self):
        self.assertIn("makeappx.exe", sdk_tools.REQUIRED_TOOLS)
        self.assertIn("signtool.exe", sdk_tools.REQUIRED_TOOLS)


class CacheDirTest(TempDirTestCase):
    """ADR-0008 決定三：快取為獨立且持久的使用者層級位置，路徑含版本號。"""

    def test_default_cache_dir_is_user_level_and_contains_the_version(self):
        local = self.sub("LocalAppData")
        path = sdk_tools.cache_dir(settings={}, environ={"LOCALAPPDATA": local})
        self.assertTrue(path.startswith(local), path)
        self.assertIn(sdk_tools.PACKAGE_VERSION, path)

    def test_default_cache_dir_is_not_inside_the_build_workspace(self):
        """決定三：不放編譯工作目錄——該處每次建置開頭即清空。"""
        import packaging_core
        local = self.sub("LocalAppData")
        environ = {"LOCALAPPDATA": local}
        cache = sdk_tools.cache_dir(settings={}, environ=environ)
        workspace = packaging_core.default_workspace_dir()
        self.assertFalse(
            os.path.normcase(cache).startswith(os.path.normcase(workspace) + os.sep),
            f"快取 {cache} 落在編譯工作目錄 {workspace} 底下",
        )

    def test_setting_overrides_the_base_but_the_version_is_still_appended(self):
        override = self.sub("ci-cache")
        path = sdk_tools.cache_dir(
            settings={"sdk_tools_cache_dir": override},
            environ={"LOCALAPPDATA": self.sub("LocalAppData")},
        )
        self.assertTrue(path.startswith(override), path)
        self.assertIn(sdk_tools.PACKAGE_VERSION, path)


class FindToolPriorityTest(TempDirTestCase):
    """ADR-0008 決定五：手動指定 -> 下載的快取 -> PATH -> 系統 SDK。"""

    def setUp(self):
        super().setUp()
        self.local = self.sub("LocalAppData")
        self.manual = self.sub("manual")
        self.path_dir = self.sub("on-path")
        self.sdk_root = self.sub("ProgramFilesX86", "Windows Kits", "10")
        self.environ = {
            "LOCALAPPDATA": self.local,
            "ProgramFiles(x86)": os.path.join(self.tmp, "ProgramFilesX86"),
            "PROCESSOR_ARCHITECTURE": "AMD64",
            "PATH": "",
        }

    def cache_bin(self):
        return sdk_tools.cache_dir(settings={}, environ=self.environ)

    def populate_all_sources(self, tool="signtool.exe"):
        make_tool(self.manual, tool)
        make_bin_layout(self.cache_bin(), "10.0.26100.0", "x64", [tool])
        make_tool(self.path_dir, tool)
        make_bin_layout(self.sdk_root, "10.0.22621.0", "x64", [tool])

    def find(self, tool="signtool.exe", settings=None):
        return sdk_tools.find_tool(tool, settings=settings or {}, environ=self.environ)

    def test_manual_setting_wins_over_everything_else(self):
        self.populate_all_sources()
        found = self.find(settings={"sdk_tools_dir": self.manual})
        self.assertEqual(found.source, "manual")
        self.assertEqual(os.path.dirname(found.path), self.manual)

    def test_cache_wins_when_no_manual_setting(self):
        self.populate_all_sources()
        found = self.find()
        self.assertEqual(found.source, "cache")
        self.assertEqual(found.version, sdk_tools.PACKAGE_VERSION)

    def test_path_is_used_before_the_system_sdk(self):
        """PATH 上的工具是使用者主動放上去的，比碰巧存在的系統安裝明確。"""
        make_tool(self.path_dir, "signtool.exe")
        make_bin_layout(self.sdk_root, "10.0.22621.0", "x64", ["signtool.exe"])
        self.environ["PATH"] = self.path_dir
        found = self.find()
        self.assertEqual(found.source, "path")

    def test_system_sdk_is_the_last_resort(self):
        make_bin_layout(self.sdk_root, "10.0.22621.0", "x64", ["signtool.exe"])
        found = self.find()
        self.assertEqual(found.source, "system")
        self.assertEqual(found.version, "10.0.22621.0")

    def test_system_sdk_picks_the_newest_version_directory(self):
        make_bin_layout(self.sdk_root, "10.0.19041.0", "x64", ["signtool.exe"])
        make_bin_layout(self.sdk_root, "10.0.22621.0", "x64", ["signtool.exe"])
        make_bin_layout(self.sdk_root, "10.0.9.0", "x64", ["signtool.exe"])
        found = self.find()
        self.assertEqual(found.version, "10.0.22621.0")

    def test_a_source_that_lacks_the_requested_tool_is_skipped(self):
        """快取裡只有 signtool 時，找 makeappx 要繼續往下找，不是報「找到了」。"""
        make_bin_layout(self.cache_bin(), "10.0.26100.0", "x64", ["signtool.exe"])
        make_bin_layout(self.sdk_root, "10.0.22621.0", "x64", ["makeappx.exe"])
        found = self.find("makeappx.exe")
        self.assertEqual(found.source, "system")

    def test_manual_setting_that_lacks_the_tool_is_an_error_not_a_fallthrough(self):
        """ADR-0008 已知限制第三項：使用前驗證手動指定的路徑確實含該工具。

        手動指定是使用者最明確的意圖表達，指錯了要當面講，不能安靜地
        改用別的來源——那會讓使用者以為自己指定的路徑正在生效。
        """
        make_bin_layout(self.sdk_root, "10.0.22621.0", "x64", ["signtool.exe"])
        with self.assertRaises(sdk_tools.SdkToolNotFound) as ctx:
            self.find(settings={"sdk_tools_dir": self.manual})
        self.assertIn(self.manual, str(ctx.exception))

    def test_manual_setting_may_point_at_a_bin_version_layout(self):
        """手動指定容許直接指向解壓出來的套件根目錄，不必指到最底層。"""
        make_bin_layout(self.manual, "10.0.26100.0", "x64", ["makeappx.exe"])
        found = self.find("makeappx.exe", settings={"sdk_tools_dir": self.manual})
        self.assertEqual(found.source, "manual")
        self.assertTrue(os.path.isfile(found.path))


class NotFoundMessageTest(TempDirTestCase):
    """ADR-0008 決定一與決定四：找不到時中止，訊息含可直接執行的取得指令。"""

    def setUp(self):
        super().setUp()
        self.environ = {
            "LOCALAPPDATA": self.sub("LocalAppData"),
            "PROCESSOR_ARCHITECTURE": "AMD64",
            "PATH": "",
        }

    def test_raises_when_no_source_has_the_tool(self):
        with self.assertRaises(sdk_tools.SdkToolNotFound):
            sdk_tools.find_tool("makeappx.exe", settings={}, environ=self.environ)

    def test_message_contains_a_runnable_fetch_command(self):
        with self.assertRaises(sdk_tools.SdkToolNotFound) as ctx:
            sdk_tools.find_tool("makeappx.exe", settings={}, environ=self.environ)
        message = str(ctx.exception)
        self.assertIn("fetch-sdk-tools", message)

    def test_message_does_not_repeat_the_misleading_path_instruction(self):
        """決定四：現行訊息叫使用者「確認它在 PATH 裡」，但 Windows SDK
        裝完不會把這些工具加進 PATH，照做不會成功。"""
        with self.assertRaises(sdk_tools.SdkToolNotFound) as ctx:
            sdk_tools.find_tool("signtool.exe", settings={}, environ=self.environ)
        self.assertNotIn("在 PATH 裡", str(ctx.exception))


class DescribeSourceTest(TempDirTestCase):
    """決定五末段：建置過程須輸出本次實際採用的來源與版本。"""

    def test_description_names_both_the_source_and_the_path(self):
        manual = self.sub("manual")
        make_tool(manual, "signtool.exe")
        found = sdk_tools.find_tool(
            "signtool.exe",
            settings={"sdk_tools_dir": manual},
            environ={"LOCALAPPDATA": self.sub("LocalAppData"), "PATH": ""},
        )
        text = found.describe()
        self.assertIn(found.path, text)
        self.assertIn("signtool.exe", text)

    def test_description_includes_the_version_when_known(self):
        sdk_root = self.sub("ProgramFilesX86", "Windows Kits", "10")
        make_bin_layout(sdk_root, "10.0.22621.0", "x64", ["signtool.exe"])
        found = sdk_tools.find_tool(
            "signtool.exe",
            settings={},
            environ={
                "LOCALAPPDATA": self.sub("LocalAppData"),
                "ProgramFiles(x86)": os.path.join(self.tmp, "ProgramFilesX86"),
                "PROCESSOR_ARCHITECTURE": "AMD64",
                "PATH": "",
            },
        )
        self.assertIn("10.0.22621.0", found.describe())


class FetchToolsTest(TempDirTestCase):
    """ADR-0008 決定一（明確要求才下載）與決定二（固定版本、驗雜湊）。"""

    def setUp(self):
        super().setUp()
        self.environ = {"LOCALAPPDATA": self.sub("LocalAppData"), "PROCESSOR_ARCHITECTURE": "AMD64", "PATH": ""}
        self.payload = make_fake_nupkg_bytes()
        self.calls = []

    def fetch(self, payload=None, settings=None, corrupt=False, **kwargs):
        return sdk_tools.fetch_tools(
            settings=settings if settings is not None else {},
            environ=self.environ,
            download=fake_downloader(
                self.payload if payload is None else payload, self.calls, corrupt=corrupt
            ),
            **kwargs,
        )

    def test_downloads_the_pinned_url_and_demands_the_pinned_hash(self):
        self.fetch()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["url"], sdk_tools.PACKAGE_URL)
        self.assertEqual(self.calls[0]["expected_sha256"], sdk_tools.PACKAGE_SHA256)

    def test_extracted_tools_are_then_discoverable_as_the_cache_source(self):
        """取得之後，find_tool() 必須真的找得到——這是這個功能的目的。"""
        self.fetch()
        for tool in ("makeappx.exe", "signtool.exe"):
            found = sdk_tools.find_tool(tool, settings={}, environ=self.environ)
            self.assertEqual(found.source, "cache", tool)
            self.assertTrue(os.path.isfile(found.path), tool)

    def test_result_reports_where_the_tools_landed(self):
        result = self.fetch()
        self.assertEqual(result.version, sdk_tools.PACKAGE_VERSION)
        self.assertTrue(os.path.isdir(result.cache_dir))

    def test_a_wrong_payload_fails_and_leaves_no_usable_cache(self):
        """雜湊不符時不能留下半套快取——留著會在下次被當成有效來源採用。"""
        with self.assertRaises(Exception):
            self.fetch(payload=b"not the real package", corrupt=True)
        with self.assertRaises(sdk_tools.SdkToolNotFound):
            sdk_tools.find_tool("makeappx.exe", settings={}, environ=self.environ)

    def test_second_call_does_not_download_again(self):
        self.fetch()
        self.fetch()
        self.assertEqual(len(self.calls), 1)

    def test_force_redownloads_even_when_cached(self):
        self.fetch()
        self.fetch(force=True)
        self.assertEqual(len(self.calls), 2)

    def test_extraction_refuses_entries_that_escape_the_cache_directory(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("../../evil.exe", b"MZ")
        with self.assertRaises(Exception):
            self.fetch(payload=buf.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "evil.exe")))

    def test_extraction_survives_paths_longer_than_the_classic_limit(self):
        """真實抓到的缺陷：套件內含名稱很長的檔案（例如
        Microsoft.Windows.Build.Appx.AppxPackaging.dll.manifest），快取位置
        稍深一點，解壓目標就超過 Windows 傳統的 260 字元路徑上限而失敗。

        使用者的家目錄名稱、CI 指定的快取路徑深度都不在本工具的控制範圍內，
        因此這不是「換個位置就好」的問題。
        """
        deep = self.tmp
        while len(deep) < 200:
            deep = os.path.join(deep, "nested-directory-segment")
        os.makedirs(deep, exist_ok=True)
        self.environ = dict(self.environ, LOCALAPPDATA=deep)
        long_name = "Microsoft.Windows.Build.Appx.AppxPackaging.dll.manifest"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for name in ("makeappx.exe", "signtool.exe", long_name):
                z.writestr(f"bin/10.0.26100.0/x64/{name}", b"MZ FAKE")
        result = self.fetch(payload=buf.getvalue(), settings={})
        # 不用 os.path.isdir 檢查落地位置：那條路徑本身就超過上限，
        # 檢查會在測試這一端失敗，與待測程式是否正確無關。
        self.assertEqual(len(result.tools), len(sdk_tools.REQUIRED_TOOLS))
        for tool in sdk_tools.REQUIRED_TOOLS:
            self.assertEqual(sdk_tools.find_tool(tool, settings={}, environ=self.environ).source, "cache")

    def test_a_package_without_the_required_tools_is_rejected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("bin/10.0.26100.0/x64/readme.txt", b"nothing useful")
        with self.assertRaises(Exception):
            self.fetch(payload=buf.getvalue())


class FetchCommandHintTest(unittest.TestCase):
    """取得指令的形狀：獨立子指令 fetch-sdk-tools。"""

    def test_hint_is_a_single_runnable_line(self):
        hint = sdk_tools.fetch_command_hint()
        self.assertIn("fetch-sdk-tools", hint)
        self.assertNotIn("\n", hint)

    def test_hint_names_the_cli_exe_when_frozen(self):
        hint = sdk_tools.fetch_command_hint(frozen=True, executable=r"C:\tools\mswi_CLI.exe")
        self.assertIn("mswi_CLI.exe", hint)

    def test_hint_uses_the_script_form_when_running_from_source(self):
        hint = sdk_tools.fetch_command_hint(frozen=False, executable=r"C:\Python313\python.exe")
        self.assertIn("builder_cli.py", hint)


class SignExecutableUsesSharedLookupTest(TempDirTestCase):
    """規劃文件第二輪決議第十三項：既有 signtool 檢索改用同一套邏輯。"""

    def setUp(self):
        super().setUp()
        import builder
        self.builder = builder
        self.exe_path = os.path.join(self.tmp, "Setup.exe")
        with open(self.exe_path, "wb") as f:
            f.write(b"MZ")
        self.signing = {
            "cert_path": os.path.join(self.tmp, "cert.pfx"),
            "cert_password_env": "TEST_CERT_PW",
            "timestamp_url": "http://timestamp.example/",
        }

    def test_sign_uses_the_signtool_that_find_tool_resolved(self):
        resolved = os.path.join(self.sub("manual"), "signtool.exe")
        make_tool(os.path.dirname(resolved), "signtool.exe")
        recorded = {}

        def fake_run(cmd, creationflags=0, capture_output=True, text=True):
            recorded["cmd"] = cmd
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        self.builder._sign_executable(
            self.exe_path, self.signing,
            find_tool=lambda name: sdk_tools.ToolLocation(resolved, "manual", "", name),
            run=fake_run,
        )
        self.assertEqual(recorded["cmd"][0], resolved)

    def test_sign_surfaces_the_shared_not_found_message(self):
        def raising(name):
            raise sdk_tools.SdkToolNotFound("找不到 signtool.exe，請執行 fetch-sdk-tools")

        with self.assertRaises(Exception) as ctx:
            self.builder._sign_executable(self.exe_path, self.signing, find_tool=raising)
        self.assertIn("fetch-sdk-tools", str(ctx.exception))

    def test_builder_no_longer_has_its_own_signtool_lookup(self):
        """兩支同目錄的工具維持兩套檢索方式，正是這次要消除的東西。"""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "builder.py"), encoding="utf-8") as f:
            source = f.read()
        self.assertFalse(
            'which("signtool")' in source,
            "builder.py 仍有自己的 signtool 檢索，應改用 sdk_tools.find_tool()",
        )


if __name__ == "__main__":
    unittest.main()
