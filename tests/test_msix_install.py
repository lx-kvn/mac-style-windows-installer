"""msix_install.py 的測試：MSIX 引擎的安裝流程協調。

這一層負責的是「照什麼順序做哪幾件事」，不負責任何一件事本身——偵測與移除
舊版、實際部署，都是注入進來的。因此測試不需要真的安裝任何東西，也不需要
winrt。

順序本身有依據：第二輪決議第九項要求在交付系統部署**之前**先移除傳統模式
的既有安裝。任何中途改採 MSIX 的下游專案，其既有使用者都處於「已安裝傳統
版本」的狀態；不處理會導致新舊並存——兩筆同名的應用程式清單項目、檔案關聯
衝突、以及使用者手動清除時刪錯的風險。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import msix_deploy
import msix_install


def ok(**overrides):
    values = {"ok": True, "error_text": "", "error_code": 0}
    values.update(overrides)
    return msix_deploy.Outcome(**values)


def _package(full_name, version="", publisher=""):
    """`find_installed_package` 回傳的形狀。

    版本與發行者預設留空——這一組測試談的是「查到了同名套件」本身，不談
    版本比較，而空值代表「不做比較」（見 msix_install.run 的說明）。
    """
    return msix_deploy.InstalledPackage(full_name=full_name, version=version,
                                        publisher=publisher)


class Recorder:
    """記錄呼叫順序，讓「先移除舊版再部署」這件事可以被驗證。"""

    def __init__(self, existing=None, deploy_outcome=None, remove_ok=True):
        self.order = []
        self.existing = existing or {"exists": False}
        self.deploy_outcome = deploy_outcome or ok()
        self.remove_ok = remove_ok
        self.deployed = None
        self.progress_seen = []

    def check_existing(self):
        self.order.append("check")
        return self.existing

    def remove_existing(self, info):
        self.order.append("remove")
        return {"status": "success"} if self.remove_ok else {
            "status": "error", "message": "舊版移除失敗"}

    def deploy(self, package_path, progress=None):
        self.order.append("deploy")
        self.deployed = package_path
        if progress:
            for value in (0, 50, 100):
                progress(value)
        return self.deploy_outcome


def run(recorder, package_path="C:\\x\\app.msix", **kwargs):
    return msix_install.run(
        package_path,
        check_existing=recorder.check_existing,
        remove_existing=recorder.remove_existing,
        deploy=recorder.deploy,
        **kwargs,
    )


class HappyPathTest(unittest.TestCase):
    def test_a_clean_machine_just_deploys(self):
        recorder = Recorder()
        result = run(recorder)
        self.assertEqual(result["status"], "success")
        self.assertEqual(recorder.order, ["check", "deploy"])

    def test_the_package_path_reaches_the_deploy_step(self):
        recorder = Recorder()
        run(recorder, package_path="C:\\somewhere\\my.msix")
        self.assertEqual(recorder.deployed, "C:\\somewhere\\my.msix")

    def test_progress_is_forwarded(self):
        recorder = Recorder()
        seen = []
        run(recorder, progress=seen.append)
        self.assertEqual(seen, [0, 50, 100])


class ExistingTraditionalInstallTest(unittest.TestCase):
    """第二輪決議第九項：交付系統部署之前先移除傳統模式的既有安裝。"""

    def test_an_existing_install_is_removed_before_deploying(self):
        recorder = Recorder(existing={"exists": True, "install_path": "C:\\Program Files\\App"})
        result = run(recorder)
        self.assertEqual(result["status"], "success")
        self.assertEqual(recorder.order, ["check", "remove", "deploy"])

    def test_a_failed_removal_stops_before_deploying(self):
        """移除失敗還繼續部署，結果就是新舊並存——那正是這一步要避免的。"""
        recorder = Recorder(
            existing={"exists": True, "install_path": "C:\\Program Files\\App"},
            remove_ok=False,
        )
        result = run(recorder)
        self.assertEqual(result["status"], "error")
        self.assertNotIn("deploy", recorder.order)
        self.assertIn("舊版移除失敗", result["message"])

    def test_the_user_is_told_that_the_old_version_will_be_removed(self):
        """第二輪決議第九項要求於介面明確告知將先移除舊版。"""
        recorder = Recorder(existing={"exists": True, "install_path": "C:\\Program Files\\App"})
        messages = []
        run(recorder, log=messages.append)
        self.assertTrue(any("舊版" in m for m in messages),
                        f"沒有任何訊息提到會先移除舊版：{messages}")


class DeploymentFailureTest(unittest.TestCase):
    def test_a_failure_is_reported_with_the_system_message(self):
        """第三輪 spike 結果第七項：error_text 是完整且已在地化的說明，
        直接轉呈即可。"""
        message = "錯誤 0x800B0109: 應用程式套件或套件組合中之簽章的根憑證必須受信任。"
        recorder = Recorder(deploy_outcome=ok(ok=False, error_text=message))
        result = run(recorder)
        self.assertEqual(result["status"], "error")
        self.assertIn(message, result["message"])

    def test_a_missing_package_file_is_caught_before_deploying(self):
        recorder = Recorder()
        result = msix_install.run(
            "C:\\definitely\\not\\here.msix",
            check_existing=recorder.check_existing,
            remove_existing=recorder.remove_existing,
            deploy=recorder.deploy,
            package_must_exist=True,
        )
        self.assertEqual(result["status"], "error")
        self.assertNotIn("deploy", recorder.order)


class ExistingMsixPackageTest(unittest.TestCase):
    """稽核 D3：同一個 identity 已經以 MSIX 裝過的情形原本完全沒有處置。

    `check_existing` 接的是登錄表查詢，只看得到傳統模式的舊安裝。同一個
    identity 已經以 MSIX 裝過的情形原本完全沒有分支。

    這一組測的是**部署失敗之後**那段附加說明——降版的事前處置在
    `DowngradeTest`。兩者都需要：事前的比較依賴打包端有寫進 `package_version`，
    而修正之前編出的安裝檔沒有那個欄位，那些安裝檔仍然只走得到這條路。
    """

    def test_a_failure_with_an_installed_package_names_it(self):
        recorder = Recorder(deploy_outcome=msix_deploy.Outcome(
            False, "錯誤 0x80073D06", 0x80073D06))
        result = run(recorder,
                     find_installed_package=lambda: _package("My.App_1.0.0.0_x64__abc"))
        self.assertEqual(result["status"], "error")
        self.assertIn("My.App_1.0.0.0_x64__abc", result["message"])

    def test_the_failure_message_keeps_the_systems_own_wording(self):
        """系統給的說明是完整且已在地化的，附加說明不該取代它。"""
        recorder = Recorder(deploy_outcome=msix_deploy.Outcome(
            False, "錯誤 0x80073D06", 0x80073D06))
        result = run(recorder,
                     find_installed_package=lambda: _package("My.App_1.0.0.0_x64__abc"))
        self.assertIn("錯誤 0x80073D06", result["message"])

    def test_the_failure_message_says_where_to_remove_it(self):
        recorder = Recorder(deploy_outcome=msix_deploy.Outcome(False, "壞了", 1))
        result = run(recorder, find_installed_package=lambda: _package("My.App_1_x64__a"))
        self.assertIn("設定", result["message"])

    def test_the_message_does_not_claim_a_same_version_reinstall_fails(self):
        """2026-09-05 於 Windows 11 25H2（26200，zh-TW）實測推翻的前提。

        量測結果：同版本重新安裝**會成功**（系統重新註冊），版本較新會就地
        更新，只有降版會失敗（`0x80073D06`）。訊息若把同版本也講成失敗原因，
        使用者會照著去移除一個其實不需要移除的東西。
        """
        recorder = Recorder(deploy_outcome=msix_deploy.Outcome(False, "壞了", 1))
        result = run(recorder, find_installed_package=lambda: _package("My.App_1_x64__a"))
        self.assertIn("舊", result["message"])
        self.assertNotIn("同一個版本或更舊", result["message"])

    def test_a_failure_without_an_installed_package_says_nothing_extra(self):
        recorder = Recorder(deploy_outcome=msix_deploy.Outcome(False, "壞了", 1))
        result = run(recorder, find_installed_package=lambda: None)
        self.assertEqual(result["message"], "安裝失敗：壞了")

    def test_an_installed_package_is_reported_before_deploying(self):
        """使用者看到安裝程式在動一個已經存在的東西時，應該已經知道那是
        預期中的步驟——比照傳統模式舊版被移除時的告知。"""
        recorder = Recorder()
        lines = []
        run(recorder, find_installed_package=lambda: _package("My.App_1_x64__a"),
            log=lines.append)
        self.assertTrue(any("My.App_1_x64__a" in line for line in lines))

    def test_the_lookup_happens_only_once(self):
        """列舉當前使用者的所有套件不是免費的，查一次就夠。"""
        calls = []

        def find():
            calls.append(1)
            return _package("My.App_1_x64__a")

        run(Recorder(deploy_outcome=msix_deploy.Outcome(False, "壞了", 1)),
            find_installed_package=find)
        self.assertEqual(len(calls), 1)

    def test_without_the_lookup_the_behaviour_is_unchanged(self):
        """沒有傳這個參數時（例如舊的呼叫端）流程完全不變。"""
        recorder = Recorder(deploy_outcome=msix_deploy.Outcome(False, "壞了", 1))
        result = run(recorder)
        self.assertEqual(result["message"], "安裝失敗：壞了")

    def test_a_lookup_that_raises_does_not_take_down_the_install(self):
        """查詢失敗不該讓一次本來會成功的安裝失敗。"""
        def find():
            raise RuntimeError("winrt 掛了")

        result = run(Recorder(), find_installed_package=find)
        self.assertEqual(result["status"], "success")


def installed(version="1.0.0.0", publisher="CN=Tester",
              full_name="My.App_1.0.0.0_x64__abc"):
    return msix_deploy.InstalledPackage(full_name=full_name, version=version,
                                        publisher=publisher)


class DowngradeTest(unittest.TestCase):
    """降版要問過使用者（ADR-0015 決定一、二）。

    實機量測（2026-09-05，Windows 11 25H2）：同版本重裝會成功、升版會就地
    更新，只有降版會失敗（`0x80073D06`）。因此「要不要降版」這個決定要發生
    在部署**之前**——放在失敗之後的話，使用者此時看到的是系統的錯誤訊息，
    不是一個他可以回答的問題。

    比照傳統引擎既有的形狀（`upgrade.check_existing()` 的三分法）。MSIX 多
    一件傳統引擎沒有的事：移除會連同應用程式的資料一起清掉。
    """

    def _run(self, recorder, current="1.0.0.0", package_version="2.0.0.0",
             publisher="CN=Tester", confirm=None, remove=None, log=None):
        return msix_install.run(
            "C:\\x\\app.msix",
            check_existing=recorder.check_existing,
            remove_existing=recorder.remove_existing,
            deploy=recorder.deploy,
            find_installed_package=lambda: installed(current, publisher),
            package_version=package_version,
            package_publisher="CN=Tester",
            confirm_downgrade=confirm,
            remove_installed_package=remove,
            log=log,
        )

    def test_a_newer_version_just_deploys(self):
        recorder = Recorder()
        result = self._run(recorder, current="1.0.0.0", package_version="2.0.0.0")
        self.assertEqual(result["status"], "success")
        self.assertIn("deploy", recorder.order)

    def test_the_same_version_just_deploys(self):
        """實機量測：同版本重新安裝會成功（系統重新註冊）。不要多此一舉。"""
        recorder = Recorder()
        result = self._run(recorder, current="1.0.0.0", package_version="1.0.0.0")
        self.assertEqual(result["status"], "success")

    def test_a_downgrade_without_consent_is_refused(self):
        recorder = Recorder()
        asked = []
        result = self._run(recorder, current="2.0.0.0", package_version="1.0.0.0",
                           confirm=lambda info: asked.append(info) or False)
        self.assertEqual(result["status"], "error")
        self.assertNotIn("deploy", recorder.order)
        self.assertEqual(len(asked), 1)

    def test_the_question_carries_both_versions(self):
        recorder = Recorder()
        asked = []
        self._run(recorder, current="2.0.0.0", package_version="1.0.0.0",
                  confirm=lambda info: asked.append(info) or False)
        self.assertEqual(asked[0]["installed_version"], "2.0.0.0")
        self.assertEqual(asked[0]["new_version"], "1.0.0.0")

    def test_the_question_says_the_data_will_go_too(self):
        """傳統引擎的降版不會清資料，MSIX 的會——那是使用者答這個問題時
        必須知道的事（ADR-0015 決定二）。"""
        recorder = Recorder()
        asked = []
        self._run(recorder, current="2.0.0.0", package_version="1.0.0.0",
                  confirm=lambda info: asked.append(info) or False)
        self.assertIn("資料", asked[0]["message"])

    def test_consenting_removes_the_old_package_then_deploys(self):
        recorder = Recorder()
        removed = []

        def remove(full_name):
            removed.append(full_name)
            return msix_deploy.Outcome(True, "", 0)

        result = self._run(recorder, current="2.0.0.0", package_version="1.0.0.0",
                           confirm=lambda info: True, remove=remove)
        self.assertEqual(result["status"], "success")
        self.assertEqual(removed, ["My.App_1.0.0.0_x64__abc"])
        self.assertIn("deploy", recorder.order)

    def test_a_failed_removal_does_not_go_on_to_deploy(self):
        recorder = Recorder()
        result = self._run(
            recorder, current="2.0.0.0", package_version="1.0.0.0",
            confirm=lambda info: True,
            remove=lambda full_name: msix_deploy.Outcome(False, "移不掉", 1))
        self.assertEqual(result["status"], "error")
        self.assertIn("移不掉", result["message"])
        self.assertNotIn("deploy", recorder.order)

    def test_without_a_confirm_callback_a_downgrade_proceeds(self):
        """靜默安裝走這一條（ADR-0015 決定三）：直接做，不中止、不加旗標。"""
        recorder = Recorder()
        removed = []
        result = self._run(
            recorder, current="2.0.0.0", package_version="1.0.0.0", confirm=None,
            remove=lambda full_name: removed.append(full_name) or msix_deploy.Outcome(True, "", 0))
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(removed), 1)

    def test_the_silent_path_records_that_the_data_is_going(self):
        """沒有畫面可以警示，紀錄檔就是唯一的出口。"""
        recorder = Recorder()
        lines = []
        self._run(recorder, current="2.0.0.0", package_version="1.0.0.0",
                  confirm=None, log=lines.append,
                  remove=lambda full_name: msix_deploy.Outcome(True, "", 0))
        joined = "\n".join(lines)
        self.assertIn("2.0.0.0", joined)
        self.assertIn("1.0.0.0", joined)
        self.assertIn("資料", joined)

    def test_without_a_package_version_nothing_is_compared(self):
        """舊版工具編出來的安裝檔沒有那個欄位，行為維持修正前的樣子。"""
        recorder = Recorder()
        asked = []
        result = msix_install.run(
            "C:\\x\\app.msix", check_existing=recorder.check_existing,
            remove_existing=recorder.remove_existing, deploy=recorder.deploy,
            find_installed_package=lambda: installed("2.0.0.0"),
            confirm_downgrade=lambda info: asked.append(info) or False)
        self.assertEqual(result["status"], "success")
        self.assertEqual(asked, [])


class DifferentPublisherTest(unittest.TestCase):
    """發行者不同的同名套件只警示、不移除（ADR-0015 決定四）。

    套件身分由「名稱 + 發行者」共同構成。打包者換憑證時名稱不變而發行者
    改變，系統把兩者當成互不相關的應用程式並存安裝。那份舊套件確有可能屬於
    另一個開發者，工具不代使用者判定兩者為同一個應用程式。
    """

    def _run(self, recorder, publisher, confirm=None, remove=None, log=None):
        return msix_install.run(
            "C:\\x\\app.msix", check_existing=recorder.check_existing,
            remove_existing=recorder.remove_existing, deploy=recorder.deploy,
            find_installed_package=lambda: installed("9.0.0.0", publisher),
            package_version="1.0.0.0", package_publisher="CN=Tester",
            confirm_downgrade=confirm, remove_installed_package=remove, log=log)

    def test_a_different_publisher_is_not_treated_as_a_downgrade(self):
        """版本比較高也不問降版——系統眼中那不是同一個應用程式。"""
        recorder = Recorder()
        asked = []
        result = self._run(recorder, "CN=Someone Else",
                           confirm=lambda info: asked.append(info) or False)
        self.assertEqual(result["status"], "success")
        self.assertEqual(asked, [])

    def test_it_is_never_removed_automatically(self):
        recorder = Recorder()
        removed = []
        self._run(recorder, "CN=Someone Else",
                  remove=lambda full_name: removed.append(full_name))
        self.assertEqual(removed, [])

    def test_the_user_is_told_the_two_will_coexist(self):
        recorder = Recorder()
        lines = []
        self._run(recorder, "CN=Someone Else", log=lines.append)
        joined = "\n".join(lines)
        self.assertIn("並存", joined)


class NoUninstallerTest(unittest.TestCase):
    """ADR-0006：MSIX 模式不提供自訂解除安裝介面，解除安裝由系統接管。"""

    def test_the_success_message_points_at_the_system_uninstall_path(self):
        recorder = Recorder()
        result = run(recorder)
        self.assertIn("設定", result["message"])


if __name__ == "__main__":
    unittest.main()
