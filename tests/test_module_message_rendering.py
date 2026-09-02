"""各模組的訊息真的照要求的語言出來。

`tests/test_message_tables.py` 檢查的是「表本身一致」——鍵集合相同、不為空、
兩種語言不是同一串字。那些檢查擋得住表的漂移，但擋不住**表沒被用到**：
一個模組可以有一張完美的雙語表，而程式碼裡照樣寫死中文字串。

這裡驗的是另一半：呼叫端要求英文時，出來的真的是英文。

各模組只驗一兩個代表性的訊息——逐則斷言內容等於把實作抄一遍，而那種測試
在實作改動時只會製造雜訊，不會抓到問題。
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cert_subject
import messages
import png_size
from _fakes import write_test_png


def _has_chinese(text):
    return any("一" <= ch <= "鿿" for ch in str(text))


class PngSizeSpeaksBothLanguages(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _png(self, name, w, h):
        path = os.path.join(self.tmp, name)
        write_test_png(path, w, h)
        return path

    def test_a_rectangular_icon_is_explained_in_english(self):
        problem = png_size.describe_problem(self._png("wide.png", 300, 150), 150, "en")
        self.assertFalse(_has_chinese(problem), problem)
        self.assertIn("300", problem)

    def test_a_too_small_icon_is_explained_in_english(self):
        problem = png_size.describe_problem(self._png("small.png", 64, 64), 150, "en")
        self.assertFalse(_has_chinese(problem), problem)
        self.assertIn("150", problem)

    def test_a_file_that_is_not_a_png_is_explained_in_english(self):
        path = os.path.join(self.tmp, "fake.png")
        with open(path, "wb") as f:
            f.write(b"\xff\xd8\xff\xe0 not a png")
        self.assertFalse(_has_chinese(png_size.describe_problem(path, 150, "en")))

    def test_the_default_is_still_traditional_chinese(self):
        """不帶語言參數時的行為要與 key 化之前一致。"""
        problem = png_size.describe_problem(self._png("wide.png", 300, 150), 150)
        self.assertIn("正方形", problem)

    def test_the_exception_carries_a_key_not_a_finished_sentence(self):
        """留著現成句子的話，呼叫端會直接印它，翻譯就永遠只做了一半。"""
        path = os.path.join(self.tmp, "bad.png")
        with open(path, "wb") as f:
            f.write(b"nope")
        with self.assertRaises(png_size.NotAPng) as ctx:
            png_size.read(path)
        self.assertTrue(hasattr(ctx.exception, "key"))
        self.assertFalse(_has_chinese(ctx.exception.key))


class CertSubjectSpeaksBothLanguages(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_missing_file_is_explained_in_english(self):
        missing = os.path.join(self.tmp, "nope.pfx")
        with self.assertRaises(cert_subject.CertificateReadError) as ctx:
            cert_subject.read_from_pfx(missing, "pw")
        self.assertFalse(_has_chinese(ctx.exception.localized("en")),
                         ctx.exception.localized("en"))
        self.assertIn("nope.pfx", ctx.exception.localized("en"))

    def test_the_default_rendering_is_still_traditional_chinese(self):
        missing = os.path.join(self.tmp, "nope.pfx")
        with self.assertRaises(cert_subject.CertificateReadError) as ctx:
            cert_subject.read_from_pfx(missing, "pw")
        self.assertIn("找不到", str(ctx.exception))

    def test_the_exception_carries_a_key(self):
        missing = os.path.join(self.tmp, "nope.pfx")
        with self.assertRaises(cert_subject.CertificateReadError) as ctx:
            cert_subject.read_from_pfx(missing, "pw")
        self.assertTrue(hasattr(ctx.exception, "key"))
        self.assertFalse(_has_chinese(ctx.exception.key))


class TheDefaultLanguageIsShared(unittest.TestCase):
    def test_no_module_declares_its_own_default(self):
        """各模組若自訂預設語言，切換語言時會有幾則訊息不跟著換。"""
        for module in (png_size, cert_subject):
            self.assertFalse(
                hasattr(module, "DEFAULT_LANGUAGE"),
                f"{module.__name__} 自己宣告了預設語言，應該用 messages 的")


if __name__ == "__main__":
    unittest.main(verbosity=2)
