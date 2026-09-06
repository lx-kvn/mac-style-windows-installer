"""授權合約有三份檔案，內容不能各走各的。

`docs/EULA.md` 是給人讀的版本（中英雙語同一份文件），`docs/eula/EULA.zh-TW.txt`
與 `docs/eula/EULA.en.txt` 是實際嵌進安裝檔、會出現在終端使用者畫面上的版本
（`eula_texts` 欄位收的是純文字，Markdown 的標記會原樣顯示）。

同一份合約存在三個地方，就會有其中一份被單獨改掉的一天。合約的內容無法以
測試比對——改寫一個條款的文字並不代表出錯——因此這裡釘住的是**改了一定會
一起改、改漏了一定是錯的那幾項事實**：版本、權利人、準據法、管轄法院、條號
的數量。

`LICENSE` 的著作權人也一併檢查：EULA 指名的權利人與著作權聲明的署名不一致，
是這份合約最容易失效的地方之一。
"""
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKDOWN = os.path.join(REPO_ROOT, "docs", "EULA.md")
PLAIN = {
    "zh-TW": os.path.join(REPO_ROOT, "docs", "eula", "EULA.zh-TW.txt"),
    "en": os.path.join(REPO_ROOT, "docs", "eula", "EULA.en.txt"),
}
LICENSE = os.path.join(REPO_ROOT, "LICENSE")

LICENSOR = "lx-kvn"
COURT_ZH = "臺灣臺中地方法院"
COURT_EN = "Taiwan Taichung District Court"
LAW_ZH = "中華民國自由地區（臺灣）現行法律"
LAW_EN = "free area of the Republic of China (Taiwan)"


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


class EveryCopyExists(unittest.TestCase):
    def test_the_three_files_are_present(self):
        for path in [MARKDOWN] + list(PLAIN.values()):
            self.assertTrue(os.path.isfile(path), path)


class TheyNameTheSameParty(unittest.TestCase):
    def test_the_licensor_is_the_same_everywhere(self):
        for path in [MARKDOWN] + list(PLAIN.values()):
            self.assertIn(LICENSOR, _read(path), path)

    def test_the_copyright_notice_names_that_same_party(self):
        """`LICENSE` 的署名與合約指名的權利人不一致時，合約在講的是另一個人。"""
        self.assertIn(LICENSOR, _read(LICENSE))


class TheySayTheSameLawApplies(unittest.TestCase):
    def test_the_chinese_texts_agree(self):
        for path in (MARKDOWN, PLAIN["zh-TW"]):
            content = _read(path)
            self.assertIn(LAW_ZH, content, path)
            self.assertIn(COURT_ZH, content, path)

    def test_the_english_texts_agree(self):
        for path in (MARKDOWN, PLAIN["en"]):
            content = _read(path)
            self.assertIn(LAW_EN, content, path)
            self.assertIn(COURT_EN, content, path)


class TheyCarryTheSameVersion(unittest.TestCase):
    """版本行是使用者唯一能分辨自己同意的是哪一版的依據。"""

    # 三份的寫法不同（中文用全形分隔號、英文用半形加空白），因此先把分隔符與
    # 空白正規化再比對——要釘住的是版本號與日期，不是排版。
    VERSION = re.compile(r"(\d+\.\d+)\|(\d{4}-\d{2}-\d{2})")

    def _version_of(self, path):
        text = (_read(path).replace("版本 ", "").replace("Version ", "")
                .replace("｜", "|").replace(" |", "|").replace("| ", "|"))
        return self.VERSION.search(text)

    def test_all_three_state_one_version(self):
        found = set()
        for path in [MARKDOWN] + list(PLAIN.values()):
            match = self._version_of(path)
            self.assertIsNotNone(match, path)
            found.add(match.group(0))
        self.assertEqual(len(found), 1, f"三份的版本行不一致：{found}")


