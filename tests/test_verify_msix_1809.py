"""tools/verify_msix_1809.py 的測試：兩項只有官方文件、沒有實機確認的敘述。

要驗的兩件事（`docs/proposals/MSIX輸出規劃.md` 待辦第 1 項）：

- A：`MinVersion=10.0.17763.0` 真的能在組建 17763 完成部署。這連帶驗證
  「避開 `uap10:RuntimeBehavior`、改用
  `EntryPoint="windows.fullTrustApplication"`」這個已經寫進程式碼的決定
  ——`uap10:` 需要 2004，判斷若有誤會在 17763 上當場失敗。
- B：企業版／教育版／LTSC 在 2004 之前預設關閉側載。

**順序不可對調，這是本模組最重要的設計。** 若先在未開啟側載的環境部署並
失敗，無法分辨失敗來自「側載關著」（B 成立）還是「MinVersion 根本不對」
（A 不成立）。先開啟側載證明 A 成立，再還原快照測 B，B 的失敗才有唯一解釋。

**「部署失敗」本身不足以宣告 B 成立。** 部署會因為很多原因失敗（憑證不受
信任、套件損毀、磁碟空間不足）。因此 B 除了要求部署被拒，還要求當下的
側載登錄值確實處於「未設定」狀態——失敗必須可歸因，否則只能回報無法判定。
"""
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import verify_msix_1809 as verify


class FakeVm:
    """記錄開機與客體操作，不實際驅動虛擬機。"""

    def __init__(self):
        self.events = []

    def copy_in(self, host_path, guest_path):
        self.events.append(("copy_in", guest_path))

    def copy_out(self, guest_path, host_path):
        self.events.append(("copy_out", guest_path))

    def run_program(self, program, *args, **kwargs):
        self.events.append(("run", program))

    def stop(self):
        self.events.append(("stop", None))


class ReportParsingTests(unittest.TestCase):
    """客體把結果寫成檔案帶回來，因為 vmrun 不轉達客體的輸出。"""

    def test_reads_key_value_lines(self):
        parsed = verify.parse_report(
            "found=True\nversion=1.2.3.0\npublisher=CN=Foo\n")
        self.assertEqual(parsed["found"], "True")
        self.assertEqual(parsed["publisher"], "CN=Foo")

    def test_ignores_blank_lines_and_comments(self):
        parsed = verify.parse_report("# 註解\n\nfound=False\n")
        self.assertEqual(parsed, {"found": "False"})

    def test_keeps_equals_signs_inside_the_value(self):
        """發行者字串本身含有等號，切第一個就好。"""
        parsed = verify.parse_report("publisher=C=TW, CN=Foo\n")
        self.assertEqual(parsed["publisher"], "C=TW, CN=Foo")


MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10">
  <Identity Name="MSWI.OneShotDemo" Version="1.2.3.0"
            Publisher="C=TW, CN=MSWI OneShot E2E" ProcessorArchitecture="x64" />
