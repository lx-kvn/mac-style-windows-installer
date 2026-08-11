"""bits_download.py 的測試：用 BITS 下載相依元件安裝檔，取代 urllib。

全程注入假的 BackgroundCopyManager/job COM 物件（跟 explorer_lock_release.py
的 shell_factory 同一種 seam 風格），不會真的呼叫 BITS，也不會真的睡眠等待
（mock 掉 time.sleep）。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bits_download


class TestBgJobStateConstantsMatchMicrosoftSpec(unittest.TestCase):
    """對照 Microsoft 官方文件的 BG_JOB_STATE enum（bits.h）逐一驗證，不跟
    模組自己的常數比較——如果常數本身就寫錯，跟自己比對永遠不會發現。

    https://learn.microsoft.com/en-us/windows/win32/api/bits/ne-bits-bg_job_state
    QUEUED=0 CONNECTING=1 TRANSFERRING=2 SUSPENDED=3 ERROR=4
    TRANSIENT_ERROR=5 TRANSFERRED=6 ACKNOWLEDGED=7 CANCELLED=8

    真實抓到的 bug：這個模組原本把 TRANSFERRED/TRANSIENT_ERROR 兩個值對調，
    導致下載成功被誤判為錯誤（取消掉已下載的內容，重新用 urllib 下載一次），
    下載途中的暫時性錯誤反而被誤判為成功（執行一個被截斷的 .exe）。
    """

    def test_transferring_is_2(self):
        self.assertEqual(bits_download._BG_JOB_STATE_TRANSFERRING, 2)

    def test_error_is_4(self):
        self.assertEqual(bits_download._BG_JOB_STATE_ERROR, 4)

    def test_transient_error_is_5(self):
        self.assertEqual(bits_download._BG_JOB_STATE_TRANSIENT_ERROR, 5)

    def test_transferred_is_6(self):
        self.assertEqual(bits_download._BG_JOB_STATE_TRANSFERRED, 6)


class _FakeProgress:
    def __init__(self, transferred, total):
        self.BytesTransferred = transferred
        self.BytesTotal = total


class _FakeJob:
    def __init__(self, states, progresses=None):
        self._states = list(states)
        self._progresses = progresses or []
        self.resumed = False
        self.completed = False
        self.cancelled = False
        self.added_file = None

    def AddFile(self, url, dest_path):
        self.added_file = (url, dest_path)

    def Resume(self):
        self.resumed = True

    def GetState(self):
        return self._states.pop(0) if len(self._states) > 1 else self._states[0]

    def GetProgress(self):
        if self._progresses:
            return self._progresses.pop(0) if len(self._progresses) > 1 else self._progresses[0]
        return _FakeProgress(0, 0)

    def Complete(self):
        self.completed = True

    def Cancel(self):
        self.cancelled = True


class _FakeBcm:
    def __init__(self, job):
        self.job = job
        self.created_with = None

    def CreateJob(self, name, job_type):
        self.created_with = (name, job_type)
        return self.job


class TestDownloadViaBits(unittest.TestCase):
    def test_success_creates_job_adds_file_and_completes(self):
        job = _FakeJob(states=[bits_download._BG_JOB_STATE_TRANSFERRED])
        bcm = _FakeBcm(job)
        with mock.patch("bits_download.time.sleep"):
            result = bits_download.download_via_bits(
                "https://example.test/vc.exe", "C:\\tmp\\vc.exe", bcm_factory=lambda: bcm,
            )

        self.assertTrue(result)
        self.assertEqual(job.added_file, ("https://example.test/vc.exe", "C:\\tmp\\vc.exe"))
        self.assertTrue(job.resumed)
        self.assertTrue(job.completed)

    def test_reports_progress_percentage_while_transferring(self):
        job = _FakeJob(
            states=[bits_download._BG_JOB_STATE_TRANSFERRING, bits_download._BG_JOB_STATE_TRANSFERRED],
            progresses=[_FakeProgress(50, 200)],
        )
        bcm = _FakeBcm(job)
        progress_calls = []
        with mock.patch("bits_download.time.sleep"):
            bits_download.download_via_bits(
                "https://example.test/vc.exe", "C:\\tmp\\vc.exe",
                on_progress=progress_calls.append, bcm_factory=lambda: bcm,
            )

        self.assertIn(25, progress_calls)  # 50/200 = 25%
        self.assertIn(100, progress_calls)  # 完成時回報 100%

    def test_error_state_cancels_job_and_returns_false(self):
        job = _FakeJob(states=[bits_download._BG_JOB_STATE_ERROR])
        bcm = _FakeBcm(job)
        with mock.patch("bits_download.time.sleep"):
            result = bits_download.download_via_bits(
                "https://example.test/vc.exe", "C:\\tmp\\vc.exe", bcm_factory=lambda: bcm,
            )

        self.assertFalse(result)
        self.assertTrue(job.cancelled)

    def test_bcm_factory_exception_is_swallowed(self):
        with mock.patch("bits_download.time.sleep"):
            result = bits_download.download_via_bits(
                "https://example.test/vc.exe", "C:\\tmp\\vc.exe",
                bcm_factory=lambda: (_ for _ in ()).throw(OSError("pywin32 未安裝")),
            )
        self.assertFalse(result)

    def test_default_factory_used_when_none_given_and_pywin32_missing(self):
        """沒有注入 bcm_factory 時，預設會嘗試 import win32com.client——
        這台測試機不保證有裝 pywin32，呼叫失敗要 best-effort 回傳 False，
        不拋例外中止呼叫端（呼叫端會退回原本的 urllib 下載）。"""
        result = bits_download.download_via_bits("https://example.test/vc.exe", "C:\\tmp\\vc.exe")
        self.assertIsInstance(result, bool)

    def test_job_stuck_queued_forever_times_out_instead_of_hanging(self):
        """真實抓到的 bug：輪詢迴圈原本是 while True，沒有任何時間上限——
        一個卡在 QUEUED/SUSPENDED（例如網路完全斷線、BITS 服務被停用）的
        job 會讓這個函式永遠不回傳，整個安裝流程的 UI 卡死、沒有任何退路。
        它取代的 urllib 下載邏輯原本有 timeout=30，這裡不能倒退。"""
        job = _FakeJob(states=[bits_download._BG_JOB_STATE_TRANSFERRING] * 100000)
        bcm = _FakeBcm(job)
        fake_now = [0.0]

        def fake_monotonic():
            fake_now[0] += 1.0
            return fake_now[0]

        with mock.patch("bits_download.time.sleep"), \
             mock.patch("bits_download.time.monotonic", side_effect=fake_monotonic):
            result = bits_download.download_via_bits(
                "https://example.test/vc.exe", "C:\\tmp\\vc.exe",
                bcm_factory=lambda: bcm, max_wait_seconds=30,
            )

        self.assertFalse(result)
        self.assertTrue(job.cancelled)


if __name__ == "__main__":
    unittest.main(verbosity=2)
