import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import install_journal


class TestInstallJournal(unittest.TestCase):
    def test_unwind_calls_undo_in_reverse_order_of_record(self):
        calls = []
        journal = install_journal.InstallJournal()
        journal.record("first", lambda: calls.append("first"))
        journal.record("second", lambda: calls.append("second"))
        journal.record("third", lambda: calls.append("third"))
        journal.unwind()
        self.assertEqual(calls, ["third", "second", "first"])

    def test_unwind_with_no_recorded_entries_does_nothing(self):
        journal = install_journal.InstallJournal()
        journal.unwind()  # 不應該拋出

    def test_a_failing_undo_does_not_stop_the_rest_from_unwinding(self):
        """真實會踩到的情境：其中一個復原動作失敗（例如 sc.exe 逾時），
        不能因此讓後面（其實照順序應該更早）記錄的動作完全沒被復原。"""
        calls = []
        journal = install_journal.InstallJournal()
        journal.record("ok-1", lambda: calls.append("ok-1"))
        journal.record("boom", mock.Mock(side_effect=RuntimeError("boom")))
        journal.record("ok-2", lambda: calls.append("ok-2"))
        journal.unwind()
        self.assertEqual(calls, ["ok-2", "ok-1"])

    def test_failing_undo_is_logged_with_its_description(self):
        journal = install_journal.InstallJournal()
        journal.record("Windows 服務: MySvc", mock.Mock(side_effect=RuntimeError("sc.exe timeout")))
        messages = []
        journal.unwind(log=messages.append)
        self.assertEqual(len(messages), 1)
        self.assertIn("Windows 服務: MySvc", messages[0])
        self.assertIn("sc.exe timeout", messages[0])

    def test_unwind_without_log_argument_does_not_raise_on_failure(self):
        journal = install_journal.InstallJournal()
        journal.record("boom", mock.Mock(side_effect=RuntimeError("boom")))
        journal.unwind()  # 沒帶 log 時，失敗訊息單純沒地方去，不應該拋出

    def test_unwind_clears_entries_so_a_second_call_does_nothing(self):
        calls = []
        journal = install_journal.InstallJournal()
        journal.record("only", lambda: calls.append("only"))
        journal.unwind()
        journal.unwind()
        self.assertEqual(calls, ["only"])


if __name__ == "__main__":
    unittest.main()
