"""version_info.py 的測試：PyInstaller --version-file 內容產生邏輯。

只測純函式的字串輸出跟寫檔行為，不涉及真的呼叫 PyInstaller。
"""
import ast
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import version_info


class TestParseVersionTuple(unittest.TestCase):
    def test_three_segments_padded_to_four(self):
        self.assertEqual(version_info._parse_version_tuple("0.12.0"), (0, 12, 0, 0))

    def test_single_segment_padded_to_four(self):
        self.assertEqual(version_info._parse_version_tuple("1"), (1, 0, 0, 0))

    def test_four_segments_kept_as_is(self):
        self.assertEqual(version_info._parse_version_tuple("1.2.3.4"), (1, 2, 3, 4))

    def test_non_numeric_segment_raises_value_error(self):
        with self.assertRaises(ValueError):
            version_info._parse_version_tuple("1.x.0")

    def test_too_many_segments_raises_value_error(self):
        with self.assertRaises(ValueError):
            version_info._parse_version_tuple("1.2.3.4.5")


class TestRenderVersionFile(unittest.TestCase):
    def test_includes_fixed_version_tuples(self):
        content = version_info.render_version_file(
            product_name="mac-style-windows-installer",
            file_version="0.12.0",
            file_description="mac-style-windows-installer GUI",
        )
        self.assertIn("filevers=(0, 12, 0, 0)", content)
        self.assertIn("prodvers=(0, 12, 0, 0)", content)

    def test_product_version_defaults_to_file_version(self):
        content = version_info.render_version_file(
            product_name="X", file_version="1.2.3", file_description="X",
        )
        self.assertIn("prodvers=(1, 2, 3, 0)", content)

    def test_product_version_can_be_overridden(self):
        content = version_info.render_version_file(
            product_name="X", file_version="1.2.3", file_description="X",
            product_version="9.9.9",
        )
        self.assertIn("prodvers=(9, 9, 9, 0)", content)

    def test_string_fields_present(self):
        content = version_info.render_version_file(
            product_name="mac-style-windows-installer",
            file_version="0.12.0",
            file_description="mac-style-windows-installer GUI",
            company_name="lx.k",
            legal_copyright="Copyright © 2026 lx.k",
        )
        self.assertIn("StringStruct('FileDescription', 'mac-style-windows-installer GUI')", content)
        self.assertIn("StringStruct('ProductName', 'mac-style-windows-installer')", content)
        self.assertIn("StringStruct('CompanyName', 'lx.k')", content)
        self.assertIn("StringStruct('LegalCopyright', 'Copyright © 2026 lx.k')", content)

    def test_missing_optional_fields_default_to_empty_string(self):
        content = version_info.render_version_file(
            product_name="X", file_version="1.0.0", file_description="X",
        )
        self.assertIn("StringStruct('CompanyName', '')", content)
        self.assertIn("StringStruct('LegalCopyright', '')", content)

    def test_apostrophe_in_company_name_does_not_break_generated_syntax(self):
        """真實抓到的 bug：欄位值原本用手動 f-string 包一層單引號直接塞進
        去，發行者/應用程式名稱只要含一個單引號（例如 "O'Brien Software"、
        "Nando's"）就會提早把 Python 字串字面值截斷，PyInstaller 讀這份
        version-file 時會因為語法錯誤而編譯失敗——而且是在 dist/ 已經被
        build_all() 清空之後才爆炸。改成用 repr() 讓 Python 自己決定
        怎麼逸出，這裡驗證產生的整份內容本身是合法的 Python 語法（用
        ast.parse 實際解析，而不是只看有沒有拋例外）。"""
        content = version_info.render_version_file(
            product_name="O'Brien Tools", file_version="1.0.0", file_description="d",
            company_name="Nando's", legal_copyright="Copyright © 2026 O'Brien",
        )
        ast.parse(content)  # 語法不合法時這裡會直接拋 SyntaxError

    def test_backslash_in_field_does_not_break_generated_syntax(self):
        content = version_info.render_version_file(
            product_name=r"C:\Weird\Name", file_version="1.0.0", file_description="d",
        )
        ast.parse(content)


class TestWriteVersionFile(unittest.TestCase):
    def test_writes_render_output_to_path(self):
        tmp_dir = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp_dir, "version_info.txt")
            fields = dict(
                product_name="X", file_version="1.0.0", file_description="X",
                company_name="Acme", legal_copyright="Copyright © 2026 Acme",
            )
            version_info.write_version_file(path, **fields)
            with open(path, "r", encoding="utf-8") as f:
                written = f.read()
            self.assertEqual(written, version_info.render_version_file(**fields))
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
