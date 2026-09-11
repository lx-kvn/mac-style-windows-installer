"""`installer_core.remove_legacy_install_dir()` 的行為。

傳統模式換成 MSIX 時，舊的解除安裝助手是以 `--upgrade` 被呼叫的，而
`self_delete` 對那個旗標不排背景自我刪除——那在傳統換傳統的更新裡是對的
（避免那段刪除把剛複製進去的新檔案一併帶走），但換成 MSIX 之後不會有任何
東西複製回那個資料夾，剩下的是一支沒有作用的解除安裝助手
（2026-09-11 由 `tools/verify_msix_dialogs.py` 的遷移那一輪量到）。

**這個函式只收自己認得的殘留，不做遞迴刪除。** 認不得的東西留在原地並回報
——那有可能是使用者自己放進去的檔案，而這一步是在安裝流程的中途跑的，
刪錯了沒有第二次機會。
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import installer_core


class TheCleanup(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.target = os.path.join(self.root, "OldApp")
        os.makedirs(self.target)

    def _put(self, name, text="x"):
        with open(os.path.join(self.target, name), "w", encoding="utf-8") as f:
            f.write(text)

    def test_a_folder_with_only_the_old_uninstaller_goes_away(self):
        self._put("uninstall.exe")
        installer_core.remove_legacy_install_dir(self.target)
        self.assertFalse(os.path.exists(self.target))

    def test_the_leftover_config_counts_as_residue_too(self):
        self._put("uninstall.exe")
        self._put("installer_config.json")
        installer_core.remove_legacy_install_dir(self.target)
        self.assertFalse(os.path.exists(self.target))

    def test_an_already_empty_folder_goes_away(self):
        installer_core.remove_legacy_install_dir(self.target)
        self.assertFalse(os.path.exists(self.target))

    def test_something_unrecognised_is_left_alone_and_reported(self):
        """認不得的東西有可能是使用者自己放的。留在原地，並讓呼叫端知道。"""
        self._put("uninstall.exe")
        self._put("我的筆記.txt")
        with self.assertRaises(OSError) as caught:
            installer_core.remove_legacy_install_dir(self.target)
        self.assertIn("我的筆記.txt", str(caught.exception))
        self.assertTrue(os.path.exists(self.target))
        self.assertTrue(os.path.exists(os.path.join(self.target, "我的筆記.txt")))

    def test_a_subfolder_counts_as_unrecognised(self):
        os.makedirs(os.path.join(self.target, "data"))
        with self.assertRaises(OSError):
            installer_core.remove_legacy_install_dir(self.target)
        self.assertTrue(os.path.exists(self.target))

    def test_a_folder_that_is_not_there_is_not_an_error(self):
        """舊版的解除安裝助手自己收乾淨了的情況——那是好事，不是失敗。"""
        installer_core.remove_legacy_install_dir(
            os.path.join(self.root, "NeverExisted"))

    def test_an_empty_path_does_nothing(self):
        """查詢沒有回報路徑時不該去猜一個。"""
        installer_core.remove_legacy_install_dir("")


class ItDoesNotReachOutsideTheFolder(unittest.TestCase):
    def test_the_residue_names_are_exact_matches_not_patterns(self):
        """名單比對的是完整檔名。用開頭比對的話，`uninstall.exe.bak` 或
        使用者的 `installer_config.json.orig` 也會被當成殘留刪掉。"""
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        target = os.path.join(root, "OldApp")
        os.makedirs(target)
        with open(os.path.join(target, "uninstall.exe.bak"), "w") as f:
            f.write("x")
        with self.assertRaises(OSError):
            installer_core.remove_legacy_install_dir(target)
        self.assertTrue(os.path.exists(os.path.join(target, "uninstall.exe.bak")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
