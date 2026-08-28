"""dependency_install.py 的測試（登錄表偵測相關的部分，從
tests/test_installer_core_misc.py 搬過來——見該檔案的異動說明）。

install()（下載/驗證/靜默安裝協定）本身跟 InstallerAPI 的委派仍留在
test_installer_core_misc.py 測，因為那部分主要在驗證「InstallerAPI 有沒有
正確傳參數/委派」，屬於整合層級；這裡只測純粹的登錄表偵測邏輯，不需要
建構 InstallerAPI。
"""
import hashlib
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _fakes import FakeWinReg
import dependency_install as di


class TestGenericRegistryCheck(unittest.TestCase):
    """_generic_registry_check()：泛用登錄表偵測，取代原本每個相依元件各自
    寫一個檢查函式的做法，custom_dependencies 的自訂相依元件也靠它。"""

    def setUp(self):
        self.fake_reg = FakeWinReg()
        self.patcher = mock.patch.dict(sys.modules, {"winreg": self.fake_reg})
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_key_missing_returns_false(self):
        self.assertFalse(di._generic_registry_check("HKLM", "Software\\NotThere"))

    def test_value_name_none_only_checks_key_exists(self):
        self.fake_reg.set_hklm("Software\\SomeApp", {})
        self.assertTrue(di._generic_registry_check("HKLM", "Software\\SomeApp"))

    def test_value_matches_expected(self):
        self.fake_reg.set_hklm("Software\\SomeApp", {"Installed": 1})
        self.assertTrue(di._generic_registry_check("HKLM", "Software\\SomeApp", "Installed", 1))

    def test_value_mismatch_returns_false(self):
        self.fake_reg.set_hklm("Software\\SomeApp", {"Installed": 0})
        self.assertFalse(di._generic_registry_check("HKLM", "Software\\SomeApp", "Installed", 1))

    def test_hkcu_hive_is_respected(self):
        self.fake_reg.set_hkcu("Software\\SomeApp", {"Installed": 1})
        self.assertTrue(di._generic_registry_check("HKCU", "Software\\SomeApp", "Installed", 1))
        self.assertFalse(di._generic_registry_check("HKLM", "Software\\SomeApp", "Installed", 1))


class TestGenericRegistryVersionCheck(unittest.TestCase):
    """_generic_registry_version_check()：相依元件版本檢查可以指定最低
    需求版本，min_version 是 None 時退化成純粹的存在性判斷。

    enum_subkeys=True 對應 .NET Desktop Runtime 那種「子機碼名稱本身就是
    版本號」的登錄表佈局（InstalledVersions\\...\\sharedfx\\...\\8.0.10）；
    enum_subkeys=False 對應 vcredist 那種「某個值本身存的就是版本字串」
    的佈局。"""

    def setUp(self):
        self.fake_reg = FakeWinReg()
        self.patcher = mock.patch.dict(sys.modules, {"winreg": self.fake_reg})
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_key_missing_returns_false_regardless_of_min_version(self):
        self.assertFalse(di._generic_registry_version_check("HKLM", "Software\\NotThere", min_version="1.0"))

    def test_no_min_version_is_pure_existence_check(self):
        self.fake_reg.set_hklm("Software\\SomeApp", {"Version": "1.0.0"})
        self.assertTrue(di._generic_registry_version_check("HKLM", "Software\\SomeApp", value_name="Version"))

    def test_value_name_mode_meets_min_version(self):
        self.fake_reg.set_hklm("Software\\SomeApp", {"Version": "14.38.33135"})
        self.assertTrue(di._generic_registry_version_check(
            "HKLM", "Software\\SomeApp", value_name="Version", min_version="14.30",
        ))

    def test_value_name_mode_below_min_version(self):
        self.fake_reg.set_hklm("Software\\SomeApp", {"Version": "14.20.0"})
        self.assertFalse(di._generic_registry_version_check(
            "HKLM", "Software\\SomeApp", value_name="Version", min_version="14.30",
        ))

    def test_enum_subkeys_mode_uses_highest_subkey_version(self):
        self.fake_reg.set_hklm("Software\\SomeApp", {})
        self.fake_reg.set_hklm("Software\\SomeApp\\8.0.1", {})
        self.fake_reg.set_hklm("Software\\SomeApp\\8.0.10", {})
        self.assertTrue(di._generic_registry_version_check(
            "HKLM", "Software\\SomeApp", enum_subkeys=True, min_version="8.0.5",
        ))

    def test_enum_subkeys_mode_below_min_version(self):
        self.fake_reg.set_hklm("Software\\SomeApp", {})
        self.fake_reg.set_hklm("Software\\SomeApp\\7.0.0", {})
        self.assertFalse(di._generic_registry_version_check(
            "HKLM", "Software\\SomeApp", enum_subkeys=True, min_version="8.0.0",
        ))

    def test_enum_subkeys_mode_no_subkeys_returns_false(self):
        self.fake_reg.set_hklm("Software\\SomeApp", {})
        self.assertFalse(di._generic_registry_version_check(
            "HKLM", "Software\\SomeApp", enum_subkeys=True, min_version="1.0",
        ))


