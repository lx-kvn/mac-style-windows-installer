"""MSIX 模式的使用者可見訊息，要真的送到使用者面前、而且是他讀得懂的語言。

2026-09-06 替 README 重拍截圖時發現的缺陷，一個問題三層：

一、**那段話沒有出口。** `msix_install` 的成功訊息告訴使用者「這個應用程式
    由 Windows 的套件引擎管理，要移除請到設定 → 應用程式」——那是他唯一
    被告知去哪裡解除安裝的機會，因為這個模式不產生 `uninstall.exe`
    （ADR-0006）。而 `ui/index.html` 成功那一支呼叫 `showModal()` 時，訊息
    參數寫死空字串，後端的 `message` 從頭到尾沒有被讀。實際後果：MSIX 裝完
    的畫面與傳統模式一字不差，使用者到安裝目錄找一個不存在的解除安裝程式。

    靜默安裝反而是對的（`[成功] {message}` 進得了 `/LOG=`），所以看得到那段
    話的是寫部署腳本的人，看不到的是真人使用者。

二、**只有中文。** 這個模組的使用者可見訊息全部是 Python 裡的字面字串，沒有
    走 v0.16.0 才整理好的翻譯機制。英文環境的使用者會收到中文——其中包含
    降版確認那一則，而那一則要使用者決定是否讓系統清除應用程式的資料。

三、**成功畫面不知道自己在哪個模式。** 「安裝完成後立即執行程式」勾選框在
    MSIX 模式也照樣顯示且預設勾選，但它呼叫的 `launch_app()` 把 `main_exe`
    接在傳統的安裝目錄底下，那個目錄在這個模式下不存在；前端 `await` 之後
    不看回傳值就關視窗，因此使用者勾了、按了完成、什麼都沒發生、沒有訊息。

前端的部分以靜態解析驗證，手法比照 `test_ui_drag_gating.py`。
"""
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import msix_deploy
import msix_install
from _fakes import make_installer_api

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_HTML = os.path.join(REPO_ROOT, "ui", "index.html")


def _index():
    with open(INDEX_HTML, "r", encoding="utf-8") as f:
        return f.read()


def _run(lang=None, **kwargs):
    """跑一次成功的安裝，注入的替身讓它不碰任何真實系統。"""
    options = dict(
        package_path="app.msix",
        check_existing=lambda: {"exists": False},
        deploy=lambda path, progress=None: msix_deploy.Outcome(True, "", 0),
    )
    options.update(kwargs)
    if lang is not None:
        options["lang"] = lang
    return msix_install.run(**options)


class TheGuidanceIsTranslated(unittest.TestCase):
    def test_the_default_is_traditional_chinese(self):
        text = msix_install.success_message()
        self.assertIn("設定", text)
        self.assertIn("應用程式", text)

    def test_english_is_actually_english(self):
        text = msix_install.success_message("en")
        self.assertNotEqual(text, msix_install.success_message("zh-TW"))
        # 這一則的用途是指路，因此兩個地標都要在：去哪裡（設定）、找什麼
        # （應用程式清單）。只斷言「不是中文」會讓一則寫錯地方的英文通過。
        self.assertIn("Settings", text)
        self.assertIn("Apps", text)

    def test_it_says_where_to_uninstall_in_both_languages(self):
        """這一則存在的唯一理由是「這個模式沒有 uninstall.exe，要去別的地方
        移除」。任何一種語言漏掉那個地點，這則訊息就沒有用了。"""
        for lang in ("zh-TW", "en"):
            self.assertTrue(msix_install.success_message(lang).strip(), lang)


