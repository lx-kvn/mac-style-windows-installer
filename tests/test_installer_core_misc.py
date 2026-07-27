"""installer_core.py 裡其他核心邏輯的測試：檔案完整性驗證、磁碟空間檢查、
靜默安裝命令列參數解析、覆蓋安裝版本偵測、安裝失敗回滾、加入 PATH。

一律用假的 winreg（tests/_fakes.py）或暫存資料夾，不會動到這台機器的登錄表
或真實磁碟空間，可以直接執行。
"""
import os
import sys
import shutil
import tempfile
import unittest
import zlib
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import installer_core as ic
from _fakes import FakeWinReg


def make_installer_api(**overrides):
    api = ic.InstallerAPI()
    for k, v in overrides.items():
        setattr(api, k, v)
    return api


class TestFileChecksum(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_matches_manual_crc32(self):
        path = os.path.join(self.tmp_dir, "a.bin")
        content = b"hello world" * 1000
        with open(path, "wb") as f:
            f.write(content)
        self.assertEqual(ic._file_checksum(path), zlib.crc32(content))

    def test_different_content_gives_different_checksum(self):
        p1 = os.path.join(self.tmp_dir, "a.bin")
        p2 = os.path.join(self.tmp_dir, "b.bin")
        with open(p1, "wb") as f:
            f.write(b"content A")
        with open(p2, "wb") as f:
            f.write(b"content B")
        self.assertNotEqual(ic._file_checksum(p1), ic._file_checksum(p2))


class TestCliArgs(unittest.TestCase):
    def _parse(self, argv):
        with mock.patch.object(sys, "argv", ["Setup_App.exe"] + argv):
            return ic._parse_cli_args()

    def test_no_args_defaults(self):
        silent, install_dir, desktop = self._parse([])
        self.assertFalse(silent)
        self.assertIsNone(install_dir)
        self.assertTrue(desktop)

    def test_silent_flag_case_insensitive(self):
        silent, _, _ = self._parse(["/s"])
        self.assertTrue(silent)

    def test_dir_flag(self):
        _, install_dir, _ = self._parse(["/D=C:\\Custom Path"])
        self.assertEqual(install_dir, "C:\\Custom Path")

    def test_long_dir_flag(self):
        _, install_dir, _ = self._parse(["/DIR=D:\\Apps\\MyApp"])
        self.assertEqual(install_dir, "D:\\Apps\\MyApp")

    def test_no_desktop_shortcut_flag(self):
        _, _, desktop = self._parse(["/NODESKTOPSHORTCUT"])
        self.assertFalse(desktop)

    def test_combined_flags(self):
        silent, install_dir, desktop = self._parse(["/S", "/D=C:\\X", "/NODESKTOPSHORTCUT"])
        self.assertTrue(silent)
        self.assertEqual(install_dir, "C:\\X")
        self.assertFalse(desktop)


class TestCheckDiskSpace(unittest.TestCase):
    def test_insufficient_space_reports_false(self):
        api = make_installer_api(selected_path="C:\\FakeApp")
        fake_usage = mock.Mock(free=100)
        with mock.patch.object(ic, "shutil") as fake_shutil, \
             mock.patch.object(api, "_required_size", return_value=1000):
            fake_shutil.disk_usage.return_value = fake_usage
            ok, free, required = api._check_disk_space()
        self.assertFalse(ok)
        self.assertEqual(free, 100)
        self.assertEqual(required, 1000)

    def test_sufficient_space_with_10_percent_buffer(self):
        """磁碟剩餘空間要 >= 需求量的 1.1 倍（保留 10% 緩衝），
        剛好等於需求量（沒有緩衝）應該視為不足。"""
        api = make_installer_api(selected_path="C:\\FakeApp")
        fake_usage = mock.Mock(free=1100)
        with mock.patch.object(ic, "shutil") as fake_shutil, \
             mock.patch.object(api, "_required_size", return_value=1000):
            fake_shutil.disk_usage.return_value = fake_usage
            ok, _, _ = api._check_disk_space()
        self.assertTrue(ok)

    def test_exactly_required_without_buffer_is_insufficient(self):
        api = make_installer_api(selected_path="C:\\FakeApp")
        fake_usage = mock.Mock(free=1000)
        with mock.patch.object(ic, "shutil") as fake_shutil, \
             mock.patch.object(api, "_required_size", return_value=1000):
            fake_shutil.disk_usage.return_value = fake_usage
            ok, _, _ = api._check_disk_space()
        self.assertFalse(ok)


class TestCheckExistingInstall(unittest.TestCase):
    def setUp(self):
        self.fake_reg = FakeWinReg()
        self.patcher = mock.patch.dict(sys.modules, {"winreg": self.fake_reg})
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def _seed_existing(self, app_name, version, install_path="C:\\Apps\\Old"):
        reg_path = f"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{app_name}"
        self.fake_reg.store[reg_path] = {
            "InstallLocation": install_path,
            "DisplayVersion": version,
        }

    def test_not_installed_before(self):
        api = make_installer_api(app_name="NeverInstalled", version="1.0.0")
        result = api.check_existing_install()
        self.assertEqual(result, {"exists": False})

    def test_upgrade_scenario_is_newer(self):
        self._seed_existing("MyApp", "1.0.0")
        api = make_installer_api(app_name="MyApp", version="2.0.0")
        result = api.check_existing_install()
        self.assertTrue(result["exists"])
        self.assertTrue(result["is_newer"])
        self.assertFalse(result["is_same_or_older"])

    def test_downgrade_scenario_is_same_or_older(self):
        self._seed_existing("MyApp", "3.0.0")
        api = make_installer_api(app_name="MyApp", version="1.0.0")
        result = api.check_existing_install()
        self.assertFalse(result["is_newer"])
        self.assertTrue(result["is_same_or_older"])

    def test_same_version_is_same_or_older(self):
        """相同版本重裝：不該被誤判成「有更新可以裝」。"""
        self._seed_existing("MyApp", "1.0.0")
        api = make_installer_api(app_name="MyApp", version="1.0.0")
        result = api.check_existing_install()
        self.assertFalse(result["is_newer"])
        self.assertTrue(result["is_same_or_older"])


class TestAddToPathEnv(unittest.TestCase):
    def setUp(self):
        self.fake_reg = FakeWinReg()
        self.patcher = mock.patch.dict(sys.modules, {"winreg": self.fake_reg})
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def _path_key(self):
        return r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"

    def test_appends_when_no_existing_path(self):
        # Environment 這個機碼在真實 Windows 上一定存在，只是底下可能沒有 "Path"
        # 這個值（極端但合法的情況）；用空機碼模擬這個狀態。
        self.fake_reg.store[self._path_key()] = {}
        api = make_installer_api(selected_path="C:\\Apps\\MyApp")
        with mock.patch("installer_core.ctypes.windll.user32.SendMessageTimeoutW"):
            api._add_to_path_env()
        self.assertEqual(self.fake_reg.store[self._path_key()]["Path"], "C:\\Apps\\MyApp")

    def test_does_not_duplicate_existing_entry(self):
        self.fake_reg.store[self._path_key()] = {"Path": "C:\\Windows;C:\\Apps\\MyApp"}
        api = make_installer_api(selected_path="C:\\Apps\\MyApp")
        with mock.patch("installer_core.ctypes.windll.user32.SendMessageTimeoutW"):
            api._add_to_path_env()
        self.assertEqual(self.fake_reg.store[self._path_key()]["Path"], "C:\\Windows;C:\\Apps\\MyApp")

    def test_registry_failure_propagates(self):
        """修復驗證：PATH 寫入失敗不再被吞掉，讓安裝流程可以整個回滾。"""
        self.fake_reg.fail_on_substring = "Session Manager"
        api = make_installer_api(selected_path="C:\\Apps\\MyApp")
        with self.assertRaises(PermissionError):
            api._add_to_path_env()


class TestRollback(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_removes_only_copied_files_and_prunes_empty_dirs(self):
        os.makedirs(os.path.join(self.tmp_dir, "sub"))
        with open(os.path.join(self.tmp_dir, "sub", "a.txt"), "w") as f:
            f.write("copied")
        with open(os.path.join(self.tmp_dir, "b.txt"), "w") as f:
            f.write("copied")
        with open(os.path.join(self.tmp_dir, "keep_me.txt"), "w") as f:
            f.write("使用者自己放的東西，不在複製清單內，不該被回滾清掉")

        api = make_installer_api(selected_path=self.tmp_dir)
        api._rollback(["sub/a.txt", "b.txt"], log=None)

        self.assertFalse(os.path.exists(os.path.join(self.tmp_dir, "sub", "a.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp_dir, "b.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp_dir, "sub")), "清空的子目錄應該被清掉")
        self.assertTrue(os.path.exists(os.path.join(self.tmp_dir, "keep_me.txt")), "不在複製清單內的檔案不該被回滾動到")

    def test_removes_whole_dir_when_nothing_left(self):
        with open(os.path.join(self.tmp_dir, "only.txt"), "w") as f:
            f.write("copied")
        api = make_installer_api(selected_path=self.tmp_dir)
        api._rollback(["only.txt"], log=None)
        self.assertFalse(os.path.exists(self.tmp_dir), "回滾後資料夾裡如果真的空了，應該連資料夾一起刪掉")


if __name__ == "__main__":
    unittest.main(verbosity=2)
