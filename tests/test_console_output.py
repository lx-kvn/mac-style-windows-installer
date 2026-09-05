"""命令列的輸出不會因為編不出某個字元而讓整個流程失敗。

真實抓到的缺陷（2026-09-05，發布 v0.16.0 時撞到）：
`python build_config_tool.py --cli` 在繁體中文的 Windows 上編譯，中途以

    UnicodeEncodeError: 'cp950' codec can't encode character '\\ufffd'

崩潰，沒有產出任何 exe。

成因是同一條管線的另外一半沒有處理過。`docs/investigations/子行程輸出的解碼修正.md`
處理的是**讀**：子行程的輸出以 `encoding="utf-8", errors="replace"` 解碼，
遇到不合法的位元組換成 `\\ufffd`。那個字元接著被 `print` **寫**到 cp950 的
主控台，而 cp950 編不出它。

CI 涵蓋不到：runner 是英文的，`sys.stdout.encoding` 不是 cp950，永遠不會踩到
（`CLAUDE.md`「CI 驗不到的事情」第二類）。

兩個命令列進入點都會走到——`build_config_tool.py --cli` 與 `builder_cli.py`
都把子行程的輸出交給 `print`。
"""
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import packaging_core

# 讀出來會變成這個字元的位元組，正是實際崩潰時出現的那一個。
REPLACEMENT = "�"


def _strict_cp950_stream():
    """一個編不出 U+FFFD 的輸出串流，模擬繁體中文 Windows 的主控台。"""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp950", errors="strict")


class TheProblemIsReal(unittest.TestCase):
    def test_writing_the_replacement_character_raises_without_the_fix(self):
        """先確認這個測試真的模擬到了那個情境——不然下面幾條會空跑而永遠通過。"""
        stream = _strict_cp950_stream()
        with self.assertRaises(UnicodeEncodeError):
            stream.write(f"pyinstaller 輸出{REPLACEMENT}結尾")
            stream.flush()


class MakeConsoleForgiving(unittest.TestCase):
    def test_a_reconfigured_stream_accepts_the_replacement_character(self):
        stream = _strict_cp950_stream()
        packaging_core.make_console_forgiving(stream)
        stream.write(f"pyinstaller 輸出{REPLACEMENT}結尾\n")
        stream.flush()

    def test_the_rest_of_the_line_survives(self):
        """退化掉的只有那一個編不出來的字元，其餘內容照樣看得到——那一行
        通常正是唯一能說明失敗原因的東西。"""
        stream = _strict_cp950_stream()
        packaging_core.make_console_forgiving(stream)
        stream.write(f"錯誤：{REPLACEMENT}找不到模組 winrt\n")
        stream.flush()
        written = stream.buffer.getvalue().decode("cp950", "replace")
        self.assertIn("找不到模組 winrt", written)
        self.assertIn("錯誤：", written)

    def test_a_stream_that_cannot_be_reconfigured_is_left_alone(self):
        """輸出被導向別的東西時（例如測試把 stdout 換成 StringIO），這個
        呼叫不該拋例外——它的職責是「讓輸出不要害死流程」，自己成為新的
        失敗來源就本末倒置了。"""
        packaging_core.make_console_forgiving(io.StringIO())
        packaging_core.make_console_forgiving(None)

    def test_it_reports_whether_it_did_anything(self):
        stream = _strict_cp950_stream()
        self.assertTrue(packaging_core.make_console_forgiving(stream))
        self.assertFalse(packaging_core.make_console_forgiving(io.StringIO()))


class BothCommandLineEntryPointsUseIt(unittest.TestCase):
    """兩個進入點都要呼叫，漏掉哪一個，那一支就會在中文環境崩潰。

    以靜態解析確認呼叫存在：真的去跑一次編譯要好幾分鐘，而這裡要釘住的是
    「那一行有沒有被寫下來」。
    """

    def _source(self, name):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, name), encoding="utf-8") as f:
            return f.read()

    def test_build_config_tool_makes_its_console_forgiving(self):
        self.assertIn("make_console_forgiving", self._source("build_config_tool.py"))

    def test_builder_cli_makes_its_console_forgiving(self):
        self.assertIn("make_console_forgiving", self._source("builder_cli.py"))

    def test_both_standard_streams_are_covered(self):
        """錯誤訊息走 stderr，而失敗時那一份才是使用者真正需要看到的。"""
        for name in ("build_config_tool.py", "builder_cli.py"):
            source = self._source(name)
            self.assertIn("sys.stdout", source, name)
            self.assertIn("sys.stderr", source, name)


if __name__ == "__main__":
    unittest.main()
