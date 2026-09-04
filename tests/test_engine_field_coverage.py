"""漂移防線：每一個打包能力都必須被分類過「它在 MSIX 引擎下會怎樣」。

## 為什麼需要這支測試

稽核 D1 的成因（見 `docs/investigations/MSIX稽核與缺陷修正.md`）：
`install_engine.py` 這個模組的職責即為「哪些設定在這個引擎下能用」，其
`_FIELD_CATEGORIES` 表登記了十二個欄位，而安裝密碼保護的三個欄位一個都不在
裡面。介面宣稱回答這個問題，實作只涵蓋一部分——那份設定因此通過打包，產出
一顆在任何機器上都裝不起來的安裝檔。

補上那三個欄位修得了那一次。**修不了的是「下一個新增的欄位會不會又被漏掉」**
——那件事在修正之前沒有任何東西會叫。這支測試就是那個會叫的東西。

## 判準來自哪裡

以 `builder.build_all()` 的參數列為母體。理由是它是這個專案裡「打包能力」
唯一的收斂點：新增一項功能一定要新增一個參數，繞不過去。用打包設定檔的範本
或 GUI 的表單欄位當母體則不然——那兩者都可能先加了欄位、之後才接到 build_all，
中間那段時間母體是不完整的。

每一個參數都必須落在三個清單其中之一，而且只能落在一個：

- `install_engine.incompatible_fields()`——在 MSIX 下不能用或無作用的。
- `install_engine.ENGINE_AGNOSTIC_FIELDS`——兩種引擎下行為相同的。
- `install_engine.ENGINE_PLUMBING_FIELDS`——不是「打包出來的東西長什麼樣」
  的設定，而是建置動作本身的參數（工作目錄、進度回報之類）。

三個清單合起來要恰好等於參數列。少了會紅（有一個能力沒被分類過），多了也
會紅（清單裡有一個已經不存在的參數，那代表分類的依據已經過期）。
"""
import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import builder
import install_engine


def _build_all_parameters():
    return set(inspect.signature(builder.build_all).parameters)


def _classified():
    return install_engine.classified_fields()


class EveryPackagingCapabilityIsClassified(unittest.TestCase):
    def test_no_parameter_is_left_unclassified(self):
        missing = _build_all_parameters() - _classified()
        self.assertEqual(missing, set(), (
            "這些 build_all() 參數沒有被分類過「它在 MSIX 引擎下會怎樣」：\n"
            f"    {sorted(missing)}\n"
            "請把每一個加進 install_engine 的 _FIELD_CATEGORIES／_PASSWORD_FIELDS（不能用或無作用）、"
            "ENGINE_AGNOSTIC_FIELDS（兩種引擎相同）或 ENGINE_PLUMBING_FIELDS"
            "（不是產品設定，是建置動作的參數）其中之一。"
        ))

    def test_no_classification_refers_to_a_parameter_that_is_gone(self):
        """清單裡有一個已經不存在的參數，代表分類的依據已經過期——通常是
        參數被改名了，而分類沒有跟著改。

        `CONFIG_ONLY_FIELDS` 是唯一的例外：那些欄位只存在於打包設定／表單，
        本來就到不了 build_all（見該常數的說明）。
        """
        stale = (_classified() - _build_all_parameters()
                 - set(install_engine.CONFIG_ONLY_FIELDS))
        self.assertEqual(stale, set(),
                         f"這些分類項目在 build_all() 裡已經沒有對應的參數：{sorted(stale)}")

    def test_the_config_only_exception_stays_small(self):
        """那個例外集合是逃生口，放寬到什麼都塞得進去的話，上面那條就失效了。"""
        self.assertLessEqual(len(install_engine.CONFIG_ONLY_FIELDS), 3)

    def test_the_three_lists_do_not_overlap(self):
        """同一個欄位落在兩個清單裡，代表兩處的判斷不一致，而讀的人不會知道
        哪一個才算數。"""
        categories = install_engine.incompatible_fields()
        agnostic = set(install_engine.ENGINE_AGNOSTIC_FIELDS)
        plumbing = set(install_engine.ENGINE_PLUMBING_FIELDS)
        for name, first, second in (
            ("不相容欄位 與 ENGINE_AGNOSTIC_FIELDS", categories, agnostic),
            ("不相容欄位 與 ENGINE_PLUMBING_FIELDS", categories, plumbing),
            ("ENGINE_AGNOSTIC_FIELDS 與 ENGINE_PLUMBING_FIELDS", agnostic, plumbing),
        ):
            self.assertEqual(first & second, set(), f"{name} 重疊")

    def test_the_password_fields_are_covered_by_the_classification(self):
        """稽核 D1 那三個欄位。build_all() 的參數名是 install_password 與
        install_password_env，而分類項目只有 install_password——後者必須被
        涵蓋，不能靠「剛好沒有人去查」。"""
        self.assertIn("install_password", _classified())
        self.assertIn("install_password_env", _classified())


class TheClassificationIsUsableByItself(unittest.TestCase):
    """三個清單不只是給這支測試看的，要真的能拿來回答問題。"""

    def test_the_agnostic_list_is_not_empty(self):
        """空的話上面那幾條會靠「全部都算 plumbing」而空跑。"""
        self.assertTrue(install_engine.ENGINE_AGNOSTIC_FIELDS)

    def test_the_plumbing_list_holds_no_product_settings(self):
        """plumbing 是逃生口——它太寬的話，這整支測試就失去意義。這裡釘住
        幾個一定不能被歸到那裡去的欄位。"""
        for field in ("dependencies", "windows_service", "custom_install_dir",
                      "install_password", "file_associations"):
            self.assertNotIn(field, install_engine.ENGINE_PLUMBING_FIELDS)


if __name__ == "__main__":
    unittest.main()
