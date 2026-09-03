"""webview2_runtime.py 的測試：偵測 WebView2 Runtime，缺少時取得它。

這個模組存在的理由是一個實測到的失效：缺少 WebView2 Runtime 時，安裝視窗
會開啟但 CSS 與 JavaScript 都不生效——版面塌成直向堆疊並溢出視窗，應用程式
名稱停在佔位文字「載入中...」，**全程不顯示任何錯誤訊息，行程也不結束**。
使用者只會看到一個像是還在載入的畫面，永遠等下去。

**這件事不能沿用既有的相依元件機制。** 那套機制的偵測結果、詢問畫面、
安裝進度全都呈現在 `ui/index.html` 裡（見該檔的「相依元件自動安裝頁」），
而那個頁面正是缺少 WebView2 時打不開的東西——雞生蛋。因此這裡全程在 Python
內完成，不碰 HTML，只重用那套機制的下載與執行部分。

判斷是否已安裝依微軟文件：讀 `EdgeUpdate\\Clients\\{F3017226-...}` 的 `pv`。
**三個位置都要查**，其中 HKCU 對應的是使用者層級的安裝——只查 HKLM 會把那種
安裝誤判成沒裝，然後對著一台已經有 WebView2 的機器要求重裝。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _fakes import FakeWinReg

import webview2_runtime


def put(registry, hive, path, version):
    """在假登錄表裡放一個「已安裝」的紀錄。

    FakeWinReg 存的是原始值，型別是 QueryValueEx 回傳時才補上的，因此這裡
    不要自己包成 tuple。
    """
    registry.store[(hive, path)] = {webview2_runtime.VERSION_VALUE: version}


class FindVersionTests(unittest.TestCase):
    def setUp(self):
        self.registry = FakeWinReg()

    def test_absent_when_no_key_exists(self):
        self.assertEqual(webview2_runtime.find_version(self.registry), "")

    def test_finds_the_machine_wide_install_on_64_bit(self):
        put(self.registry, FakeWinReg.HKEY_LOCAL_MACHINE,
            webview2_runtime.WOW6432_PATH, "145.0.3800.97")
        self.assertEqual(webview2_runtime.find_version(self.registry),
                         "145.0.3800.97")

    def test_finds_the_machine_wide_install_on_32_bit(self):
        put(self.registry, FakeWinReg.HKEY_LOCAL_MACHINE,
            webview2_runtime.NATIVE_PATH, "144.0.1.1")
        self.assertEqual(webview2_runtime.find_version(self.registry),
                         "144.0.1.1")

    def test_finds_a_per_user_install(self):
        """只查 HKLM 會把使用者層級的安裝誤判成沒裝，然後要求重裝一份已經有的。"""
        put(self.registry, FakeWinReg.HKEY_CURRENT_USER,
            webview2_runtime.NATIVE_PATH, "143.2.3.4")
        self.assertEqual(webview2_runtime.find_version(self.registry),
                         "143.2.3.4")

    def test_zero_version_counts_as_absent(self):
        """EdgeUpdate 會留下 pv=0.0.0.0 的空殼機碼，那不是「已安裝」。"""
        put(self.registry, FakeWinReg.HKEY_LOCAL_MACHINE,
            webview2_runtime.WOW6432_PATH, "0.0.0.0")
        self.assertEqual(webview2_runtime.find_version(self.registry), "")

    def test_empty_version_counts_as_absent(self):
        put(self.registry, FakeWinReg.HKEY_LOCAL_MACHINE,
            webview2_runtime.WOW6432_PATH, "   ")
        self.assertEqual(webview2_runtime.find_version(self.registry), "")

    def test_a_broken_key_does_not_stop_the_remaining_lookups(self):
        """其中一個位置讀不到時要繼續查下一個，不是整個放棄。"""
        self.registry.store[(FakeWinReg.HKEY_LOCAL_MACHINE,
                             webview2_runtime.WOW6432_PATH)] = {}
        put(self.registry, FakeWinReg.HKEY_CURRENT_USER,
            webview2_runtime.NATIVE_PATH, "142.0.0.1")
        self.assertEqual(webview2_runtime.find_version(self.registry),
                         "142.0.0.1")


class EnsureAvailableTests(unittest.TestCase):
    """缺少時的完整流程。ask／install 是接縫：真正的實作會跳原生對話框、
    下載並執行微軟的載入器，測試裡兩者都用替身。"""

    def setUp(self):
        self.registry = FakeWinReg()
        self.asked = []
        self.installed = []

    def ask(self, answer):
        def fake():
            self.asked.append(True)
            return answer
        return fake

    def install(self, succeeds, then_version=""):
        def fake():
            self.installed.append(True)
            if then_version:
                put(self.registry, FakeWinReg.HKEY_LOCAL_MACHINE,
                    webview2_runtime.WOW6432_PATH, then_version)
            return succeeds
        return fake

    def ensure(self, ask, install, sleeps=None):
        return webview2_runtime.ensure_available(
            ask, install, registry=self.registry,
            sleep=(sleeps.append if sleeps is not None else (lambda s: None)))

    def test_already_installed_asks_nothing(self):
        put(self.registry, FakeWinReg.HKEY_LOCAL_MACHINE,
            webview2_runtime.WOW6432_PATH, "145.0.0.1")
        result = self.ensure(self.ask(True), self.install(True))
        self.assertEqual(result.state, webview2_runtime.INSTALLED)
        self.assertEqual(result.version, "145.0.0.1")
        self.assertEqual(self.asked, [])
        self.assertEqual(self.installed, [])

    def test_declining_does_not_install_anything(self):
        result = self.ensure(self.ask(False), self.install(True))
        self.assertEqual(result.state, webview2_runtime.DECLINED)
        self.assertEqual(self.installed, [])

    def test_successful_install_reports_the_new_version(self):
        result = self.ensure(self.ask(True), self.install(True, "145.0.3800.97"))
        self.assertEqual(result.state, webview2_runtime.JUST_INSTALLED)
        self.assertEqual(result.version, "145.0.3800.97")

    def test_a_failed_install_is_not_retried(self):
        """下載失敗或使用者在微軟的畫面按取消，重試不會改變結果。"""
        result = self.ensure(self.ask(True), self.install(False))
        self.assertEqual(result.state, webview2_runtime.FAILED)
        self.assertEqual(len(self.installed), 1)

    def test_rechecks_once_before_giving_up(self):
        """裝完卻查不到時先等一下再查一次——登錄表的寫入可能還沒落地。

        對使用者說「還是沒有」而他明明剛裝完，是最沒有說服力的一種錯誤訊息。
        """
        sleeps = []
        result = self.ensure(self.ask(True), self.install(True), sleeps=sleeps)
        self.assertEqual(result.state, webview2_runtime.FAILED)
        self.assertEqual(len(sleeps), 1, "重查之前要先等一下")

    def test_the_recheck_can_succeed(self):
        calls = []

        def install_then_appear_late():
            calls.append(True)
            return True

        def sleep(seconds):
            # 模擬「登錄表在稍後才寫入」：等待期間值才出現。
            put(self.registry, FakeWinReg.HKEY_LOCAL_MACHINE,
                webview2_runtime.WOW6432_PATH, "145.9.9.9")

        result = webview2_runtime.ensure_available(
            self.ask(True), install_then_appear_late,
            registry=self.registry, sleep=sleep)
        self.assertEqual(result.state, webview2_runtime.JUST_INSTALLED)
        self.assertEqual(result.version, "145.9.9.9")


class FakeResponse:
    def __init__(self, payload, declared_length=None):
        self._payload = payload
        self._offset = 0
        self._length = declared_length

    def getheader(self, name):
        return None if self._length is None else str(self._length)

    def read(self, size):
        chunk = self._payload[self._offset:self._offset + size]
        self._offset += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class DownloadTests(unittest.TestCase):
    """下載載入器。不走 BITS：那的好處是背景下載與斷點續傳，對 1.7 MB 沒有
    意義，為此把 dependency_install 裡那段邏輯抽出來反而動到一支測試完整的
    函式。"""

    def setUp(self):
        self.dest = os.path.join(tempfile.mkdtemp(), "setup.exe")

    def test_writes_the_payload(self):
        ok = webview2_runtime.download(
            "https://example.invalid/x.exe", self.dest,
            opener=lambda url, timeout: FakeResponse(b"abcdef", 6))
        self.assertTrue(ok)
        with open(self.dest, "rb") as handle:
            self.assertEqual(handle.read(), b"abcdef")

    def test_a_truncated_download_is_a_failure(self):
        """真實抓到過的問題（稽核 F06）：連線中途斷掉時 read() 只是回傳空
        字串正常結束迴圈，不會拋例外——不比對長度就會去執行一個被截斷的
        安裝檔。"""
        ok = webview2_runtime.download(
            "https://example.invalid/x.exe", self.dest,
            opener=lambda url, timeout: FakeResponse(b"abc", 6))
        self.assertFalse(ok)

    def test_a_truncated_download_leaves_no_file_behind(self):
        """留著半截的檔案，下一次就可能被當成「已經下載過」而直接執行。"""
        webview2_runtime.download(
            "https://example.invalid/x.exe", self.dest,
            opener=lambda url, timeout: FakeResponse(b"abc", 6))
        self.assertFalse(os.path.exists(self.dest))

    def test_a_connection_error_is_a_failure_not_an_exception(self):
        """下載失敗是預期中的結局之一（沒網路、被防火牆擋），不是例外狀況。"""
        def boom(url, timeout):
            raise OSError("模擬連線失敗")
        self.assertFalse(webview2_runtime.download(
            "https://example.invalid/x.exe", self.dest, opener=boom))


class BootstrapperTests(unittest.TestCase):
    def test_runs_without_the_silent_flag(self):
        """不傳 /silent：安裝端沒有可用的畫面可以自行顯示進度（HTML 打不開、
        Tkinter 不在安裝檔裡），因此讓微軟的安裝程式顯示它自己的進度介面。"""
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            return type("R", (), {"returncode": 0})()

        self.assertTrue(webview2_runtime.run_bootstrapper(r"C:\x.exe",
                                                          run=fake_run))
        self.assertEqual(calls, [[r"C:\x.exe"]])

    def test_a_nonzero_exit_is_a_failure(self):
        """使用者在微軟的畫面按取消也走這條。"""
        def fake_run(cmd, **kwargs):
            return type("R", (), {"returncode": 1})()
        self.assertFalse(webview2_runtime.run_bootstrapper(r"C:\x.exe",
                                                           run=fake_run))


class DialogTests(unittest.TestCase):
    """原生對話框。三個進入點共用，因此放在模組裡而不是各自寫一份。"""

    def test_confirm_reports_yes(self):
        calls = []

        def fake_box(hwnd, body, title, flags):
            calls.append((body, title, flags))
            return webview2_runtime.IDYES

        self.assertTrue(webview2_runtime.confirm("t", "b", message_box=fake_box))
        self.assertEqual(calls[0][2],
                         webview2_runtime.MB_YESNO | webview2_runtime.MB_ICONQUESTION)

    def test_confirm_reports_anything_else_as_no(self):
        """關掉對話框（右上角的 X）不是「是」。"""
        self.assertFalse(webview2_runtime.confirm(
            "t", "b", message_box=lambda *a: 7))

    def test_notify_uses_a_warning_icon(self):
        calls = []
        webview2_runtime.notify("t", "b",
                                message_box=lambda h, b, t, f: calls.append(f))
        self.assertEqual(calls, [webview2_runtime.MB_ICONWARNING])

    def test_a_broken_message_box_is_not_a_crash(self):
        """對話框失敗不該讓安裝程式以未處理例外收場——那比原本的症狀更糟。"""
        def boom(*args):
            raise OSError("模擬 user32 呼叫失敗")
        self.assertFalse(webview2_runtime.confirm("t", "b", message_box=boom))
        webview2_runtime.notify("t", "b", message_box=boom)


class AcquireTests(unittest.TestCase):
    def test_downloads_then_runs(self):
        order = []

        def fake_download(url, dest, **kwargs):
            order.append("download")
            return True

        def fake_run(path, **kwargs):
            order.append("run")
            return True

        self.assertTrue(webview2_runtime.acquire(
            download_fn=fake_download, run_fn=fake_run))
        self.assertEqual(order, ["download", "run"])

    def test_a_failed_download_does_not_run_anything(self):
        ran = []
        self.assertFalse(webview2_runtime.acquire(
            download_fn=lambda url, dest, **k: False,
            run_fn=lambda path, **k: ran.append(True)))
        self.assertEqual(ran, [])


class MessageTests(unittest.TestCase):
    """對話框的文字。

    這幾則都在 webview 視窗建立之前顯示，因此不能用 `ui/*.html` 的翻譯表
    ——那正是缺少這個元件時打不開的東西。改用模組自己的訊息表，語言由
    `lang_detect` 的偵測結果決定，與單一實例鎖的對話框同一個作法。

    鍵集合的一致性由 tests/test_message_tables.py 統一檢查，這裡只驗證
    取用方式與參數代入。
    """

    def test_asks_in_the_detected_language(self):
        zh = webview2_runtime.text("ask.body", "zh-TW")
        en = webview2_runtime.text("ask.body", "en")
        self.assertIn("WebView2", zh)
        self.assertIn("WebView2", en)
        self.assertNotEqual(zh, en)

    def test_the_download_page_is_spelled_out(self):
        """MessageBoxW 的網址不能點，使用者只能照著打，所以一定要完整寫出來。"""
        body = webview2_runtime.text("unavailable.body", "zh-TW",
                                     url=webview2_runtime.DOWNLOAD_PAGE_URL)
        self.assertIn(webview2_runtime.DOWNLOAD_PAGE_URL, body)

    def test_the_uninstall_prompt_names_the_application(self):
        body = webview2_runtime.text("uninstall.body", "zh-TW", app="MyApp")
        self.assertIn("MyApp", body)

    def test_an_unknown_language_falls_back_rather_than_failing(self):
        self.assertTrue(webview2_runtime.text("ask.title", "ja"))


if __name__ == "__main__":
    unittest.main()
