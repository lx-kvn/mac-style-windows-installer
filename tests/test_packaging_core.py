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


class TestDefaultWorkspaceDir(unittest.TestCase):
    """真實抓到的 bug：舊版 get_workspace_dir() 在 frozen exe 情境下固定用
    「這支 exe 自己被安裝到的資料夾」，如果這支工具（GUI 版）被裝在
    Program Files，一般權限執行時寫不進去，編譯/打包直接失敗——跟這支
    exe 裝在哪個位置脫鉤，改用一個保證可寫入的使用者層級固定位置。"""

    def test_uses_localappdata_subfolder(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "C:\\Users\\Tester\\AppData\\Local"}):
            self.assertEqual(
                packaging_core.default_workspace_dir(),
                os.path.join(
                    "C:\\Users\\Tester\\AppData\\Local", "mac-style-windows-installer", "workspace",
                ),
            )


class TestGetWorkspaceDir(unittest.TestCase):
    def setUp(self):
        if hasattr(sys, "_MEIPASS"):
            del sys._MEIPASS

    def test_non_frozen_uses_cwd(self):
        self.assertEqual(packaging_core.get_workspace_dir(), os.path.abspath("."))

    def test_frozen_without_custom_setting_uses_default(self):
        with mock.patch.object(sys, "_MEIPASS", "C:\\fake\\meipass", create=True), \
             mock.patch.object(sys, "executable", "C:\\Program Files\\App\\InstallerBuilder.exe"), \
             mock.patch("packaging_core.packaging_settings.load_settings", return_value={}):
            self.assertEqual(packaging_core.get_workspace_dir(), packaging_core.default_workspace_dir())

    def test_frozen_with_custom_setting_uses_persisted_override(self):
        with mock.patch.object(sys, "_MEIPASS", "C:\\fake\\meipass", create=True), \
             mock.patch(
                 "packaging_core.packaging_settings.load_settings",
                 return_value={"workspace_dir": "D:\\Builds\\Workspace"},
             ):
            self.assertEqual(packaging_core.get_workspace_dir(), "D:\\Builds\\Workspace")

    def test_frozen_ignores_exe_directory_even_when_no_custom_setting(self):
        """舊行為（用 exe 所在資料夾）不該再是任何情況下的結果。"""
        with mock.patch.object(sys, "_MEIPASS", "C:\\fake\\meipass", create=True), \
             mock.patch.object(sys, "executable", "C:\\Users\\Test\\InstallerBuilder.exe"), \
             mock.patch("packaging_core.packaging_settings.load_settings", return_value={}):
            self.assertNotEqual(packaging_core.get_workspace_dir(), "C:\\Users\\Test")


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
        with open(os.path.join(self.embedded_dir, "dependency_defs.py"), "w") as f:
            f.write("# NEW dependency_defs content")
        with open(os.path.join(self.embedded_dir, "install_scope.py"), "w") as f:
            f.write("# NEW install_scope content")
        with open(os.path.join(self.embedded_dir, "self_delete.py"), "w") as f:
            f.write("# NEW self_delete content")
        with open(os.path.join(self.embedded_dir, "system_entries.py"), "w") as f:
            f.write("# NEW system_entries content")
        with open(os.path.join(self.embedded_dir, "explorer_lock_release.py"), "w") as f:
            f.write("# NEW explorer_lock_release content")
        with open(os.path.join(self.embedded_dir, "windows_service.py"), "w") as f:
            f.write("# NEW windows_service content")
        with open(os.path.join(self.embedded_dir, "scheduled_task.py"), "w") as f:
            f.write("# NEW scheduled_task content")
        with open(os.path.join(self.embedded_dir, "restore_point.py"), "w") as f:
            f.write("# NEW restore_point content")
        with open(os.path.join(self.embedded_dir, "bits_download.py"), "w") as f:
            f.write("# NEW bits_download content")
        with open(os.path.join(self.embedded_dir, "install_journal.py"), "w") as f:
            f.write("# NEW install_journal content")
        with open(os.path.join(self.embedded_dir, "install_encryption.py"), "w") as f:
            f.write("# NEW install_encryption content")
        with open(os.path.join(self.embedded_dir, "progress_report.py"), "w") as f:
            f.write("# NEW progress_report content")
        with open(os.path.join(self.embedded_dir, "dependency_install.py"), "w") as f:
            f.write("# NEW dependency_install content")
        with open(os.path.join(self.embedded_dir, "version_compare.py"), "w") as f:
            f.write("# NEW version_compare content")
        with open(os.path.join(self.embedded_dir, "upgrade.py"), "w") as f:
            f.write("# NEW upgrade content")
        os.makedirs(os.path.join(self.embedded_dir, "ui"))
        with open(os.path.join(self.embedded_dir, "ui", "index.html"), "w") as f:
            f.write("<!-- NEW index.html -->")
        with open(os.path.join(self.embedded_dir, "ui", "uninstall.html"), "w") as f:
            f.write("<!-- NEW uninstall.html -->")
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
        for name in ("window_drag.py", "disk_space.py", "file_assoc.py", "lang_detect.py", "restart_manager.py", "dependency_defs.py"):
            with open(os.path.join(self.workspace_dir, name), "w") as f:
                f.write(f"# STALE old {name} content")

        packaging_core.ensure_workspace_files(self.workspace_dir)

        for name in ("window_drag.py", "disk_space.py", "file_assoc.py", "lang_detect.py", "restart_manager.py", "dependency_defs.py"):
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

    def test_uninstall_html_is_always_overwritten(self):
        """ui/uninstall.html 是解除安裝端的內部實作（跟 index.html 同一套
        macOS 風格彈窗語彙），同樣不是使用者自訂項目，要無條件覆蓋。"""
        os.makedirs(os.path.join(self.workspace_dir, "ui"))
        with open(os.path.join(self.workspace_dir, "ui", "uninstall.html"), "w") as f:
            f.write("<!-- STALE old uninstall.html -->")

        packaging_core.ensure_workspace_files(self.workspace_dir)

        with open(os.path.join(self.workspace_dir, "ui", "uninstall.html")) as f:
            self.assertEqual(f.read(), "<!-- NEW uninstall.html -->")

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

    def test_a_newly_added_ui_implementation_file_is_also_overwritten(self):
        """F01：覆蓋策略原本是一份寫死的檔名白名單
        （`name in ("index.html", "uninstall.html")`），之後新增的任何實作
        檔案（例如把自繪拖曳抽成 `ui/drag_to_target.js`）都會落進「只在
        缺少時才補」那個分支——重複使用同一個工作目錄的人，卡在那裡的舊版
        永遠不會被換掉，而且沒有任何錯誤訊息。

        這正是這個函式說明文字裡以「【重要】」標記、已經修過一次的同一個
        缺陷，只是換一道門重新出現。根因是修正的形式是白名單，白名單天生
        涵蓋不到之後新增的檔案。改成白名單反轉：**只有已知的使用者可自訂
        靜態資源不覆蓋**，其餘一律覆蓋——新增實作檔案時不需要記得更新任何
        清單。

        這個測試用一個「目前不存在、將來才會加」的檔名，斷言的是規則本身
        而不是某一份清單的內容。
        """
        with open(os.path.join(self.embedded_dir, "ui", "some_future_module.js"), "w") as f:
            f.write("// NEW shared js")
        os.makedirs(os.path.join(self.workspace_dir, "ui"))
        with open(os.path.join(self.workspace_dir, "ui", "some_future_module.js"), "w") as f:
            f.write("// STALE old shared js")

        packaging_core.ensure_workspace_files(self.workspace_dir)

        with open(os.path.join(self.workspace_dir, "ui", "some_future_module.js")) as f:
            self.assertEqual(
                f.read(), "// NEW shared js",
                "新增的實作檔案沒有被覆蓋——工作目錄裡的舊版會靜默生效",
            )

    def test_the_customizable_asset_list_is_the_thing_that_is_declared(self):
        """既然規則反轉了，「哪些是使用者可自訂的」就必須是一份明確宣告的
        清單，而不是靠「沒列在覆蓋清單裡」推導出來的。"""
        self.assertTrue(
            hasattr(packaging_core, "USER_CUSTOMIZABLE_UI_ASSETS"),
            "找不到明確宣告的可自訂資源清單",
        )
        self.assertIn("folder_icon.png", packaging_core.USER_CUSTOMIZABLE_UI_ASSETS)

    def test_copy_failure_returns_readable_error_message(self):
        with mock.patch.object(shutil, "copy2", side_effect=PermissionError("拒絕存取")):
            result = packaging_core.ensure_workspace_files(self.workspace_dir)
        self.assertIsNotNone(result)
        self.assertIn("寫入權限", result)


