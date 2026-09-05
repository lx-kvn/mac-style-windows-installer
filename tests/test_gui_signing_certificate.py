"""配置精靈的簽章憑證來源二選一（ADR-0014 決定一、五）。

GUI 這邊比命令列多做一件事：存放區那條路做成下拉選單，直接列出兩個個人存放區
裡可以簽章的憑證。會用 GUI 的人通常就是不想碰命令列的那一群，要他先去跑一次
`list-certs` 才拿得到指紋，等於把安全的那條路留給命令列使用者專用。

手法比照 `test_gui_engine_linkage.py`：後端的部分直接呼叫 `ConfigAPI`，前端的
部分靜態解析 HTML——這個專案沒有 JavaScript 測試執行環境。
"""
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cert_store
import gui_config

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_HTML = os.path.join(REPO_ROOT, "ui", "config.html")


def _html():
    with open(CONFIG_HTML, "r", encoding="utf-8") as f:
        return f.read()


def _signing_block(html):
    """收集 signing 那一段的原始碼。

    以「`signing:` 起、下一個頂層欄位止」擷取，不假設中間是哪一種運算式——
    形狀從單一物件變成三元運算之後，寫死大括號的正規表示式會找不到東西，
    而那個失敗指向的是測試自己，不是被測的行為。
    """
    start = html.index("signing: document.getElementById('enable_signing')")
    end = html.index("install_engine: getInstallEngine()", start)
    return html[start:end]

def _certificate(thumbprint="AB" * 20, subject="CN=Tester, O=Tester, C=TW",
                 store=None, not_after="2030-01-01"):
    return cert_store.StoreCertificate(
        thumbprint=thumbprint, subject=subject,
        store=store or cert_store.CURRENT_USER, has_private_key=True,
        not_after=not_after, usages=(cert_store.OID_CODE_SIGNING,))


class TheBackendServesTheCertificateList(unittest.TestCase):
    def setUp(self):
        self.api = gui_config.ConfigAPI()

    def _list(self, found):
        with mock.patch("cert_store.list_signing_certificates", return_value=found):
            return self.api.list_signing_certificates()

    def test_it_returns_one_entry_per_certificate(self):
        got = self._list([_certificate(), _certificate(thumbprint="CD" * 20)])
        self.assertEqual(len(got), 2)

    def test_each_entry_carries_what_the_dropdown_needs(self):
        got = self._list([_certificate()])[0]
        self.assertEqual(got["thumbprint"], "AB" * 20)
        self.assertEqual(got["subject"], "CN=Tester, O=Tester, C=TW")
        self.assertEqual(got["not_after"], "2030-01-01")
        self.assertIn("label", got)

    def test_the_label_lets_a_person_tell_them_apart(self):
        """下拉選單上顯示的那一行。只有指紋的話沒有人分得出哪一張是自己的。"""
        got = self._list([_certificate()])[0]
        self.assertIn("CN=Tester", got["label"])
        self.assertIn("2030-01-01", got["label"])

    def test_an_empty_store_gives_an_empty_list_not_an_error(self):
        self.assertEqual(self._list([]), [])

    def test_a_failure_gives_an_empty_list_rather_than_breaking_the_page(self):
        """存放區讀不到不是致命的——使用者仍然可以改用檔案模式，或自己貼指紋。
        讓它拋例外會讓整個表單停在那裡。"""
        with mock.patch("cert_store.list_signing_certificates",
                        side_effect=OSError("存放區打不開")):
            self.assertEqual(self.api.list_signing_certificates(), [])

    def test_no_private_key_material_ever_leaves_the_backend(self):
        """送進前端的東西會出現在 webview 裡。只送辨識用的欄位。"""
        got = self._list([_certificate()])[0]
        self.assertEqual(set(got), {"thumbprint", "subject", "not_after",
                                    "store", "label"})


class TheFrontEndOffersBothSources(unittest.TestCase):
    def setUp(self):
        self.html = _html()

    def test_there_is_a_choice_between_the_two_sources(self):
        self.assertIn("cert_source_file", self.html)
        self.assertIn("cert_source_store", self.html)

    def test_the_two_choices_are_radio_buttons_in_one_group(self):
        """比照安裝密碼那組的做法——二選一而不是兩組各自獨立的欄位，
        因為兩者互斥，同時填在後端會被擋下。"""
        self.assertRegex(
            self.html,
            r'type="radio"[^>]*name="cert_source"[^>]*id="cert_source_file"')
        self.assertRegex(
            self.html,
            r'type="radio"[^>]*name="cert_source"[^>]*id="cert_source_store"')

    def test_the_store_choice_uses_a_dropdown_not_a_text_box(self):
        """會用 GUI 的人不該被要求去別的地方複製四十個十六進位字元。"""
        self.assertRegex(self.html, r'<select[^>]*id="cert_thumbprint"')

    def test_the_dropdown_is_filled_from_the_backend(self):
        self.assertIn("list_signing_certificates", self.html)

    def test_switching_source_shows_only_the_relevant_fields(self):
        self.assertRegex(self.html, r"function\s+onCertSourceChange\s*\(")

    def test_the_collected_config_carries_the_thumbprint(self):
        block = _signing_block(self.html)
        self.assertIn("cert_thumbprint", block)

    def test_only_the_chosen_source_is_sent(self):
        """兩邊都送出去的話後端會以「兩種來源互斥」擋下建置，而使用者只是
        先前在另一種模式下填過東西。"""
        block = _signing_block(self.html)
        self.assertRegex(block, r"cert_source_store|isCertStoreMode",
                         "送出的內容沒有依選定的來源分岔")

    def test_the_note_mentions_the_command_line_exposure(self):
        """使用者要有辦法知道兩種模式的差別在哪裡（ADR-0014 決定四）。"""
        self.assertIn("命令列", self.html)


if __name__ == "__main__":
    unittest.main()
