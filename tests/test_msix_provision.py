"""`msix_provision.py`：提權的子行程實際做的那幾件事。

依 [ADR-0013](docs/adr/0013-msix-all-users-scope-is-an-opt-in-field.md)：

- **決定三**：佈建交由提權的子行程執行。這個模組是那個子行程的內容。
- **決定四之外的安全決定**（`/grill-with-docs` 第十二題）：`Setup.exe` 帶
  內部旗標再跑一次，代價是那個旗標任何人都能打，而它會以提權身分佈建參數
  指定的那一份套件。主行程因此把套件的 SHA-256 一併傳進來，比對不符就
  拒絕——那個旗標只能用來佈建「這一次安裝自己解壓出來的那一份」。
- **決定五**：僅在套件不位於系統磁碟區時複製。複製由這個提權的子行程做，
  目的地是只有管理員寫得進去的位置，佈建結束後由它自己刪掉（第七題）。
- **第四題**：佈建失敗就當場取消佈建。子行程當下還是提權的，清理不用再跳
  一次 UAC，而它擋掉的正是「佈建紀錄留下來、使用者自己清不掉」那個情形。

## 佈建與註冊是兩件事

這個模組只做**佈建**（把套件登記到這台機器上，讓每位使用者有資格拿到），
不做**註冊**（某一位使用者那一端真的完成安裝）。註冊留在未提權的主行程，
因為提權之後做註冊會以 `0x80070005` 失敗（ADR-0013 背景第五項）。
"""
import hashlib
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import msix_deploy
import msix_provision


def ok():
    return msix_deploy.Outcome(True, "", 0)


def failed(text="系統拒絕了這個要求。"):
    return msix_deploy.Outcome(False, text, 0x80070005)


class Recorder:
    """記下每一步被呼叫的順序——這個流程的重點正是順序。"""

    def __init__(self, stage=None, provision=None, deprovision=None,
                 family="MyApp_8wekyb3d8bbwe"):
        self.calls = []
        self._stage = stage or ok
        self._provision = provision or ok
        self._deprovision = deprovision or ok
        self._family = family
        self.staged_path = None

    def stage(self, path, progress=None):
        self.calls.append("stage")
        self.staged_path = path
        return self._stage()

    def provision(self, family_name, progress=None):
        self.calls.append("provision")
        self.provisioned = family_name
        return self._provision()

    def deprovision(self, family_name, progress=None):
        self.calls.append("deprovision")
        return self._deprovision()

    def find_family_name(self, identity_name, publisher=""):
        self.calls.append("find_family_name")
        return self._family


def write_package(directory, content=b"pretend this is an msix"):
    path = os.path.join(directory, "app.msix")
    with open(path, "wb") as f:
        f.write(content)
    return path, hashlib.sha256(content).hexdigest()


class PackageBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.package, self.digest = write_package(self.tmp)

    def run_it(self, recorder=None, **overrides):
        recorder = recorder or Recorder()
        options = dict(
            stage=recorder.stage,
            provision=recorder.provision,
            deprovision=recorder.deprovision,
            find_family_name=recorder.find_family_name,
            on_system_volume=lambda path: True,
            copy_to_system_volume=lambda path: path,
            remove_copy=lambda path: None,
        )
        options.update(overrides)
        return recorder, msix_provision.run_provisioning(
            self.package, self.digest, "MyApp", **options)


class TheHappyPath(PackageBase):
    def test_it_stages_then_provisions(self):
        """佈建要求套件先經過 stage（ADR-0009 所列的三項前置條件之一），
        而佈建本身吃的是套件家族名稱，不是路徑。"""
        recorder, (code, _message) = self.run_it()
        self.assertEqual(code, msix_provision.EXIT_OK)
        self.assertEqual(recorder.calls,
                         ["stage", "find_family_name", "provision"])

    def test_it_provisions_the_family_name_that_was_looked_up(self):
        recorder, _ = self.run_it()
        self.assertEqual(recorder.provisioned, "MyApp_8wekyb3d8bbwe")

    def test_nothing_is_deprovisioned_when_it_works(self):
        recorder, _ = self.run_it()
        self.assertNotIn("deprovision", recorder.calls)


