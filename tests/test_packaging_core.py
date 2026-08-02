"""packaging_core.py 的測試（原本分散在 test_workspace_files.py 跟
test_gui_config_validation.py 裡測 gui_config.py 對應函式，這幾個函式已經
搬到不依賴 pywebview 的 packaging_core.py——見該檔案開頭的拆分紀錄，這裡
純粹是搬移 + import 路徑更新，斷言邏輯不變。

get_workspace_dir()/ensure_workspace_files()：這裡測的是規格文件 §4 記錄
過的真實 bug 重現場景：installer_core.py / uninstall.py / ui/index.html
這幾個「內部實作檔案」每次都要無條件覆蓋（不然重複用同一個工作目錄重新
打包新版時，舊版本永遠換不掉、後續修正都不會生效），而 ui/ 底下其他
「使用者可能自訂過的靜態資源」（例如 folder_icon.png）要維持「只在缺少
時才補」，不能覆蓋使用者的客製化。這兩條規則刻意設計成相反的行為，
最容易在修改時不小心弄反，值得專門測試鎖住。

validate_and_build_pack_data()：從 GUI 的 start_pack() 抽出來的純函式，
CLI 的 pack 子指令也共用同一份——不碰 threading、不呼叫
check_build_environment()/ensure_workspace_files() 這類有外部副作用的
檢查，純粹是「表單/JSON 資料 -> (pack_data, error)」的轉換，可以直接
單元測試每一條驗證分支。
"""
import os
import sys
import shutil
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import packaging_core


class TestGetWorkspaceDir(unittest.TestCase):
    def test_non_frozen_uses_cwd(self):
        with mock.patch.object(sys, "_MEIPASS", "C:\\fake\\meipass", create=True):
            pass  # 只是確保下面刪除時不會因為屬性本來就不存在而出錯
        if hasattr(sys, "_MEIPASS"):
            del sys._MEIPASS
        self.assertEqual(packaging_core.get_workspace_dir(), os.path.abspath("."))

    def test_frozen_uses_exe_directory(self):
        with mock.patch.object(sys, "_MEIPASS", "C:\\fake\\meipass", create=True), \
             mock.patch.object(sys, "executable", "C:\\Users\\Test\\InstallerBuilder.exe"):
            self.assertEqual(packaging_core.get_workspace_dir(), "C:\\Users\\Test")