class TestListAppDirFiles(unittest.TestCase):
    """list_app_dir_files()：掃描 app_dir 底下所有檔案的相對路徑，供
    GUI 的分支圖勾選跟 CLI 的 list-files 指令共用同一份掃描邏輯。"""

    def setUp(self):
        self.app_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.app_dir, ignore_errors=True)

    def test_lists_files_recursively_with_forward_slash_paths(self):
        os.makedirs(os.path.join(self.app_dir, "tools"))
        with open(os.path.join(self.app_dir, "main.exe"), "wb") as f:
            f.write(b"x")
        with open(os.path.join(self.app_dir, "tools", "cli.exe"), "wb") as f:
            f.write(b"x")
        result = packaging_core.list_app_dir_files(self.app_dir)
        self.assertEqual(result, ["main.exe", "tools/cli.exe"])

    def test_returns_empty_list_for_missing_dir(self):
        self.assertEqual(packaging_core.list_app_dir_files(os.path.join(self.app_dir, "nope")), [])

    def test_returns_empty_list_for_empty_app_dir(self):
        self.assertEqual(packaging_core.list_app_dir_files(""), [])


class PackDataValidationTestBase(unittest.TestCase):
    """`validate_and_build_pack_data()` 的共用測試骨架：一個含 main.exe 的
    暫時 app_dir，加上「其他欄位都填好、只有這個測試關心的欄位不同」的
    `_base_data()`。下面幾個測試類別都從這裡繼承，不各自複製一份。"""

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


