"""restore_point.py 的測試：安裝前建立系統還原點（SRSetRestorePointW 包裝）。

全程注入假的 srclient DLL 物件（跟 restart_manager.py 的 rm_dll 參數同一種
seam 風格），不會真的呼叫系統 API、不會真的在這台開發機建立還原點。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import restore_point


class _FakeSrclient:
    """模擬 srclient.dll：紀錄每次呼叫收到的 RESTOREPOINTINFOW 內容，讓測試
    可以斷言呼叫順序跟參數（BEGIN 在先、END 用 BEGIN 回傳的序號）。
    """

    def __init__(self, begin_succeeds=True, end_succeeds=True, begin_sequence=42):
        self.begin_succeeds = begin_succeeds
        self.end_succeeds = end_succeeds
        self.begin_sequence = begin_sequence
        self.calls = []

    def SRSetRestorePointW(self, p_info, p_status):
        info = p_info.contents
        status = p_status.contents
        self.calls.append({
            "dwEventType": info.dwEventType,
            "dwRestorePtType": info.dwRestorePtType,
            "llSequenceNumber": info.llSequenceNumber,
            "szDescription": info.szDescription,
        })
        if info.dwEventType == restore_point._BEGIN_SYSTEM_CHANGE:
            status.llSequenceNumber = self.begin_sequence
            status.nStatus = 0
            return 1 if self.begin_succeeds else 0
        status.nStatus = 0
        return 1 if self.end_succeeds else 0


class TestCreateRestorePoint(unittest.TestCase):
    def test_calls_begin_then_end_with_matching_sequence_number(self):
        fake = _FakeSrclient(begin_sequence=99)
        result = restore_point.create_restore_point("安裝 MyApp", srclient_dll=fake)

        self.assertTrue(result)
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(fake.calls[0]["dwEventType"], restore_point._BEGIN_SYSTEM_CHANGE)
        self.assertEqual(fake.calls[1]["dwEventType"], restore_point._END_SYSTEM_CHANGE)
        self.assertEqual(fake.calls[1]["llSequenceNumber"], 99)

    def test_description_is_passed_through(self):
        fake = _FakeSrclient()
        restore_point.create_restore_point("安裝 MyApp", srclient_dll=fake)
        self.assertEqual(fake.calls[0]["szDescription"], "安裝 MyApp")

    def test_uses_application_install_restore_point_type(self):
        fake = _FakeSrclient()
        restore_point.create_restore_point("安裝 MyApp", srclient_dll=fake)
        self.assertEqual(fake.calls[0]["dwRestorePtType"], restore_point._APPLICATION_INSTALL)

    def test_begin_failure_short_circuits_before_end_call(self):
        fake = _FakeSrclient(begin_succeeds=False)
        result = restore_point.create_restore_point("安裝 MyApp", srclient_dll=fake)

        self.assertFalse(result)
        self.assertEqual(len(fake.calls), 1)

    def test_end_failure_is_reported_as_failure(self):
        fake = _FakeSrclient(end_succeeds=False)
        result = restore_point.create_restore_point("安裝 MyApp", srclient_dll=fake)

        self.assertFalse(result)
        self.assertEqual(len(fake.calls), 2)

    def test_dll_load_failure_is_swallowed(self):
        with mock.patch("restore_point._srclient", side_effect=OSError("找不到 srclient.dll")):
            result = restore_point.create_restore_point("安裝 MyApp")
        self.assertFalse(result)

    def test_unexpected_exception_during_call_is_swallowed(self):
        fake = mock.Mock()
        fake.SRSetRestorePointW.side_effect = RuntimeError("boom")
        result = restore_point.create_restore_point("安裝 MyApp", srclient_dll=fake)
        self.assertFalse(result)

    def test_calls_co_initialize_security_before_srsetrestorepoint(self):
        """真實抓到的問題：Microsoft 官方文件明講呼叫 SRSetRestorePoint
        之前必須先呼叫 CoInitializeSecurity，允許 NetworkService/
        LocalService/System 回呼目前行程，否則這個 API「無法正常運作」
        （文件原文）。這裡驗證有呼叫，且發生在 SRSetRestorePointW 之前。
        """
        fake = _FakeSrclient()
        with mock.patch("restore_point.ctypes.windll.ole32.CoInitializeSecurity") as mock_security:
            restore_point.create_restore_point("安裝 MyApp", srclient_dll=fake)
        mock_security.assert_called_once()

    def test_co_initialize_security_failure_does_not_block_restore_point(self):
        """CoInitializeSecurity 在同一個行程裡只能成功呼叫一次，第二次
        （例如同一個行程已經因為其他原因初始化過 COM 安全性）會回傳
        RPC_E_TOO_LATE——這是預期內、可以忽略的情況，不該讓還原點整個
        建立失敗。"""
        fake = _FakeSrclient()
        with mock.patch("restore_point.ctypes.windll.ole32.CoInitializeSecurity",
                         side_effect=OSError("RPC_E_TOO_LATE")):
            result = restore_point.create_restore_point("安裝 MyApp", srclient_dll=fake)
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