class TestCheckVcredistX64VersionAware(unittest.TestCase):
    def setUp(self):
        self.fake_reg = FakeWinReg()
        self.patcher = mock.patch.dict(sys.modules, {"winreg": self.fake_reg})
        self.patcher.start()
        self.path = "SOFTWARE\\Microsoft\\VisualStudio\\14.0\\VC\\Runtimes\\x64"

    def tearDown(self):
        self.patcher.stop()

    def test_installed_flag_missing_is_false_even_without_min_version(self):
        self.assertFalse(di._check_vcredist_x64())

    def test_no_min_version_only_checks_installed_flag(self):
        self.fake_reg.set_hklm(self.path, {"Installed": 1})
        self.assertTrue(di._check_vcredist_x64())

    def test_min_version_met(self):
        self.fake_reg.set_hklm(self.path, {"Installed": 1, "Version": "14.38.33135"})
        self.assertTrue(di._check_vcredist_x64(min_version="14.30"))

    def test_min_version_not_met(self):
        self.fake_reg.set_hklm(self.path, {"Installed": 1, "Version": "14.20.0"})
        self.assertFalse(di._check_vcredist_x64(min_version="14.30"))


class TestCheckDotnetDesktopVersionAware(unittest.TestCase):
    def setUp(self):
        self.fake_reg = FakeWinReg()
        self.patcher = mock.patch.dict(sys.modules, {"winreg": self.fake_reg})
        self.patcher.start()
        self.path = "SOFTWARE\\WOW6432Node\\dotnet\\Setup\\InstalledVersions\\x64\\sharedfx\\Microsoft.WindowsDesktop.App"
        # _check_dotnet_desktop() 登錄表查不到時會 fallback 掃實際安裝目錄
        # （見 TestCheckDotnetDesktopFilesystemFallback）——這裡純粹測登錄表
        # 這條路徑本身，指到不存在的目錄，避免撈到開發機真實裝的 .NET 汙染
        # 這幾個測試案例的預期結果。
        self.env_patcher = mock.patch.dict(
            os.environ, {"ProgramFiles": "", "ProgramW6432": "", "ProgramFiles(x86)": ""},
        )
        self.env_patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.env_patcher.stop()

    def test_no_min_version_true_when_any_version_subkey_present(self):
        self.fake_reg.set_hklm(self.path, {})
        self.fake_reg.set_hklm(self.path + "\\8.0.10", {})
        self.assertTrue(di._check_dotnet_desktop())

    def test_min_version_met(self):
        self.fake_reg.set_hklm(self.path, {})
        self.fake_reg.set_hklm(self.path + "\\8.0.10", {})
        self.assertTrue(di._check_dotnet_desktop(min_version="8.0.0"))

    def test_min_version_not_met(self):
        self.fake_reg.set_hklm(self.path, {})
        self.fake_reg.set_hklm(self.path + "\\6.0.0", {})
        self.assertFalse(di._check_dotnet_desktop(min_version="8.0.0"))


