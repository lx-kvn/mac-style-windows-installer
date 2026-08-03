"""builder_cli.py 的測試：打包工具 CLI 版本的參數解析、JSON + flag 覆蓋
合併邏輯、環境檢查/驗證失敗時的 exit code。

跟 packaging_core.py 共用的驗證邏輯（validate_and_build_pack_data 的每條
分支）已經在 tests/test_packaging_core.py 測過，這裡只測 CLI 這層自己的
邏輯：JSON 載入、CLI flag 覆蓋規則、need_file_assoc/use_custom_doc_icon
的推斷、環境檢查失敗/驗證失敗的 exit code 與輸出。
"""
import os
import sys
import json
import shutil
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import builder_cli


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
