"""全機器使用者範圍（`msix.all_users`）的打包端：欄位、分類與建置提示。

依 [ADR-0013](docs/adr/0013-msix-all-users-scope-is-an-opt-in-field.md)：

- **決定一**：新增 `msix.all_users`，預設為假（當前使用者範圍）。
- **決定二**：`no_admin_install` 在 MSIX 模式下由第二類（擋建置）改列第四類
  （不擋，只說明）——它原本綁的兩件事在這個模式下各自另有歸屬：安裝路徑由
  系統決定，使用者範圍改由 `msix.all_users` 表達。
- **決定七**：啟用全機器範圍時，於建置階段輸出一則提示，說明附帶條件。

這一段只做打包端。安裝端的行為在這一段之後仍完全不變——設定填得下去、
建置會提示，但產出的安裝檔還是當前使用者範圍。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import install_engine
import msix_settings


VALID = {
    "identity_name": "MyCompany.MyApp",
    "certificate_subject": "CN=Demo, O=Demo, C=TW",
}


def _validate(**overrides):
    block = dict(VALID)
    block.update(overrides)
    return msix_settings.validate(block)


class TheFieldDefaultsToCurrentUser(unittest.TestCase):
    """預設為假是決定一的核心：現代應用程式散布的通行預設就是當前使用者，
    而既有的設定檔沒有這個欄位，它們的行為必須完全不變。"""

    def test_a_config_without_the_field_is_current_user(self):
        normalized, error = _validate()
        self.assertIsNone(error)
        self.assertIs(normalized["all_users"], False)

    def test_true_passes_through(self):
        normalized, error = _validate(all_users=True)
        self.assertIsNone(error)
        self.assertIs(normalized["all_users"], True)

    def test_false_passes_through(self):
        normalized, error = _validate(all_users=False)
        self.assertIsNone(error)
        self.assertIs(normalized["all_users"], False)


class ANonBooleanIsRefused(unittest.TestCase):
    """不做真假值轉換：JSON 裡寫 `"all_users": "false"` 是可能發生的手誤，
    而轉換的結果會是真——與作者的意思完全相反，且打包不會有任何徵兆。
    這個欄位決定的是要不要動整台機器，猜錯的代價太大。
    """

    def test_a_string_is_refused_and_the_message_names_the_field(self):
        for value in ("true", "false", "yes", ""):
            normalized, error = _validate(all_users=value)
            self.assertIsNone(normalized, repr(value))
            self.assertIn("msix.all_users", error, repr(value))

    def test_a_number_is_refused(self):
        normalized, error = _validate(all_users=1)
        self.assertIsNone(normalized)
        self.assertIn("msix.all_users", error)

    def test_the_english_message_also_names_the_field(self):
        _normalized, error = msix_settings.validate(
            dict(VALID, all_users="true"), lang="en")
        self.assertIn("msix.all_users", error)
        self.assertNotIn("必須", error)


class NoAdminInstallNoLongerBlocks(unittest.TestCase):
    """決定二：從第二類改列第四類。

    它原本擋建置的理由是「Program Files 即全機器範圍，而第一版只做當前使用者
    範圍」。使用者範圍改由 `msix.all_users` 表達之後，那個理由不存在了。
    """

    def _report(self, **settings):
        base = {"install_engine": "msix"}
        base.update(settings)
        return install_engine.check_settings(install_engine.MSIX, base)

    def test_it_is_not_in_the_blocking_list(self):
        report = self._report(no_admin_install=False)
        self.assertEqual([f.field for f in report.blocking if
                          f.field == "no_admin_install"], [])

    def test_the_build_is_not_refused_because_of_it(self):
        self.assertFalse(self._report(no_admin_install=False).blocking)

    def test_it_is_explained_instead(self):
        """第四類不擋建置，但要說明為什麼那個設定不會有作用。"""
        report = self._report(no_admin_install=False)
        self.assertTrue(report.notices)
        joined = " ".join(report.notice_messages())
        self.assertIn("no_admin_install", joined)

    def test_setting_it_true_says_nothing(self):
        """填了真代表使用者要的就是當前使用者範圍——與這個模式的預設一致，
        沒有落差可說。"""
        report = self._report(no_admin_install=True)
        joined = " ".join(report.notice_messages())
        self.assertNotIn("no_admin_install", joined)


class TurningItOnExplainsWhatItCosts(unittest.TestCase):
    """決定七：附帶條件於建置階段以 notices 提示，不擋建置。"""

    def _notices(self, all_users):
        report = install_engine.check_settings(install_engine.MSIX, {
            "install_engine": "msix",
            "no_admin_install": True,
            "msix": {"identity_name": "A.B", "all_users": all_users},
        })
        return " ".join(report.notice_messages())

    def test_it_is_silent_when_the_field_is_off(self):
        self.assertNotIn("所有使用者", self._notices(False))

    def test_it_does_not_block_the_build(self):
        report = install_engine.check_settings(install_engine.MSIX, {
            "install_engine": "msix", "no_admin_install": True,
            "msix": {"all_users": True},
        })
        self.assertFalse(report.blocking)

    def test_the_notice_covers_every_caveat_the_adr_lists(self):
        """四項都要講到。少講一項，下游專案就是在不知情的狀況下承擔它。"""
        notice = self._notices(True)
        self.assertIn("系統管理員權限", notice)   # 決定四：拿不到就降級
        self.assertIn("啟動", notice)             # 其他使用者要先啟動才完成註冊
        self.assertIn("檔案關聯", notice)         # 註冊完成前不生效
        self.assertIn("移除", notice)             # 系統介面無法完整移除

    def test_the_english_notice_says_the_same_things(self):
        report = install_engine.check_settings(install_engine.MSIX, {
            "install_engine": "msix", "no_admin_install": True,
            "msix": {"all_users": True},
        })
        notice = " ".join(report.notice_messages(lang="en"))
        self.assertIn("administrator", notice.lower())
        self.assertIn("file association", notice.lower())


class TheTraditionalEngineIsUntouched(unittest.TestCase):
    def test_nothing_is_reported(self):
        report = install_engine.check_settings(install_engine.TRADITIONAL, {
            "no_admin_install": False,
            "msix": {"all_users": True},
        })
        self.assertFalse(report.blocking)
        self.assertFalse(report.notices)


class TheValueReachesTheBuilder(unittest.TestCase):
    """設定裡填的值要真的送到打包，兩個呼叫端都要。

    這正是這個專案出過的事故形態：欄位加好了、驗證也寫了，但傳遞漏接一處，
    於是 CLI 打包出來的安裝檔行為與 GUI 不同，而兩邊都不報錯。以原始碼靜態
    確認兩處都有接上——真的跑一次打包要好幾分鐘，而這裡要釘住的是「那一行
    有沒有被寫下來」。
    """

    def _source(self, name):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, name), encoding="utf-8") as f:
            return f.read()

    def test_the_cli_passes_it(self):
        self.assertIn("msix_all_users=", self._source("builder_cli.py"))

    def test_the_gui_passes_it(self):
        self.assertIn("msix_all_users=", self._source("gui_config.py"))

    def test_both_read_it_from_the_msix_block(self):
        """它住在 msix 巢狀物件底下（ADR-0013 決定一），不是最上層。"""
        for name in ("builder_cli.py", "gui_config.py"):
            source = self._source(name)
            index = source.index("msix_all_users=")
            line = source[index:source.index("\n", index)]
            self.assertIn("all_users", line, name)


class TheTemplateMentionsIt(unittest.TestCase):
    """`init` 產生的範本要列出這個欄位。

    真實抓過的問題（A3：config schema 單一真實來源）：範本沒列出某個欄位時，
    跑 `init` 拿到範本的人看起來就像這個工具不支援那個功能。
    """

    def test_the_msix_block_lists_the_field(self):
        import builder_cli
        self.assertIn("all_users", builder_cli.TEMPLATE["msix"])

    def test_the_template_default_is_current_user(self):
        import builder_cli
        self.assertIs(builder_cli.TEMPLATE["msix"]["all_users"], False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
