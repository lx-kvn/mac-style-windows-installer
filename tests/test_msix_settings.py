"""msix_settings.py 的測試：MSIX 專屬設定欄位的驗證與正規化。

對應 docs/proposals/MSIX輸出規劃.md 第九輪定案決議（欄位命名）、第二輪決議
第十項（版本號三段補四段、預發布後綴報錯）、第十一項（發行者須與憑證一致）、
第六輪查證結果第一項（最低 Windows 版本預設值），以及
docs/adr/0007（套件身分名稱為獨立必填欄位，格式檢查在
validate_and_build_pack_data() 這個純函式裡執行）。

套件身分名稱的字元規則來自 Microsoft 的 Package String 定義，不是憑印象
寫的——其中「不能以 xn-- 開頭」「不能包含 .xn--」「保留字比對大小寫不敏感」
這幾條憑直覺不會想到。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import msix_settings


class IdentityNameTest(unittest.TestCase):
    """ADR-0007：套件身分名稱為獨立必填欄位，一經發布不可變更。"""

    def bad(self, name):
        return msix_settings.validate_identity_name(name)

    def test_a_typical_name_passes(self):
        self.assertIsNone(self.bad("MyCompany.MyApp"))

    def test_missing_is_rejected_and_the_message_says_it_cannot_be_derived(self):
        """ADR-0007 決定一：不提供由 app_name 推導的預設值——會踩到失效情境
        的使用者，正是不知道需要覆蓋該值的那些人。"""
        error = self.bad("")
        self.assertIsNotNone(error)
        self.assertIn("identity_name", error)

    def test_length_bounds(self):
        self.assertIsNone(self.bad("abc"))
        self.assertIsNone(self.bad("a" * 50))
        self.assertIsNotNone(self.bad("ab"))
        self.assertIsNotNone(self.bad("a" * 51))

    def test_only_letters_digits_dot_and_dash_are_allowed(self):
        self.assertIsNone(self.bad("A-b.C-9"))
        for name in ("my app", "my_app", "我的應用程式", "app!", "app/name", "app+1"):
            with self.subTest(name=name):
                self.assertIsNotNone(self.bad(name), f"{name} 應該被拒絕")

    def test_cannot_end_with_a_dot(self):
        self.assertIsNotNone(self.bad("MyApp."))

    def test_dot_and_double_dot_are_rejected(self):
        """長度下限本來就會擋掉「.」，「..」則要靠這條規則。"""
        self.assertIsNotNone(self.bad("."))
        self.assertIsNotNone(self.bad(".."))

    def test_reserved_device_names_are_rejected(self):
        for name in ("con", "prn", "aux", "nul", "com1", "com9", "lpt1", "lpt9"):
            with self.subTest(name=name):
                self.assertIsNotNone(self.bad(name), f"{name} 是保留字，應該被拒絕")

    def test_reserved_names_are_compared_case_insensitively(self):
        """官方規定 package string 用 ordinal case-insensitive 比較，
        只擋小寫等於漏掉一半。"""
        for name in ("CON", "Prn", "COM9", "LpT9"):
            with self.subTest(name=name):
                self.assertIsNotNone(self.bad(name), f"{name} 應該被拒絕")

    def test_cannot_begin_with_a_reserved_name_followed_by_a_dot(self):
        for name in ("con.MyApp", "COM9.MyApp", "lpt9.MyApp", "nul.Thing"):
            with self.subTest(name=name):
                self.assertIsNotNone(self.bad(name), f"{name} 應該被拒絕")

    def test_a_reserved_word_that_is_merely_a_prefix_is_fine(self):
        """「console」開頭是 con 但不是 con.，不該被誤擋。"""
        self.assertIsNone(self.bad("console.MyApp"))
        self.assertIsNone(self.bad("connect"))

    def test_cannot_begin_with_the_punycode_prefix(self):
        self.assertIsNotNone(self.bad("xn--MyApp"))

    def test_cannot_contain_a_dotted_punycode_prefix(self):
        self.assertIsNotNone(self.bad("MyCompany.xn--MyApp"))

    def test_case_is_preserved_not_rewritten(self):
        """名稱大小寫會被保留（比較時才不分大小寫），工具不該擅自改寫——
        改動它等於改動套件身分。"""
        self.assertIsNone(self.bad("MyCompany.MyApp"))


class QuadVersionTest(unittest.TestCase):
    """第二輪決議第十項：三段自動補四段（無損轉換），預發布後綴報錯。"""

    def test_three_parts_are_padded(self):
        self.assertEqual(msix_settings.to_quad_version("1.2.3"), "1.2.3.0")

    def test_shorter_forms_are_padded_too(self):
        self.assertEqual(msix_settings.to_quad_version("1.2"), "1.2.0.0")
        self.assertEqual(msix_settings.to_quad_version("1"), "1.0.0.0")

    def test_four_parts_are_unchanged(self):
        self.assertEqual(msix_settings.to_quad_version("1.2.3.4"), "1.2.3.4")

    def test_a_prerelease_suffix_is_rejected(self):
        with self.assertRaises(msix_settings.InvalidVersion):
            msix_settings.to_quad_version("1.0.0-rc1")

    def test_the_prerelease_message_explains_why_dropping_the_suffix_is_not_an_option(self):
        """第二輪決議第十項：捨棄後綴會使 1.0.0-rc1 與 1.0.0 版本號相同，
        系統認定為同一版本而不執行升級，且該問題在打包階段不產生任何錯誤。
        訊息若不說明這一點，使用者會認為工具只是懶得處理。
        """
        with self.assertRaises(msix_settings.InvalidVersion) as ctx:
            msix_settings.to_quad_version("1.0.0-rc1")
        self.assertIn("升級", str(ctx.exception))

    def test_each_segment_has_an_upper_bound(self):
        """官方限制每段最大 65535，超過的值要在打包階段擋下來。"""
        self.assertEqual(
            msix_settings.to_quad_version("65535.65535.65535.65535"),
            "65535.65535.65535.65535",
        )
        with self.assertRaises(msix_settings.InvalidVersion):
            msix_settings.to_quad_version("65536.0.0.0")


class MinWindowsVersionTest(unittest.TestCase):
    """第五輪決議第二項＋第六輪查證結果第一項。"""

    def test_unset_falls_back_to_the_verified_default(self):
        normalized, error = msix_settings.validate({"identity_name": "A.B", "certificate_subject": "CN=X"})
        self.assertIsNone(error)
        self.assertEqual(normalized["min_windows_version"], msix_settings.DEFAULT_MIN_WINDOWS_VERSION)

    def test_the_default_is_windows_10_1809(self):
        """第六輪查證：依據是微軟支援矩陣的起點，不是格式的絕對下限。"""
        self.assertEqual(msix_settings.DEFAULT_MIN_WINDOWS_VERSION, "10.0.17763.0")

    def test_an_explicit_value_is_used(self):
        normalized, error = msix_settings.validate({
            "identity_name": "A.B", "certificate_subject": "CN=X",
            "min_windows_version": "10.0.19041.0",
        })
        self.assertIsNone(error)
        self.assertEqual(normalized["min_windows_version"], "10.0.19041.0")

    def test_a_malformed_value_is_rejected(self):
        _, error = msix_settings.validate({
            "identity_name": "A.B", "certificate_subject": "CN=X",
            "min_windows_version": "Windows 11",
        })
        self.assertIsNotNone(error)
        self.assertIn("min_windows_version", error)


class CertificateSubjectTest(unittest.TestCase):
    """第二輪決議第十一項：發行者必須與簽章憑證上記載的名稱完全一致。"""

    def test_missing_is_rejected(self):
        _, error = msix_settings.validate({"identity_name": "A.B"})
        self.assertIsNotNone(error)
        self.assertIn("certificate_subject", error)

    def test_the_message_says_it_must_match_the_certificate(self):
        """不一致時系統拒絕安裝，且其錯誤訊息不指向此原因——所以這裡的
        訊息要把規則講出來。"""
        _, error = msix_settings.validate({"identity_name": "A.B"})
        self.assertIn("憑證", error)


class ReportsEveryProblemTest(unittest.TestCase):
    """跟相容性檢查一致：一次列出全部，不是第一個錯就停。"""

    def test_all_missing_fields_are_listed_together(self):
        _, error = msix_settings.validate({"min_windows_version": "nope"})
        for field in ("identity_name", "certificate_subject", "min_windows_version"):
            self.assertIn(field, error, f"{field} 沒有出現在錯誤訊息裡")


class NormalizedOutputTest(unittest.TestCase):
    def test_whitespace_is_trimmed(self):
        normalized, error = msix_settings.validate({
            "identity_name": "  A.B  ", "certificate_subject": "  CN=X  ",
        })
        self.assertIsNone(error)
        self.assertEqual(normalized["identity_name"], "A.B")
        self.assertEqual(normalized["certificate_subject"], "CN=X")

    def test_a_non_dict_block_is_rejected_rather_than_crashing(self):
        _, error = msix_settings.validate("not a dict")
        self.assertIsNotNone(error)


if __name__ == "__main__":
    unittest.main()


class CertificateSubjectAutoFillTest(unittest.TestCase):
    """第二輪決議第十一項：憑證可取得時自動讀取並填入，並執行一致性檢查。

    兩者都要支援而非僅取其一，原因來自決議三：兩截式流程的第一個步驟即
    產出含發行者宣告的套件清單，而該步驟先於簽章發生——雲端代簽情境下
    憑證不在本機，工具在該時點必須已知發行者為何。
    """

    CERT = "C=TW, O=Demo Org, CN=Demo Co"

    def test_an_unset_field_is_filled_in_from_the_certificate(self):
        normalized, error = msix_settings.validate(
            {"identity_name": "A.B"}, cert_subject=self.CERT,
        )
        self.assertIsNone(error)
        self.assertEqual(normalized["certificate_subject"], self.CERT)

    def test_a_matching_value_passes(self):
        _, error = msix_settings.validate(
            {"identity_name": "A.B", "certificate_subject": self.CERT},
            cert_subject=self.CERT,
        )
        self.assertIsNone(error)

    def test_a_mismatching_value_is_rejected_at_packaging_time(self):
        """不一致時系統拒絕安裝，且其錯誤訊息不指向此原因——所以要在打包
        階段就攔下來，不能等到終端使用者安裝失敗。"""
        _, error = msix_settings.validate(
            {"identity_name": "A.B", "certificate_subject": "CN=Someone Else"},
            cert_subject=self.CERT,
        )
        self.assertIsNotNone(error)
        self.assertIn("CN=Someone Else", error)
        self.assertIn(self.CERT, error)

    def test_the_comparison_is_exact_not_normalized(self):
        """大小寫與空白的差異一樣會讓系統拒絕安裝，工具不該幫忙抹平——
        抹平之後打包會通過，失敗改在終端使用者那邊發生。"""
        _, error = msix_settings.validate(
            {"identity_name": "A.B", "certificate_subject": "c=tw, o=Demo Org, cn=Demo Co"},
            cert_subject=self.CERT,
        )
        self.assertIsNotNone(error)

    def test_without_a_certificate_the_field_stays_required(self):
        """雲端代簽情境：憑證不在本機，使用者自己填。"""
        _, error = msix_settings.validate({"identity_name": "A.B"}, cert_subject=None)
        self.assertIn("certificate_subject", error)

    def test_without_a_certificate_a_user_supplied_value_is_accepted_as_is(self):
        normalized, error = msix_settings.validate(
            {"identity_name": "A.B", "certificate_subject": "CN=Whatever"}, cert_subject=None,
        )
        self.assertIsNone(error)
        self.assertEqual(normalized["certificate_subject"], "CN=Whatever")
