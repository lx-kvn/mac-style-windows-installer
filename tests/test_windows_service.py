"""windows_service.py 的測試：Windows 服務建立/移除原語（sc.exe 包裝）。

全程 mock subprocess.run，不會真的呼叫 sc.exe（要系統管理員權限，也不該
依賴這台開發機的服務控制管理員狀態）。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import windows_service


class TestCreateService(unittest.TestCase):
    def test_builds_sc_create_command_with_binpath_and_start_type(self):
        with mock.patch("windows_service.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            result = windows_service.create_service("MyService", r"C:\Apps\MyApp\service.exe")

        self.assertTrue(result)
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[:3], ["sc.exe", "create", "MyService"])
        self.assertIn("binPath=", cmd)
        self.assertEqual(cmd[cmd.index("binPath=") + 1], r"C:\Apps\MyApp\service.exe")
        self.assertIn("start=", cmd)
        self.assertEqual(cmd[cmd.index("start=") + 1], "auto")

    def test_custom_start_type_is_passed_through(self):
        with mock.patch("windows_service.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            windows_service.create_service("MyService", "svc.exe", start_type="demand")

        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[cmd.index("start=") + 1], "demand")

    def test_display_name_appends_displayname_flag(self):
        with mock.patch("windows_service.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            windows_service.create_service("MyService", "svc.exe", display_name="My Friendly Service")

        cmd = mock_run.call_args[0][0]
        self.assertIn("DisplayName=", cmd)
        self.assertEqual(cmd[cmd.index("DisplayName=") + 1], "My Friendly Service")

    def test_no_display_name_omits_displayname_flag(self):
        with mock.patch("windows_service.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            windows_service.create_service("MyService", "svc.exe")

        cmd = mock_run.call_args[0][0]
        self.assertNotIn("DisplayName=", cmd)

    def test_uses_create_no_window_flag(self):
        with mock.patch("windows_service.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            windows_service.create_service("MyService", "svc.exe")

        self.assertIn("creationflags", mock_run.call_args.kwargs)

    def test_nonzero_returncode_is_reported_as_failure(self):
        with mock.patch("windows_service.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=1)
            result = windows_service.create_service("MyService", "svc.exe")

        self.assertFalse(result)

    def test_exception_is_swallowed_and_reported_as_failure(self):
        with mock.patch("windows_service.subprocess.run", side_effect=OSError("boom")):
            result = windows_service.create_service("MyService", "svc.exe")

        self.assertFalse(result)


class TestRemoveService(unittest.TestCase):
    def test_builds_sc_delete_command(self):
        with mock.patch("windows_service.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            result = windows_service.remove_service("MyService")

        self.assertTrue(result)
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd, ["sc.exe", "delete", "MyService"])

    def test_nonzero_returncode_is_reported_as_failure(self):
        with mock.patch("windows_service.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=1)
            result = windows_service.remove_service("MyService")

        self.assertFalse(result)

    def test_exception_is_swallowed_and_reported_as_failure(self):
        with mock.patch("windows_service.subprocess.run", side_effect=OSError("boom")):
            result = windows_service.remove_service("MyService")

        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