class TestValidateAndBuildPackData(PackDataValidationTestBase):
    def test_success_path_returns_pack_data_with_no_error(self):
        pack_data, error = self._validate(self._base_data())
        self.assertIsNone(error)
        self.assertEqual(pack_data["app_name"], "TestApp")
        self.assertEqual(pack_data["folder_name"], "TestApp", "folder_name 留空時要 fallback 成 app_name")
        self.assertEqual(pack_data["file_associations"], [])
        self.assertTrue(pack_data["restart_explorer_on_update"])

    def test_restart_explorer_on_update_is_always_true_regardless_of_input(self):
        """真實抓到的問題：偵測並結束鎖定安裝檔案的程式，最終決定權還是在
        使用者手上（互動式解除安裝一定會先跳警示問過使用者才會真的結束），
        打包時讓開發者關掉這個偵測反而只是徒增要理解的設定項——改成不管
        開發者傳什麼，一律內建開啟。"""
        pack_data_true, error_true = self._validate(self._base_data(restart_explorer_on_update=True))
        pack_data_false, error_false = self._validate(self._base_data(restart_explorer_on_update=False))
        self.assertIsNone(error_true)
        self.assertIsNone(error_false)
        self.assertTrue(pack_data_true["restart_explorer_on_update"])
        self.assertTrue(pack_data_false["restart_explorer_on_update"])

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

    def test_no_admin_install_passes_through(self):
        pack_data, error = self._validate(self._base_data(no_admin_install=True))
        self.assertIsNone(error)
        self.assertTrue(pack_data["no_admin_install"])

    def test_custom_install_dir_defaults_to_empty_string(self):
        pack_data, error = self._validate(self._base_data())
        self.assertIsNone(error)
        self.assertEqual(pack_data["custom_install_dir"], "")

    def test_custom_install_dir_passes_through_trimmed(self):
        pack_data, error = self._validate(self._base_data(custom_install_dir="  %APPDATA%\\MyApp  "))
        self.assertIsNone(error)
        self.assertEqual(pack_data["custom_install_dir"], "%APPDATA%\\MyApp")

    def test_pre_install_script_must_exist_in_app_dir(self):
        _, error = self._validate(self._base_data(pre_install_script="not_there.bat"))
        self.assertIsNotNone(error)
        self.assertIn("安裝前置", error)

    def test_pre_install_script_existing_file_passes(self):
        script_path = os.path.join(self.app_dir, "setup.bat")
        with open(script_path, "w") as f:
            f.write("@echo off")
        pack_data, error = self._validate(self._base_data(pre_install_script="setup.bat"))
        self.assertIsNone(error)
        self.assertEqual(pack_data["pre_install_script"], "setup.bat")

    def test_custom_dependency_missing_required_field_is_rejected(self):
        _, error = self._validate(self._base_data(custom_dependencies=[{"key": "my_dep"}]))
        self.assertIsNotNone(error)

    def test_custom_dependency_colliding_with_built_in_key_is_rejected(self):
        _, error = self._validate(self._base_data(custom_dependencies=[{
            "key": "vcredist_x64", "display_name": "X", "download_url": "https://example.test/x.exe",
            "registry_check": {"path": "Software\\X"},
        }]))
        self.assertIsNotNone(error)
        self.assertIn("撞名", error)

    def test_custom_dependency_duplicate_key_is_rejected(self):
        entry = {
            "key": "my_dep", "display_name": "X", "download_url": "https://example.test/x.exe",
            "registry_check": {"path": "Software\\X"},
        }
        _, error = self._validate(self._base_data(custom_dependencies=[entry, dict(entry)]))
        self.assertIsNotNone(error)
        self.assertIn("重複", error)

    def test_valid_custom_dependency_passes_through(self):
        pack_data, error = self._validate(self._base_data(custom_dependencies=[{
            "key": "my_dep", "display_name": "My Dep", "download_url": "https://example.test/x.exe",
            "silent_args": ["/quiet"], "registry_check": {"hive": "HKLM", "path": "Software\\X"},
        }]))
        self.assertIsNone(error)
        self.assertEqual(pack_data["custom_dependencies"][0]["key"], "my_dep")

    def test_bundle_dependencies_not_in_dependencies_list_is_rejected(self):
        _, error = self._validate(self._base_data(dependencies=["vcredist_x64"], bundle_dependencies=["dotnet_desktop"]))
        self.assertIsNotNone(error)

    def test_bundle_dependencies_matching_dependencies_list_passes(self):
        pack_data, error = self._validate(self._base_data(dependencies=["vcredist_x64"], bundle_dependencies=["vcredist_x64"]))
        self.assertIsNone(error)
        self.assertEqual(pack_data["bundle_dependencies"], ["vcredist_x64"])

    def test_windows_service_missing_exe_relative_path_is_rejected(self):
        """真實抓到的問題：windows_service 完全沒有驗證——勾了「安裝為
        Windows 服務」、填了服務名稱，但主程式下拉選單剛好還沒選（或
        app_dir 沒有任何 .exe 時的預設空白選項），這種半填的設定原本會
        直接打包成功，裝到使用者機器上時 installer_core.py 的條件判斷
        （service_name 跟 exe_relative_path 都要有才會建立）悄悄跳過整個
        服務建立，沒有任何錯誤訊息、沒有任何警告，使用者以為裝了服務，
        其實完全沒有。"""
        _, error = self._validate(self._base_data(windows_service={"service_name": "MySvc"}))
        self.assertIsNotNone(error)

    def test_windows_service_exe_not_in_app_dir_is_rejected(self):
        """真實抓到的問題：exe_relative_path 原本完全沒有存在性檢查，
        跟 main_exe/path_target_exe/pre_install_script 這些同類欄位不
        一致——sc.exe 不會驗證 binPath 對不對，打錯字會註冊一個永久
        壞掉、開機就報錯的服務，而且是靜默失敗（installer_core.py 只記
        警告 log，不會讓安裝回報失敗）。"""
        _, error = self._validate(self._base_data(windows_service={
            "service_name": "MySvc", "exe_relative_path": "does_not_exist.exe",
        }))
        self.assertIsNotNone(error)

    def test_windows_service_invalid_start_type_is_rejected(self):
        _, error = self._validate(self._base_data(windows_service={
            "service_name": "MySvc", "exe_relative_path": "main.exe", "start_type": "whenever",
        }))
        self.assertIsNotNone(error)

    def test_valid_windows_service_passes_through(self):
        pack_data, error = self._validate(self._base_data(windows_service={
            "service_name": "MySvc", "exe_relative_path": "main.exe", "start_type": "demand",
        }))
        self.assertIsNone(error)
        self.assertEqual(pack_data["windows_service"]["service_name"], "MySvc")

    def test_empty_windows_service_is_valid(self):
        pack_data, error = self._validate(self._base_data(windows_service={}))
        self.assertIsNone(error)

    def test_start_type_validation_follows_windows_service_constant(self):
        """A3（config schema 單一真實來源）：真實抓到的問題——
        `_VALID_SERVICE_START_TYPES` 原本是這個檔案自己寫死的一份
        {"auto", "demand", "disabled"}，跟真正執行
        `sc create ... start= <start_type>` 的 windows_service.py 完全
        脫鉤。改成從 windows_service.VALID_START_TYPES 讀，windows_service.py
        才是真正知道 sc.exe 支援哪些 start_type 值的模組。這裡不斷言目前
        的常數值本身，而是把 windows_service.VALID_START_TYPES 換成一組
        完全不同的假值，驗證這裡的行為真的跟著變——如果還是走自己寫死
        的字面常數，這個測試會照樣通過，沒辦法真的證明兩邊有沒有掛勾。"""
        with mock.patch.object(packaging_core.windows_service, "VALID_START_TYPES", frozenset({"only_this_one"})):
            _, error = self._validate(self._base_data(windows_service={
                "service_name": "MySvc", "exe_relative_path": "main.exe", "start_type": "demand",
            }))
            self.assertIsNotNone(error, "換掉 windows_service 的常數後，原本有效的 demand 不應該再被接受")

            pack_data, error = self._validate(self._base_data(windows_service={
                "service_name": "MySvc", "exe_relative_path": "main.exe", "start_type": "only_this_one",
            }))
            self.assertIsNone(error, "換掉之後唯一有效的值，應該要被接受")

    def test_scheduled_task_missing_exe_relative_path_is_rejected(self):
        _, error = self._validate(self._base_data(scheduled_task={"task_name": "MyTask"}))
        self.assertIsNotNone(error)

    def test_scheduled_task_exe_not_in_app_dir_is_rejected(self):
        _, error = self._validate(self._base_data(scheduled_task={
            "task_name": "MyTask", "exe_relative_path": "does_not_exist.exe",
        }))
        self.assertIsNotNone(error)

    def test_valid_scheduled_task_passes_through(self):
        pack_data, error = self._validate(self._base_data(scheduled_task={
            "task_name": "MyTask", "exe_relative_path": "main.exe", "trigger": "onlogon",
        }))
        self.assertIsNone(error)
        self.assertEqual(pack_data["scheduled_task"]["task_name"], "MyTask")

    def test_dependencies_min_version_for_disabled_dependency_is_rejected(self):
        """真實抓到的問題：dependencies_min_version 的 key 完全沒有跟
        dependencies 清單交叉比對——填了 vcredist_x64 的最低版本，卻沒有
        在上面勾選啟用 vcredist_x64 偵測，這個最低版本設定形同無效，
        跟 bundle_dependencies 已經在做的交叉驗證是同一個道理。"""
        _, error = self._validate(self._base_data(
            dependencies=[], dependencies_min_version={"vcredist_x64": "14.38"},
        ))
        self.assertIsNotNone(error)

    def test_dependencies_min_version_for_custom_dependency_key_is_rejected(self):
        """真實抓到的問題：dependencies_min_version 只有內建的
        vcredist_x64/dotnet_desktop 兩個 key 會被 installer_core.py 的
        _build_dependency_checkers() 實際套用；custom_dependencies 的
        版本比較走的是各自 registry_check.min_version 那個獨立欄位（見
        F6）。如果把 custom_dependencies 的 key 填進 dependencies_min_version，
        會被靜默忽略，使用者以為設定生效了，其實完全沒有。"""
        _, error = self._validate(self._base_data(
            dependencies=["my_dep"],
            custom_dependencies=[{
                "key": "my_dep", "display_name": "X", "download_url": "https://example.test/x.exe",
                "registry_check": {"path": "Software\\X"},
            }],
            dependencies_min_version={"my_dep": "1.0"},
        ))
        self.assertIsNotNone(error)

    def test_valid_dependencies_min_version_passes_through(self):
        pack_data, error = self._validate(self._base_data(
            dependencies=["vcredist_x64"], dependencies_min_version={"vcredist_x64": "14.38"},
        ))
        self.assertIsNone(error)
        self.assertEqual(pack_data["dependencies_min_version"], {"vcredist_x64": "14.38"})

    def test_signing_without_cert_file_is_rejected(self):
        _, error = self._validate(self._base_data(signing={
            "cert_path": "C:\\does\\not\\exist.pfx", "cert_password_env": "MY_CERT_PW",
        }))
        self.assertIsNotNone(error)

    def test_signing_with_missing_env_var_is_rejected(self):
        cert_path = os.path.join(self.app_dir, "cert.pfx")
        with open(cert_path, "wb") as f:
            f.write(b"fake cert bytes")
        os.environ.pop("MY_TEST_CERT_PW_MISSING", None)
        _, error = self._validate(self._base_data(signing={
            "cert_path": cert_path, "cert_password_env": "MY_TEST_CERT_PW_MISSING",
        }))
        self.assertIsNotNone(error)

    def test_valid_signing_config_passes_through(self):
        cert_path = os.path.join(self.app_dir, "cert.pfx")
        with open(cert_path, "wb") as f:
            f.write(b"fake cert bytes")
        with mock.patch.dict(os.environ, {"MY_TEST_CERT_PW": "hunter2"}):
            pack_data, error = self._validate(self._base_data(signing={
                "cert_path": cert_path, "cert_password_env": "MY_TEST_CERT_PW",
            }))
        self.assertIsNone(error)
        self.assertEqual(pack_data["signing"]["cert_path"], cert_path)
        self.assertEqual(pack_data["signing"]["timestamp_url"], "http://timestamp.digicert.com")