class TheDigestIsCheckedFirst(PackageBase):
    """第十二題：那個旗標只能用來佈建這一次安裝自己解壓出來的那一份。"""

    def test_a_mismatch_is_refused(self):
        recorder = Recorder()
        result = msix_provision.run_provisioning(
            self.package, "0" * 64, "MyApp",
            stage=recorder.stage, provision=recorder.provision,
            deprovision=recorder.deprovision,
            find_family_name=recorder.find_family_name,
            on_system_volume=lambda path: True)
        self.assertEqual(result[0], msix_provision.EXIT_VERIFY_FAILED)

    def test_nothing_at_all_happens_on_a_mismatch(self):
        """比對不符時連複製都不該發生——那是提權的行程去碰一個來路不明的
        檔案，而拒絕的成本是零。"""
        recorder = Recorder()
        copies = []
        msix_provision.run_provisioning(
            self.package, "0" * 64, "MyApp",
            stage=recorder.stage, provision=recorder.provision,
            deprovision=recorder.deprovision,
            find_family_name=recorder.find_family_name,
            on_system_volume=lambda path: False,
            copy_to_system_volume=lambda path: copies.append(path) or path)
        self.assertEqual(recorder.calls, [])
        self.assertEqual(copies, [])

    def test_a_matching_digest_is_accepted_regardless_of_letter_case(self):
        recorder = Recorder()
        result = msix_provision.run_provisioning(
            self.package, self.digest.upper(), "MyApp",
            stage=recorder.stage, provision=recorder.provision,
            deprovision=recorder.deprovision,
            find_family_name=recorder.find_family_name,
            on_system_volume=lambda path: True)
        self.assertEqual(result[0], msix_provision.EXIT_OK)

    def test_a_missing_package_is_its_own_answer(self):
        result = msix_provision.run_provisioning(
            os.path.join(self.tmp, "gone.msix"), self.digest, "MyApp")
        self.assertEqual(result[0], msix_provision.EXIT_BAD_REQUEST)

    def test_an_empty_expected_digest_is_refused(self):
        """空字串不是「不用檢查」的意思。把它當成通行證，等於讓呼叫端漏傳
        一個參數就悄悄關掉這道檢查。"""
        recorder = Recorder()
        result = msix_provision.run_provisioning(
            self.package, "", "MyApp",
            stage=recorder.stage, provision=recorder.provision,
            deprovision=recorder.deprovision,
            find_family_name=recorder.find_family_name,
            on_system_volume=lambda path: True)
        self.assertEqual(result[0], msix_provision.EXIT_BAD_REQUEST)
        self.assertEqual(recorder.calls, [])


