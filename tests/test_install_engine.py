"""install_engine.py 的測試：引擎選擇與 MSIX 的設定相容性檢查。

對應 docs/proposals/MSIX輸出規劃.md 第二輪決議第七、八項（現有功能的四類
分類與第一版範圍）、第七輪（逐項重新檢查的結果）、第八輪定案決議，以及
docs/adr/0009（第一版只提供當前使用者範圍、類別檢查一次列出全部違規項）。

這裡測的是「一份設定丟進來，會被判成哪一類、訊息長什麼樣」，不測 MSIX
套件實際怎麼產生——後者尚未實作。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import install_engine


def settings(**overrides):
    """一份通過 MSIX 檢查的乾淨設定，測試各自覆蓋要踩的那一項。

    no_admin_install=True 是乾淨的那一側（當前使用者範圍，第一類）；
    這裡刻意不使用 packaging_core 的預設值，因為那個預設值正好是會被擋下
    的那一側，拿它當基準會讓每個測試都連帶踩到使用者範圍那條。
    """
    base = {
        "no_admin_install": True,
        "folder_name": "",
        "local_appdata_files": [],
        "custom_install_dir": "",
        "pre_install_script": "",
        "post_install_script": "",
        "dependencies": [],
        "custom_dependencies": [],
        "dependencies_min_version": {},
        "bundle_dependencies": [],
        "windows_service": {},
        "scheduled_task": {},
    }
    base.update(overrides)
    return base


class EngineSelectionTest(unittest.TestCase):
    """設定檔裡 install_engine 這個欄位怎麼被解讀。"""

    def test_missing_field_means_traditional(self):
        """既有的設定檔沒有這個欄位，行為必須完全不變。"""
        self.assertEqual(install_engine.normalize({}), install_engine.TRADITIONAL)

    def test_empty_value_means_traditional(self):
        self.assertEqual(install_engine.normalize({"install_engine": ""}), install_engine.TRADITIONAL)

    def test_the_literal_string_users_write_selects_msix(self):
        """使用者在 JSON 裡寫的就是這個字串，這是對外契約。"""
        self.assertEqual(install_engine.normalize({"install_engine": "msix"}), install_engine.MSIX)

    def test_case_and_whitespace_are_tolerated(self):
        self.assertEqual(install_engine.normalize({"install_engine": "  MSIX "}), install_engine.MSIX)

    def test_unknown_value_is_rejected_with_the_valid_ones_listed(self):
        with self.assertRaises(install_engine.UnknownEngine) as ctx:
            install_engine.normalize({"install_engine": "msi"})
        message = str(ctx.exception)
        self.assertIn("msi", message)
        self.assertIn("traditional", message)
        self.assertIn("msix", message)


class TraditionalEngineTest(unittest.TestCase):
    """傳統引擎不受任何 MSIX 限制影響。"""

    def test_everything_passes_under_the_traditional_engine(self):
        report = install_engine.check_settings(
            install_engine.TRADITIONAL,
            settings(
                no_admin_install=False, custom_install_dir=r"C:\Somewhere",
                pre_install_script="setup.bat", bundle_dependencies=["vcredist"],
                windows_service={"service_name": "Svc"},
            ),
        )
        self.assertEqual(report.blocking, [])
        self.assertEqual(report.notices, [])


class MsixCleanConfigTest(unittest.TestCase):
    def test_a_clean_config_produces_nothing(self):
        report = install_engine.check_settings(install_engine.MSIX, settings())
        self.assertEqual(report.blocking, [])
        self.assertEqual(report.notices, [])
        self.assertFalse(report.has_blocking)


class UserScopeTest(unittest.TestCase):
    """ADR-0009：第一版只提供當前使用者範圍。

    「安裝位置三選一」的三支各自的下場，見第八輪定案決議第二項的表格。
    """

    def test_current_user_scope_passes(self):
        report = install_engine.check_settings(install_engine.MSIX, settings(no_admin_install=True))
        self.assertEqual(report.blocking, [])

    def test_all_users_scope_is_blocked(self):
        report = install_engine.check_settings(install_engine.MSIX, settings(no_admin_install=False))
        self.assertTrue(report.has_blocking)

    def test_all_users_message_uses_the_deferred_wording_not_the_format_limit_one(self):
        """第八項：第二類是「尚未支援」（等本工具補），不是「格式限制」。

        ADR-0009 決定二：講成格式限制不誠實——MSIX 做得到全機器，只是第一版
        不做，寫成格式限制會讓後續維護者認定此路不通。
        """
        report = install_engine.check_settings(install_engine.MSIX, settings(no_admin_install=False))
        text = report.error_message()
        self.assertIn("尚未支援", text)
        self.assertNotIn("格式本身的限制", text)

    def test_all_users_message_points_at_the_traditional_engine(self):
        """ADR-0009 決定二：附一句替代方案，讓對方知道等待期間有路可走。"""
        report = install_engine.check_settings(install_engine.MSIX, settings(no_admin_install=False))
        self.assertIn("傳統引擎", report.error_message())

    def test_the_loss_is_described_as_other_users_not_as_program_files(self):
        """第八輪決議第一項：真正的損失是「其他使用者不會有這個應用程式」，
        不是「裝不到 Program Files」——後者對使用者不具意義。"""
        report = install_engine.check_settings(install_engine.MSIX, settings(no_admin_install=False))
        text = report.error_message()
        self.assertIn("使用者", text)
        self.assertNotIn("Program Files", text)


class ImpossibleCategoryTest(unittest.TestCase):
    """第三類：格式本身不允許，語氣須與第二類可區分。"""

    CASES = [
        ("custom_install_dir", r"C:\MyApp"),
        ("pre_install_script", "before.bat"),
        ("post_install_script", "after.bat"),
        ("bundle_dependencies", ["vcredist2015"]),
    ]

    def test_each_is_blocked(self):
        for field, value in self.CASES:
            with self.subTest(field=field):
                report = install_engine.check_settings(
                    install_engine.MSIX, settings(**{field: value})
                )
                self.assertTrue(report.has_blocking)

    def test_each_uses_the_format_limit_wording(self):
        for field, value in self.CASES:
            with self.subTest(field=field):
                report = install_engine.check_settings(
                    install_engine.MSIX, settings(**{field: value})
                )
                text = report.error_message()
                self.assertIn("格式本身的限制", text)
                self.assertNotIn("尚未支援", text)


class UnsupportedCategoryTest(unittest.TestCase):
    """第二類：可對應但需另行設計，第一版報「尚未支援」。"""

    CASES = [
        ("dependencies", ["vcredist2015"]),
        ("custom_dependencies", [{"key": "x"}]),
        ("dependencies_min_version", {"vcredist2015": "14.0"}),
        ("windows_service", {"service_name": "MySvc"}),
        ("scheduled_task", {"task_name": "MyTask"}),
    ]

    def test_each_is_blocked_with_the_deferred_wording(self):
        for field, value in self.CASES:
            with self.subTest(field=field):
                report = install_engine.check_settings(
                    install_engine.MSIX, settings(**{field: value})
                )
                self.assertTrue(report.has_blocking)
                self.assertIn("尚未支援", report.error_message())


class MootCategoryTest(unittest.TestCase):
    """第四類：動機消失，設定無害失效——不擋建置，只在建置訊息說明。"""

    def test_local_appdata_files_does_not_block(self):
        report = install_engine.check_settings(
            install_engine.MSIX, settings(local_appdata_files=["cli/tool.exe"])
        )
        self.assertFalse(report.has_blocking)
        self.assertTrue(report.notices)

    def test_folder_name_does_not_block(self):
        report = install_engine.check_settings(install_engine.MSIX, settings(folder_name="MyApp"))
        self.assertFalse(report.has_blocking)
        self.assertTrue(report.notices)

    def test_an_unset_folder_name_stays_quiet(self):
        """folder_name 沒填時會被 packaging_core 補成 app_name，若拿補完的值
        判斷，每一次 MSIX 建置都會噴一則使用者沒設定過的說明。"""
        report = install_engine.check_settings(install_engine.MSIX, settings(folder_name=""))
        self.assertEqual(report.notices, [])

    def test_the_two_path_settings_share_one_notice(self):
        """第七輪第二項：兩者的失效是同一件事的兩個位置，不分開講兩次。"""
        report = install_engine.check_settings(
            install_engine.MSIX,
            settings(folder_name="MyApp", local_appdata_files=["cli/tool.exe"]),
        )
        self.assertEqual(len(report.notices), 1)

    def test_the_notice_says_why_nothing_is_lost(self):
        """第五輪決議第三項：使用者填的設定沒作用時有權得知原因，否則會被
        理解為工具吞掉了設定。"""
        report = install_engine.check_settings(
            install_engine.MSIX, settings(local_appdata_files=["cli/tool.exe"])
        )
        self.assertIn("local_appdata_files", report.notices[0])


class ListsEveryViolationTest(unittest.TestCase):
    """ADR-0009 決定四：一次列出全部，不沿用「第一個錯誤即回傳」。"""

    def test_two_violations_from_the_same_choice_are_both_listed(self):
        """自訂路徑 + 需要管理員權限：路徑是第三類、使用者範圍是第二類。"""
        report = install_engine.check_settings(
            install_engine.MSIX,
            settings(no_admin_install=False, custom_install_dir=r"C:\MyApp"),
        )
        self.assertEqual(len(report.blocking), 2)
        text = report.error_message()
        self.assertIn("尚未支援", text)
        self.assertIn("格式本身的限制", text)

    def test_every_violating_field_appears(self):
        report = install_engine.check_settings(
            install_engine.MSIX,
            settings(
                no_admin_install=False,
                custom_install_dir=r"C:\MyApp",
                pre_install_script="before.bat",
                post_install_script="after.bat",
                bundle_dependencies=["vcredist2015"],
                dependencies=["vcredist2015"],
                windows_service={"service_name": "MySvc"},
                scheduled_task={"task_name": "MyTask"},
            ),
        )
        text = report.error_message()
        for field in (
            "custom_install_dir", "pre_install_script", "post_install_script",
            "bundle_dependencies", "dependencies", "windows_service", "scheduled_task",
        ):
            self.assertIn(field, text, f"{field} 沒有出現在錯誤訊息裡")

    def test_no_blocking_means_no_error_message(self):
        report = install_engine.check_settings(install_engine.MSIX, settings())
        self.assertEqual(report.error_message(), "")


class InstallPasswordTest(unittest.TestCase):
    """安裝密碼保護在 MSIX 引擎下必須被擋下（稽核 D1）。

    真實抓到的缺陷：`builder.build_all()` 的內嵌內容是 `if is_msix /
    elif password_protected / else` 三選一，選了 MSIX 就永遠走不到加密那一
    條，但 `password_protected` 這個布林值仍然無條件寫進 `installer_config
    .json`。產出的安裝檔會顯示密碼關卡，然後去開一個從未被內嵌的
    `app_contents.enc`——每一台機器都失敗，而打包階段毫無徵兆。

    分類為第二類（尚未支援）而非第三類：MSIX 模式做得到這件事（把已簽章的
    套件加密內嵌、驗證通過後解密再交給系統部署），只是第一版不做。講成格式
    限制會讓後續維護者認定此路不通。

    三種來源都要認得：GUI 的勾選框（`need_install_password`）、GUI 的直接
    輸入（`install_password`）、設定檔的環境變數名稱（`install_password_env`）。
    只認其中一種等於讓另外兩條路照樣編出壞掉的安裝檔。
    """

    def test_the_checkbox_alone_is_blocked(self):
        report = install_engine.check_settings(
            install_engine.MSIX, settings(need_install_password=True))
        self.assertTrue(report.has_blocking)

    def test_the_environment_variable_name_is_blocked(self):
        report = install_engine.check_settings(
            install_engine.MSIX, settings(install_password_env="MSWI_PW"))
        self.assertTrue(report.has_blocking)

    def test_the_inline_password_is_blocked(self):
        report = install_engine.check_settings(
            install_engine.MSIX, settings(install_password="hunter2"))
        self.assertTrue(report.has_blocking)

    def test_all_three_sources_together_produce_one_finding_not_three(self):
        """三個欄位描述的是同一個功能，逐項列出會讓使用者以為要修三件事。"""
        report = install_engine.check_settings(install_engine.MSIX, settings(
            need_install_password=True,
            install_password="hunter2",
            install_password_env="MSWI_PW",
        ))
        password_findings = [f for f in report.blocking
                             if f.field == "install_password"]
        self.assertEqual(len(password_findings), 1)

    def test_it_is_the_deferred_wording_not_the_format_limit_one(self):
        report = install_engine.check_settings(
            install_engine.MSIX, settings(need_install_password=True))
        text = report.error_message()
        self.assertIn("尚未支援", text)
        self.assertNotIn("格式本身的限制", text)

    def test_the_message_points_at_the_traditional_engine(self):
        """使用者需要知道等待期間有路可走。"""
        report = install_engine.check_settings(
            install_engine.MSIX, settings(need_install_password=True))
        self.assertIn("傳統引擎", report.error_message())

    def test_a_config_without_any_password_field_passes(self):
        report = install_engine.check_settings(install_engine.MSIX, settings())
        self.assertEqual(report.blocking, [])

    def test_the_field_appears_in_the_static_classification(self):
        """GUI 需要在使用者填之前就標出這個欄位（見 field_categories()）。"""
        self.assertIn("install_password", install_engine.field_categories())

    def test_the_traditional_engine_is_unaffected(self):
        report = install_engine.check_settings(
            install_engine.TRADITIONAL, settings(need_install_password=True))
        self.assertEqual(report.blocking, [])


if __name__ == "__main__":
    unittest.main()
