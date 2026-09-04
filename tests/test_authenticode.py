"""authenticode.py 的測試：下載回來、要在使用者機器上執行的檔案，執行之前
先驗它的數位簽章（稽核 S2）。

`verify_file()` 有兩道關卡，測試也分兩層：

- **組織名稱的比對**是純函式，完整測。它有一個不直覺的地方：不能用「主體
  字串裡有沒有出現這段文字」判斷，`O=Microsoft Corporation Fake` 會通過。
- **`WinVerifyTrust` 那一段**是 ctypes，只測機器無關的結局（檔案不存在、
  檔案沒有簽章）。「有效簽章會通過」這件事以本機的 Python 直譯器驗證，該
  檔案不是內嵌簽章時跳過——那個條件依機器而定，讓它變成紅燈只會製造一個
  與程式碼無關的失敗。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import authenticode


class OrganizationMatchTest(unittest.TestCase):
    MICROSOFT = "CN=Microsoft Corporation, O=Microsoft Corporation, L=Redmond, S=Washington, C=US"

    def test_the_expected_organization_matches(self):
        self.assertTrue(authenticode._matches_organization(
            self.MICROSOFT, "Microsoft Corporation"))

    def test_a_different_organization_does_not(self):
        subject = "CN=Python Software Foundation, O=Python Software Foundation, L=Beaverton, S=Oregon, C=US"
        self.assertFalse(authenticode._matches_organization(
            subject, "Microsoft Corporation"))

    def test_a_longer_organization_starting_with_it_does_not_match(self):
        """真正的風險：用「字串裡有沒有出現這段文字」判斷的話，攻擊者只要
        用一張 O=Microsoft Corporation Fake 的憑證就通過了。"""
        subject = "CN=Evil, O=Microsoft Corporation Fake, C=XX"
        self.assertFalse(authenticode._matches_organization(
            subject, "Microsoft Corporation"))

    def test_it_matches_when_the_organization_is_the_last_component(self):
        self.assertTrue(authenticode._matches_organization(
            "CN=Something, O=Microsoft Corporation", "Microsoft Corporation"))

    def test_it_matches_when_the_organization_is_the_first_component(self):
        self.assertTrue(authenticode._matches_organization(
            "O=Microsoft Corporation, C=US", "Microsoft Corporation"))

    def test_a_subject_without_an_organization_does_not_match(self):
        self.assertFalse(authenticode._matches_organization(
            "CN=Microsoft Corporation, C=US", "Microsoft Corporation"))

    def test_an_empty_subject_does_not_match(self):
        self.assertFalse(authenticode._matches_organization("", "Microsoft Corporation"))


class VerifyFileTest(unittest.TestCase):
    def test_a_missing_file_fails_with_a_reason(self):
        ok, reason = authenticode.verify_file("C:\\no\\such\\file.exe")
        self.assertFalse(ok)
        self.assertTrue(reason.strip())

    def test_a_file_without_a_signature_fails(self):
        """`download()` 已經確認過長度，因此走到這裡的檔案是完整的——沒有
        簽章代表它不是我們以為的那個檔案。"""
        handle, path = tempfile.mkstemp(suffix=".exe")
        os.close(handle)
        self.addCleanup(os.remove, path)
        with open(path, "wb") as f:
            f.write(b"MZ" + b"\x00" * 4096)
        ok, reason = authenticode.verify_file(path)
        self.assertFalse(ok)
        self.assertTrue(reason.strip())

    def test_the_failure_reason_never_claims_success(self):
        ok, reason = authenticode.verify_file("C:\\no\\such\\file.exe")
        self.assertFalse(ok)
        self.assertNotIn("通過", reason)

    def test_a_validly_signed_file_passes(self):
        """以本機的 Python 直譯器當樣本。它不是內嵌簽章時跳過——Windows 的
        系統檔案多半是目錄簽章（catalog），`WinVerifyTrust` 以檔案為對象時
        看不到那種簽章，會回報 TRUST_E_NOSIGNATURE。"""
        ok, reason = authenticode.verify_file(sys.executable)
        if not ok and "簽章" in reason:
            self.skipTest(f"這台機器的 {sys.executable} 不是內嵌簽章：{reason}")
        self.assertTrue(ok, reason)

    def test_an_unexpected_organization_is_rejected(self):
        """同一支檔案，換一個不可能相符的組織名稱就要被擋下——這確認第二道
        關卡真的有在把關，不是只有簽章有效與否。"""
        ok, _ = authenticode.verify_file(sys.executable)
        if not ok:
            self.skipTest("這台機器的 Python 直譯器沒有有效的內嵌簽章")
        ok, reason = authenticode.verify_file(
            sys.executable, expected_organization="No Such Organization Ltd")
        self.assertFalse(ok)
        self.assertIn("No Such Organization Ltd", reason)


if __name__ == "__main__":
    unittest.main()
