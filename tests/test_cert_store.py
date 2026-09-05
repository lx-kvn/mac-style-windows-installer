"""cert_store.py 的測試：在 Windows 的個人存放區裡定位簽章憑證。

對應 [ADR-0014](../docs/adr/0014-signing-certificate-is-identified-by-thumbprint-only.md)。
這裡測的是「指紋怎麼被正規化、找不到時回傳什麼、找到時帶回哪些欄位」，
不測簽章本身。

**注意這裡講的是個人存放區**（放憑證與私鑰，簽章要用），不是 ADR-0005 談的
信任存放區。兩者是不同的東西，模組也不碰後者。

依賴這台機器上實際有什麼憑證的斷言一律寫成「有的話就檢查、沒有就跳過」：
存放區的內容不在測試的控制範圍內，讓它決定紅綠等於製造一個與程式碼無關的
失敗。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cert_store


class ThumbprintNormalisationTest(unittest.TestCase):
    """使用者會從各種地方複製指紋過來，形式不一。

    `certmgr` 的內容窗格複製出來是每兩個字元一個空格；PowerShell 的
    `Thumbprint` 是連續的大寫；有些文件寫成冒號分隔。三種都要認得——不認的話
    使用者會得到「找不到憑證」，而他手上那張憑證明明就在存放區裡。
    """

    def test_spaces_are_removed(self):
        self.assertEqual(cert_store.normalize_thumbprint("ab cd ef 01"), "ABCDEF01")

    def test_colons_are_removed(self):
        self.assertEqual(cert_store.normalize_thumbprint("ab:cd:ef:01"), "ABCDEF01")

    def test_case_is_folded_up(self):
        self.assertEqual(cert_store.normalize_thumbprint("abcdef01"), "ABCDEF01")

    def test_surrounding_whitespace_is_dropped(self):
        self.assertEqual(cert_store.normalize_thumbprint("  abcdef01  "), "ABCDEF01")

    def test_an_empty_value_stays_empty(self):
        self.assertEqual(cert_store.normalize_thumbprint(""), "")
        self.assertEqual(cert_store.normalize_thumbprint(None), "")


class ThumbprintValidationTest(unittest.TestCase):
    """形式的檢查與「存不存在」分開：前者不必碰存放區，後者要。"""

    def test_a_forty_character_hex_string_is_well_formed(self):
        self.assertIsNone(cert_store.validate_thumbprint("a" * 40))

    def test_a_short_value_is_rejected(self):
        self.assertIsNotNone(cert_store.validate_thumbprint("abcd"))

    def test_a_non_hex_value_is_rejected(self):
        self.assertIsNotNone(cert_store.validate_thumbprint("z" * 40))

    def test_an_empty_value_is_rejected(self):
        self.assertIsNotNone(cert_store.validate_thumbprint(""))

    def test_the_message_follows_the_requested_language(self):
        zh = cert_store.validate_thumbprint("abcd", lang="zh-TW")
        en = cert_store.validate_thumbprint("abcd", lang="en")
        self.assertNotEqual(zh, en)

    def test_a_well_formed_value_in_any_of_the_accepted_shapes_passes(self):
        raw = " ".join("ab" for _ in range(20))
        self.assertIsNone(cert_store.validate_thumbprint(raw))


class ListingTest(unittest.TestCase):
    def setUp(self):
        self.found = cert_store.list_signing_certificates()

    def test_it_returns_a_list(self):
        self.assertIsInstance(self.found, list)

    def test_every_entry_carries_the_fields_the_caller_needs(self):
        for entry in self.found:
            self.assertTrue(entry.thumbprint)
            self.assertEqual(entry.thumbprint, entry.thumbprint.upper())
            self.assertEqual(len(entry.thumbprint), 40)
            self.assertIn(entry.store, (cert_store.CURRENT_USER,
                                        cert_store.LOCAL_MACHINE))
            self.assertIsInstance(entry.has_private_key, bool)

    def test_entries_have_a_subject(self):
        """主體是使用者唯一認得出「這是哪一張」的東西，空的話清單沒有用。"""
        for entry in self.found:
            self.assertTrue(entry.subject.strip(), entry.thumbprint)

    def test_no_thumbprint_appears_twice(self):
        """同一張憑證兩個存放區都有時只回報一次——列出兩行會讓使用者以為
        自己有兩張。"""
        seen = [e.thumbprint for e in self.found]
        self.assertEqual(len(seen), len(set(seen)))


class CodeSigningFilterTest(unittest.TestCase):
    """清單只留真的能簽程式碼的憑證。

    實測抓到的問題：開發機的個人存放區裡有 194 張憑證，絕大多數是 Fiddler
    之類的工具產生的 TLS 憑證。全部列出來，使用者要在裡面找出自己那一張——
    而那些憑證交給 signtool 也簽不了東西。

    判準是憑證的「加強金鑰使用方法」（EKU）：完全沒有宣告 EKU 的憑證不受
    用途限制，可以用；有宣告的則必須含程式碼簽章（`1.3.6.1.5.5.7.3.3`）或
    「任何用途」（`2.5.29.37.0`）。
    """

    def test_a_certificate_without_any_usage_restriction_is_usable(self):
        self.assertTrue(cert_store._can_code_sign([]))

    def test_the_code_signing_usage_is_usable(self):
        self.assertTrue(cert_store._can_code_sign([cert_store.OID_CODE_SIGNING]))

    def test_the_any_usage_marker_is_usable(self):
        self.assertTrue(cert_store._can_code_sign([cert_store.OID_ANY_USAGE]))

    def test_a_server_certificate_is_not_usable(self):
        """Fiddler 產生的那一批就是這種。"""
        self.assertFalse(cert_store._can_code_sign(["1.3.6.1.5.5.7.3.1"]))

    def test_code_signing_among_several_usages_is_usable(self):
        self.assertTrue(cert_store._can_code_sign(
            ["1.3.6.1.5.5.7.3.1", cert_store.OID_CODE_SIGNING]))

    def test_the_listing_does_not_return_an_unusable_pile(self):
        """這條不是斷言確切數量——存放區的內容不在測試的控制範圍內——而是
        釘住「有做過篩選」這件事：TLS 憑證不該出現在簽章憑證的清單上。"""
        for entry in cert_store.list_signing_certificates():
            self.assertTrue(cert_store._can_code_sign(entry.usages), entry.subject)


class FindByThumbprintTest(unittest.TestCase):
    def test_a_thumbprint_that_is_not_there_returns_none(self):
        self.assertIsNone(cert_store.find_by_thumbprint("0" * 40))

    def test_a_malformed_thumbprint_returns_none_rather_than_raising(self):
        """呼叫端已經先做過形式檢查；這裡再拋一次例外只是多一條要接的路。"""
        self.assertIsNone(cert_store.find_by_thumbprint("not-a-thumbprint"))

    def test_an_existing_certificate_is_found_by_its_own_thumbprint(self):
        found = cert_store.list_signing_certificates()
        if not found:
            self.skipTest("這台機器的個人存放區裡沒有憑證")
        first = found[0]
        again = cert_store.find_by_thumbprint(first.thumbprint)
        self.assertIsNotNone(again)
        self.assertEqual(again.thumbprint, first.thumbprint)
        self.assertEqual(again.subject, first.subject)

    def test_the_accepted_shapes_all_find_the_same_certificate(self):
        found = cert_store.list_signing_certificates()
        if not found:
            self.skipTest("這台機器的個人存放區裡沒有憑證")
        raw = found[0].thumbprint
        spaced = " ".join(raw[i:i + 2] for i in range(0, len(raw), 2))
        for shape in (raw.lower(), spaced, ":".join(raw[i:i + 2] for i in range(0, len(raw), 2))):
            hit = cert_store.find_by_thumbprint(shape)
            self.assertIsNotNone(hit, shape)
            self.assertEqual(hit.thumbprint, raw)


class SigntoolArgumentsTest(unittest.TestCase):
    """工具依實際找到的位置決定要不要帶 /sm——那是查得出來的事，不是設定。"""

    def test_a_current_user_certificate_needs_no_machine_store_flag(self):
        args = cert_store.signtool_arguments(
            cert_store.StoreCertificate("AB" * 20, "CN=x", cert_store.CURRENT_USER,
                                        True, "", ()))
        self.assertIn("/sha1", args)
        self.assertIn("AB" * 20, args)
        self.assertNotIn("/sm", args)

    def test_a_local_machine_certificate_needs_the_machine_store_flag(self):
        args = cert_store.signtool_arguments(
            cert_store.StoreCertificate("AB" * 20, "CN=x", cert_store.LOCAL_MACHINE,
                                        True, "", ()))
        self.assertIn("/sm", args)

    def test_no_password_ever_appears_in_the_arguments(self):
        """這個模式存在的理由就是這件事（ADR-0014）。"""
        args = cert_store.signtool_arguments(
            cert_store.StoreCertificate("AB" * 20, "CN=x", cert_store.CURRENT_USER,
                                        True, "", ()))
        self.assertNotIn("/p", args)


if __name__ == "__main__":
    unittest.main()