class TestEnsureWorkspaceFiles(unittest.TestCase):
    def setUp(self):
        self.embedded_dir = tempfile.mkdtemp()
        self.workspace_dir = tempfile.mkdtemp()

        with open(os.path.join(self.embedded_dir, "installer_core.py"), "w") as f:
            f.write("# NEW installer_core content")
        with open(os.path.join(self.embedded_dir, "uninstall.py"), "w") as f:
            f.write("# NEW uninstall content")
        with open(os.path.join(self.embedded_dir, "window_drag.py"), "w") as f:
            f.write("# NEW window_drag content")
        with open(os.path.join(self.embedded_dir, "disk_space.py"), "w") as f:
            f.write("# NEW disk_space content")
        with open(os.path.join(self.embedded_dir, "file_assoc.py"), "w") as f:
            f.write("# NEW file_assoc content")
        with open(os.path.join(self.embedded_dir, "lang_detect.py"), "w") as f:
            f.write("# NEW lang_detect content")
        with open(os.path.join(self.embedded_dir, "restart_manager.py"), "w") as f:
            f.write("# NEW restart_manager content")
        os.makedirs(os.path.join(self.embedded_dir, "ui"))
        with open(os.path.join(self.embedded_dir, "ui", "index.html"), "w") as f:
            f.write("<!-- NEW index.html -->")
        with open(os.path.join(self.embedded_dir, "ui", "folder_icon.png"), "wb") as f:
            f.write(b"NEW_ICON_BYTES")

        def fake_get_resource_path(relative_path):
            return os.path.join(self.embedded_dir, relative_path)

        self.patcher_meipass = mock.patch.object(sys, "_MEIPASS", "C:\\fake\\meipass", create=True)
        self.patcher_meipass.start()
        self.patcher_resource = mock.patch.object(
            packaging_core, "get_resource_path", side_effect=fake_get_resource_path
        )
        self.patcher_resource.start()

    def tearDown(self):
        self.patcher_resource.stop()
        self.patcher_meipass.stop()
        shutil.rmtree(self.embedded_dir, ignore_errors=True)
        shutil.rmtree(self.workspace_dir, ignore_errors=True)

    def test_non_frozen_mode_does_nothing(self):
        """.py 直接執行（沒有 _MEIPASS）時，工作目錄就是原始碼目錄，不需要複製任何東西。"""
        self.patcher_meipass.stop()
        try:
            result = packaging_core.ensure_workspace_files(self.workspace_dir)
            self.assertIsNone(result)
            self.assertEqual(os.listdir(self.workspace_dir), [])
        finally:
            self.patcher_meipass.start()

    def test_core_scripts_are_always_overwritten(self):
        """installer_core.py / uninstall.py 是內部實作，即使工作目錄裡已經有舊版本，
        也必須無條件被目前這顆 exe 內嵌的新版本蓋掉——這正是規格文件記錄過的真實 bug：
        『只在缺少時才複製』會導致重複用同一個工作目錄打包新版時，舊版本永遠換不掉。
        """
        with open(os.path.join(self.workspace_dir, "installer_core.py"), "w") as f:
            f.write("# STALE old content that must be replaced")

        result = packaging_core.ensure_workspace_files(self.workspace_dir)

        self.assertIsNone(result)
        with open(os.path.join(self.workspace_dir, "installer_core.py")) as f:
            self.assertEqual(f.read(), "# NEW installer_core content")

    def test_shared_deep_modules_are_always_overwritten(self):
        """window_drag.py / disk_space.py / file_assoc.py / lang_detect.py /
        restart_manager.py 是 installer_core.py 跟 uninstall.py 匯入的共用深
        模組，跟 installer_core.py/uninstall.py 本身一樣是內部實作，必須無
        條件覆蓋，理由相同：漏了任何一個沒同步更新，重新編譯出來的 exe
        用的還是這個共用模組的舊版本。"""
        for name in ("window_drag.py", "disk_space.py", "file_assoc.py", "lang_detect.py", "restart_manager.py"):
            with open(os.path.join(self.workspace_dir, name), "w") as f:
                f.write(f"# STALE old {name} content")

        packaging_core.ensure_workspace_files(self.workspace_dir)

        for name in ("window_drag.py", "disk_space.py", "file_assoc.py", "lang_detect.py", "restart_manager.py"):
            with open(os.path.join(self.workspace_dir, name)) as f:
                self.assertEqual(f.read(), f"# NEW {name.replace('.py', '')} content")

    def test_index_html_is_always_overwritten(self):
        """ui/index.html 也是內部實作（安裝端介面），不是使用者自訂項目，同樣要無條件覆蓋。"""
        os.makedirs(os.path.join(self.workspace_dir, "ui"))
        with open(os.path.join(self.workspace_dir, "ui", "index.html"), "w") as f:
            f.write("<!-- STALE old index.html -->")

        packaging_core.ensure_workspace_files(self.workspace_dir)

        with open(os.path.join(self.workspace_dir, "ui", "index.html")) as f:
            self.assertEqual(f.read(), "<!-- NEW index.html -->")

    def test_user_customized_static_asset_is_preserved(self):
        """folder_icon.png 這類使用者可能自訂過的靜態資源，行為要跟上面兩個相反：
        已經存在的話絕對不能覆蓋，否則使用者換掉的自訂圖示會被每次重新編譯無聲蓋掉。
        """
        os.makedirs(os.path.join(self.workspace_dir, "ui"))
        with open(os.path.join(self.workspace_dir, "ui", "folder_icon.png"), "wb") as f:
            f.write(b"USER_CUSTOM_ICON_BYTES")

        packaging_core.ensure_workspace_files(self.workspace_dir)

        with open(os.path.join(self.workspace_dir, "ui", "folder_icon.png"), "rb") as f:
            self.assertEqual(f.read(), b"USER_CUSTOM_ICON_BYTES")

    def test_missing_static_asset_gets_copied_in(self):
        """使用者還沒自訂過（工作目錄裡根本沒有這個檔案）時，要從內嵌資源補上，
        不然編譯出來的安裝檔會缺這個靜態資源（規格文件記錄過的另一個真實 bug：
        安裝視窗右側資料夾圖示消失）。"""
        packaging_core.ensure_workspace_files(self.workspace_dir)

        dest = os.path.join(self.workspace_dir, "ui", "folder_icon.png")
        self.assertTrue(os.path.exists(dest))
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), b"NEW_ICON_BYTES")

    def test_copy_failure_returns_readable_error_message(self):
        with mock.patch.object(shutil, "copy2", side_effect=PermissionError("拒絕存取")):
            result = packaging_core.ensure_workspace_files(self.workspace_dir)
        self.assertIsNotNone(result)
        self.assertIn("寫入權限", result)


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
        return packaging_core.validate_and_build_pack_data(data, self.app_dir, png_path, ico_path, doc_icon_path_selected)

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
        pack_data, error = packaging_core.validate_and_build_pack_data(data, "C:\\does\\not\\exist", "fake.png", "fake.ico", "")
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

    def test_local_appdata_files_missing_from_app_dir_is_rejected(self):
        _, error = self._validate(self._base_data(local_appdata_files=["missing_cli.exe"]))
        self.assertIsNotNone(error)
        self.assertIn("不存在", error)

    def test_local_appdata_files_list_passes_through(self):
        with open(os.path.join(self.app_dir, "cli.exe"), "wb") as f:
            f.write(b"fake")
        pack_data, error = self._validate(self._base_data(local_appdata_files=["cli.exe"]))
        self.assertIsNone(error)
        self.assertEqual(pack_data["local_appdata_files"], ["cli.exe"])

    def test_local_appdata_files_accepts_comma_separated_string(self):
        """CLI/GUI 兩邊都可能送一個逗號分隔的原始字串，不是 JSON 裡現成的 list
        （比照 file_associations 的處理方式）。"""
        with open(os.path.join(self.app_dir, "cli.exe"), "wb") as f:
            f.write(b"fake")
        pack_data, error = self._validate(self._base_data(local_appdata_files="cli.exe, "))
        self.assertIsNone(error)
        self.assertEqual(pack_data["local_appdata_files"], ["cli.exe"])

    def test_local_appdata_files_defaults_to_empty_list(self):
        pack_data, error = self._validate(self._base_data())
        self.assertIsNone(error)
        self.assertEqual(pack_data["local_appdata_files"], [])

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

    def test_doc_icons_per_extension_passes_through(self):
        pack_data, error = self._validate(self._base_data(
            need_file_assoc=True, file_associations=".a, .b",
            doc_icons={".a": "icon_a.ico", ".b": "icon_b.ico"},
        ))
        self.assertIsNone(error)
        self.assertEqual(pack_data["doc_icons"], {".a": "icon_a.ico", ".b": "icon_b.ico"})

    def test_doc_icons_extension_not_in_file_associations_is_rejected(self):
        _, error = self._validate(self._base_data(
            need_file_assoc=True, file_associations=".a", doc_icons={".b": "icon_b.ico"},
        ))
        self.assertIsNotNone(error)
        self.assertIn(".b", error)

    def test_doc_icons_normalizes_missing_dot_and_case(self):
        pack_data, error = self._validate(self._base_data(
            need_file_assoc=True, file_associations=".a", doc_icons={"A": "icon_a.ico"},
        ))
        self.assertIsNone(error)
        self.assertEqual(pack_data["doc_icons"], {".a": "icon_a.ico"})

    def test_doc_icons_non_ico_value_is_rejected(self):
        _, error = self._validate(self._base_data(
            need_file_assoc=True, file_associations=".a", doc_icons={".a": "icon_a.png"},
        ))
        self.assertIsNotNone(error)

    def test_doc_icons_defaults_to_empty_dict(self):
        pack_data, error = self._validate(self._base_data())
        self.assertIsNone(error)
        self.assertEqual(pack_data["doc_icons"], {})

    def test_empty_app_dir_is_rejected(self):
        """驗證順序上，main_exe 是否存在的檢查排在「資料夾是否為空」之前，
        所以只要 main_exe 找不到（空資料夾一定找不到），錯誤訊息會是
        「主要執行檔不存在」，不會走到「資料夾是空的」那條分支——
        這是原本 start_pack() 就有的驗證順序，這裡原封不動保留，只是換了個
        地方測。"""
        empty_dir = tempfile.mkdtemp()
        try:
            data = self._base_data()
            _, error = packaging_core.validate_and_build_pack_data(data, empty_dir, "fake.png", "fake.ico", "")
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
        """workspace_dir 是呼叫端呼叫 ensure_workspace_files() 之後才加進去的
        （那一步有真的複製檔案的副作用，刻意留在純函式外面），這裡確認沒有洩漏進來。"""
        pack_data, _ = self._validate(self._base_data())
        self.assertNotIn("workspace_dir", pack_data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