class TestNoAdminInstallConflicts(PackDataValidationTestBase):
    """F09：「免管理員權限安裝」與「建立 Windows 服務／系統還原點」可以
    同時設定，但兩者在一般權限下必定失敗。

    `no_admin_install` 開啟時 builder.py 不加入提權設定，整個安裝流程在
    一般權限下執行；`sc.exe create` 與系統還原點建立都需要管理員權限。
    這個組合原本沒有任何驗證，失敗只會變成安裝完成畫面上的警告——使用者
    要等到裝到終端機器上、發現服務不存在才知道設定從一開始就不可能成立。

    `validate_and_build_pack_data()` 對欄位之間的矛盾本來就有在驗證
    （`dependencies_min_version` 與 `dependencies` 交叉比對、`doc_icons` 與
    `file_associations` 交叉比對），這是少數會在終端使用者機器上實際失敗、
    卻沒有對應驗證的組合。
    """

    def _service(self):
        return {"service_name": "MySvc", "exe_relative_path": "main.exe", "start_type": "auto"}

    def test_no_admin_install_with_windows_service_is_rejected(self):
        _pack_data, error = self._validate(self._base_data(
            no_admin_install=True, windows_service=self._service(),
        ))
        self.assertIsNotNone(error, "免權限安裝 + Windows 服務必定失敗，應該在打包階段就攔下來")
        self.assertIn("Windows 服務", error)
        self.assertIn("管理員權限", error)

    def test_no_admin_install_with_restore_point_is_rejected(self):
        _pack_data, error = self._validate(self._base_data(
            no_admin_install=True, create_restore_point_before_install=True,
        ))
        self.assertIsNotNone(error)
        self.assertIn("還原點", error)
        self.assertIn("管理員權限", error)

    def test_windows_service_alone_is_fine(self):
        _pack_data, error = self._validate(self._base_data(windows_service=self._service()))
        self.assertIsNone(error)

    def test_restore_point_alone_is_fine(self):
        _pack_data, error = self._validate(self._base_data(create_restore_point_before_install=True))
        self.assertIsNone(error)

    def test_no_admin_install_alone_is_fine(self):
        _pack_data, error = self._validate(self._base_data(no_admin_install=True))
        self.assertIsNone(error)

    def test_no_admin_install_with_scheduled_task_is_still_allowed(self):
        """排程工作不在互斥清單裡：schtasks.exe 以目前使用者身分建立
        onlogon 觸發的工作不需要管理員權限，跟 sc.exe／還原點不同。"""
        _pack_data, error = self._validate(self._base_data(
            no_admin_install=True,
            scheduled_task={"task_name": "MyTask", "exe_relative_path": "main.exe"},
        ))
        self.assertIsNone(error)