</Package>
"""


class PackageIdentityTests(unittest.TestCase):
    """期望值從套件本身讀出，不寫死在腳本裡。

    寫死的話，比對的是「常數等於常數」——換一份測試套件、或套件的版本改了
    而腳本沒跟著改，測試會在實際不符時仍然通過。
    """

    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "demo.msix")
        with zipfile.ZipFile(self.path, "w") as archive:
            archive.writestr("AppxManifest.xml", MANIFEST)

    def test_reads_name_version_and_publisher(self):
        identity = verify.read_package_identity(self.path)
        self.assertEqual(identity.name, "MSWI.OneShotDemo")
        self.assertEqual(identity.version, "1.2.3.0")
        self.assertEqual(identity.publisher, "C=TW, CN=MSWI OneShot E2E")

    def test_attribute_order_does_not_matter(self):
        """實際產出的清單順序是 Name → Publisher → Version，與此處的範例不同。

        用正規表示式綁死順序時，換一份清單就解析不到，而錯誤訊息會是
        「找不到 Identity 宣告」——看起來像套件壞了，不像是解析寫死了順序。
        """
        path = os.path.join(tempfile.mkdtemp(), "reordered.msix")
        manifest = MANIFEST.replace(
            'Name="MSWI.OneShotDemo" Version="1.2.3.0"\n'
            '            Publisher="C=TW, CN=MSWI OneShot E2E"',
            'Name="MSWI.OneShotDemo"\n'
            '            Publisher="C=TW, CN=MSWI OneShot E2E"\n'
            '            Version="1.2.3.0"')
        self.assertNotEqual(manifest, MANIFEST)  # 確認替換真的發生
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("AppxManifest.xml", manifest)
        identity = verify.read_package_identity(path)
        self.assertEqual(identity.version, "1.2.3.0")
        self.assertEqual(identity.publisher, "C=TW, CN=MSWI OneShot E2E")

    def test_a_package_without_a_manifest_is_an_error(self):
        broken = os.path.join(tempfile.mkdtemp(), "broken.msix")
        with zipfile.ZipFile(broken, "w") as archive:
            archive.writestr("nothing.txt", "")
        with self.assertRaises(verify.VerifyError):
            verify.read_package_identity(broken)


class DeploymentVerdictTests(unittest.TestCase):
    """A 的判準：四項全中才算通過，任何一項不符都不算。"""

    def good(self, **overrides):
        report = {
            "found": "True",
            "version": "1.2.3.0",
            "publisher": "C=TW, CN=MSWI OneShot E2E",
            "install_location_exists": "True",
        }
        report.update(overrides)
        return report

    def test_passes_when_everything_matches(self):
        result = verify.evaluate_deployment(
            self.good(), expected_version="1.2.3.0",
            expected_publisher="C=TW, CN=MSWI OneShot E2E")
        self.assertEqual(result.verdict, verify.PASS)

    def test_fails_when_the_package_is_not_found(self):
        result = verify.evaluate_deployment(
            self.good(found="False"), expected_version="1.2.3.0",
            expected_publisher="C=TW, CN=MSWI OneShot E2E")
        self.assertEqual(result.verdict, verify.FAIL)

    def test_fails_when_only_the_version_differs(self):
        """版本對不上代表裝到的不是這次送進去的套件，不算通過。"""
        result = verify.evaluate_deployment(
            self.good(version="9.9.9.0"), expected_version="1.2.3.0",
            expected_publisher="C=TW, CN=MSWI OneShot E2E")
        self.assertEqual(result.verdict, verify.FAIL)

    def test_fails_when_only_the_publisher_differs(self):
        result = verify.evaluate_deployment(
            self.good(publisher="C=TW, CN=Someone Else"),
            expected_version="1.2.3.0",
            expected_publisher="C=TW, CN=MSWI OneShot E2E")
        self.assertEqual(result.verdict, verify.FAIL)

    def test_fails_when_the_install_location_is_missing(self):
        """登記存在但檔案沒落地，那不是一次成功的部署。"""
        result = verify.evaluate_deployment(
            self.good(install_location_exists="False"),
            expected_version="1.2.3.0",
            expected_publisher="C=TW, CN=MSWI OneShot E2E")
        self.assertEqual(result.verdict, verify.FAIL)

    def test_the_detail_names_which_criterion_failed(self):
        """回報要能直接看出是哪一項不符，不必回頭翻原始輸出。"""
        result = verify.evaluate_deployment(
            self.good(version="9.9.9.0"), expected_version="1.2.3.0",
            expected_publisher="C=TW, CN=MSWI OneShot E2E")
        self.assertIn("version", result.detail)
        self.assertIn("9.9.9.0", result.detail)


class SideloadVerdictTests(unittest.TestCase):
    """B 的判準：部署被拒，且失敗可歸因於側載設定。"""

    def test_passes_when_refused_with_no_policy_set(self):
        result = verify.evaluate_sideload_default(
            {"deploy_succeeded": "False", "allow_all_trusted_apps": ""})
        self.assertEqual(result.verdict, verify.PASS)

    def test_fails_when_deployment_succeeds(self):
        """裝得起來，就表示預設沒有關閉側載——敘述不成立。"""
        result = verify.evaluate_sideload_default(
            {"deploy_succeeded": "True", "allow_all_trusted_apps": ""})
        self.assertEqual(result.verdict, verify.FAIL)

    def test_inconclusive_when_the_policy_was_already_on(self):
        """側載已經被開啟卻仍然失敗，失敗來自別的原因，不能拿來佐證。"""
        result = verify.evaluate_sideload_default(
            {"deploy_succeeded": "False", "allow_all_trusted_apps": "1"})
        self.assertEqual(result.verdict, verify.INCONCLUSIVE)

    def test_inconclusive_verdict_explains_why(self):
        result = verify.evaluate_sideload_default(
            {"deploy_succeeded": "False", "allow_all_trusted_apps": "1"})
        self.assertIn("AllowAllTrustedApps", result.detail)


class CertificateExportTests(unittest.TestCase):
    """從已簽章的檔案取出憑證，供客體匯入信任存放區。"""

    def setUp(self):
        self.out = os.path.join(tempfile.mkdtemp(), "test.cer")

    def _run(self, seen):
        def fake_run(cmd, **kwargs):
            seen["cmd"] = list(cmd)
            seen["kwargs"] = kwargs
            with open(self.out, "wb") as handle:
                handle.write(b"cert")
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        return fake_run

    def test_points_the_child_at_windows_powershells_own_modules(self):
        """從 PowerShell 7 啟動 Windows PowerShell 5.1 時，繼承來的
        PSModulePath 只含 7 的路徑，5.1 因此載不到自己的
        Microsoft.PowerShell.Security，`Get-AuthenticodeSignature` 回報
        「模組存在但無法載入」——訊息看起來像指令不存在，不會讓人聯想到
        環境變數。

        把該變數刪掉並不能解決：實測 5.1 不會因為變數不存在就退回預設值，
        照樣找不到模組。必須明確指向它自己的系統模組目錄。
        """
        seen = {}
        verify.export_signing_certificate(
            "signed.msix", self.out, run=self._run(seen),
            environ={"PSModulePath": r"C:\ps7\modules",
                     "SystemRoot": r"C:\Windows", "PATH": "C:\\"})
        passed = seen["kwargs"]["env"]
        self.assertIn(r"WindowsPowerShell\v1.0\Modules", passed["PSModulePath"])
        self.assertNotIn("ps7", passed["PSModulePath"])
        self.assertIn("PATH", passed)

    def test_reports_the_reason_when_export_fails(self):
        def failing(cmd, **kwargs):
            return type("R", (), {"returncode": 1, "stdout": "",
                                  "stderr": "拒絕存取"})()
        with self.assertRaises(verify.VerifyError) as caught:
            verify.export_signing_certificate("signed.msix", self.out,
                                              run=failing)
        self.assertIn("拒絕存取", str(caught.exception))


class OrderingTests(unittest.TestCase):
    def test_the_deployment_check_runs_first(self):
        """先證明部署得起來，後面那項的失敗才有唯一解釋。"""
        self.assertIs(verify.CHECKS[0], verify.check_deploys)
        self.assertIs(verify.CHECKS[1], verify.check_sideload_default)

    def test_each_check_starts_from_a_fresh_snapshot(self):
        """第一項會開啟側載，不還原的話第二項測到的是它留下的狀態。"""
        vm = FakeVm()
        boots = []
        calls = []

        def fake_check(name):
            def check(vm, package, workdir):
                calls.append(name)
                return verify.Result(name, verify.PASS, "")
            return check

        verify.run_all(vm, "pkg.msix", "work",
                       checks=(fake_check("a"), fake_check("b")),
                       boot=lambda machine: boots.append(machine))
        self.assertEqual(len(boots), 2)
        self.assertEqual(calls, ["a", "b"])

    def test_later_checks_become_inconclusive_when_the_first_one_fails(self):
        """順序的意義全在這裡：A 沒通過時，B 的失敗可能與 A 同一個原因。

        實際發生過：A 因為安裝檔本身缺少 winrt 綁定而失敗，B 照樣回報通過
        ——但那個「部署被拒」跟側載設定無關，是同一個缺失造成的。單看側載
        登錄值無法分辨，必須把前一項的結果納入判斷。
        """
        def failing(vm, package, workdir):
            return verify.Result("A", verify.FAIL, "裝不起來")

        def passing(vm, package, workdir):
            return verify.Result("B", verify.PASS, "側載值未設定且部署被拒")

        results = verify.run_all(FakeVm(), "pkg.msix", "work",
                                 checks=(failing, passing),
                                 boot=lambda machine: None)
        self.assertEqual(results[0].verdict, verify.FAIL)
        self.assertEqual(results[1].verdict, verify.INCONCLUSIVE)
        self.assertIn("側載值未設定且部署被拒", results[1].detail)

    def test_later_checks_keep_their_verdict_when_the_first_one_passes(self):
        def passing(name):
            def check(vm, package, workdir):
                return verify.Result(name, verify.PASS, "")
            return check

        results = verify.run_all(FakeVm(), "pkg.msix", "work",
                                 checks=(passing("A"), passing("B")),
                                 boot=lambda machine: None)
        self.assertEqual([r.verdict for r in results],
                         [verify.PASS, verify.PASS])

    def test_a_failing_check_does_not_stop_the_rest_from_running(self):
        """第一項失敗時仍然跑完第二項——它的原始判定寫進 detail 有診斷價值。"""
        ran = []

        def failing(vm, package, workdir):
            ran.append("a")
            return verify.Result("a", verify.FAIL, "boom")

        def second(vm, package, workdir):
            ran.append("b")
            return verify.Result("b", verify.PASS, "")

        verify.run_all(FakeVm(), "pkg.msix", "work",
                       checks=(failing, second),
                       boot=lambda machine: None)
        self.assertEqual(ran, ["a", "b"])


if __name__ == "__main__":
    unittest.main()
