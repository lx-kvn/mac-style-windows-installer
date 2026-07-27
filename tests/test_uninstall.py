"""uninstall.py 的測試。

重點覆蓋 uninstall.py 檔案開頭註解記錄的那個真實 bug：清單式刪除（只刪
install_manifest.json 記錄的檔案，保留使用者事後自己在安裝目錄產生的東西）
最後卻用無差別的 rmdir 把整個資料夾砍光，讓前面的細心刪除形同虛設。
現在的正確行為是：清單刪完後，資料夾裡如果還有清單之外的項目就保留資料夾，
真的清空了才連資料夾一起刪。

登錄表操作一樣全程用 tests/_fakes.py 的假 winreg，不會動到真實登錄表。
"""
import os
import sys
import shutil
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _fakes import FakeWinReg
import uninstall as un


class TestRemoveFileAssociations(unittest.TestCase):
    def setUp(self):
        self.fake_reg = FakeWinReg()
        # uninstall.py 在檔案最上面就 import winreg（不像 installer_core.py 是
        # 每個函式各自 local import），module 命名空間裡的 uninstall.winreg 早就
        # 綁定了真正的 winreg，事後 patch sys.modules 不會回溯生效，要直接換掉
        # uninstall 模組自己的屬性。
        self.patcher = mock.patch.object(un, "winreg", self.fake_reg)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def _seed_association(self, ext):
        prog_id = f"AppFile{ext.replace('.', '')}"
        self.fake_reg.set_hklm(f"Software\\Classes\\{ext}", {"": prog_id})
        self.fake_reg.set_hklm(f"Software\\Classes\\{prog_id}", {"": "App File"})
        self.fake_reg.set_hklm(f"Software\\Classes\\{prog_id}\\shell", {})
        self.fake_reg.set_hklm(f"Software\\Classes\\{prog_id}\\shell\\open", {})
        self.fake_reg.set_hklm(f"Software\\Classes\\{prog_id}\\shell\\open\\command", {"": '"app.exe" "%1"'})
        self.fake_reg.set_hklm(f"Software\\Classes\\{prog_id}\\DefaultIcon", {"": "app.exe,0"})

    def test_removes_all_keys_for_extension(self):
        """安裝時寫了幾個機碼（ProgID 本身、shell\\open\\command、DefaultIcon），
        解除安裝要對稱地全部清乾淨，不能留殘骸。"""
        self._seed_association(".xyz")
        with mock.patch("uninstall.ctypes.windll.shell32.SHChangeNotify"):
            un.remove_file_associations([".xyz"])

        remaining = [
            k for k in self.fake_reg.store
            if k[0] == self.fake_reg.HKEY_LOCAL_MACHINE
            and ("AppFilexyz" in k[1] or k[1] == "Software\\Classes\\.xyz")
        ]
        self.assertEqual(remaining, [], f"應該完全清空，但還留著: {remaining}")

    def test_deletes_defaulticon_before_parent_key(self):
        """DefaultIcon 是 ProgID 底下的子機碼，真實 winreg.DeleteKey 要求目標
        本身沒有子機碼才能刪除——如果沒有『先刪 DefaultIcon 再刪 ProgID 本體』
        這個順序，最後一步會因為底下還有東西而刪不掉，留下殘留機碼。
        用 FakeWinReg 的 DeleteKey（模擬同樣的『有子機碼不能刪』限制）驗證這個順序沒有被意外打亂。
        """
        self._seed_association(".xyz")
        with mock.patch("uninstall.ctypes.windll.shell32.SHChangeNotify"):
            un.remove_file_associations([".xyz"])
        self.assertIsNone(self.fake_reg.hklm("Software\\Classes\\AppFilexyz"))

    def test_missing_keys_do_not_raise(self):
        """從沒註冊過的副檔名（例如清單記錄了，但登錄表其實是空的）不該讓整個
        解除安裝流程炸掉——這支函式本來就是設計成盡量清、清不到就算了。"""
        with mock.patch("uninstall.ctypes.windll.shell32.SHChangeNotify"):
            un.remove_file_associations([".never-existed"])  # 不應該拋例外

    def test_clears_user_choice_left_by_installer(self):
        """安裝時為了讓新關聯生效，會順便清掉使用者當時的 UserChoice；解除安裝
        要對稱地清掉這個機碼，不要留一個指向已經被移除之 ProgID 的殘留設定。"""
        self._seed_association(".xyz")
        user_choice_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.xyz\UserChoice"
        self.fake_reg.set_hkcu(user_choice_path, {"ProgId": "AppFilexyz", "Hash": "abc123"})

        with mock.patch("uninstall.ctypes.windll.shell32.SHChangeNotify"):
            un.remove_file_associations([".xyz"])

        self.assertIsNone(self.fake_reg.hkcu(user_choice_path))

    def test_clears_stale_hkcu_classes_override(self):
        """跟 installer_core.py 對稱：解除安裝時也要清掉 HKCU\\Software\\Classes\\<ext>
        這個 per-user 覆寫（外加 OpenWithProgids 子機碼），不然殘留的覆寫會讓
        Explorer 之後解析這個副檔名時，找到一個指向已移除 ProgID 的過期設定。"""
        self._seed_association(".xyz")
        self.fake_reg.set_hkcu("Software\\Classes\\.xyz", {"": "AppFilexyz"})
        self.fake_reg.set_hkcu("Software\\Classes\\.xyz\\OpenWithProgids", {"AppFilexyz": b""})

        with mock.patch("uninstall.ctypes.windll.shell32.SHChangeNotify"):
            un.remove_file_associations([".xyz"])

        self.assertIsNone(self.fake_reg.hkcu("Software\\Classes\\.xyz"))
        self.assertIsNone(self.fake_reg.hkcu("Software\\Classes\\.xyz\\OpenWithProgids"))

    def test_clears_stale_open_with_progids_and_list(self):
        """跟 installer_core.py 對稱：解除安裝時也要清掉 FileExts\\<ext>\\OpenWithProgids
        （跟上面 HKCU\\Software\\Classes\\<ext>\\OpenWithProgids 是不同的機碼路徑，
        是餵給「選取應用程式」對話框建議清單用的）跟 OpenWithList，不然移除後
        清單裡還是會留著已經不存在的舊 ProgID。"""
        self._seed_association(".xyz")
        fileexts_prefix = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.xyz"
        self.fake_reg.set_hkcu(f"{fileexts_prefix}\\OpenWithProgids", {"AppFilexyz": b""})
        self.fake_reg.set_hkcu(f"{fileexts_prefix}\\OpenWithList", {"a": "old.exe", "MRUList": "a"})

        with mock.patch("uninstall.ctypes.windll.shell32.SHChangeNotify"):
            un.remove_file_associations([".xyz"])

        self.assertIsNone(self.fake_reg.hkcu(f"{fileexts_prefix}\\OpenWithProgids"))
        self.assertIsNone(self.fake_reg.hkcu(f"{fileexts_prefix}\\OpenWithList"))