class TheyHaveTheSameClauses(unittest.TestCase):
    CLAUSES = 17

    def test_the_chinese_plain_text_has_every_clause(self):
        content = _read(PLAIN["zh-TW"])
        numerals = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
                    "十一", "十二", "十三", "十四", "十五", "十六", "十七"]
        for numeral in numerals[:self.CLAUSES]:
            self.assertIn(f"第{numeral}條", content, numeral)

    def test_the_english_plain_text_has_every_clause(self):
        content = _read(PLAIN["en"])
        for number in range(1, self.CLAUSES + 1):
            self.assertRegex(content, rf"(?m)^{number}\. ", str(number))

    def test_the_markdown_has_every_clause_in_both_languages(self):
        content = _read(MARKDOWN)
        self.assertEqual(len(re.findall(r"### 第[一二三四五六七八九十]+條", content)),
                         self.CLAUSES)
        self.assertEqual(len(re.findall(r"(?m)^### \d+\. ", content)), self.CLAUSES)

    def test_the_two_languages_split_each_clause_the_same_way(self):
        """同一條在兩種語言底下要分成同樣多款。

        使用者實際看出來的問題：中文半段是「條→款」分項編號，英文半段卻寫成
        整段散文，同一份合約兩種長相，而文件開頭寫著兩者內容一致。條文的字數
        本來就會因語言而不同，但「這一條拆成幾款」是結構，不該隨語言改變
        ——不一致時，逐條對照就對不起來。
        """
        chinese = self._sub_items(_read(PLAIN["zh-TW"]),
                                  r"^第([一二三四五六七八九十]+)條",
                                  r"^(\d+)\. ")
        english = self._sub_items(_read(PLAIN["en"]),
                                  r"^(\d+)\. [A-Z]",
                                  r"^(\d+)\.(\d+) ")
        self.assertEqual(len(chinese), self.CLAUSES)
        self.assertEqual(len(english), self.CLAUSES)
        for index, (zh_count, en_count) in enumerate(zip(chinese, english), start=1):
            self.assertEqual(zh_count, en_count,
                             f"第 {index} 條：中文 {zh_count} 款、英文 {en_count} 款")

    @staticmethod
    def _sub_items(text, clause_pattern, item_pattern):
        """回傳每一條底下的款數，依條號順序。"""
        clause = re.compile(clause_pattern)
        item = re.compile(item_pattern)
        counts = []
        for line in text.splitlines():
            stripped = line.strip()
            if clause.match(stripped):
                counts.append(0)
            elif counts and item.match(stripped):
                counts[-1] += 1
        return counts


class ThePlainTextIsActuallyPlain(unittest.TestCase):
    """嵌進安裝檔的那兩份不能帶 Markdown 標記——條款框原樣顯示，`**` 會被看見。"""

    def test_no_markdown_emphasis_or_headings(self):
        for lang, path in PLAIN.items():
            content = _read(path)
            self.assertNotIn("**", content, lang)
            self.assertNotRegex(content, r"(?m)^#{1,6} ", lang)
            self.assertNotRegex(content, r"\[[^\]]+\]\([^)]+\)", lang)

    def test_paragraphs_are_not_hard_wrapped(self):
        """段落內不硬換行：安裝畫面的條款框自己會斷行，兩層疊起來會排出一堆
        只有兩三個字的短行（實際看過才發現）。條款正文因此必然有長行。"""
        for lang, path in PLAIN.items():
            longest = max(len(line) for line in _read(path).splitlines())
            self.assertGreater(longest, 120, lang)


class ThePackagingFlowCanTakeTheAgreementFromAConfigFile(unittest.TestCase):
    """`/released` 的打包步驟靠「設定檔帶條款、旗標帶其餘」這個組合。

    `eula_texts` 是巢狀結構，沒有對應的命令列旗標，因此發布流程改成先寫一份
    只帶那個欄位的設定檔再以 `--config` 傳入。這條測試釘住那個組合成立——
    合併規則若哪天改成「有設定檔就忽略旗標」（或反過來），這個專案自己的
    安裝檔會安靜地少掉授權條款那一頁：`eula_texts` 為空時的行為就是不顯示，
    不會報錯。
    """

    def test_the_config_supplies_the_agreement_while_flags_supply_the_rest(self):
        import json
        import sys
        import tempfile

        sys.path.insert(0, REPO_ROOT)
        import builder_cli

        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, "eula_config.json")
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump({
                    "eula_texts": {lang: _read(path)
                                   for lang, path in PLAIN.items()},
                    "eula_default_lang": "en",
                }, f, ensure_ascii=False)

            args = builder_cli.build_arg_parser().parse_args([
                "pack", "--config", config_path,
                "--app-dir", tmp, "--app-name", "mac-style-windows-installer",
                "--version", "1.2.3", "--main-exe", "mswi-gui.exe",
            ])
            data, app_dir, _png, _ico, _doc = builder_cli._load_pack_input(args)

        self.assertIn(LICENSOR, data["eula_texts"]["zh-TW"])
        self.assertIn(LICENSOR, data["eula_texts"]["en"])
        self.assertEqual(data["eula_default_lang"], "en")
        # 旗標仍然生效——設定檔沒有這些欄位，兩邊是合併不是二選一。
        self.assertEqual(data["app_name"], "mac-style-windows-installer")
        self.assertEqual(data["version"], "1.2.3")
        self.assertEqual(app_dir, args.app_dir)


if __name__ == "__main__":
    unittest.main(verbosity=2)
