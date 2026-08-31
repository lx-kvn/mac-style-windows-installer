"""builder_cli.py 的測試：打包工具 CLI 版本的參數解析、JSON + flag 覆蓋
合併邏輯、環境檢查/驗證失敗時的 exit code。

跟 packaging_core.py 共用的驗證邏輯（validate_and_build_pack_data 的每條
分支）已經在 tests/test_packaging_core.py 測過，這裡只測 CLI 這層自己的
邏輯：JSON 載入、CLI flag 覆蓋規則、need_file_assoc/use_custom_doc_icon
的推斷、環境檢查失敗/驗證失敗的 exit code 與輸出。
"""
import argparse
import io
import os
import sys
import json
import shutil
import tempfile
import unittest
import contextlib
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import builder_cli
import sdk_tools


class TestCmdInit(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.output_path = os.path.join(self.tmp_dir, "config.json")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_writes_template_json_with_expected_keys(self):
        exit_code = builder_cli.main(["init", "--output", self.output_path])
        self.assertEqual(exit_code, 0)
        with open(self.output_path, "r", encoding="utf-8") as f:
            template = json.load(f)
        for key in ("app_name", "main_exe", "eula_texts", "path_target_exe", "add_to_path"):
            self.assertIn(key, template)

    def test_template_covers_every_field_validate_and_build_pack_data_recognizes(self):
        """A3（config schema 單一真實來源）：真實抓到的問題——`init` 產生
        的範本 JSON 原本沒有列出 windows_service/scheduled_task/
        create_restore_point_before_install/dependencies_min_version 這幾個
        比較新的欄位，使用者跑 `builder_cli.py init` 拿到的範本，看起來
        就像這個工具不支援這幾個功能一樣，CLI_USAGE.md 也沒補文件，兩邊
        一起漏（見 F16 investigation 附帶發現的 A3 audit）。這裡改成拿
        packaging_core.validate_and_build_pack_data() 實際會處理、且合理
        預期使用者可能會想在範本裡看到的欄位當作最低限度的清單，
        確保新加的功能欄位不會被漏在範本之外。"""
        exit_code = builder_cli.main(["init", "--output", self.output_path])
        self.assertEqual(exit_code, 0)
        with open(self.output_path, "r", encoding="utf-8") as f:
            template = json.load(f)
        for key in (
            "windows_service", "scheduled_task", "create_restore_point_before_install",
            "dependencies_min_version",
        ):
            self.assertIn(key, template, f"範本缺少欄位：{key}")


class TestCmdListFiles(unittest.TestCase):
    """list-files 子指令：CLI 使用者寫 --local-appdata-files 或 JSON 設定檔
    之前，先查一下 app_dir 底下有哪些檔案可以選，不用自己土法煉鋼翻資料夾。
    掃描邏輯共用 packaging_core.list_app_dir_files()，這裡只測 CLI 這層
    （引數解析、輸出格式、exit code）。"""

    def setUp(self):
        self.app_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.app_dir, ignore_errors=True)

    def test_lists_relative_paths_one_per_line(self):
        os.makedirs(os.path.join(self.app_dir, "tools"))
        with open(os.path.join(self.app_dir, "main.exe"), "wb") as f:
            f.write(b"x")
        with open(os.path.join(self.app_dir, "tools", "cli.exe"), "wb") as f:
            f.write(b"x")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exit_code = builder_cli.main(["list-files", "--app-dir", self.app_dir])

        self.assertEqual(exit_code, 0)
        lines = buf.getvalue().splitlines()
        self.assertIn("main.exe", lines)
        self.assertIn("tools/cli.exe", lines)

    def test_missing_app_dir_reports_message_without_crashing(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exit_code = builder_cli.main(["list-files", "--app-dir", os.path.join(self.app_dir, "nope")])
        self.assertEqual(exit_code, 0)
        self.assertIn("nope", buf.getvalue())


class TestLoadPackInput(unittest.TestCase):
    """_load_pack_input()：JSON 為底，CLI flag 有帶值就覆蓋，這是題目要求
    的「兩者都支援」的合併規則，這裡鎖住覆蓋方向不會反過來。"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmp_dir, "config.json")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write_config(self, data):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def _parse(self, argv):
        parser = builder_cli.build_arg_parser()
        return parser.parse_args(argv)

    def test_cli_flag_overrides_json_value(self):
        self._write_config({"app_name": "FromJSON", "version": "1.0.0"})
        args = self._parse(["pack", "--config", self.config_path, "--app-name", "FromCLI"])
        data, *_ = builder_cli._load_pack_input(args)
        self.assertEqual(data["app_name"], "FromCLI")
        self.assertEqual(data["version"], "1.0.0", "沒被 CLI 覆蓋的欄位要維持 JSON 裡的值")

    def test_no_config_relies_entirely_on_flags(self):
        args = self._parse(["pack", "--app-name", "OnlyCLI", "--app-dir", "C:\\App"])
        data, app_dir, *_ = builder_cli._load_pack_input(args)
        self.assertEqual(data["app_name"], "OnlyCLI")
        self.assertEqual(app_dir, "C:\\App")

    def test_dependencies_csv_parsed_into_list(self):
        args = self._parse(["pack", "--dependencies", "vcredist_x64, dotnet_desktop"])
        data, *_ = builder_cli._load_pack_input(args)
        self.assertEqual(data["dependencies"], ["vcredist_x64", "dotnet_desktop"])

    def test_add_to_path_boolean_optional_flag(self):
        args = self._parse(["pack", "--add-to-path"])
        data, *_ = builder_cli._load_pack_input(args)
        self.assertTrue(data["add_to_path"])

        args = self._parse(["pack", "--no-add-to-path"])
        data, *_ = builder_cli._load_pack_input(args)
        self.assertFalse(data["add_to_path"])

    def test_need_file_assoc_inferred_from_file_associations(self):
        args = self._parse(["pack", "--file-associations", ".xyz"])
        data, *_ = builder_cli._load_pack_input(args)
        self.assertTrue(data["need_file_assoc"])

    def test_use_custom_doc_icon_inferred_from_doc_icon_path(self):
        args = self._parse(["pack", "--doc-icon", "C:\\icon.ico"])
        data, _, _, _, doc_icon_path_selected = builder_cli._load_pack_input(args)
        self.assertTrue(data["use_custom_doc_icon"])
        self.assertEqual(doc_icon_path_selected, "C:\\icon.ico")

    def test_no_admin_install_flag_actually_enables_it(self):
        """真實抓到的 bug：這個旗標原本用 argparse.BooleanOptionalAction，
        但它的判斷方式是看實際打的 option string 開頭是不是 "--no-"
        （CPython 原始碼：not option_string.startswith("--no-")）——而這個
        旗標自己的名字「--no-admin-install」開頭剛好就是 "--no-"，導致
        不管使用者是想開啟還是關閉，argparse 都會判斷成「關閉」，把值設成
        False。也就是說這個旗標過去在命令列上從來沒有真的生效過，一路被
        silently 解讀反了——直到使用者實測發現「打包出來的東西還是要求
        系統管理員權限、裝到 Program Files」才抓到。鎖住這裡：帶上
        --no-admin-install 之後，解析出來的值必須是 True，不能是 False。"""
        args = self._parse(["pack", "--no-admin-install"])
        data, *_ = builder_cli._load_pack_input(args)
        self.assertTrue(data["no_admin_install"])

    def test_no_admin_install_not_set_when_flag_absent(self):
        args = self._parse(["pack"])
        data, *_ = builder_cli._load_pack_input(args)
        self.assertNotIn("no_admin_install", data)

    def test_custom_install_dir_flag_overrides_json(self):
        self._write_config({"custom_install_dir": "FromJSON"})
        args = self._parse(["pack", "--custom-install-dir", "%APPDATA%\\MyApp", "--config", self.config_path])
        data, *_ = builder_cli._load_pack_input(args)
        self.assertEqual(data["custom_install_dir"], "%APPDATA%\\MyApp")

    def test_local_appdata_files_csv_parsed_into_list(self):
        args = self._parse(["pack", "--local-appdata-files", "cli.exe, tools/helper.exe"])
        data, *_ = builder_cli._load_pack_input(args)
        self.assertEqual(data["local_appdata_files"], ["cli.exe", "tools/helper.exe"])

    def test_local_appdata_files_not_set_when_flag_absent(self):
        args = self._parse(["pack"])
        data, *_ = builder_cli._load_pack_input(args)
        self.assertNotIn("local_appdata_files", data)


class TestCmdPack(unittest.TestCase):
    def setUp(self):
        self.app_dir = tempfile.mkdtemp()
        with open(os.path.join(self.app_dir, "main.exe"), "wb") as f:
            f.write(b"fake")
        self.base_argv = [
            "pack",
            "--app-dir", self.app_dir,
            "--png-icon", "fake.png",
            "--ico-icon", "fake.ico",
            "--app-name", "TestApp",
            "--version", "1.0.0",
            "--publisher", "Tester",
            "--exe-name", "Setup_TestApp",
            "--main-exe", "main.exe",
        ]

    def tearDown(self):
        shutil.rmtree(self.app_dir, ignore_errors=True)

    def _parse(self, extra_argv=()):
        parser = builder_cli.build_arg_parser()
        return parser.parse_args(self.base_argv + list(extra_argv))

    def test_environment_not_ready_returns_nonzero_and_does_not_build(self):
        args = self._parse()
        not_ready_env = {
            "pyinstaller_found": False, "python_found": True, "python_path": "python",
            "webview_found": True, "pywin32_found": True, "ready": False,
        }
        with mock.patch("builder_cli.packaging_core.check_build_environment", return_value=not_ready_env), \
             mock.patch("builder_cli.builder.build_all") as mock_build:
            exit_code = builder_cli.cmd_pack(args)
        self.assertEqual(exit_code, 1)
        mock_build.assert_not_called()

    def test_validation_failure_returns_nonzero_and_does_not_build(self):
        args = self._parse(["--main-exe", "missing.exe"])
        ready_env = {
            "pyinstaller_found": True, "python_found": True, "python_path": "python",
            "webview_found": True, "pywin32_found": True, "ready": True,
        }
        with mock.patch("builder_cli.packaging_core.check_build_environment", return_value=ready_env), \
             mock.patch("builder_cli.builder.build_all") as mock_build:
            exit_code = builder_cli.cmd_pack(args)
        self.assertEqual(exit_code, 1)
        mock_build.assert_not_called()

    def test_success_path_calls_build_all_and_returns_zero(self):
        args = self._parse()
        ready_env = {
            "pyinstaller_found": True, "python_found": True, "python_path": "python",
            "webview_found": True, "pywin32_found": True, "ready": True,
        }
        with mock.patch("builder_cli.packaging_core.check_build_environment", return_value=ready_env), \
             mock.patch("builder_cli.packaging_core.ensure_workspace_files", return_value=None), \
             mock.patch("builder_cli.packaging_core.get_workspace_dir", return_value="C:\\workspace"), \
             mock.patch("builder_cli.builder.build_all") as mock_build:
            exit_code = builder_cli.cmd_pack(args)
        self.assertEqual(exit_code, 0)
        mock_build.assert_called_once()
        self.assertEqual(mock_build.call_args.kwargs["app_name"], "TestApp")
        self.assertEqual(mock_build.call_args.kwargs["main_exe"], "main.exe")

    def test_workspace_prep_failure_returns_nonzero(self):
        args = self._parse()
        ready_env = {
            "pyinstaller_found": True, "python_found": True, "python_path": "python",
            "webview_found": True, "pywin32_found": True, "ready": True,
        }
        with mock.patch("builder_cli.packaging_core.check_build_environment", return_value=ready_env), \
             mock.patch("builder_cli.packaging_core.ensure_workspace_files", return_value="無法準備工作目錄"), \
             mock.patch("builder_cli.builder.build_all") as mock_build:
            exit_code = builder_cli.cmd_pack(args)
        self.assertEqual(exit_code, 1)
        mock_build.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestFetchSdkToolsCommand(unittest.TestCase):
    """`fetch-sdk-tools` 子指令（ADR-0008 決定一：明確要求才下載）。

    取得動作做成獨立子指令而非 pack 的旗標：它是一次性的環境準備動作，
    混進打包指令會使「打包流程不自動下載」這項決定在某些呼叫方式下自相
    矛盾。這裡測的是「指令有沒有把使用者的意思正確轉成 fetch_tools() 的
    參數」，實際下載由 tests/test_sdk_tools.py 涵蓋。
    """

    def test_pack_does_not_fetch_anything(self):
        """打包流程不得自行取得 SDK 工具，這是決定一的核心。"""
        parser = builder_cli.build_arg_parser()
        args = parser.parse_args(["pack", "--app-dir", "x"])
        self.assertFalse(any("fetch" in name for name in vars(args)))

    def test_invokes_fetch_tools_and_returns_zero(self):
        with mock.patch("sdk_tools.fetch_tools") as fetch:
            fetch.return_value = sdk_tools.FetchResult(r"C:\cache\1.0", "1.0", {})
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = builder_cli.main(["fetch-sdk-tools"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(fetch.call_count, 1)

    def test_cache_dir_flag_is_passed_through_as_a_setting_override(self):
        with mock.patch("sdk_tools.fetch_tools") as fetch:
            fetch.return_value = sdk_tools.FetchResult(r"C:\ci\1.0", "1.0", {})
            with contextlib.redirect_stdout(io.StringIO()):
                builder_cli.main(["fetch-sdk-tools", "--cache-dir", r"C:\ci"])
        settings = fetch.call_args.kwargs["settings"]
        self.assertEqual(settings[sdk_tools.SETTING_CACHE_DIR], r"C:\ci")

    def test_force_flag_is_passed_through(self):
        with mock.patch("sdk_tools.fetch_tools") as fetch:
            fetch.return_value = sdk_tools.FetchResult(r"C:\cache\1.0", "1.0", {})
            with contextlib.redirect_stdout(io.StringIO()):
                builder_cli.main(["fetch-sdk-tools", "--force"])
        self.assertTrue(fetch.call_args.kwargs["force"])

    def test_failure_returns_nonzero_and_reports_the_reason(self):
        with mock.patch("sdk_tools.fetch_tools", side_effect=Exception("網路斷了")):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                exit_code = builder_cli.main(["fetch-sdk-tools"])
        self.assertEqual(exit_code, 1)
        self.assertIn("網路斷了", err.getvalue())


class TestPackSdkToolsOverrides(unittest.TestCase):
    """pack 的 --sdk-tools-dir／--sdk-tools-cache-dir。

    需求性質與既有的 --workspace-dir 相同：CI 需要在不改動這台機器的持久
    設定的前提下，指定這一次建置要用哪裡的工具。
    """

    def test_flags_override_the_persisted_settings(self):
        merged = sdk_tools.settings_with_overrides(
            tools_dir=r"C:\flag-tools",
            cache_dir=r"C:\flag-cache",
            settings={sdk_tools.SETTING_TOOLS_DIR: r"C:\persisted", "workspace_dir": r"C:\ws"},
        )
        self.assertEqual(merged[sdk_tools.SETTING_TOOLS_DIR], r"C:\flag-tools")
        self.assertEqual(merged[sdk_tools.SETTING_CACHE_DIR], r"C:\flag-cache")

    def test_absent_flags_leave_the_persisted_settings_alone(self):
        merged = sdk_tools.settings_with_overrides(
            settings={sdk_tools.SETTING_TOOLS_DIR: r"C:\persisted", "workspace_dir": r"C:\ws"},
        )
        self.assertEqual(merged[sdk_tools.SETTING_TOOLS_DIR], r"C:\persisted")
        self.assertEqual(merged["workspace_dir"], r"C:\ws")

    def test_merging_does_not_mutate_the_caller_settings(self):
        original = {sdk_tools.SETTING_TOOLS_DIR: r"C:\persisted"}
        sdk_tools.settings_with_overrides(tools_dir=r"C:\flag", settings=original)
        self.assertEqual(original[sdk_tools.SETTING_TOOLS_DIR], r"C:\persisted")

    def test_pack_hands_the_overrides_to_build_all(self):
        parser = builder_cli.build_arg_parser()
        args = parser.parse_args([
            "pack", "--app-dir", "x",
            "--sdk-tools-dir", r"C:\tools", "--sdk-tools-cache-dir", r"C:\cache",
        ])
        self.assertEqual(args.sdk_tools_dir, r"C:\tools")
        self.assertEqual(args.sdk_tools_cache_dir, r"C:\cache")


class TestHelpTextRenders(unittest.TestCase):
    """真實抓到的缺陷：`--help` 直接拋 ValueError。

    argparse 會把 help 字串當成格式字串做 `%` 展開，說明文字裡寫
    `%LOCALAPPDATA%` 這種路徑就會被當成格式指示詞而爆掉。這個錯誤只在
    使用者實際打 `--help` 時才發生，任何測「指令有沒有做對事」的測試
    都碰不到它。
    """

    def test_top_level_help_renders(self):
        builder_cli.build_arg_parser().format_help()

    def test_every_subcommand_help_renders(self):
        parser = builder_cli.build_arg_parser()
        subparsers = [
            action for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        ]
        self.assertTrue(subparsers, "找不到子指令，這個測試的前提不成立")
        names = []
        for action in subparsers:
            for name, sub in action.choices.items():
                sub.format_help()
                names.append(name)
        for expected in ("init", "list-files", "pack", "fetch-sdk-tools"):
            self.assertIn(expected, names)