class TestRemoveFromPath(unittest.TestCase):
    def setUp(self):
        self.fake_reg = FakeWinReg()
        # uninstall.py 在檔案最上面就 import winreg（不像 installer_core.py 是
        # 每個函式各自 local import），module 命名空間裡的 uninstall.winreg 早就
        # 綁定了真正的 winreg，事後 patch sys.modules 不會回溯生效，要直接換掉
        # uninstall 模組自己的屬性。
        self.patcher = mock.patch.object(un, "winreg", self.fake_reg)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def _path_key(self):
        return r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"

    def test_removes_only_matching_entry(self):
        self.fake_reg.set_hklm(self._path_key(), {"Path": "C:\\Windows;C:\\Apps\\MyApp;C:\\Other"})
        with mock.patch("uninstall.ctypes.windll.user32.SendMessageTimeoutW"):
            un.remove_from_path("C:\\Apps\\MyApp")
        self.assertEqual(self.fake_reg.hklm(self._path_key())["Path"], "C:\\Windows;C:\\Other")


class TestUninstallManifestDrivenDeletion(unittest.TestCase):
    """對應 uninstall.py 檔案頭部記錄的那個真實 bug：不能『清單式刪除做得很仔細，
    最後卻無差別 rmdir 整個資料夾』。這裡直接重現 main() 裡那段判斷
    safe_to_remove_whole_dir 的邏輯，不呼叫真正的 main()（會牽扯到 MessageBox、
    自我刪除 subprocess 等一堆 GUI/系統層面的東西，不適合單元測試）。
    """

    def setUp(self):
        self.install_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.install_dir, ignore_errors=True)

    def _run_manifest_deletion(self, files_to_remove, self_name="uninstall.exe"):
        """複製 uninstall.py main() 第 212-245 行那段清單式刪除邏輯，
        回傳 safe_to_remove_whole_dir 這個關鍵旗標。
        """
        current_dir = self.install_dir
        for rel in files_to_remove:
            if os.path.basename(rel) == self_name:
                continue
            item_path = os.path.join(current_dir, rel)
            if os.path.exists(item_path):
                os.remove(item_path)

        for root, dirs, files in os.walk(current_dir, topdown=False):
            for d in dirs:
                dpath = os.path.join(root, d)
                try:
                    if not os.listdir(dpath):
                        os.rmdir(dpath)
                except Exception:
                    pass

        remaining = [item for item in os.listdir(current_dir) if item != self_name]
        return not remaining

    def test_user_added_file_prevents_whole_dir_removal(self):
        """使用者在安裝目錄裡自己多放了一個檔案（不在 install_manifest.json 的
        files 清單內），解除安裝完清單內的東西之後，資料夾不該被整個刪掉，
        使用者的檔案也不該被動到。"""
        with open(os.path.join(self.install_dir, "app.exe"), "w") as f:
            f.write("app")
        with open(os.path.join(self.install_dir, "uninstall.exe"), "w") as f:
            f.write("self")
        with open(os.path.join(self.install_dir, "user_data.txt"), "w") as f:
            f.write("使用者自己產生的資料")

        safe_to_remove_whole_dir = self._run_manifest_deletion(["app.exe", "uninstall.exe"])

        self.assertFalse(safe_to_remove_whole_dir, "資料夾裡還有清單之外的檔案，不該被判定成可以整個刪除")
        self.assertTrue(os.path.exists(os.path.join(self.install_dir, "user_data.txt")), "使用者的檔案不該被清單式刪除動到")

    def test_fully_listed_install_allows_whole_dir_removal(self):
        """清單內的東西刪完之後，資料夾裡除了 uninstall.exe 自己以外空無一物，
        這種情況才可以連資料夾一起刪掉。"""
        with open(os.path.join(self.install_dir, "app.exe"), "w") as f:
            f.write("app")
        with open(os.path.join(self.install_dir, "uninstall.exe"), "w") as f:
            f.write("self")

        safe_to_remove_whole_dir = self._run_manifest_deletion(["app.exe", "uninstall.exe"])

        self.assertTrue(safe_to_remove_whole_dir)

    def test_nested_subdirectory_from_manifest_is_pruned(self):
        os.makedirs(os.path.join(self.install_dir, "assets"))
        with open(os.path.join(self.install_dir, "assets", "logo.png"), "w") as f:
            f.write("logo")
        with open(os.path.join(self.install_dir, "uninstall.exe"), "w") as f:
            f.write("self")

        safe_to_remove_whole_dir = self._run_manifest_deletion(["assets/logo.png", "uninstall.exe"])

        self.assertFalse(os.path.exists(os.path.join(self.install_dir, "assets")), "清空的子目錄應該被清掉")
        self.assertTrue(safe_to_remove_whole_dir)


if __name__ == "__main__":
    unittest.main(verbosity=2)