class TestCheckDotnetDesktopFilesystemFallback(unittest.TestCase):
    """真實抓到的 bug：_check_dotnet_desktop() 原本只信登錄表
    HKLM\\SOFTWARE\\WOW6432Node\\dotnet\\Setup\\InstalledVersions\\...，
    但這把機碼只有透過官方 MSI 版安裝程式裝的才會寫入——實測發現透過
    winget/Visual Studio Installer/dotnet-install.ps1 裝的完全不會寫這把
    機碼，即使 `dotnet --list-runtimes` 能正常列出已安裝版本，登錄表判斷
    還是會誤判成沒裝，導致使用者明明裝好了還被要求「自動安裝」，裝完一樣
    偵測不到。改成登錄表查不到時，改掃 dotnet CLI 本身也是靠掃描判斷的
    實際安裝目錄（%ProgramFiles%\\dotnet\\shared\\Microsoft.WindowsDesktop.App）
    當備援。"""

    def setUp(self):
        self.fake_reg = FakeWinReg()
        self.patcher = mock.patch.dict(sys.modules, {"winreg": self.fake_reg})
        self.patcher.start()
        import tempfile
        self.tmp_dir = tempfile.mkdtemp()
        self.shared_dir = os.path.join(self.tmp_dir, "dotnet", "shared", "Microsoft.WindowsDesktop.App")
        self.env_patcher = mock.patch.dict(
            os.environ, {"ProgramFiles": self.tmp_dir, "ProgramW6432": "", "ProgramFiles(x86)": ""},
        )
        self.env_patcher.start()

    def tearDown(self):
        import shutil
        self.patcher.stop()
        self.env_patcher.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_registry_empty_but_shared_fx_dir_present_is_detected(self):
        os.makedirs(os.path.join(self.shared_dir, "10.0.11"))
        self.assertTrue(di._check_dotnet_desktop())

    def test_registry_empty_and_no_shared_fx_dir_is_false(self):
        self.assertFalse(di._check_dotnet_desktop())

    def test_registry_empty_min_version_met_via_filesystem(self):
        os.makedirs(os.path.join(self.shared_dir, "10.0.11"))
        self.assertTrue(di._check_dotnet_desktop(min_version="9.0.0"))

    def test_registry_empty_min_version_not_met_via_filesystem(self):
        os.makedirs(os.path.join(self.shared_dir, "6.0.36"))
        self.assertFalse(di._check_dotnet_desktop(min_version="8.0.0"))


class TestBundledDependencyIsAlsoVerified(unittest.TestCase):
    """F06：`sha256` 驗證原本位於「需要連線下載」的分支內，走內嵌路徑
    （打包時就把安裝檔嵌進 Setup.exe）時整段跳過。

    加上打包端當時也沒有任何驗證，合併效果是：使用者同時填寫 `sha256`
    並勾選內嵌時，該檔案從打包到安裝沒有任何一個環節驗證過。把驗證移到
    兩條路徑的共同位置成本極低（讀一次檔案算摘要），而且能涵蓋「Setup.exe
    打包完成之後、內嵌檔案被替換」這種下載端驗證抓不到的情況。
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.payload = b"bundled-dependency-installer"
        os.makedirs(os.path.join(self.tmp_dir, "dependencies"))
        self.bundled_path = os.path.join(self.tmp_dir, "dependencies", "mydep.exe")
        with open(self.bundled_path, "wb") as f:
            f.write(self.payload)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _install(self, sha256, check_result=True):
        checkers = {
            "mydep": (lambda: check_result, "My Dep", "https://example.invalid/mydep.exe", ["/S"]),
        }
        custom_dependencies = [{"key": "mydep", "sha256": sha256}] if sha256 else []
        with mock.patch("dependency_install.subprocess.run") as mock_run:
            result = di.install(
                "mydep", checkers,
                custom_dependencies=custom_dependencies,
                bundle_dependencies=["mydep"],
                resolve_resource_path=lambda rel: os.path.join(self.tmp_dir, rel),
            )
        return result, mock_run

    def test_bundled_file_with_wrong_sha256_is_not_executed(self):
        result, mock_run = self._install("0" * 64)
        self.assertEqual(result["status"], "error")
        mock_run.assert_not_called()

    def test_bundled_file_with_matching_sha256_is_executed(self):
        digest = hashlib.sha256(self.payload).hexdigest()
        result, mock_run = self._install(digest)
        self.assertEqual(result["status"], "success")
        mock_run.assert_called_once()

    def test_bundled_file_without_sha256_is_executed_as_before(self):
        """沒填 sha256 的情況維持原行為，不是「沒填就一律拒絕」。"""
        result, mock_run = self._install(None)
        self.assertEqual(result["status"], "success")
        mock_run.assert_called_once()

    def test_bundled_file_is_not_deleted_when_verification_fails(self):
        """內嵌檔案是這次安裝檔自己的資源（PyInstaller 解壓出來的暫存內容，
        或開發模式下的原始檔案），不是我們下載出來的暫存檔——驗證失敗時
        只能拒絕執行，不能把它刪掉。"""
        self._install("0" * 64)
        self.assertTrue(os.path.exists(self.bundled_path))


if __name__ == "__main__":
    unittest.main()
