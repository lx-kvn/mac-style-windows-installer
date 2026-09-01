"""msix_deploy.py 的測試：請求系統部署／移除 MSIX 套件。

第二輪決議第十五項要求把「請求系統部署套件」設計為可注入的參數，測試傳入
替身、僅記錄呼叫而不實際部署——實際部署耗時，且失敗時會在開發機留下殘留。
這裡的替身注入的是 `PackageManager` 本身，不是更上層的包裝，因為要被測到的
正是「拿到非同步操作之後怎麼判斷成敗」那一段。

**這份測試存在的主要理由是第三輪 spike 抓到的陷阱**：非同步操作有兩種取得
結果的方式，`get_results()` 不等待操作完成，在操作仍進行中呼叫會回傳一個
`extended_error_code = 0`、`error_text = ""`、`is_registered = False` 的
結果——與成功難以區分。誤用它會做出「安裝失敗卻回報成功」的安裝檔，而且
本機測試不易顯現（本機部署快，看起來就像成功）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import msix_deploy


class FakeResult:
    def __init__(self, is_registered=True, error_text="", error_code=0):
        self.is_registered = is_registered
        self.error_text = error_text
        self.extended_error_code = error_code


class FakeOperation:
    """假的非同步操作。

    `get()` 與 `get_results()` 刻意回傳不同的東西：前者是真正的結果，後者是
    「操作還沒完成」時那個看起來像成功的空結果。用錯的那一個，測試就會抓到。
    """

    def __init__(self, result, progress_values=(), status=1):
        self._result = result
        self._progress_values = list(progress_values)
        self.status = status
        self.progress = None

    def get(self):
        for value in self._progress_values:
            if self.progress:
                self.progress(self, type("P", (), {"percentage": value})())
        return self._result

    def get_results(self):
        return FakeResult(is_registered=False, error_text="", error_code=0)


class FakePackageId:
    def __init__(self, name, full_name):
        self.name = name
        self.full_name = full_name


class FakePackage:
    def __init__(self, name, full_name):
        self.id = FakePackageId(name, full_name)


class FakeManager:
    def __init__(self, result=None, packages=(), progress_values=()):
        self.result = result if result is not None else FakeResult()
        self.packages = list(packages)
        self.progress_values = list(progress_values)
        self.add_calls = []
        self.remove_calls = []
        self.find_calls = []

    def add_package_async(self, uri, dependencies, options):
        self.add_calls.append((uri, dependencies, options))
        return FakeOperation(self.result, self.progress_values)

    def remove_package_async(self, full_name):
        self.remove_calls.append(full_name)
        return FakeOperation(self.result)

    def find_packages_by_user_security_id(self, sid):
        self.find_calls.append(sid)
        return list(self.packages)


class DeployTest(unittest.TestCase):
    def test_a_successful_deployment_is_reported_as_success(self):
        outcome = msix_deploy.deploy("C:\\x\\app.msix", manager=FakeManager())
        self.assertTrue(outcome.ok)

    def test_the_package_path_reaches_the_manager(self):
        manager = FakeManager()
        msix_deploy.deploy("C:\\x\\app.msix", manager=manager)
        self.assertEqual(len(manager.add_calls), 1)
        uri = manager.add_calls[0][0]
        self.assertIn("app.msix", str(uri))

    def test_the_result_comes_from_get_not_get_results(self):
        """第三輪 spike 第四項：get_results() 不等待操作完成，回傳的空結果
        與成功難以區分。用錯會做出「安裝失敗卻回報成功」的安裝檔。

        這裡的假操作讓兩者回傳不同的東西：get() 是真正的失敗結果，
        get_results() 是那個看起來像成功的空結果。實作若取錯，這個測試會
        看到 ok=True。
        """
        failure = FakeResult(is_registered=False, error_text="憑證未受信任", error_code=0x800B0109)
        outcome = msix_deploy.deploy("C:\\x\\app.msix", manager=FakeManager(result=failure))
        self.assertFalse(outcome.ok)

    def test_an_empty_result_without_an_error_text_is_still_a_failure(self):
        """不能只依賴例外或錯誤碼——is_registered 才是「真的裝上去了」的依據。"""
        empty = FakeResult(is_registered=False, error_text="", error_code=0)
        outcome = msix_deploy.deploy("C:\\x\\app.msix", manager=FakeManager(result=empty))
        self.assertFalse(outcome.ok)

    def test_the_system_error_text_is_passed_through_verbatim(self):
        """第三輪 spike 第七項：error_text 是完整且已在地化的說明文字，
        直接轉呈即可，不需要另外編一則訊息。"""
        message = "錯誤 0x800B0109: 應用程式套件或套件組合中之簽章的根憑證必須受信任。"
        failure = FakeResult(is_registered=False, error_text=message)
        outcome = msix_deploy.deploy("C:\\x\\app.msix", manager=FakeManager(result=failure))
        self.assertEqual(outcome.error_text, message)

    def test_progress_is_forwarded_to_the_caller(self):
        """第十一輪 CI 探針確認進度 callback 會實際觸發，且是真實百分比，
        因此進度條不需要退化為不確定動畫。"""
        seen = []
        msix_deploy.deploy(
            "C:\\x\\app.msix",
            manager=FakeManager(progress_values=[0, 15, 61, 97]),
            progress=seen.append,
        )
        self.assertEqual(seen, [0, 15, 61, 97])

    def test_deploying_without_a_progress_callback_does_not_fail(self):
        outcome = msix_deploy.deploy(
            "C:\\x\\app.msix", manager=FakeManager(progress_values=[0, 50, 100]))
        self.assertTrue(outcome.ok)

    def test_an_exception_from_the_system_becomes_a_failure_not_a_crash(self):
        class Exploding(FakeManager):
            def add_package_async(self, uri, dependencies, options):
                raise OSError("存取被拒")

        outcome = msix_deploy.deploy("C:\\x\\app.msix", manager=Exploding())
        self.assertFalse(outcome.ok)
        self.assertIn("存取被拒", outcome.error_text)


class FindInstalledTest(unittest.TestCase):
    """第十一輪 CI 探針確認的綁定命名：列舉當前使用者的套件用
    find_packages_by_user_security_id("")，沒有 find_packages_for_user。"""

    def test_a_matching_package_returns_its_full_name(self):
        manager = FakeManager(packages=[
            FakePackage("Other.App", "Other.App_1.0.0.0_x64__abc"),
            FakePackage("My.App", "My.App_1.0.0.0_x64__xyz"),
        ])
        self.assertEqual(
            msix_deploy.find_installed("My.App", manager=manager),
            "My.App_1.0.0.0_x64__xyz",
        )

    def test_no_match_returns_none(self):
        manager = FakeManager(packages=[FakePackage("Other.App", "Other.App_1_x64__abc")])
        self.assertIsNone(msix_deploy.find_installed("My.App", manager=manager))

    def test_the_current_user_is_requested(self):
        manager = FakeManager()
        msix_deploy.find_installed("My.App", manager=manager)
        self.assertEqual(manager.find_calls, [""])

    def test_a_failure_to_enumerate_is_reported_as_not_found(self):
        """列舉全機器的套件會被系統拒絕（權限），那是正確的權限檢查而非故障
        （第三輪 spike 第二項）。當前使用者的版本失敗時視為找不到即可。"""
        class Exploding(FakeManager):
            def find_packages_by_user_security_id(self, sid):
                raise PermissionError("存取被拒")

        self.assertIsNone(msix_deploy.find_installed("My.App", manager=Exploding()))


class RemoveTest(unittest.TestCase):
    def test_remove_is_called_with_a_single_argument(self):
        """第十一輪 CI 探針實測：remove_package_async 只收一個參數，以兩個
        參數呼叫會得到 TypeError: Invalid parameter count。"""
        manager = FakeManager()
        msix_deploy.remove("My.App_1.0.0.0_x64__xyz", manager=manager)
        self.assertEqual(manager.remove_calls, ["My.App_1.0.0.0_x64__xyz"])

    def test_a_failing_removal_is_reported(self):
        failure = FakeResult(is_registered=True, error_text="移除失敗")
        outcome = msix_deploy.remove("X_1_x64__a", manager=FakeManager(result=failure))
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.error_text, "移除失敗")


class AvailabilityTest(unittest.TestCase):
    """綁定套件缺席時要講清楚，不是拋一個看不懂的 ImportError。"""

    def test_a_missing_binding_produces_an_explanatory_failure(self):
        def exploding_manager():
            raise ImportError("No module named 'winrt'")

        outcome = msix_deploy.deploy("C:\\x\\app.msix", manager_factory=exploding_manager)
        self.assertFalse(outcome.ok)
        self.assertIn("winrt", outcome.error_text)


if __name__ == "__main__":
    unittest.main()
