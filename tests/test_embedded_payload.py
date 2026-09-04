"""embedded_payload.py 的測試：安裝檔裡要放哪一份應用程式內容。

## 這個模組要防的是什麼

稽核 D1（見 `docs/investigations/MSIX稽核與缺陷修正.md`）：那個決定原本是
`builder.build_all()` 裡的一段 `if / elif / else`，而「設定檔裡的
`password_protected` 該寫什麼」是另外一行、在別的地方。兩者可以不一致，而
且真的不一致了——MSIX 引擎加上密碼保護時，加密那一條走不到，設定檔卻仍然
寫成有密碼保護。

這個模組把兩件事綁在同一個 `kind` 上：`password_protected` 由 `kind` 推導，
`kind` 決定內嵌什麼，因此「設定檔說有加密內容、實際上沒有」在結構上不可能
發生。這些測試釘住的就是那個綁定。
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import embedded_payload


class KindTest(unittest.TestCase):
    def test_the_traditional_engine_without_a_password_embeds_the_files(self):
        self.assertEqual(
            embedded_payload.kind_for("traditional", password_protected=False),
            embedded_payload.PLAIN)

    def test_the_traditional_engine_with_a_password_embeds_the_encrypted_file(self):
        self.assertEqual(
            embedded_payload.kind_for("traditional", password_protected=True),
            embedded_payload.ENCRYPTED)

    def test_the_msix_engine_embeds_the_package(self):
        self.assertEqual(
            embedded_payload.kind_for("msix", password_protected=False),
            embedded_payload.MSIX)

    def test_the_msix_engine_with_a_password_is_refused_not_silently_resolved(self):
        """稽核 D1 的組合。打包階段的驗證（install_engine）已經擋下它，這裡
        是第二道——`build_all()` 也可以被直接呼叫，而安靜地挑一邊正是原本
        產出壞安裝檔的方式。"""
        with self.assertRaises(embedded_payload.UnsupportedCombination):
            embedded_payload.kind_for("msix", password_protected=True)

    def test_the_refusal_says_which_two_settings_clash(self):
        try:
            embedded_payload.kind_for("msix", password_protected=True)
        except embedded_payload.UnsupportedCombination as e:
            message = str(e)
        self.assertIn("MSIX", message)
        self.assertIn("密碼", message)


class PasswordFlagTest(unittest.TestCase):
    """設定檔裡的 `password_protected` 只能由 kind 推導。"""

    def test_only_the_encrypted_kind_is_password_protected(self):
        self.assertTrue(embedded_payload.is_password_protected(embedded_payload.ENCRYPTED))
        self.assertFalse(embedded_payload.is_password_protected(embedded_payload.PLAIN))
        self.assertFalse(embedded_payload.is_password_protected(embedded_payload.MSIX))

    def test_the_flag_and_the_content_can_never_disagree(self):
        """D1 的核心不變式：設定檔說有加密內容時，實際內嵌的一定是加密檔。"""
        for engine in ("traditional", "msix"):
            for protected in (True, False):
                try:
                    kind = embedded_payload.kind_for(engine, protected)
                except embedded_payload.UnsupportedCombination:
                    continue
                if embedded_payload.is_password_protected(kind):
                    self.assertEqual(kind, embedded_payload.ENCRYPTED)


class MaterialiseTest(unittest.TestCase):
    def setUp(self):
        self.app_dir = tempfile.mkdtemp()
        self.workspace = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.app_dir, True)
        self.addCleanup(shutil.rmtree, self.workspace, True)
        with open(os.path.join(self.app_dir, "main.exe"), "wb") as f:
            f.write(b"app")

    def test_the_plain_kind_embeds_the_app_directory_under_app_contents(self):
        """對外契約：安裝端以 `app_contents` 這個名字取用（見
        installer_core._app_contents_dir()）。"""
        prepared = embedded_payload.materialise(
            embedded_payload.PLAIN, app_dir=self.app_dir,
            workspace_dir=self.workspace)
        self.assertEqual(prepared.add_data, f"{self.app_dir};app_contents")
        self.assertIsNone(prepared.temp_file)

    def test_the_msix_kind_embeds_the_package_at_the_root(self):
        prepared = embedded_payload.materialise(
            embedded_payload.MSIX, app_dir=self.app_dir,
            workspace_dir=self.workspace,
            embedded_msix="C:\\ws\\My.App.msix")
        self.assertEqual(prepared.add_data, "C:\\ws\\My.App.msix;.")
        self.assertIsNone(prepared.temp_file)

    def test_the_msix_kind_never_embeds_the_application_files_as_well(self):
        """檔案由系統從套件裡落地，另外帶一份等於同一批檔案在 exe 裡放兩次。"""
        prepared = embedded_payload.materialise(
            embedded_payload.MSIX, app_dir=self.app_dir,
            workspace_dir=self.workspace, embedded_msix="C:\\ws\\a.msix")
        self.assertNotIn("app_contents", prepared.add_data)

    def test_the_encrypted_kind_produces_the_encrypted_file_and_reports_it(self):
        calls = []

        def fake_encrypt(source, dest, password):
            calls.append((source, password))
            with open(dest, "wb") as f:
                f.write(b"cipher")

        prepared = embedded_payload.materialise(
            embedded_payload.ENCRYPTED, app_dir=self.app_dir,
            workspace_dir=self.workspace, password="hunter2",
            encrypt=fake_encrypt)
        expected = os.path.join(self.workspace, embedded_payload.ENCRYPTED_FILE_NAME)
        self.assertEqual(calls, [(self.app_dir, "hunter2")])
        self.assertEqual(prepared.add_data, f"{expected};.")
        self.assertEqual(prepared.temp_file, expected,
                         "加密檔沒有被回報為暫存產物，會留在工作目錄裡")
        self.assertTrue(os.path.exists(expected))

    def test_the_encrypted_kind_embeds_at_the_root_not_under_app_contents(self):
        """安裝端以 `app_contents.enc` 這個名字取用（見
        installer_core.verify_install_password()）。"""
        def write_something(_source, dest, _password):
            with open(dest, "wb") as f:
                f.write(b"x")

        prepared = embedded_payload.materialise(
            embedded_payload.ENCRYPTED, app_dir=self.app_dir,
            workspace_dir=self.workspace, password="pw",
            encrypt=write_something)
        self.assertTrue(prepared.add_data.endswith(";."))
        self.assertIn(embedded_payload.ENCRYPTED_FILE_NAME, prepared.add_data)

    def test_the_msix_kind_without_a_package_is_refused(self):
        with self.assertRaises(ValueError):
            embedded_payload.materialise(
                embedded_payload.MSIX, app_dir=self.app_dir,
                workspace_dir=self.workspace, embedded_msix="")

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            embedded_payload.materialise(
                "something_else", app_dir=self.app_dir,
                workspace_dir=self.workspace)


class TheNamesAreTheContractWithTheInstaller(unittest.TestCase):
    """兩個資源名稱是打包端與安裝端之間的契約。

    打包端以這兩個名字內嵌，安裝端以同樣的名字取用（`installer_core.py` 的
    `_app_contents_dir()` 與 `verify_install_password()`）。安裝端不匯入這個
    模組——為了兩個字串常數多帶一支模組進安裝檔不划算——因此改用靜態比對把
    兩邊釘在一起。漂移的症狀是安裝檔編得出來、裝的時候找不到內容。
    """

    def setUp(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(repo_root, "installer_core.py"),
                  encoding="utf-8") as f:
            self.installer_core = f.read()

    def test_the_app_contents_name_matches(self):
        self.assertIn(f'"{embedded_payload.APP_CONTENTS_DIR_NAME}"',
                      self.installer_core)

    def test_the_encrypted_file_name_matches(self):
        self.assertIn(f'"{embedded_payload.ENCRYPTED_FILE_NAME}"',
                      self.installer_core)


if __name__ == "__main__":
    unittest.main()