class CopyingToTheSystemVolume(PackageBase):
    """決定五：只在套件不位於系統磁碟區時複製。"""

    def test_no_copy_when_it_is_already_there(self):
        copies = []
        recorder, (code, _message) = self.run_it(
            on_system_volume=lambda path: True,
            copy_to_system_volume=lambda path: copies.append(path) or path)
        self.assertEqual(code, msix_provision.EXIT_OK)
        self.assertEqual(copies, [])
        self.assertEqual(recorder.staged_path, self.package)

    def test_it_stages_the_copy_not_the_original(self):
        elsewhere = os.path.join(self.tmp, "copied.msix")
        recorder, (code, _message) = self.run_it(
            on_system_volume=lambda path: False,
            copy_to_system_volume=lambda path: elsewhere)
        self.assertEqual(code, msix_provision.EXIT_OK)
        self.assertEqual(recorder.staged_path, elsewhere)

    def test_the_copy_is_removed_afterwards(self):
        removed = []
        elsewhere = os.path.join(self.tmp, "copied.msix")
        self.run_it(on_system_volume=lambda path: False,
                    copy_to_system_volume=lambda path: elsewhere,
                    remove_copy=removed.append)
        self.assertEqual(removed, [elsewhere])

    def test_the_original_is_never_removed(self):
        """主行程還要拿它替當前使用者註冊（決定三：先佈建、後註冊）。"""
        removed = []
        self.run_it(on_system_volume=lambda path: True,
                    remove_copy=removed.append)
        self.assertEqual(removed, [])

    def test_a_failing_copy_stops_before_staging(self):
        def boom(path):
            raise OSError("磁碟空間不足")

        recorder, (code, message) = self.run_it(
            on_system_volume=lambda path: False, copy_to_system_volume=boom)
        self.assertEqual(code, msix_provision.EXIT_COPY_FAILED)
        self.assertEqual(recorder.calls, [])
        self.assertIn("磁碟空間不足", message)

    def test_the_copy_is_removed_even_when_provisioning_fails(self):
        removed = []
        elsewhere = os.path.join(self.tmp, "copied.msix")
        self.run_it(Recorder(provision=failed),
                    on_system_volume=lambda path: False,
                    copy_to_system_volume=lambda path: elsewhere,
                    remove_copy=removed.append)
        self.assertEqual(removed, [elsewhere])

    def test_a_failing_cleanup_does_not_change_the_answer(self):
        """清不掉一個暫存檔不足以把一次成功的佈建說成失敗。"""
        def boom(path):
            raise OSError("檔案使用中")

        _recorder, (code, _message) = self.run_it(
            on_system_volume=lambda path: False,
            copy_to_system_volume=lambda path: path, remove_copy=boom)
        self.assertEqual(code, msix_provision.EXIT_OK)


class WhenTheSystemRefuses(PackageBase):
    def test_a_failing_stage_stops_there(self):
        recorder, (code, message) = self.run_it(Recorder(stage=failed))
        self.assertEqual(code, msix_provision.EXIT_STAGE_FAILED)
        self.assertEqual(recorder.calls, ["stage"])
        self.assertIn("系統拒絕了這個要求。", message)

    def test_an_unknown_family_name_stops_before_provisioning(self):
        recorder, (code, _message) = self.run_it(Recorder(family=None))
        self.assertEqual(code, msix_provision.EXIT_FAMILY_UNKNOWN)
        self.assertEqual(recorder.calls, ["stage", "find_family_name"])

    def test_a_failing_provision_is_reported_with_the_system_wording(self):
        """`error_text` 是系統給的完整且已在地化的說明，直接轉呈——自己另編
        一則只會失去資訊（`msix_deploy` 模組說明所立的慣例）。"""
        _recorder, (code, message) = self.run_it(Recorder(provision=failed))
        self.assertEqual(code, msix_provision.EXIT_PROVISION_FAILED)
        self.assertIn("系統拒絕了這個要求。", message)


class CleaningUpAfterAFailedProvision(PackageBase):
    """第四題：佈建失敗就當場取消佈建，趁子行程還是提權的。"""

    def test_it_deprovisions(self):
        recorder, _ = self.run_it(Recorder(provision=failed))
        self.assertEqual(recorder.calls,
                         ["stage", "find_family_name", "provision", "deprovision"])

    def test_a_failing_cleanup_is_information_not_a_second_failure(self):
        recorder = Recorder(provision=failed, deprovision=lambda: failed("清不掉"))
        _recorder, (code, message) = self.run_it(recorder)
        self.assertEqual(code, msix_provision.EXIT_PROVISION_FAILED)
        self.assertIn("系統拒絕了這個要求。", message)

    def test_a_raising_cleanup_does_not_escape(self):
        def boom(family_name, progress=None):
            raise RuntimeError("爆了")

        recorder = Recorder(provision=failed)
        recorder.deprovision = boom
        _recorder, (code, _message) = self.run_it(recorder)
        self.assertEqual(code, msix_provision.EXIT_PROVISION_FAILED)

    def test_nothing_is_deprovisioned_when_staging_is_what_failed(self):
        """還沒佈建，沒有東西可以取消。"""
        recorder, _ = self.run_it(Recorder(stage=failed))
        self.assertNotIn("deprovision", recorder.calls)