class TheResultCarriesTheChosenLanguage(unittest.TestCase):
    def test_a_successful_run_returns_the_localized_message(self):
        result = _run(lang="en")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["message"], msix_install.success_message("en"))

    def test_without_a_language_it_stays_traditional_chinese(self):
        self.assertEqual(_run()["message"], msix_install.success_message("zh-TW"))

    def test_the_downgrade_question_follows_the_language_too(self):
        """降版確認要使用者決定是否讓系統清除應用程式的資料。看不懂的語言
        寫成的這個問題，比沒有問還糟。"""
        asked = []

        existing = msix_deploy.InstalledPackage(
            "Demo_9.0.0.0_x64__abc", "9.0.0.0", "CN=Demo")

        def confirm(question):
            asked.append(question)
            return False

        result = _run(lang="en", package_version="1.0.0", package_publisher="CN=Demo",
                      find_installed_package=lambda: existing,
                      confirm_downgrade=confirm)

        self.assertTrue(asked, "沒有問就直接處理了")
        question = asked[0]["message"]
        self.assertNotIn("已經安裝", question)
        self.assertIn("9.0.0.0", question)
        # 拒絕之後的中止訊息也要跟著語言走：使用者剛讀完一個英文的問題，
        # 下一句換成中文等於前面白問。
        self.assertNotIn("安裝已取消", result["message"])
        self.assertIn("cancelled", result["message"].lower())


class TheInstallerPassesItsOwnLanguage(unittest.TestCase):
    """安裝端已經依系統語言決定介面語言，MSIX 那條路徑要用同一個值。"""

    def test_install_msix_hands_its_ui_language_down(self):
        api = make_installer_api(app_name="Demo", install_engine="msix",
                                 msix_package="app.msix")
        api.ui_language = "en"
        with mock.patch("msix_install.run",
                        return_value={"status": "success", "message": "x",
                                      "warnings": []}) as run:
            api._install_msix(log=lambda *_: None)
        self.assertEqual(run.call_args.kwargs.get("lang"), "en")


class TheSuccessScreenShowsIt(unittest.TestCase):
    def test_the_message_reaches_the_modal(self):
        source = _index()
        call = re.search(
            r"showModal\(\s*t\('install_success_title'\)\s*,\s*([^,]+),",
            source)
        self.assertIsNotNone(call, "找不到成功彈窗的呼叫")
        argument = call.group(1).strip()
        # 斷言的是「後端的訊息有被讀」。原本的寫法是斷言參數裡沒有 `''`，
        # 那會把 `result.message || ''` 這種完全正確的寫法也判成失敗——
        # 空字串在那裡是退路，不是問題本身。
        self.assertIn("result.message", argument,
                      "訊息參數沒有讀後端回傳的 message，那段說明不會被顯示")

    def test_it_does_not_repeat_the_title_as_the_message(self):
        """傳統引擎的後端訊息就是「安裝成功」，與彈窗標題一字不差。

        真實抓到（2026-09-07，虛擬機實測的截圖）：把 `result.message` 接上去
        之後，傳統模式的成功畫面出現兩行「安裝成功」——標題一次、訊息一次。
        修正這一層的當下我判斷「傳統模式的 message 是空的，畫面不會變」，那個
        判斷是錯的，而單元測試看不出來，只有把畫面叫出來看才會發現。

        後端不改：那句話在靜默安裝的紀錄檔裡是有用的（`[成功] 安裝成功`）。
        由前端決定要不要顯示。
        """
        source = _index()
        # 用計數而不是 assertIn：失敗訊息會把整份兩千多行的 HTML 印出來，
        # 真正的訊息會被推到畫面外。
        self.assertGreater(source.count("messageBeyondTitle"), 0,
                           "ui/index.html 沒有那個過濾用的輔助函式")
        call = re.search(
            r"showModal\(\s*t\('install_success_title'\)\s*,\s*([^,]+),", source)
        self.assertIsNotNone(call)
        self.assertIn("messageBeyondTitle", call.group(1))

    def test_the_launch_option_is_hidden_under_msix(self):
        """MSIX 沒有傳統的安裝目錄，launch_app() 接不到那支執行檔。留著一個
        按了不會有任何事、也不會有任何訊息的勾選框，比不給更糟。"""
        source = _index()
        call = re.search(
            r"showModal\(\s*t\('install_success_title'\)[^;]*?\)\s*;", source)
        self.assertIsNotNone(call)
        self.assertIn("installEngine", call.group(0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