class TestVersionStringValidation(PackDataValidationTestBase):
    """F10：版本號的合法定義，打包端與安裝端互相矛盾。

    `version_compare.py` 完整處理帶預發布後綴的版本（`1.0.0-rc2` 比
    `1.0.0` 舊），但 `version_info._parse_version_tuple()` 要求每一段都是
    純整數，`1.0.0-rc1` 會拋 ValueError 中止建置——這種版本號根本無法打包
    產出，安裝端的預發布比較邏輯永遠不會被執行到。

    附帶問題：這裡原本對版本號只檢查非空字串，真正的格式檢查發生在
    `builder.py` 中段，此時 `dist/`／`build/` 已於流程開頭被清空。純函式
    `validate_and_build_pack_data()` 的設計目的正是在產生任何副作用之前
    攔截設定錯誤。

    決定與理由見 docs/adr/0003-allow-prerelease-suffix-in-version-string.md。
    """

    def test_accepts_a_prerelease_suffix(self):
        _pack_data, error = self._validate(self._base_data(version="1.0.0-rc1"))
        self.assertIsNone(error, "帶預發布後綴的版本號應該可以打包")

    def test_accepts_plain_numeric_versions(self):
        for version in ("1", "1.0", "1.0.0", "1.0.0.0"):
            with self.subTest(version=version):
                _pack_data, error = self._validate(self._base_data(version=version))
                self.assertIsNone(error)

    def test_rejects_more_than_four_numeric_segments(self):
        """Win32 VERSIONINFO 的 filevers/prodvers 依規格固定是 4 個 16 位元
        整數，第 5 段無處可放。"""
        _pack_data, error = self._validate(self._base_data(version="1.0.0.0.1"))
        self.assertIsNotNone(error)
        self.assertIn("版本", error)

    def test_rejects_non_numeric_segments(self):
        _pack_data, error = self._validate(self._base_data(version="1.x.0"))
        self.assertIsNotNone(error)
        self.assertIn("版本", error)

    def test_rejects_an_empty_suffix(self):
        _pack_data, error = self._validate(self._base_data(version="1.0.0-"))
        self.assertIsNotNone(error)
        self.assertIn("版本", error)

    def test_rejects_a_negative_segment(self):
        _pack_data, error = self._validate(self._base_data(version="1.-2.0"))
        self.assertIsNotNone(error)
        self.assertIn("版本", error)


class TestServiceAndTaskFieldsAreNormalized(PackDataValidationTestBase):
    """F11：部分設定欄位以未正規化的原始值進入設定檔。

    `windows_service`／`scheduled_task` 的驗證會先 `.strip()` 再檢查，但
    `pack_data` 是由 `dict(data)` 整包複製而來，這幾個欄位沒有像其他欄位
    那樣把正規化後的值寫回。結果是 `start_type` 填成 `"auto "` 能通過驗證，
    實際傳給 `sc.exe` 時失敗——驗證看的值跟實際使用的值是兩個不同的東西。
    """

    def test_windows_service_fields_are_stripped_in_pack_data(self):
        pack_data, error = self._validate(self._base_data(windows_service={
            "service_name": "  MySvc  ", "exe_relative_path": " main.exe ",
            "display_name": "  My Service  ", "start_type": "auto ",
        }))
        self.assertIsNone(error)
        self.assertEqual(pack_data["windows_service"]["service_name"], "MySvc")
        self.assertEqual(pack_data["windows_service"]["exe_relative_path"], "main.exe")
        self.assertEqual(pack_data["windows_service"]["display_name"], "My Service")
        self.assertEqual(pack_data["windows_service"]["start_type"], "auto")

    def test_scheduled_task_fields_are_stripped_in_pack_data(self):
        pack_data, error = self._validate(self._base_data(scheduled_task={
            "task_name": "  MyTask  ", "exe_relative_path": " main.exe ",
            "trigger": " onlogon ",
        }))
        self.assertIsNone(error)
        self.assertEqual(pack_data["scheduled_task"]["task_name"], "MyTask")
        self.assertEqual(pack_data["scheduled_task"]["exe_relative_path"], "main.exe")
        self.assertEqual(pack_data["scheduled_task"]["trigger"], "onlogon")

    def test_start_type_defaults_to_auto_when_omitted(self):
        pack_data, error = self._validate(self._base_data(windows_service={
            "service_name": "MySvc", "exe_relative_path": "main.exe",
        }))
        self.assertIsNone(error)
        self.assertEqual(pack_data["windows_service"]["start_type"], "auto")

    def test_unused_service_and_task_stay_empty(self):
        pack_data, error = self._validate(self._base_data())
        self.assertIsNone(error)
        self.assertEqual(pack_data["windows_service"], {})
        self.assertEqual(pack_data["scheduled_task"], {})