class TellingSystemVolumeFromTheRest(unittest.TestCase):
    """判斷用磁碟機代號比對，不查掛載點那類進階情形——那些情境下就算判錯，
    後果也只是多複製一次（第七題的附帶決定）。"""

    def test_the_system_drive_is_recognised(self):
        self.assertTrue(msix_provision.is_on_system_volume(
            "C:\\Users\\a\\Temp\\app.msix", system_drive="C:"))

    def test_another_drive_is_not(self):
        self.assertFalse(msix_provision.is_on_system_volume(
            "D:\\stuff\\app.msix", system_drive="C:"))

    def test_the_comparison_ignores_letter_case(self):
        self.assertTrue(msix_provision.is_on_system_volume(
            "c:\\Users\\a\\app.msix", system_drive="C:"))

    def test_a_unc_path_is_not_on_the_system_volume(self):
        self.assertFalse(msix_provision.is_on_system_volume(
            "\\\\server\\share\\app.msix", system_drive="C:"))

    def test_it_reads_the_system_drive_from_the_environment_by_default(self):
        with mock.patch.dict(os.environ, {"SystemDrive": "E:"}):
            self.assertTrue(msix_provision.is_on_system_volume("E:\\a.msix"))
            self.assertFalse(msix_provision.is_on_system_volume("C:\\a.msix"))


