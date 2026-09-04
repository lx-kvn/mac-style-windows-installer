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

    `check_existing` 接的是登錄表查詢，只看得到傳統模式的舊安裝。Windows 對
    版本較高的套件本來就會就地更新，因此升級那條路一直是好的；問題出在同版本
    重裝與降版——那兩種會直接失敗，而使用者拿到的是系統的原始錯誤碼。

    這一輪的處置是「查得到就把話說清楚」，不自動移除：自動移除的前提是失敗
    原因確實是同名套件已存在，而失敗也可能來自別的原因（憑證不受信任、磁碟
    空間不足），那時移除等於白白弄丟使用者的既有應用程式，而重試照樣失敗。
    自動移除待 `add_package_async` 對同版本／降版的真實行為於虛擬機驗證後
    再決定（見 docs/investigations/MSIX稽核與缺陷修正.md 的待辦清單）。
    """

    def test_a_failure_with_an_installed_package_names_it(self):
        recorder = Recorder(deploy_outcome=msix_deploy.Outcome(
            False, "錯誤 0x80073D06", 0x80073D06))
        result = run(recorder,
                     find_installed_package=lambda: "My.App_1.0.0.0_x64__abc")
        self.assertEqual(result["status"], "error")
        self.assertIn("My.App_1.0.0.0_x64__abc", result["message"])

    def test_the_failure_message_keeps_the_systems_own_wording(self):
        """系統給的說明是完整且已在地化的，附加說明不該取代它。"""
        recorder = Recorder(deploy_outcome=msix_deploy.Outcome(
            False, "錯誤 0x80073D06", 0x80073D06))
        result = run(recorder,
                     find_installed_package=lambda: "My.App_1.0.0.0_x64__abc")
        self.assertIn("錯誤 0x80073D06", result["message"])

    def test_the_failure_message_says_where_to_remove_it(self):
        recorder = Recorder(deploy_outcome=msix_deploy.Outcome(False, "壞了", 1))
        result = run(recorder, find_installed_package=lambda: "My.App_1_x64__a")
        self.assertIn("設定", result["message"])

    def test_the_message_does_not_claim_a_same_version_reinstall_fails(self):
        """2026-09-05 於 Windows 11 25H2（26200，zh-TW）實測推翻的前提。

        量測結果：同版本重新安裝**會成功**（系統重新註冊），版本較新會就地
        更新，只有降版會失敗（`0x80073D06`）。訊息若把同版本也講成失敗原因，
        使用者會照著去移除一個其實不需要移除的東西。
        """
        recorder = Recorder(deploy_outcome=msix_deploy.Outcome(False, "壞了", 1))
        result = run(recorder, find_installed_package=lambda: "My.App_1_x64__a")
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
        run(recorder, find_installed_package=lambda: "My.App_1_x64__a",
            log=lines.append)
        self.assertTrue(any("My.App_1_x64__a" in line for line in lines))

    def test_the_lookup_happens_only_once(self):
        """列舉當前使用者的所有套件不是免費的，查一次就夠。"""
        calls = []

        def find():
            calls.append(1)
            return "My.App_1_x64__a"

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


class NoUninstallerTest(unittest.TestCase):
    """ADR-0006：MSIX 模式不提供自訂解除安裝介面，解除安裝由系統接管。"""

    def test_the_success_message_points_at_the_system_uninstall_path(self):
        recorder = Recorder()
        result = run(recorder)
        self.assertIn("設定", result["message"])


if __name__ == "__main__":
    unittest.main()
