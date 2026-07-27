"""disk_space.py 的測試。

抽出來的純函式：不需要建構 InstallerAPI()，直接測 check_disk_space()/
required_install_size() 本身。
"""
import os
import sys
import shutil
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import disk_space


class TestRequiredInstallSize(unittest.TestCase):
    def test_sums_all_files_recursively(self):
        tmp_dir = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmp_dir, "a.bin"), "wb") as f:
                f.write(b"x" * 100)
            sub = os.path.join(tmp_dir, "sub")
            os.makedirs(sub)
            with open(os.path.join(sub, "b.bin"), "wb") as f:
                f.write(b"y" * 50)
            self.assertEqual(disk_space.required_install_size(tmp_dir), 150)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


class TestCheckDiskSpace(unittest.TestCase):
    def test_insufficient_space_reports_false(self):
        fake_usage = mock.Mock(free=100)
        with mock.patch.object(disk_space.shutil, "disk_usage", return_value=fake_usage):
            ok, free, required = disk_space.check_disk_space(1000, "C:\\FakeApp", "C:\\Fallback")
        self.assertFalse(ok)
        self.assertEqual(free, 100)
        self.assertEqual(required, 1000)

    def test_sufficient_space_with_10_percent_buffer(self):
        """磁碟剩餘空間要 >= 需求量的 1.1 倍（保留 10% 緩衝），
        剛好等於需求量（沒有緩衝）應該視為不足。"""
        fake_usage = mock.Mock(free=1100)
        with mock.patch.object(disk_space.shutil, "disk_usage", return_value=fake_usage):
            ok, _, _ = disk_space.check_disk_space(1000, "C:\\FakeApp", "C:\\Fallback")
        self.assertTrue(ok)

    def test_exactly_required_without_buffer_is_insufficient(self):
        fake_usage = mock.Mock(free=1000)
        with mock.patch.object(disk_space.shutil, "disk_usage", return_value=fake_usage):
            ok, _, _ = disk_space.check_disk_space(1000, "C:\\FakeApp", "C:\\Fallback")
        self.assertFalse(ok)

    def test_falls_back_to_fallback_path_when_target_has_no_drive(self):
        fake_usage = mock.Mock(free=999999)
        with mock.patch.object(disk_space.shutil, "disk_usage", return_value=fake_usage) as mock_disk_usage:
            disk_space.check_disk_space(100, "no-drive-path", "D:\\Fallback")
        mock_disk_usage.assert_called_once_with("D:\\")


if __name__ == "__main__":
    unittest.main(verbosity=2)