class WhereTheCopyGoes(unittest.TestCase):
    """第七題：目的地要是只有管理員寫得進去的地方。放在一個未提權使用者也
    寫得進去的位置，從複製完成到佈建開始之間會有一個掉包的空窗。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_it_copies_under_the_given_root(self):
        source, _digest = write_package(self.tmp, b"payload")
        copied = msix_provision.copy_to_admin_temp(source, temp_root=self.tmp)
        self.assertTrue(os.path.isfile(copied))
        with open(copied, "rb") as f:
            self.assertEqual(f.read(), b"payload")

    def test_it_keeps_the_file_name(self):
        source, _digest = write_package(self.tmp)
        copied = msix_provision.copy_to_admin_temp(source, temp_root=self.tmp)
        self.assertEqual(os.path.basename(copied), "app.msix")

    def test_each_copy_gets_its_own_directory(self):
        source, _digest = write_package(self.tmp)
        first = msix_provision.copy_to_admin_temp(source, temp_root=self.tmp)
        second = msix_provision.copy_to_admin_temp(source, temp_root=self.tmp)
        self.assertNotEqual(os.path.dirname(first), os.path.dirname(second))

    def test_the_default_root_is_the_windows_directory(self):
        """`%SystemRoot%\\Temp`：一般使用者寫不進去。`%TEMP%` 與
        `C:\\Users\\Public` 都寫得進去，因此都不是這裡要的。"""
        with mock.patch.dict(os.environ, {"SystemRoot": "C:\\Windows"}):
            self.assertEqual(msix_provision.admin_temp_root(),
                             os.path.join("C:\\Windows", "Temp"))

    def test_removing_a_copy_takes_its_directory_with_it(self):
        source, _digest = write_package(self.tmp)
        copied = msix_provision.copy_to_admin_temp(source, temp_root=self.tmp)
        msix_provision.remove_copy(copied)
        self.assertFalse(os.path.exists(os.path.dirname(copied)))


class HashingTheFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_it_matches_hashlib(self):
        path, digest = write_package(self.tmp, b"x" * 5000)
        self.assertEqual(msix_provision.file_digest(path), digest)

    def test_it_reads_in_chunks(self):
        """GB 級的安裝檔真實存在（決定五就是為它而設）。整份讀進記憶體會在
        那些情形下失敗，而失敗的時候安裝已經走到一半了。"""
        path, digest = write_package(self.tmp, b"y" * (3 * 1024 * 1024))
        self.assertEqual(msix_provision.file_digest(path, chunk_size=64 * 1024),
                         digest)


class FindingTheFamilyName(unittest.TestCase):
    """佈建吃的是套件家族名稱。它不從完整名稱拆字串取得——`Name_Ver_Arch__Hash`
    是系統的內部慣例，拆它等於把一個我們控制不了的格式變成本專案的相依
    （`msix_deploy.InstalledPackage` 立下的同一條規矩）。"""

    def _manager(self, packages):
        manager = mock.Mock()
        manager.find_packages.return_value = packages
        return manager

    def _package(self, name, family, publisher=""):
        package = mock.Mock()
        package.id.name = name
        package.id.family_name = family
        package.id.publisher = publisher
        return package

    def test_it_reads_the_family_name_from_the_package(self):
        manager = self._manager([self._package("MyApp", "MyApp_abc", "CN=Demo")])
        self.assertEqual(
            msix_provision.find_family_name("MyApp", "CN=Demo", manager=manager),
            "MyApp_abc")

    def test_it_enumerates_every_package_rather_than_querying_by_publisher(self):
        """不以發行者字串當查詢條件。設定檔裡的那一串與系統回報的那一串是否
        逐字相同不在我們的控制之內，而查詢對不上時系統只會回傳空集合——結果
        會是一次莫名其妙的降級。列舉全部在這裡行得通，因為這個模組只在提權的
        子行程裡執行。"""
        manager = self._manager([self._package("Other", "Other_x"),
                                 self._package("MyApp", "MyApp_abc")])
        self.assertEqual(
            msix_provision.find_family_name("MyApp", "CN=Demo", manager=manager),
            "MyApp_abc")
        manager.find_packages.assert_called_once_with()

    def test_without_a_publisher_a_single_name_match_is_enough(self):
        """發行者是設定檔帶來的，修正之前編出的安裝檔沒有那個欄位。"""
        manager = self._manager([self._package("MyApp", "MyApp_abc")])
        self.assertEqual(
            msix_provision.find_family_name("MyApp", "", manager=manager),
            "MyApp_abc")

    def test_the_publisher_picks_ours_out_of_two_same_named_packages(self):
        """同名但簽章者不同的套件會被系統當成兩個不相關的應用程式並存
        （ADR-0015 已處理過這個情形）。挑錯一個的後果是把別人的應用程式
        登記給這台機器上的每一位使用者。"""
        manager = self._manager([
            self._package("MyApp", "MyApp_theirs", "CN=Someone Else"),
            self._package("MyApp", "MyApp_ours", "CN=Demo")])
        self.assertEqual(
            msix_provision.find_family_name("MyApp", "CN=Demo", manager=manager),
            "MyApp_ours")

    def test_two_same_named_packages_and_no_publisher_match_gives_none(self):
        """分不出哪一個是自己的時候，寧可降級也不要猜。"""
        manager = self._manager([
            self._package("MyApp", "MyApp_a", "CN=A"),
            self._package("MyApp", "MyApp_b", "CN=B")])
        self.assertIsNone(
            msix_provision.find_family_name("MyApp", "CN=Demo", manager=manager))

    def test_a_lone_name_match_is_accepted_even_if_the_publisher_string_differs(self):
        """發行者字串的形式由系統決定，與設定檔裡那一串未必逐字相同。只有一個
        同名套件時它就是剛剛備妥的那一份——那是這一步的唯一可能來源。"""
        manager = self._manager([self._package("MyApp", "MyApp_abc",
                                               "CN=Demo, O=Demo, C=TW")])
        self.assertEqual(
            msix_provision.find_family_name("MyApp", "CN=Demo", manager=manager),
            "MyApp_abc")

    def test_a_name_that_is_not_there_gives_none(self):
        manager = self._manager([self._package("Other", "Other_x")])
        self.assertIsNone(
            msix_provision.find_family_name("MyApp", "", manager=manager))

    def test_a_failing_query_gives_none(self):
        manager = mock.Mock()
        manager.find_packages.side_effect = OSError("nope")
        self.assertIsNone(
            msix_provision.find_family_name("MyApp", "", manager=manager))


class JudgingWhetherTheSystemAgreed(unittest.TestCase):
    """`is_registered` 是部署的判準，不是這裡的：佈建與 stage 都不會讓
    套件變成「已註冊」，沿用那個判準會把每一次成功都讀成失敗。"""

    def _result(self, error_text="", code=0, registered=False):
        result = mock.Mock()
        result.error_text = error_text
        result.extended_error_code = code
        result.is_registered = registered
        return result

    def test_no_error_text_means_it_worked(self):
        self.assertTrue(msix_provision.outcome_from(self._result()).ok)

    def test_error_text_means_it_did_not(self):
        outcome = msix_provision.outcome_from(self._result("套件損毀。"))
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.error_text, "套件損毀。")

    def test_a_nonzero_code_without_text_is_still_a_failure(self):
        """訊息空白、錯誤碼不為零的組合出現過（`msix_deploy` 模組說明第一
        點）。只看訊息會把它讀成成功。"""
        outcome = msix_provision.outcome_from(self._result(code=0x80070005))
        self.assertFalse(outcome.ok)
        self.assertIn("0x80070005", outcome.error_text)


class TheExitCodesAreDistinct(unittest.TestCase):
    """主行程只拿得到一個結束碼與一份報告檔。結束碼撞在一起的話，兩種不同的
    失敗在紀錄裡會長得一樣。"""

    def test_every_code_is_different(self):
        codes = [msix_provision.EXIT_OK, msix_provision.EXIT_BAD_REQUEST,
                 msix_provision.EXIT_VERIFY_FAILED, msix_provision.EXIT_COPY_FAILED,
                 msix_provision.EXIT_STAGE_FAILED, msix_provision.EXIT_FAMILY_UNKNOWN,
                 msix_provision.EXIT_PROVISION_FAILED]
        self.assertEqual(len(codes), len(set(codes)))

    def test_they_do_not_collide_with_the_installers_own_codes(self):
        """`installer_core` 已經用掉 0（成功）、1（安裝失敗）、2（缺少
        WebView2 Runtime）。"""
        for code in (msix_provision.EXIT_BAD_REQUEST,
                     msix_provision.EXIT_VERIFY_FAILED,
                     msix_provision.EXIT_COPY_FAILED,
                     msix_provision.EXIT_STAGE_FAILED,
                     msix_provision.EXIT_FAMILY_UNKNOWN,
                     msix_provision.EXIT_PROVISION_FAILED):
            self.assertGreater(code, 2)


class TheReportFile(unittest.TestCase):
    """子行程沒有主控台，結束碼也只夠說出「哪一類失敗」。系統給的那段完整
    說明要送回主行程，才能出現在完成畫面上。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_it_writes_the_message(self):
        path = os.path.join(self.tmp, "report.txt")
        msix_provision.write_report(path, "系統拒絕了這個要求。")
        self.assertEqual(msix_provision.read_report(path), "系統拒絕了這個要求。")

    def test_reading_a_missing_report_gives_an_empty_string(self):
        self.assertEqual(
            msix_provision.read_report(os.path.join(self.tmp, "nope.txt")), "")

    def test_writing_somewhere_impossible_does_not_raise(self):
        """報告寫不出來時，佈建本身的結果仍然由結束碼帶回去。為了寫不出一份
        報告而讓子行程崩潰，會把一次成功變成一次不明的失敗。"""
        msix_provision.write_report(
            os.path.join(self.tmp, "no-such-dir", "report.txt"), "訊息")


if __name__ == "__main__":
    unittest.main(verbosity=2)
