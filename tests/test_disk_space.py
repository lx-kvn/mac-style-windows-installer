"""disk_space.py 的測試。

抽出來的純函式：不需要建構 InstallerAPI()，直接測 check_drive_space()/
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


class TestCheckDriveSpace(unittest.TestCase):
    """F08：磁碟空間檢查原本只涵蓋一顆磁碟。

    `_required_size()` 加總來源資料夾全部檔案，原本的 `check_disk_space()` 只檢查
    `selected_path` 所在磁碟。但 `local_appdata_files` 指定的檔案實際落在
    `%LOCALAPPDATA%\\Programs\\<folder_name>`，可能位於另一顆磁碟——那顆
    磁碟從未被檢查，而目標磁碟的需求量同時被高估。覆蓋安裝時整份舊安裝
    資料夾會複製到 `%TEMP%`，那份需求也完全沒有被計入。

    介面因此從「一個路徑、一個需求量」改成「一組（落地路徑, 需求量）」，
    依磁碟代號分組加總後逐一檢查。
    """

    def _usage(self, free_by_drive, default_free=10 ** 12):
        def fake_disk_usage(path):
            return mock.Mock(free=free_by_drive.get(path, default_free))
        return fake_disk_usage

    def test_groups_requirements_by_drive_and_checks_each_one(self):
        with mock.patch.object(
            disk_space.shutil, "disk_usage",
            side_effect=self._usage({"C:\\": 10 ** 9, "D:\\": 10 ** 9}),
        ) as mock_disk_usage:
            ok, drives = disk_space.check_drive_space(
                [("C:\\Program Files\\App", 1000), ("D:\\Users\\Me\\AppData", 2000)],
                fallback_path="C:\\",
            )
        self.assertTrue(ok)
        self.assertEqual({d["drive"] for d in drives}, {"C:", "D:"})
        self.assertEqual(
            sorted(c.args[0] for c in mock_disk_usage.call_args_list), ["C:\\", "D:\\"],
        )

    def test_requirements_on_the_same_drive_are_summed(self):
        with mock.patch.object(disk_space.shutil, "disk_usage", side_effect=self._usage({})):
            _ok, drives = disk_space.check_drive_space(
                [("C:\\Program Files\\App", 1000), ("C:\\Users\\Me\\AppData", 2500)],
                fallback_path="C:\\",
            )
        self.assertEqual(len(drives), 1)
        self.assertEqual(drives[0]["required"], 3500)

    def test_shortage_on_a_secondary_drive_fails_the_whole_check(self):
        """目標磁碟很空、但 local_appdata 落地的那顆磁碟不夠——原本這個
        情境完全不會被擋下來，安裝跑到一半才在複製檔案時失敗。"""
        with mock.patch.object(
            disk_space.shutil, "disk_usage",
            side_effect=self._usage({"C:\\": 10 ** 12, "D:\\": 100}),
        ):
            ok, drives = disk_space.check_drive_space(
                [("C:\\Program Files\\App", 1000), ("D:\\AppData", 2000)],
                fallback_path="C:\\",
            )
        self.assertFalse(ok)
        insufficient = [d for d in drives if not d["sufficient"]]
        self.assertEqual([d["drive"] for d in insufficient], ["D:"])
        self.assertEqual(insufficient[0]["free"], 100)
        self.assertEqual(insufficient[0]["required"], 2000)

    def test_keeps_the_10_percent_buffer(self):
        """剩餘空間要 >= 需求量的 1.1 倍，剛好等於需求量視為不足——
        這個緩衝是既有行為，介面換形狀不該把它弄丟。"""
        with mock.patch.object(disk_space.shutil, "disk_usage", side_effect=self._usage({"C:\\": 1100})):
            ok, _ = disk_space.check_drive_space([("C:\\App", 1000)], fallback_path="C:\\")
        self.assertTrue(ok)

        with mock.patch.object(disk_space.shutil, "disk_usage", side_effect=self._usage({"C:\\": 1000})):
            ok, _ = disk_space.check_drive_space([("C:\\App", 1000)], fallback_path="C:\\")
        self.assertFalse(ok)

    def test_falls_back_when_a_path_has_no_drive_letter(self):
        with mock.patch.object(
            disk_space.shutil, "disk_usage", side_effect=self._usage({}),
        ) as mock_disk_usage:
            disk_space.check_drive_space([("no-drive-path", 100)], fallback_path="D:\\Fallback")
        mock_disk_usage.assert_called_once_with("D:\\")

    def test_zero_sized_requirements_are_dropped(self):
        """需求量 0 的落地位置（例如這次沒有任何 local_appdata 檔案）不該
        害使用者被檢查一顆根本沒要寫入的磁碟——那顆磁碟可能是唯讀的、或
        disk_usage() 會直接拋例外。"""
        with mock.patch.object(
            disk_space.shutil, "disk_usage", side_effect=self._usage({}),
        ) as mock_disk_usage:
            _ok, drives = disk_space.check_drive_space(
                [("C:\\App", 1000), ("E:\\Nothing", 0)], fallback_path="C:\\",
            )
        self.assertEqual([d["drive"] for d in drives], ["C:"])
        mock_disk_usage.assert_called_once_with("C:\\")

    def test_unreadable_drive_is_not_treated_as_a_shortage(self):
        """disk_usage() 對某顆磁碟拋例外（磁碟機代號無效、網路磁碟掉線）
        時，不能因此把整個安裝擋下來——原本的單磁碟版本也沒有這種行為，
        這是「查不到」不是「空間不足」。"""
        def fake_disk_usage(path):
            if path == "D:\\":
                raise OSError("模擬查不到這顆磁碟")
            return mock.Mock(free=10 ** 12)

        with mock.patch.object(disk_space.shutil, "disk_usage", side_effect=fake_disk_usage):
            ok, drives = disk_space.check_drive_space(
                [("C:\\App", 1000), ("D:\\App", 2000)], fallback_path="C:\\",
            )
        self.assertTrue(ok)
        self.assertEqual([d["drive"] for d in drives], ["C:"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
