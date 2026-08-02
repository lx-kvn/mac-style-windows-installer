"""build_config_tool.py 的測試：build_one_exe() 組出來的 PyInstaller 指令、
版本號讀取邏輯（VERSION 檔案存在/不存在）、--cli 非互動模式的驅動邏輯。

不會真的呼叫 pyinstaller（一次真的編譯要數十秒，而且不該依賴外部工具是否
安裝），全程用假的 subprocess.Popen/check_output 頂替。
"""
import os
import sys
import shutil
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import build_config_tool as bct


class FakeCompletedProcess:
    def __init__(self, lines, returncode=0):
        self.stdout = iter(lines)
        self._returncode = returncode

    def wait(self):
        return self._returncode


class TestReadVersion(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_explicit_version_wins(self):
        self.assertEqual(bct.read_version("9.9.9"), "9.9.9")

    def test_reads_version_file_when_no_explicit_version(self):
        version_path = os.path.join(self.tmp_dir, "VERSION")
        with open(version_path, "w", encoding="utf-8") as f:
            f.write("1.2.3\n")
        with mock.patch("build_config_tool.__file__", os.path.join(self.tmp_dir, "build_config_tool.py")):
            self.assertEqual(bct.read_version(None), "1.2.3")

    def test_falls_back_to_dev_default_when_version_file_missing(self):
        with mock.patch("build_config_tool.__file__", os.path.join(self.tmp_dir, "build_config_tool.py")):
            self.assertEqual(bct.read_version(None), "0.0.0-dev")


class TestBuildOneExe(unittest.TestCase):
    def setUp(self):
        self.work_dir = tempfile.mkdtemp()
        self.old_cwd = os.getcwd()
        os.chdir(self.work_dir)

    def tearDown(self):
        os.chdir(self.old_cwd)
        shutil.rmtree(self.work_dir, ignore_errors=True)

    def test_detects_output_exe_already_running(self):
        with mock.patch(
            "build_config_tool.subprocess.check_output",
            return_value="Image Name\nMyTool.exe  1234",
        ):
            success, message, exe_path = bct.build_one_exe("entry.py", "MyTool")
        self.assertFalse(success)
        self.assertIn("正在執行中", message)
        self.assertIsNone(exe_path)

    def test_success_path_builds_command_and_returns_exe_path(self):
        captured_cmd = {}

        def fake_popen(cmd, **kwargs):
            captured_cmd["cmd"] = cmd
            os.makedirs("dist", exist_ok=True)
            with open(os.path.join("dist", "MyTool.exe"), "wb") as f:
                f.write(b"fake exe")
            return FakeCompletedProcess(["line1\n", "line2\n"], returncode=0)

        with mock.patch("build_config_tool.subprocess.check_output", return_value=""), \
             mock.patch("build_config_tool.subprocess.Popen", side_effect=fake_popen):
            success, message, exe_path = bct.build_one_exe(
                "entry.py", "MyTool", extra_add_data=["installer_core.py", "uninstall.py"],
            )

        self.assertTrue(success)
        self.assertTrue(exe_path.endswith("MyTool.exe"))
        cmd = captured_cmd["cmd"]
        self.assertIn("--onefile", cmd)
        self.assertIn("--noconsole", cmd)
        self.assertIn("--name=MyTool", cmd)
        self.assertIn("--add-data=installer_core.py;.", cmd)
        self.assertIn("--add-data=uninstall.py;.", cmd)
        self.assertIn("entry.py", cmd)

    def test_noconsole_false_omits_flag(self):
        captured_cmd = {}

        def fake_popen(cmd, **kwargs):
            captured_cmd["cmd"] = cmd
            os.makedirs("dist", exist_ok=True)
            with open(os.path.join("dist", "MyTool.exe"), "wb") as f:
                f.write(b"fake exe")
            return FakeCompletedProcess([], returncode=0)

        with mock.patch("build_config_tool.subprocess.check_output", return_value=""), \
             mock.patch("build_config_tool.subprocess.Popen", side_effect=fake_popen):
            bct.build_one_exe("entry.py", "MyTool", noconsole=False)

        self.assertNotIn("--noconsole", captured_cmd["cmd"])

    def test_nonzero_returncode_reports_permission_error_specially(self):
        def fake_popen(cmd, **kwargs):
            return FakeCompletedProcess(["Traceback...\n", "PermissionError: blah\n"], returncode=1)

        with mock.patch("build_config_tool.subprocess.check_output", return_value=""), \
             mock.patch("build_config_tool.subprocess.Popen", side_effect=fake_popen):
            success, message, exe_path = bct.build_one_exe("entry.py", "MyTool")

        self.assertFalse(success)
        self.assertIn("存取被拒", message)

    def test_missing_exe_after_success_returncode_is_reported(self):
        def fake_popen(cmd, **kwargs):
            return FakeCompletedProcess([], returncode=0)

        with mock.patch("build_config_tool.subprocess.check_output", return_value=""), \
             mock.patch("build_config_tool.subprocess.Popen", side_effect=fake_popen):
            success, message, exe_path = bct.build_one_exe("entry.py", "MyTool")

        self.assertFalse(success)
        self.assertIn("找不到產出的 exe", message)

    def test_progress_and_log_callbacks_invoked(self):
        def fake_popen(cmd, **kwargs):
            os.makedirs("dist", exist_ok=True)
            with open(os.path.join("dist", "MyTool.exe"), "wb") as f:
                f.write(b"fake exe")
            return FakeCompletedProcess(["hello\n"], returncode=0)

        logs, progresses = [], []
        with mock.patch("build_config_tool.subprocess.check_output", return_value=""), \
             mock.patch("build_config_tool.subprocess.Popen", side_effect=fake_popen):
            bct.build_one_exe(
                "entry.py", "MyTool", on_log=logs.append,
                on_progress=lambda v, s: progresses.append((v, s)),
            )

        self.assertIn("hello", logs)
        self.assertTrue(any(v == 15 for v, s in progresses))

    def test_stale_cleanup_only_touches_own_target_not_other_builds_output(self):
        def make_fake_popen(output_name):
            def fake_popen(cmd, **kwargs):
                os.makedirs("dist", exist_ok=True)
                with open(os.path.join("dist", f"{output_name}.exe"), "wb") as f:
                    f.write(b"fake exe")
                return FakeCompletedProcess([], returncode=0)
            return fake_popen

        with mock.patch("build_config_tool.subprocess.check_output", return_value=""), \
             mock.patch("build_config_tool.subprocess.Popen", side_effect=make_fake_popen("ToolA")):
            success_a, _, exe_path_a = bct.build_one_exe("entry_a.py", "ToolA")
        with mock.patch("build_config_tool.subprocess.check_output", return_value=""), \
             mock.patch("build_config_tool.subprocess.Popen", side_effect=make_fake_popen("ToolB")):
            success_b, _, exe_path_b = bct.build_one_exe("entry_b.py", "ToolB")

        self.assertTrue(success_a)
        self.assertTrue(success_b)
        self.assertTrue(os.path.exists(exe_path_a))
        self.assertTrue(os.path.exists(exe_path_b))


class TestRunCli(unittest.TestCase):
    def test_missing_prerequisites_returns_nonzero_without_building(self):
        with mock.patch("build_config_tool.check_prerequisites", return_value=["找不到 pyinstaller"]), \
             mock.patch("build_config_tool.build_one_exe") as mock_build:
            exit_code = bct.run_cli(version="1.0.0")
        self.assertEqual(exit_code, 1)
        mock_build.assert_not_called()

    def test_builds_both_gui_and_cli_targets_with_version_in_name(self):
        calls = []

        def fake_build_one_exe(entry_script, output_name, **kwargs):
            calls.append((entry_script, output_name))
            return True, "ok", f"dist/{output_name}.exe"

        with mock.patch("build_config_tool.check_prerequisites", return_value=[]), \
             mock.patch("build_config_tool.build_one_exe", side_effect=fake_build_one_exe):
            exit_code = bct.run_cli(version="1.2.3")

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, [
            ("gui_config.py", "mac-style-windows-installer_GUI_v1.2.3"),
            ("builder_cli.py", "mac-style-windows-installer_CLI_v1.2.3"),
        ])

    def test_stops_after_first_failure(self):
        def fake_build_one_exe(entry_script, output_name, **kwargs):
            return False, "編譯失敗", None

        with mock.patch("build_config_tool.check_prerequisites", return_value=[]), \
             mock.patch("build_config_tool.build_one_exe", side_effect=fake_build_one_exe) as mock_build:
            exit_code = bct.run_cli(version="1.2.3")

        self.assertEqual(exit_code, 1)
        mock_build.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
