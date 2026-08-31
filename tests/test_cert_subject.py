"""cert_subject.py 的測試：從簽章憑證讀出套件清單要用的發行者字串。

對應 docs/proposals/MSIX輸出規劃.md 第二輪決議第十一項（發行者必須與簽章
憑證上記載的名稱完全一致，憑證可取得時由工具自動讀取並填入）。

字串形式不是推論出來的，是實測的（見規劃文件「第十輪 spike 結果」）：
Windows 的 crypt32!CertNameToStrW 產生的字串，正是 makeappx 與 signtool
接受的那一個。RFC 4514 的形式（`cryptography` 的 rfc4514_string()）順序
正確但分隔符沒有空格，而且值裡含逗號時用的是反斜線轉義而非雙引號——
makeappx 兩者都拒絕。

這裡的測試只需要主體的 DER，不需要產生金鑰，因此跑得快。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cert_subject

from cryptography import x509
from cryptography.x509.oid import NameOID


def subject_der(*attrs):
    return x509.Name([x509.NameAttribute(oid, value) for oid, value in attrs]).public_bytes()


class SubjectStringFormatTest(unittest.TestCase):
    """實測結果的回歸測試：每一條都是 spike 裡實際打包簽章通過的形式。"""

    def test_single_common_name(self):
        der = subject_der((NameOID.COMMON_NAME, "MswiSimple"))
        self.assertEqual(cert_subject.subject_string_from_der(der), "CN=MswiSimple")

    def test_multiple_rdns_are_reversed_and_separated_by_comma_space(self):
        """宣告順序是 CN, O, C，輸出必須是反序，且分隔符是逗號加空格——
        makeappx 拒絕沒有空格的形式，signtool 拒絕宣告順序的形式。"""
        der = subject_der(
            (NameOID.COMMON_NAME, "Mswi Spike Co"),
            (NameOID.ORGANIZATION_NAME, "Mswi Spike Org"),
            (NameOID.COUNTRY_NAME, "TW"),
        )
        self.assertEqual(
            cert_subject.subject_string_from_der(der),
            "C=TW, O=Mswi Spike Org, CN=Mswi Spike Co",
        )

    def test_a_value_containing_a_comma_is_wrapped_in_double_quotes(self):
        """公司名稱含逗號（Foo, Inc.）是常見情形，而這正是自行實作會寫錯的
        地方：RFC 4514 用反斜線轉義成 CN=Foo\\, Inc.，makeappx 直接拒絕。"""
        der = subject_der((NameOID.COMMON_NAME, "Foo, Inc."), (NameOID.COUNTRY_NAME, "TW"))
        self.assertEqual(cert_subject.subject_string_from_der(der), 'C=TW, CN="Foo, Inc."')

    def test_values_containing_plus_or_equals_are_quoted(self):
        for value, expected in (("A+B Ltd", 'C=TW, CN="A+B Ltd"'), ("X=Y Corp", 'C=TW, CN="X=Y Corp"')):
            with self.subTest(value=value):
                der = subject_der((NameOID.COMMON_NAME, value), (NameOID.COUNTRY_NAME, "TW"))
                self.assertEqual(cert_subject.subject_string_from_der(der), expected)

    def test_embedded_double_quotes_are_doubled(self):
        der = subject_der((NameOID.COMMON_NAME, 'The "Best" Co'), (NameOID.COUNTRY_NAME, "TW"))
        self.assertEqual(cert_subject.subject_string_from_der(der), 'C=TW, CN="The ""Best"" Co"')

    def test_leading_or_trailing_whitespace_is_preserved_by_quoting(self):
        der = subject_der((NameOID.COMMON_NAME, " Spacey "), (NameOID.COUNTRY_NAME, "TW"))
        self.assertEqual(cert_subject.subject_string_from_der(der), 'C=TW, CN=" Spacey "')

    def test_non_ascii_values_pass_through_unquoted(self):
        der = subject_der((NameOID.COMMON_NAME, "測試公司"), (NameOID.COUNTRY_NAME, "TW"))
        self.assertEqual(cert_subject.subject_string_from_der(der), "C=TW, CN=測試公司")

    def test_garbage_input_raises_rather_than_returning_something_plausible(self):
        """讀不出來時必須拋例外：回傳空字串會讓呼叫端把它當成「憑證裡沒有
        主體」，進而寫出一個空的發行者。"""
        with self.assertRaises(cert_subject.CertificateReadError):
            cert_subject.subject_string_from_der(b"not a DER encoded name")


class ReadFromPfxTest(unittest.TestCase):
    """從 .pfx 檔案讀取。這一層很薄，重點在錯誤處理。"""

    def test_a_missing_file_raises_a_clear_error(self):
        with self.assertRaises(cert_subject.CertificateReadError) as ctx:
            cert_subject.read_from_pfx(r"C:\definitely\not\here.pfx", "pw")
        self.assertIn("here.pfx", str(ctx.exception))

    def test_a_wrong_password_raises_a_clear_error(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pfx", delete=False) as f:
            f.write(b"not a real pfx")
            path = f.name
        self.addCleanup(os.remove, path)
        with self.assertRaises(cert_subject.CertificateReadError):
            cert_subject.read_from_pfx(path, "wrong")


if __name__ == "__main__":
    unittest.main()