class TestInstallPasswordModes(PackDataValidationTestBase):
    """安裝密碼保護的兩種填法（見 docs/adr/0004）。

    配置精靈可以直接輸入密碼，也可以填環境變數名稱；設定檔（CLI）只支援
    後者。兩條路的能力不對等是決定，不是遺漏——`data` 的欄位集合就是設定檔
    的格式，讓「直接輸入」變成一個一般欄位，等於同時讓設定檔也能寫明文
    密碼，把當初繞環境變數要避開的風險原封不動放回來。

    直接輸入的密碼因此不進 `data`：`validate_and_build_pack_data()` 只收到
    一個布林值，知道「這次有沒有用直接輸入」就足以做驗證，不需要看到密碼
    本身。這個純函式維持「只處理設定值」的性質。
    """

    def _validate_pw(self, data, has_inline_password=False):
        return packaging_core.validate_and_build_pack_data(
            data, self.app_dir, "fake.png", "fake.ico", "",
            has_inline_password=has_inline_password,
        )

    def test_not_enabled_is_fine(self):
        _pack_data, error = self._validate_pw(self._base_data())
        self.assertIsNone(error)

    def test_inline_password_alone_is_accepted(self):
        _pack_data, error = self._validate_pw(
            self._base_data(need_install_password=True), has_inline_password=True,
        )
        self.assertIsNone(error)

    def test_env_var_name_alone_is_accepted(self):
        with mock.patch.dict(os.environ, {"MY_INSTALL_PW": "hunter2"}):
            _pack_data, error = self._validate_pw(self._base_data(
                need_install_password=True, install_password_env="MY_INSTALL_PW",
            ))
        self.assertIsNone(error)

    def test_enabled_but_nothing_supplied_is_rejected(self):
        """勾了卻兩邊都沒填——跟「需要註冊檔案關聯」勾了沒填副檔名、
        「安裝為 Windows 服務」勾了沒填名稱同一種處理。默默放行的話，
        使用者會以為自己的安裝檔有密碼保護，實際上完全沒有。"""
        _pack_data, error = self._validate_pw(self._base_data(need_install_password=True))
        self.assertIsNotNone(error)
        self.assertIn("密碼", error)

    def test_supplying_both_ways_is_rejected(self):
        """兩種填法同時給就無從判斷該用哪一個，明白擋下來而不是自己挑一個。"""
        with mock.patch.dict(os.environ, {"MY_INSTALL_PW": "hunter2"}):
            _pack_data, error = self._validate_pw(
                self._base_data(need_install_password=True, install_password_env="MY_INSTALL_PW"),
                has_inline_password=True,
            )
        self.assertIsNotNone(error)
        self.assertIn("擇一", error)

    def test_env_var_without_a_value_is_still_rejected(self):
        """既有規則不變：填了名稱但那個環境變數當下沒有值，就擋下來。"""
        with mock.patch.dict(os.environ, {}, clear=True):
            _pack_data, error = self._validate_pw(self._base_data(
                need_install_password=True, install_password_env="NOT_SET_ANYWHERE",
            ))
        self.assertIsNotNone(error)
        self.assertIn("NOT_SET_ANYWHERE", error)

    def test_a_plaintext_password_field_in_the_config_is_rejected(self):
        """ADR-0004 決定三：設定檔裡出現直接寫密碼的欄位要明白報錯，訊息
        指向環境變數那個欄位。不採用「不認得的欄位本來就會被忽略」——這一項
        被忽略的後果是使用者以為有保護、實際上完全沒有，而且要等到把安裝檔
        發出去才可能發現。"""
        _pack_data, error = self._validate_pw(self._base_data(install_password="hunter2"))
        self.assertIsNotNone(error)
        self.assertIn("install_password_env", error)

    def test_an_empty_plaintext_password_field_is_also_rejected(self):
        """空字串同樣要擋：使用者已經在設定檔裡寫下這個欄位，代表他以為
        這條路可行，只是這次剛好留空。默默放行會讓他下次填了值才發現沒用。"""
        _pack_data, error = self._validate_pw(self._base_data(install_password=""))
        self.assertIsNotNone(error)
        self.assertIn("install_password_env", error)

    def test_the_inline_password_never_appears_in_pack_data(self):
        """密碼不進 `pack_data`，因此不會出現在任何可能被序列化的結構裡。"""
        pack_data, error = self._validate_pw(
            self._base_data(need_install_password=True), has_inline_password=True,
        )
        self.assertIsNone(error)
        self.assertNotIn("install_password", pack_data)


class TestEncryptionDependencyIsCheckedBeforeAnySideEffect(PackDataValidationTestBase):
    """ADR-0004 決定四：加密實作依賴 `cryptography`，該 import 在函式內部。
    `build_all()` 的順序是「清空 dist/build → 編譯 uninstall.exe（數十秒）
    → 加密」，套件缺少時使用者會白等完整段編譯、產物也已被清空，最後才收到
    一個 ImportError。

    跟 ADR-0003 第三點（版本號格式驗證前移）同一個原則：純函式的職責就是
    在任何檔案系統副作用發生之前攔截設定錯誤。
    """

    def test_missing_package_is_reported_when_password_protection_is_on(self):
        with mock.patch("packaging_core._encryption_backend_available", return_value=False):
            _pack_data, error = packaging_core.validate_and_build_pack_data(
                self._base_data(need_install_password=True), self.app_dir,
                "fake.png", "fake.ico", "", has_inline_password=True,
            )
        self.assertIsNotNone(error)
        self.assertIn("cryptography", error)

    def test_missing_package_is_ignored_when_password_protection_is_off(self):
        """沒有要用密碼保護的人不該被一個他用不到的套件擋下來。"""
        with mock.patch("packaging_core._encryption_backend_available", return_value=False):
            _pack_data, error = self._validate(self._base_data())
        self.assertIsNone(error)


class TestValidateSigningConfig(unittest.TestCase):
    """_validate_signing_config()：獨立測試，不用像
    TestValidateAndBuildPackData 那樣先準備 app_dir/png_path/main_exe
    等一整包跟 signing 完全無關的欄位。"""

    def test_empty_signing_is_valid_and_none(self):
        signing, error = packaging_core._validate_signing_config({})
        self.assertIsNone(signing)
        self.assertIsNone(error)

    def test_missing_cert_file_is_rejected(self):
        signing, error = packaging_core._validate_signing_config({
            "cert_path": "C:\\does\\not\\exist.pfx", "cert_password_env": "MY_CERT_PW",
        })
        self.assertIsNone(signing)
        self.assertIsNotNone(error)

    def test_missing_password_env_value_is_rejected(self):
        tmp_dir = tempfile.mkdtemp()
        try:
            cert_path = os.path.join(tmp_dir, "cert.pfx")
            with open(cert_path, "wb") as f:
                f.write(b"fake cert bytes")
            os.environ.pop("MY_TEST_CERT_PW_MISSING_UNIT", None)
            signing, error = packaging_core._validate_signing_config({
                "cert_path": cert_path, "cert_password_env": "MY_TEST_CERT_PW_MISSING_UNIT",
            })
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        self.assertIsNone(signing)
        self.assertIsNotNone(error)

    def test_valid_config_defaults_timestamp_url(self):
        tmp_dir = tempfile.mkdtemp()
        try:
            cert_path = os.path.join(tmp_dir, "cert.pfx")
            with open(cert_path, "wb") as f:
                f.write(b"fake cert bytes")
            with mock.patch.dict(os.environ, {"MY_TEST_CERT_PW_UNIT": "hunter2"}):
                signing, error = packaging_core._validate_signing_config({
                    "cert_path": cert_path, "cert_password_env": "MY_TEST_CERT_PW_UNIT",
                })
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        self.assertIsNone(error)
        self.assertEqual(signing["timestamp_url"], "http://timestamp.digicert.com")


