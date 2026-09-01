"""png_size.py 的測試：讀出 PNG 的像素尺寸。

第五輪決議第一項要求兩項檢查：圖示必須是正方形（三個宣告位置皆為正方形，
長方形圖會被拉扁），且邊長不得小於 150 像素（低於此值即發生放大，而放大
是會產生明顯劣化的操作）。

要做這兩項檢查就得知道圖片的尺寸，而本專案沒有影像處理能力（第五輪決議
第一項明載，且該決議正是以「不為此引進影像處理相依」為理由成立的）。所幸
不需要——PNG 的寬高就寫在檔頭的固定位置，讀出來只需要標準函式庫。
"""
import os
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import png_size


def write_png(path, width, height):
    raw = b"".join(b"\x00" + b"\x00\x00\x00\xff" * width for _ in range(height))

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    blob = b"\x89PNG\r\n\x1a\n"
    blob += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    blob += chunk(b"IDAT", zlib.compress(raw, 1))
    blob += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(blob)
    return path


class ReadSizeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        import shutil
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def path(self, name):
        return os.path.join(self.tmp, name)

    def test_a_square_png(self):
        p = write_png(self.path("a.png"), 256, 256)
        self.assertEqual(png_size.read(p), (256, 256))

    def test_a_rectangular_png(self):
        p = write_png(self.path("b.png"), 320, 200)
        self.assertEqual(png_size.read(p), (320, 200))

    def test_a_one_pixel_png(self):
        p = write_png(self.path("c.png"), 1, 1)
        self.assertEqual(png_size.read(p), (1, 1))

    def test_a_missing_file_raises(self):
        with self.assertRaises(png_size.NotAPng):
            png_size.read(self.path("nope.png"))

    def test_a_file_that_is_not_a_png_raises(self):
        """副檔名是 .png 不代表內容是 PNG。既有的驗證只看副檔名，那讓一顆
        改過副檔名的 JPEG 一路走到 makeappx 才失敗。"""
        p = self.path("fake.png")
        with open(p, "wb") as f:
            f.write(b"\xff\xd8\xff\xe0 this is a JPEG header")
        with self.assertRaises(png_size.NotAPng):
            png_size.read(p)

    def test_a_truncated_png_raises_rather_than_returning_garbage(self):
        p = self.path("short.png")
        with open(p, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4)
        with self.assertRaises(png_size.NotAPng):
            png_size.read(p)

    def test_the_first_chunk_must_be_the_header_chunk(self):
        """規格要求 IHDR 是第一個區塊。不檢查的話，遇到不符規格的檔案會把
        另一個區塊的內容當成寬高讀出來，得到一組看起來合理的假尺寸。"""
        p = self.path("wrongchunk.png")
        with open(p, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
            f.write(struct.pack(">I", 13) + b"IDAT" + b"\x00" * 13 + b"\x00" * 4)
        with self.assertRaises(png_size.NotAPng):
            png_size.read(p)


class DescribeProblemTest(unittest.TestCase):
    """把「尺寸合不合格」的判斷跟訊息放在一起，呼叫端不必各自組一次。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        import shutil
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def path(self, name):
        return os.path.join(self.tmp, name)

    def test_a_square_icon_of_sufficient_size_passes(self):
        p = write_png(self.path("ok.png"), 256, 256)
        self.assertIsNone(png_size.describe_problem(p, minimum=150))

    def test_exactly_the_minimum_passes(self):
        p = write_png(self.path("exact.png"), 150, 150)
        self.assertIsNone(png_size.describe_problem(p, minimum=150))

    def test_a_rectangular_icon_is_rejected(self):
        p = write_png(self.path("wide.png"), 300, 150)
        problem = png_size.describe_problem(p, minimum=150)
        self.assertIsNotNone(problem)
        self.assertIn("正方形", problem)
        self.assertIn("300", problem)

    def test_a_too_small_icon_is_rejected(self):
        p = write_png(self.path("small.png"), 64, 64)
        problem = png_size.describe_problem(p, minimum=150)
        self.assertIsNotNone(problem)
        self.assertIn("150", problem)
        self.assertIn("64", problem)

    def test_the_message_explains_why_small_is_a_problem(self):
        """縮小是安全的、放大不是——那正是第五輪決議第一項成立的理由。
        訊息不說明的話，使用者會以為這是個沒有必要的刁難。"""
        p = write_png(self.path("small.png"), 64, 64)
        self.assertIn("放大", png_size.describe_problem(p, minimum=150))

    def test_an_unreadable_file_is_reported_rather_than_raising(self):
        p = self.path("bad.png")
        with open(p, "wb") as f:
            f.write(b"not a png at all")
        problem = png_size.describe_problem(p, minimum=150)
        self.assertIsNotNone(problem)


if __name__ == "__main__":
    unittest.main()
