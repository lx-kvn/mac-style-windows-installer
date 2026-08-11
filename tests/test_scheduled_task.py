"""scheduled_task.py 的測試：排程工作建立/移除原語（schtasks.exe 包裝）。

全程 mock subprocess.run，不會真的呼叫 schtasks.exe。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scheduled_task


class TestCreateScheduledTask(unittest.TestCase):
    def test_builds_schtasks_create_command_with_default_trigger(self):
        with mock.patch("scheduled_task.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            result = scheduled_task.create_scheduled_task("MyTask", r"C:\Apps\MyApp\task.exe")

        self.assertTrue(result)
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[:3], ["schtasks.exe", "/create", "/tn"])
        self.assertIn("MyTask", cmd)
        self.assertIn("/tr", cmd)
        self.assertEqual(cmd[cmd.index("/tr") + 1], r"C:\Apps\MyApp\task.exe")
        self.assertIn("/sc", cmd)
        self.assertEqual(cmd[cmd.index("/sc") + 1], "onlogon")
        self.assertIn("/f", cmd)

    def test_custom_trigger_is_passed_through(self):
        with mock.patch("scheduled_task.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            scheduled_task.create_scheduled_task("MyTask", "task.exe", trigger="daily")

        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[cmd.index("/sc") + 1], "daily")

    def test_uses_create_no_window_flag(self):
        with mock.patch("scheduled_task.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            scheduled_task.create_scheduled_task("MyTask", "task.exe")

        self.assertIn("creationflags", mock_run.call_args.kwargs)

    def test_nonzero_returncode_is_reported_as_failure(self):
        with mock.patch("scheduled_task.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=1)
            result = scheduled_task.create_scheduled_task("MyTask", "task.exe")

        self.assertFalse(result)

    def test_exception_is_swallowed_and_reported_as_failure(self):
        with mock.patch("scheduled_task.subprocess.run", side_effect=OSError("boom")):
            result = scheduled_task.create_scheduled_task("MyTask", "task.exe")

        self.assertFalse(result)


class TestRemoveScheduledTask(unittest.TestCase):
    def test_builds_schtasks_delete_command(self):
        with mock.patch("scheduled_task.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            result = scheduled_task.remove_scheduled_task("MyTask")

        self.assertTrue(result)
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd, ["schtasks.exe", "/delete", "/tn", "MyTask", "/f"])

    def test_nonzero_returncode_is_reported_as_failure(self):
        with mock.patch("scheduled_task.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=1)
            result = scheduled_task.remove_scheduled_task("MyTask")

        self.assertFalse(result)

    def test_exception_is_swallowed_and_reported_as_failure(self):
        with mock.patch("scheduled_task.subprocess.run", side_effect=OSError("boom")):
            result = scheduled_task.remove_scheduled_task("MyTask")

        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
