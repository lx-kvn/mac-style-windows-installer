"""file_assoc.py 的測試——register()/unregister() 這個深模組本身。

這是 installer_core.py 的 _register_file_associations() 跟 uninstall.py 的
remove_file_associations() 收斂之後的共用實作：安裝寫了什麼、解除安裝就該
對稱地清掉什麼，現在是同一份程式碼，不再是兩個檔案裡兩份手動對齊的清單。

用 FakeWinReg（tests/_fakes.py）當 registry 參數注入，不需要 monkeypatch
sys.modules 或模組屬性——這就是收斂帶來的可測試性：呼叫端把假的登錄表當
一般參數傳進去即可。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _fakes import FakeWinReg
import file_assoc


class TestProgId(unittest.TestCase):
    def test_strips_dot_and_prefixes(self):
        self.assertEqual(file_assoc.prog_id(".xyz"), "AppFilexyz")


class TestRegister(unittest.TestCase):
    def setUp(self):
        self.reg = FakeWinReg()

    def test_writes_expected_registry_shape(self):
        with mock.patch("file_assoc.ctypes.windll.shell32.SHChangeNotify"):
            file_assoc.register(
                [".xyz"], "C:\\Apps\\MyApp\\MyApp.exe", "MyApp", {".xyz": "C:\\Apps\\MyApp\\MyApp.exe,0"},
                registry=self.reg,
            )

        self.assertEqual(self.reg.hklm("Software\\Classes\\.xyz")[""], "AppFilexyz")
        command = self.reg.hklm("Software\\Classes\\AppFilexyz\\shell\\open\\command")[""]
        self.assertIn("MyApp.exe", command)
        self.assertIn('"%1"', command)
        self.assertEqual(
            self.reg.hklm("Software\\Classes\\AppFilexyz\\DefaultIcon")[""],
            "C:\\Apps\\MyApp\\MyApp.exe,0",
        )

    def test_no_op_when_extensions_or_main_exe_missing(self):
        with mock.patch("file_assoc.ctypes.windll.shell32.SHChangeNotify") as notify:
            file_assoc.register([], "C:\\Apps\\MyApp\\MyApp.exe", "MyApp", {}, registry=self.reg)
            file_assoc.register([".xyz"], "", "MyApp", {}, registry=self.reg)
        notify.assert_not_called()
        self.assertEqual(self.reg.store, {})

    def test_registry_failure_propagates(self):
        """核心機碼寫入失敗不能被吞掉，要讓呼叫端（trigger_installation()）接住、
        決定要不要整個安裝失敗回滾。"""
        self.reg.fail_on_substring = ".xyz"
        with mock.patch("file_assoc.ctypes.windll.shell32.SHChangeNotify"):
            with self.assertRaises(PermissionError):
                file_assoc.register([".xyz"], "MyApp.exe", "MyApp", {".xyz": "MyApp.exe,0"}, registry=self.reg)
        self.assertIsNone(self.reg.hklm("Software\\Classes\\.xyz"), "確認登錄表真的沒寫成功")

    def test_clears_existing_user_choice_override(self):
        user_choice_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.xyz\UserChoice"
        self.reg.set_hkcu(user_choice_path, {"ProgId": "Notepad", "Hash": "abc123"})
        logged = []

        with mock.patch("file_assoc.ctypes.windll.shell32.SHChangeNotify"):
            file_assoc.register(
                [".xyz"], "MyApp.exe", "MyApp", {".xyz": "MyApp.exe,0"}, log=logged.append, registry=self.reg,
            )

        self.assertIsNone(self.reg.hkcu(user_choice_path))
        self.assertTrue(any(".xyz" in msg for msg in logged))

    def test_clears_stale_hkcu_classes_override(self):
        self.reg.set_hkcu("Software\\Classes\\.xyz", {"": "SomeOldApp.xyzfile"})
        self.reg.set_hkcu("Software\\Classes\\.xyz\\OpenWithProgids", {"SomeOldApp.xyzfile": b""})

        with mock.patch("file_assoc.ctypes.windll.shell32.SHChangeNotify"):
            file_assoc.register([".xyz"], "MyApp.exe", "MyApp", {".xyz": "MyApp.exe,0"}, registry=self.reg)

        self.assertIsNone(self.reg.hkcu("Software\\Classes\\.xyz"), "HKCU 的殘留覆寫應該被清掉")
        self.assertIsNone(self.reg.hkcu("Software\\Classes\\.xyz\\OpenWithProgids"))
        self.assertEqual(self.reg.hklm("Software\\Classes\\.xyz")[""], "AppFilexyz", "HKLM 那份不能被誤刪")

    def test_clears_stale_open_with_progids_and_list(self):
        fileexts_prefix = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.xyz"
        self.reg.set_hkcu(
            f"{fileexts_prefix}\\OpenWithProgids",
            {"FileLockerApp.locked": b"", "FileLockerApp.lockedfile": b"", "AppFilexyz": b""},
        )
        self.reg.set_hkcu(f"{fileexts_prefix}\\OpenWithList", {"a": "old.exe", "MRUList": "a"})

        with mock.patch("file_assoc.ctypes.windll.shell32.SHChangeNotify"):
            file_assoc.register([".xyz"], "MyApp.exe", "MyApp", {".xyz": "MyApp.exe,0"}, registry=self.reg)

        self.assertIsNone(self.reg.hkcu(f"{fileexts_prefix}\\OpenWithProgids"))
        self.assertIsNone(self.reg.hkcu(f"{fileexts_prefix}\\OpenWithList"))

    def test_missing_user_choice_does_not_raise(self):
        """最常見的情況：這個副檔名從沒被手動選過，殘留機碼根本不存在，
        清除動作本來就該是「盡量做」，不存在就跳過，不能讓整個關聯因此失敗。"""
        with mock.patch("file_assoc.ctypes.windll.shell32.SHChangeNotify"):
            file_assoc.register([".xyz"], "MyApp.exe", "MyApp", {".xyz": "MyApp.exe,0"}, registry=self.reg)


class TestRegisterNoAdminInstall(unittest.TestCase):
    """真實抓到的問題：register()/unregister() 原本完全沒有 no_admin_install
    的概念，四個寫入點一律硬寫 HKEY_LOCAL_MACHINE——no_admin_install=True
    時整個安裝流程刻意不要求提權，但這裡仍然嘗試寫 HKLM\\Software\\Classes，
    一般使用者帳號寫不進去，installer_core.py 不會吞掉這個例外（核心機碼
    寫入失敗要讓呼叫端接住決定是否整個安裝回滾），導致「只要同時勾選
    no_admin_install 跟檔案關聯，安裝一定失敗」。改成接上跟 system_entries.py
    同一種 InstallScope seam：no_admin_install=True 時改寫 HKCU（Windows
    的 HKEY_CLASSES_ROOT 合併規則本來就會納入 HKCU\\Software\\Classes，
    效果對等，且不需要任何權限）。"""

    def setUp(self):
        self.reg = FakeWinReg()

    def test_no_admin_install_writes_to_hkcu_instead_of_hklm(self):
        with mock.patch("file_assoc.ctypes.windll.shell32.SHChangeNotify"):
            file_assoc.register(
                [".xyz"], "C:\\Apps\\MyApp\\MyApp.exe", "MyApp", {".xyz": "C:\\Apps\\MyApp\\MyApp.exe,0"},
                registry=self.reg, no_admin_install=True,
            )
        self.assertEqual(self.reg.hkcu("Software\\Classes\\.xyz")[""], "AppFilexyz")
        self.assertIsNone(self.reg.hklm("Software\\Classes\\.xyz"), "no_admin_install 開啟時不應該寫入 HKLM")
        self.assertEqual(
            self.reg.hkcu("Software\\Classes\\AppFilexyz\\DefaultIcon")[""],
            "C:\\Apps\\MyApp\\MyApp.exe,0",
        )

    def test_default_still_writes_to_hklm(self):
        """對照組：不帶 no_admin_install（或明確 False）維持原本行為，
        不能因為新增這個參數就連預設情境都改掉。"""
        with mock.patch("file_assoc.ctypes.windll.shell32.SHChangeNotify"):
            file_assoc.register([".xyz"], "MyApp.exe", "MyApp", {".xyz": "MyApp.exe,0"}, registry=self.reg)
        self.assertEqual(self.reg.hklm("Software\\Classes\\.xyz")[""], "AppFilexyz")


class TestUnregister(unittest.TestCase):
    def setUp(self):
        self.reg = FakeWinReg()

    def _seed_association(self, ext):
        pid = file_assoc.prog_id(ext)
        self.reg.set_hklm(f"Software\\Classes\\{ext}", {"": pid})
        self.reg.set_hklm(f"Software\\Classes\\{pid}", {"": "App File"})
        self.reg.set_hklm(f"Software\\Classes\\{pid}\\shell", {})
        self.reg.set_hklm(f"Software\\Classes\\{pid}\\shell\\open", {})
        self.reg.set_hklm(f"Software\\Classes\\{pid}\\shell\\open\\command", {"": '"app.exe" "%1"'})
        self.reg.set_hklm(f"Software\\Classes\\{pid}\\DefaultIcon", {"": "app.exe,0"})

    def test_removes_all_keys_for_extension(self):
        self._seed_association(".xyz")
        with mock.patch("file_assoc.ctypes.windll.shell32.SHChangeNotify"):
            file_assoc.unregister([".xyz"], registry=self.reg)

        remaining = [
            k for k in self.reg.store
            if k[0] == self.reg.HKEY_LOCAL_MACHINE
            and ("AppFilexyz" in k[1] or k[1] == "Software\\Classes\\.xyz")
        ]
        self.assertEqual(remaining, [], f"應該完全清空，但還留著: {remaining}")

    def test_deletes_defaulticon_before_parent_key(self):
        """DefaultIcon 是 ProgID 底下的子機碼，真實 winreg.DeleteKey 要求目標本身
        沒有子機碼才能刪除——沒有『先刪子機碼再刪本體』的順序，最後一步會因為
        底下還有東西而刪不掉，留下殘留機碼。FakeWinReg 的 DeleteKey 模擬同樣的
        限制，用來驗證這個順序沒有被意外打亂。"""
        self._seed_association(".xyz")
        with mock.patch("file_assoc.ctypes.windll.shell32.SHChangeNotify"):
            file_assoc.unregister([".xyz"], registry=self.reg)
        self.assertIsNone(self.reg.hklm("Software\\Classes\\AppFilexyz"))

    def test_missing_keys_do_not_raise(self):
        with mock.patch("file_assoc.ctypes.windll.shell32.SHChangeNotify"):
            file_assoc.unregister([".never-existed"], registry=self.reg)  # 不應該拋例外

    def test_returns_true_after_removing_all_keys(self):
        """F02：這個函式原本對每一次 DeleteKey 都 try/except: pass 且不回傳
        任何值，呼叫端（uninstall.py 的檔案關聯移除步驟）因此無條件記錄成功。
        改成回傳布林值，語義跟 system_entries 的兩個移除原語一致。"""
        self._seed_association(".xyz")
        with mock.patch("file_assoc.ctypes.windll.shell32.SHChangeNotify"):
            self.assertTrue(file_assoc.unregister([".xyz"], registry=self.reg))

    def test_returns_true_when_keys_already_absent(self):
        """依 F04 定下的語義：機碼本來就不存在（DeleteKey 拋 FileNotFoundError）
        代表結束後目標確實不存在，算成功，不是失敗。"""
        with mock.patch("file_assoc.ctypes.windll.shell32.SHChangeNotify"):
            self.assertTrue(file_assoc.unregister([".never-existed"], registry=self.reg))

    def test_returns_false_when_delete_fails_for_other_reason(self):
        """FileNotFoundError 以外的例外（權限不足、底下還有子機碼刪不掉）
        代表機碼還留在登錄表裡，這才是真正該回報給使用者的失敗。"""
        self._seed_association(".xyz")
        with mock.patch("file_assoc.ctypes.windll.shell32.SHChangeNotify"), \
             mock.patch.object(self.reg, "DeleteKey", side_effect=PermissionError("模擬權限不足")):
            self.assertFalse(file_assoc.unregister([".xyz"], registry=self.reg))

    def _seed_association_in_hkcu(self, ext):
        pid = file_assoc.prog_id(ext)
        self.reg.set_hkcu(f"Software\\Classes\\{ext}", {"": pid})
        self.reg.set_hkcu(f"Software\\Classes\\{pid}", {"": "App File"})
        self.reg.set_hkcu(f"Software\\Classes\\{pid}\\shell", {})
        self.reg.set_hkcu(f"Software\\Classes\\{pid}\\shell\\open", {})
        self.reg.set_hkcu(f"Software\\Classes\\{pid}\\shell\\open\\command", {"": '"app.exe" "%1"'})
        self.reg.set_hkcu(f"Software\\Classes\\{pid}\\DefaultIcon", {"": "app.exe,0"})

    def test_falls_back_to_the_other_hive_when_the_association_is_there_instead(self):
        """F12：跟 system_entries.remove_registry_entry() 的雙 hive 探測
        同一個理由——manifest 裡的 no_admin_install 可能跟當初實際安裝時
        用的模式不符，關聯實際寫在另一個 hive 時完全找不到，殘留永遠留著。
        """
        self._seed_association_in_hkcu(".xyz")
        with mock.patch("file_assoc.ctypes.windll.shell32.SHChangeNotify"):
            # manifest 說是需要提權的安裝（關聯應該寫在 HKLM），
            # 但實際上寫在 HKCU。
            result = file_assoc.unregister([".xyz"], registry=self.reg, no_admin_install=False)

        self.assertTrue(result)
        remaining = [
            k for k in self.reg.store
            if "AppFilexyz" in k[1] or k[1] == "Software\\Classes\\.xyz"
        ]
        self.assertEqual(remaining, [], f"另一個 hive 的關聯也應該被清掉，但還留著: {remaining}")

    def test_does_not_delete_an_extension_key_that_points_at_another_app(self):
        """擴大到兩個 hive 之後多出來的風險：`Software\\Classes\\<ext>` 這個
        機碼本身不是「我們專屬」的——它的預設值指向某個 ProgID，那個 ProgID
        隨時可能已經是另一個應用程式的（使用者事後改用別的程式開啟這個
        副檔名）。ProgID 機碼本身（`AppFile<ext>`，見 prog_id() 的命名慣例）
        確定是我們寫的，可以直接刪；副檔名機碼只有在它確實指著我們的
        ProgID 時才刪。
        """
        self._seed_association(".xyz")
        # 使用者事後把 .xyz 改成用另一個應用程式開啟
        self.reg.set_hklm("Software\\Classes\\.xyz", {"": "SomeOtherApp.xyzfile"})

        with mock.patch("file_assoc.ctypes.windll.shell32.SHChangeNotify"):
            result = file_assoc.unregister([".xyz"], registry=self.reg)

        self.assertTrue(result)
        self.assertIsNotNone(
            self.reg.hklm("Software\\Classes\\.xyz"),
            "指向另一個應用程式的副檔名機碼不該被我們的解除安裝清掉",
        )
        self.assertEqual(self.reg.hklm("Software\\Classes\\.xyz")[""], "SomeOtherApp.xyzfile")
        self.assertIsNone(
            self.reg.hklm("Software\\Classes\\AppFilexyz"),
            "我們自己的 ProgID 機碼仍然要清掉",
        )

    def test_does_not_touch_user_choice_on_uninstall(self):
        """真實抓到的問題：unregister() 原本呼叫跟 register() 同一個
        _clear_stale_user_associations()——那個函式存在的理由是「讓剛
        寫入的新關聯在 HKCU 覆寫層級之下也生效」，解除安裝當下沒有要
        寫入任何新關聯，這組清除完全沒有正當理由執行。UserChoice 隨時
        可能已經是使用者事後手動改選的其他應用程式，解除安裝我們自己的
        應用程式不應該連帶清掉使用者事後的選擇。"""
        self._seed_association(".xyz")
        user_choice_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.xyz\UserChoice"
        self.reg.set_hkcu(user_choice_path, {"ProgId": "SomeOtherApp.xyzfile", "Hash": "abc123"})

        with mock.patch("file_assoc.ctypes.windll.shell32.SHChangeNotify"):
            file_assoc.unregister([".xyz"], registry=self.reg)

        self.assertIsNotNone(self.reg.hkcu(user_choice_path), "使用者事後的選擇不該被解除安裝清掉")

    def test_does_not_touch_hkcu_classes_override_on_uninstall(self):
        """同上一個測試的理由：這個 HKCU 覆寫隨時可能已經是另一個完全
        無關的應用程式寫入的，解除安裝我們自己的應用程式不該連帶清掉它。
        """
        self._seed_association(".xyz")
        self.reg.set_hkcu("Software\\Classes\\.xyz", {"": "SomeOtherApp.xyzfile"})
        self.reg.set_hkcu("Software\\Classes\\.xyz\\OpenWithProgids", {"SomeOtherApp.xyzfile": b""})

        with mock.patch("file_assoc.ctypes.windll.shell32.SHChangeNotify"):
            file_assoc.unregister([".xyz"], registry=self.reg)

        self.assertIsNotNone(self.reg.hkcu("Software\\Classes\\.xyz"), "可能屬於另一個應用程式的覆寫不該被清掉")
        self.assertIsNotNone(self.reg.hkcu("Software\\Classes\\.xyz\\OpenWithProgids"))

    def test_does_not_touch_open_with_progids_and_list_on_uninstall(self):
        self._seed_association(".xyz")
        fileexts_prefix = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.xyz"
        self.reg.set_hkcu(f"{fileexts_prefix}\\OpenWithProgids", {"SomeOtherApp.xyzfile": b""})
        self.reg.set_hkcu(f"{fileexts_prefix}\\OpenWithList", {"a": "old.exe", "MRUList": "a"})

        with mock.patch("file_assoc.ctypes.windll.shell32.SHChangeNotify"):
            file_assoc.unregister([".xyz"], registry=self.reg)

        self.assertIsNotNone(self.reg.hkcu(f"{fileexts_prefix}\\OpenWithProgids"))
        self.assertIsNotNone(self.reg.hkcu(f"{fileexts_prefix}\\OpenWithList"))


class TestUnregisterNoAdminInstall(unittest.TestCase):
    def setUp(self):
        self.reg = FakeWinReg()

    def _seed_hkcu_association(self, ext):
        pid = file_assoc.prog_id(ext)
        self.reg.set_hkcu(f"Software\\Classes\\{ext}", {"": pid})
        self.reg.set_hkcu(f"Software\\Classes\\{pid}", {"": "App File"})
        self.reg.set_hkcu(f"Software\\Classes\\{pid}\\shell", {})
        self.reg.set_hkcu(f"Software\\Classes\\{pid}\\shell\\open", {})
        self.reg.set_hkcu(f"Software\\Classes\\{pid}\\shell\\open\\command", {"": '"app.exe" "%1"'})
        self.reg.set_hkcu(f"Software\\Classes\\{pid}\\DefaultIcon", {"": "app.exe,0"})

    def test_no_admin_install_removes_from_hkcu_not_hklm(self):
        self._seed_hkcu_association(".xyz")
        with mock.patch("file_assoc.ctypes.windll.shell32.SHChangeNotify"):
            file_assoc.unregister([".xyz"], registry=self.reg, no_admin_install=True)
        self.assertIsNone(self.reg.hkcu("Software\\Classes\\.xyz"))
        self.assertIsNone(self.reg.hkcu("Software\\Classes\\AppFilexyz"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
