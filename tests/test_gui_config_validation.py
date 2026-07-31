"""gui_config.py 的 validate_and_build_pack_data() 測試。

這是從 ConfigAPI.start_pack() 抽出來的純函式：不碰 threading、不呼叫
check_build_environment()/ensure_workspace_files() 這類有外部副作用的檢查，
純粹是「表單資料 -> (pack_data, error)」的轉換，可以直接單元測試每一條
驗證分支，不需要啟動背景執行緒或等待非同步結果。
"""
import os
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui_config import validate_and_build_pack_data


class TestValidateAndBuildPackData(unittest.TestCase):
    def setUp(self):
        self.app_dir = tempfile.mkdtemp()
        with open(os.path.join(self.app_dir, "main.exe"), "wb") as f:
            f.write(b"fake")

    def tearDown(self):
        shutil.rmtree(self.app_dir, ignore_errors=True)

    def _base_data(self, **overrides):
        data = {
            "app_name": "TestApp",
            "folder_name": "",
            "version": "1.0.0",
            "publisher": "Tester",
            "exe_name": "Setup_TestApp",
            "main_exe": "main.exe",
            "eula_texts": {},
            "eula_default_lang": "",
            "dependencies": [],
            "file_associations": "",
            "need_file_assoc": False,
            "use_custom_doc_icon": False,
            "add_to_path": False,
            "path_target_exe": "",
            "restart_explorer_on_update": False,
        }
        data.update(overrides)
        return data

    def _validate(self, data, png_path="fake.png", ico_path="fake.ico", doc_icon_path_selected=""):
        return validate_and_build_pack_data(data, self.app_dir, png_path, ico_path, doc_icon_path_selected)

    def test_success_path_returns_pack_data_with_no_error(self):
        pack_data, error = self._validate(self._base_data())
        self.assertIsNone(error)
        self.assertEqual(pack_data["app_name"], "TestApp")
        self.assertEqual(pack_data["folder_name"], "TestApp", "folder_name 留空時要 fallback 成 app_name")
        self.assertEqual(pack_data["file_associations"], [])
        self.assertFalse(pack_data["restart_explorer_on_update"])

    def test_restart_explorer_on_update_passes_through(self):
        pack_data, error = self._validate(self._base_data(restart_explorer_on_update=True))
        self.assertIsNone(error)
        self.assertTrue(pack_data["restart_explorer_on_update"])

    def test_eula_texts_pass_through_with_trimmed_empty_entries_dropped(self):
        pack_data, error = self._validate(self._base_data(
            eula_texts={"zh-TW": "  合約全文  ", "en": "   ", "ja-JP": ""},
            eula_default_lang="zh-TW",
        ))
        self.assertIsNone(error)
        self.assertEqual(pack_data["eula_texts"], {"zh-TW": "合約全文"}, "空白/空字串的語言版本應該被丟棄")
        self.assertEqual(pack_data["eula_default_lang"], "zh-TW")

    def test_eula_default_lang_not_among_provided_languages_is_rejected(self):
        _, error = self._validate(self._base_data(
            eula_texts={"zh-TW": "合約全文"}, eula_default_lang="en",
        ))
        self.assertIsNotNone(error, "預設/回退語言不在已提供的 EULA 語言清單裡，應該擋下來")

    def test_empty_eula_texts_does_not_require_default_lang(self):
        pack_data, error = self._validate(self._base_data(eula_texts={}, eula_default_lang=""))
        self.assertIsNone(error)
        self.assertEqual(pack_data["eula_texts"], {})

    def test_missing_required_text_field_is_rejected(self):
        _, error = self._validate(self._base_data(publisher=""))
        self.assertIsNotNone(error)
        self.assertIn("必填", error)

    def test_need_file_assoc_checked_but_empty_is_rejected(self):
        _, error = self._validate(self._base_data(need_file_assoc=True, file_associations=""))
        self.assertIsNotNone(error)
        self.assertIn("副檔名", error)

    def test_invalid_app_dir_is_rejected(self):
        data = self._base_data()
        pack_data, error = validate_and_build_pack_data(data, "C:\\does\\not\\exist", "fake.png", "fake.ico", "")
        self.assertIsNotNone(error)
        self.assertIn("應用程式內容資料夾", error)

    def test_png_path_wrong_extension_is_rejected(self):
        _, error = self._validate(self._base_data(), png_path="fake.jpg")
        self.assertIsNotNone(error)
        self.assertIn("PNG", error)

    def test_ico_path_wrong_extension_is_rejected(self):
        _, error = self._validate(self._base_data(), ico_path="fake.png")
        self.assertIsNotNone(error)
        self.assertIn("ICO", error)

    def test_main_exe_not_found_in_app_dir_is_rejected(self):
        _, error = self._validate(self._base_data(main_exe="missing.exe"))
        self.assertIsNotNone(error)
        self.assertIn("不存在", error)

    def test_path_target_exe_not_found_in_app_dir_is_rejected(self):
        """backlog #1：「加入 PATH」指定的執行檔如果不在應用程式資料夾裡，
        要擋下來，不能讓打包出去的 installer_config.json 記一個不存在的路徑。"""
        _, error = self._validate(self._base_data(add_to_path=True, path_target_exe="missing_cli.exe"))
        self.assertIsNotNone(error)

    def test_path_target_exe_passes_through_when_add_to_path_enabled(self):
        with open(os.path.join(self.app_dir, "cli.exe"), "wb") as f:
            f.write(b"fake")
        pack_data, error = self._validate(self._base_data(add_to_path=True, path_target_exe="cli.exe"))
        self.assertIsNone(error)
        self.assertEqual(pack_data["path_target_exe"], "cli.exe")

    def test_path_target_exe_cleared_when_add_to_path_disabled(self):
        """add_to_path 沒勾選時，就算欄位裡殘留了值也不該送出去，
        避免使用者取消勾選後、後端仍誤用上一次殘留的設定。"""
        with open(os.path.join(self.app_dir, "cli.exe"), "wb") as f:
            f.write(b"fake")
        pack_data, error = self._validate(self._base_data(add_to_path=False, path_target_exe="cli.exe"))
        self.assertIsNone(error)
        self.assertEqual(pack_data["path_target_exe"], "")

    def test_custom_doc_icon_checked_but_not_selected_is_rejected(self):
        _, error = self._validate(
            self._base_data(use_custom_doc_icon=True), doc_icon_path_selected="",
        )
        self.assertIsNotNone(error)
        self.assertIn("文件圖示", error)

    def test_custom_doc_icon_checked_and_selected_is_accepted(self):
        pack_data, error = self._validate(
            self._base_data(use_custom_doc_icon=True), doc_icon_path_selected="custom.ico",
        )
        self.assertIsNone(error)
        self.assertEqual(pack_data["doc_icon_path"], "custom.ico")

    def test_empty_app_dir_is_rejected(self):
        """驗證順序上，main_exe 是否存在的檢查排在「資料夾是否為空」之前，
        所以只要 main_exe 找不到（空資料夾一定找不到），錯誤訊息會是
        「主要執行檔不存在」，不會走到「資料夾是空的」那條分支——
        這是原本 start_pack() 就有的驗證順序，這裡原封不動保留，只是換了個
        地方測。"""
        empty_dir = tempfile.mkdtemp()
        try:
            data = self._base_data()
            _, error = validate_and_build_pack_data(data, empty_dir, "fake.png", "fake.ico", "")
            self.assertIsNotNone(error)
            self.assertIn("不存在", error)
        finally:
            shutil.rmtree(empty_dir, ignore_errors=True)

    def test_extension_csv_parsing(self):
        pack_data, error = self._validate(
            self._base_data(need_file_assoc=True, file_associations="txt, .abc,,  xyz")
        )
        self.assertIsNone(error)
        self.assertEqual(pack_data["file_associations"], [".txt", ".abc", ".xyz"])

    def test_does_not_have_workspace_dir_key(self):
        """workspace_dir 是 start_pack() 呼叫 ensure_workspace_files() 之後才加進去的
        （那一步有真的複製檔案的副作用，刻意留在純函式外面），這裡確認沒有洩漏進來。"""
        pack_data, _ = self._validate(self._base_data())
        self.assertNotIn("workspace_dir", pack_data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