class TestValidateInstallPassword(unittest.TestCase):
    """_validate_install_password()（安裝密碼保護，見 CONTEXT.md 與
    docs/adr/0004）：獨立測試，不用像 TestInstallPasswordModes 那樣先準備
    app_dir/png_path/main_exe 等一整包跟密碼完全無關的欄位。

    環境變數那條路的規則比照 _validate_signing_config() 的 cert_password_env
    ——密碼本身不放在設定檔裡，只存環境變數名稱，只檢查環境變數有沒有值，
    不額外要求密碼長度/複雜度。這是使用者自己開發環境裡設定的密碼，工具
    沒有立場替使用者決定「多長才算安全」。
    """

    def _validate(self, need=False, env_raw="", inline=False, plaintext_field=False):
        return packaging_core._validate_install_password(need, env_raw, inline, plaintext_field)

    def test_empty_value_is_valid_and_feature_off(self):
        install_password_env, error = self._validate()
        self.assertEqual(install_password_env, "")
        self.assertIsNone(error)

    def test_missing_env_var_value_is_rejected(self):
        os.environ.pop("MY_TEST_INSTALL_PW_MISSING_UNIT", None)
        install_password_env, error = self._validate(
            need=True, env_raw="MY_TEST_INSTALL_PW_MISSING_UNIT",
        )
        self.assertIsNone(install_password_env)
        self.assertIsNotNone(error)

    def test_env_var_with_value_passes(self):
        with mock.patch.dict(os.environ, {"MY_TEST_INSTALL_PW_UNIT": "hunter2"}):
            install_password_env, error = self._validate(
                need=True, env_raw="MY_TEST_INSTALL_PW_UNIT",
            )
        self.assertEqual(install_password_env, "MY_TEST_INSTALL_PW_UNIT")
        self.assertIsNone(error)

    def test_env_var_name_is_stripped(self):
        with mock.patch.dict(os.environ, {"MY_TEST_INSTALL_PW_UNIT": "hunter2"}):
            install_password_env, error = self._validate(
                need=True, env_raw="  MY_TEST_INSTALL_PW_UNIT  ",
            )
        self.assertIsNone(error)
        self.assertEqual(install_password_env, "MY_TEST_INSTALL_PW_UNIT")

    def test_inline_password_needs_no_env_var(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            install_password_env, error = self._validate(need=True, inline=True)
        self.assertEqual(install_password_env, "")
        self.assertIsNone(error)

    def test_plaintext_field_is_rejected_before_anything_else(self):
        """設定檔裡出現直接寫密碼的欄位時，不管其他欄位怎麼填都先擋下來。"""
        _install_password_env, error = self._validate(
            env_raw="WHATEVER", plaintext_field=True,
        )
        self.assertIsNotNone(error)
        self.assertIn("install_password_env", error)


class TestValidateDependencyPolicy(unittest.TestCase):
    """_validate_dependency_policy()：獨立測試 custom_dependencies/
    bundle_dependencies 的交叉驗證規則，不需要 app_dir 等其他欄位。"""

    def test_custom_dependency_missing_required_field_is_rejected(self):
        custom, bundle, error = packaging_core._validate_dependency_policy(
            [], [{"key": "my_dep"}], []
        )
        self.assertIsNone(custom)
        self.assertIsNone(bundle)
        self.assertIsNotNone(error)

    def test_custom_dependency_key_colliding_with_builtin_is_rejected(self):
        custom, bundle, error = packaging_core._validate_dependency_policy(
            [], [{
                "key": "vcredist_x64", "display_name": "X", "download_url": "https://x",
                "registry_check": {"path": "SOFTWARE\\X"},
            }], []
        )
        self.assertIsNotNone(error)

    def test_duplicate_custom_dependency_keys_rejected(self):
        entry = {
            "key": "my_dep", "display_name": "X", "download_url": "https://x",
            "registry_check": {"path": "SOFTWARE\\X"},
        }
        custom, bundle, error = packaging_core._validate_dependency_policy(
            [], [entry, dict(entry)], []
        )
        self.assertIsNotNone(error)

    def test_valid_custom_dependency_passes_through(self):
        custom, bundle, error = packaging_core._validate_dependency_policy(
            [], [{
                "key": "my_dep", "display_name": "X", "download_url": "https://x",
                "registry_check": {"path": "SOFTWARE\\X"},
            }], []
        )
        self.assertIsNone(error)
        self.assertEqual(custom[0]["key"], "my_dep")

    def test_bundle_dependency_not_in_dependencies_list_is_rejected(self):
        custom, bundle, error = packaging_core._validate_dependency_policy(
            ["vcredist_x64"], [], ["dotnet_desktop"]
        )
        self.assertIsNotNone(error)

    def test_bundle_dependency_matching_dependencies_list_passes(self):
        custom, bundle, error = packaging_core._validate_dependency_policy(
            ["vcredist_x64"], [], ["vcredist_x64"]
        )
        self.assertIsNone(error)
        self.assertEqual(bundle, ["vcredist_x64"])

    def test_non_https_download_url_is_rejected(self):
        """真實抓到的安全性問題：download_url 原本沒有限制協定，http:// 的
        自訂相依元件會被安裝端下載後直接執行——中間人可以竄改成任意惡意
        程式，這支安裝程式預設是 --uac-admin 編譯的，等於是遠端程式碼
        執行。打包階段就要擋掉，不要等到使用者的機器上才出事。"""
        custom, bundle, error = packaging_core._validate_dependency_policy(
            [], [{
                "key": "my_dep", "display_name": "X", "download_url": "http://example.test/x.exe",
                "registry_check": {"path": "SOFTWARE\\X"},
            }], []
        )
        self.assertIsNone(custom)
        self.assertIsNotNone(error)

    def test_https_download_url_is_accepted(self):
        custom, bundle, error = packaging_core._validate_dependency_policy(
            [], [{
                "key": "my_dep", "display_name": "X", "download_url": "https://example.test/x.exe",
                "registry_check": {"path": "SOFTWARE\\X"},
            }], []
        )
        self.assertIsNone(error)

    def test_sha256_is_passed_through_and_normalized_to_lowercase(self):
        custom, bundle, error = packaging_core._validate_dependency_policy(
            [], [{
                "key": "my_dep", "display_name": "X", "download_url": "https://example.test/x.exe",
                "registry_check": {"path": "SOFTWARE\\X"},
                "sha256": "ABCDEF0123456789" * 4,
            }], []
        )
        self.assertIsNone(error)
        self.assertEqual(custom[0]["sha256"], "abcdef0123456789" * 4)

    def test_sha256_with_invalid_format_is_rejected(self):
        custom, bundle, error = packaging_core._validate_dependency_policy(
            [], [{
                "key": "my_dep", "display_name": "X", "download_url": "https://example.test/x.exe",
                "registry_check": {"path": "SOFTWARE\\X"},
                "sha256": "not-a-valid-hash",
            }], []
        )
        self.assertIsNone(custom)
        self.assertIsNotNone(error)

    def test_min_version_is_passed_through_registry_check(self):
        """真實抓到的 bug：這裡原本用白名單（hive/path/value_name/expected
        四個鍵）重建 registry_check，min_version/enum_subkeys 兩個欄位
        被悄悄丟掉——installer_core._make_custom_dependency_checker() 明明
        已經支援讀 min_version 改走版本比較，GUI 表單填的最低版本卻永遠
        傳不到那裡，形同無效欄位（更糟的是使用者填了 min_version 卻沒填
        expected，會退回 exact-match 語意，變成 value==None 恆為 False，
        這個相依元件在任何機器上都會被誤判成未安裝）。"""
        custom, bundle, error = packaging_core._validate_dependency_policy(
            [], [{
                "key": "my_dep", "display_name": "X", "download_url": "https://example.test/x.exe",
                "registry_check": {"path": "SOFTWARE\\X", "min_version": "1.2.3", "enum_subkeys": True},
            }], []
        )
        self.assertIsNone(error)
        self.assertEqual(custom[0]["registry_check"]["min_version"], "1.2.3")
        self.assertTrue(custom[0]["registry_check"]["enum_subkeys"])

    def test_min_version_omitted_defaults_to_none(self):
        custom, bundle, error = packaging_core._validate_dependency_policy(
            [], [{
                "key": "my_dep", "display_name": "X", "download_url": "https://example.test/x.exe",
                "registry_check": {"path": "SOFTWARE\\X"},
            }], []
        )
        self.assertIsNone(error)
        self.assertIsNone(custom[0]["registry_check"]["min_version"])
        self.assertFalse(custom[0]["registry_check"]["enum_subkeys"])

    def test_sha256_omitted_defaults_to_none(self):
        custom, bundle, error = packaging_core._validate_dependency_policy(
            [], [{
                "key": "my_dep", "display_name": "X", "download_url": "https://example.test/x.exe",
                "registry_check": {"path": "SOFTWARE\\X"},
            }], []
        )
        self.assertIsNone(error)
        self.assertIsNone(custom[0]["sha256"])


class TestBuiltInDependencyKeysFollowDependencyDefs(unittest.TestCase):
    """A3（config schema 單一真實來源）：真實抓到的問題——這個檔案原本
    自己獨立寫死了兩份一模一樣的 `{"vcredist_x64", "dotnet_desktop"}`
    （`_validate_dependency_policy()`/`validate_and_build_pack_data()` 各
    一份），跟 installer_core.py 實際用來下載安裝這兩個相依元件的
    `dependency_defs.BUILT_IN_DEPENDENCIES` 完全脫鉤——哪天 dependency_defs
    新增/移除一個內建相依元件，這裡的驗證邏輯不會自動跟著變，會悄悄跟
    實際能用的相依元件清單不同步。改成從 dependency_defs.BUILT_IN_DEPENDENCIES
    動態算出來，不是自己另外維護一份字面常數。

    這裡不斷言目前的常數值（{"vcredist_x64", "dotnet_desktop"}）本身，
    而是把 dependency_defs.BUILT_IN_DEPENDENCIES 換成一組完全不同的假
    key，驗證 packaging_core.py 的行為真的跟著變——如果這裡還是走自己
    寫死的字面常數，這個測試會照樣通過（因為假 key 不在寫死的常數
    裡），沒辦法真的證明兩邊有沒有掛勾，所以還要反過來確認原本內建的
    「vcredist_x64」在假清單底下不再被當成內建。"""

    def test_custom_dependency_collision_check_follows_dependency_defs(self):
        with mock.patch.object(
            packaging_core.dependency_defs, "BUILT_IN_DEPENDENCIES",
            {"fake_builtin_dep": {"display_name": "Fake", "download_url": "https://x", "silent_args": []}},
        ):
            # 假清單裡有的 key：現在應該被當成內建，跟自訂的撞名。
            _, _, error = packaging_core._validate_dependency_policy(
                [], [{
                    "key": "fake_builtin_dep", "display_name": "X", "download_url": "https://x",
                    "registry_check": {"path": "SOFTWARE\\X"},
                }], []
            )
            self.assertIsNotNone(error)

            # 原本內建的 vcredist_x64：假清單底下已經不算內建了，可以被
            # 自訂相依元件使用同一個 key，不應該再被擋下來。
            custom, _, error = packaging_core._validate_dependency_policy(
                [], [{
                    "key": "vcredist_x64", "display_name": "X", "download_url": "https://x",
                    "registry_check": {"path": "SOFTWARE\\X"},
                }], []
            )
            self.assertIsNone(error)
            self.assertEqual(custom[0]["key"], "vcredist_x64")

    def test_bundle_dependency_known_keys_follow_dependency_defs(self):
        with mock.patch.object(
            packaging_core.dependency_defs, "BUILT_IN_DEPENDENCIES",
            {"fake_builtin_dep": {"display_name": "Fake", "download_url": "https://x", "silent_args": []}},
        ):
            _, bundle, error = packaging_core._validate_dependency_policy(
                ["fake_builtin_dep"], [], ["fake_builtin_dep"]
            )
            self.assertIsNone(error)
            self.assertEqual(bundle, ["fake_builtin_dep"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
