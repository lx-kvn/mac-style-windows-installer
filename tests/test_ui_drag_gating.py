"""安裝進行中／完成後，拖曳圖示不能再觸發一次安裝。

使用者實測回報的缺陷（2026-08-30）：在切換到「安裝成功」畫面之前抓住應用
程式圖示不放，畫面切過去之後再把圖示拖到資料夾上放開，安裝會被觸發第二次
——密碼保護的安裝會跳「尚未通過密碼驗證」（成功後解密的暫存資料夾已經被
清掉），沒有密碼保護的安裝則會安靜地再裝一次。

成因有三個，這份測試對應前兩個：

一、`absorbIntoDropTarget()` 的收尾把 `dragAbsorbing` 設回 `false` **之後**
才呼叫 `triggerInstallFromDragItem()`，所以整段安裝期間圖示的狀態跟安裝
開始前完全相同；而 `pointerdown` 只檢查 `dragAbsorbing`，沒有任何「安裝
進行中／已完成」的狀態被檢查。

二、成功彈窗是不透明的全螢幕覆蓋層，擋得住**新的** `pointerdown`，但擋不住
已經 `setPointerCapture()` 的拖曳——捕獲中的指標事件完全不經過命中測試。
而且 `showModal()` 不會隱藏 `mainView`，安裝目的地圖示的座標仍然是真實的，
放開時照樣算命中。所以顯示成功彈窗時要主動終結進行中的拖曳，不能只靠
覆蓋層。

第三個成因（Python 端沒有重入防護）由
`tests/test_installer_core_misc.py` 的 TestTriggerInstallationIsNotReentrant
涵蓋。前端與後端兩層都要有：前端是使用者體驗（圖示直接變成不能抓），後端
是最後防線（`trigger_installation()` 是 JS API 的公開方法）。

手法比照 `test_ui_accessibility.py`／`test_js_api_contract.py`：靜態解析
HTML。實際的手勢時序（抓著不放、期間畫面切換）沒有辦法用靜態解析涵蓋，
那部分依 ADR-0002 已載明的限制，只能由人在真實視窗上實際操作驗證。
"""
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_HTML = os.path.join(REPO_ROOT, "ui", "index.html")


DRAG_MODULE = os.path.join(REPO_ROOT, "ui", "drag_to_target.js")


def _read():
    with open(INDEX_HTML, "r", encoding="utf-8") as f:
        return f.read()


def _read_module():
    with open(DRAG_MODULE, "r", encoding="utf-8") as f:
        return f.read()


def _function_body(content, name):
    match = re.search(
        r"function\s+" + re.escape(name) + r"\s*\(.*?\n        \}",
        content, re.DOTALL,
    )
    return match.group(0) if match else None


class TestInstallStateExists(unittest.TestCase):
    def setUp(self):
        self.content = _read()

    def test_there_is_an_explicit_install_state(self):
        """原本只有 `dragAbsorbing`（吸入動畫播放中）跟 `installSucceeded`
        （關閉彈窗時判斷要不要啟動程式用），兩個都不代表「安裝進行中」。"""
        self.assertIn("installState", self.content)

    def test_the_state_covers_running_and_done_separately(self):
        """兩者要分開：進行中再放一次會讓兩個安裝並行；已完成再放一次會
        因為暫存資料被清掉而失敗。前端的處置一樣（都不准拖），但狀態要
        分得出來，失敗時才知道該回到哪裡。"""
        self.assertIn("'running'", self.content)
        self.assertIn("'done'", self.content)
        self.assertIn("'idle'", self.content)


class TestDragIsGatedByInstallState(unittest.TestCase):
    def setUp(self):
        self.content = _read()

    def test_the_drag_is_gated_by_the_install_state(self):
        """手勢本體抽到 ui/drag_to_target.js 之後，守門分成兩段，兩段都要
        在：安裝端把「現在能不能拖」的判斷交給共用模組（canDrag），共用
        模組在 pointerdown 真的去問它。少任何一段，安裝進行中／完成後圖示
        都還是抓得起來。"""
        match = re.search(r"canDrag:\s*([^\n,]+)", self.content)
        self.assertIsNotNone(match, "ui/index.html 沒有把可否拖曳的判斷交給共用模組")
        self.assertIn("installState", match.group(1))

        module = _read_module()
        pointerdown = re.search(
            r"item\.addEventListener\('pointerdown',.*?\n    \}\);",
            module, re.DOTALL,
        )
        self.assertIsNotNone(pointerdown, "ui/drag_to_target.js 找不到 pointerdown 監聽器")
        self.assertIn(
            "canDrag()", pointerdown.group(0),
            "共用模組的 pointerdown 沒有問呼叫端能不能拖",
        )

    def test_the_shared_trigger_also_checks_the_install_state(self):
        """滑鼠與鍵盤共用 `triggerInstallFromDragItem()`（見
        test_ui_accessibility.py）。只擋 pointerdown 的話，鍵盤那條路徑
        仍然可以在安裝完成後再觸發一次。"""
        body = _function_body(self.content, "triggerInstallFromDragItem")
        self.assertIsNotNone(body, "找不到 triggerInstallFromDragItem()")
        self.assertIn("installState", body)

    def test_attempt_install_marks_the_state_before_calling_the_backend(self):
        body = _function_body(self.content, "attemptInstall")
        self.assertIsNotNone(body, "找不到 attemptInstall()")
        self.assertIn("'running'", body)
        marker = body.index("'running'")
        call = body.index("pywebview.api.trigger_installation")
        self.assertLess(
            marker, call,
            "狀態要在呼叫後端之前就設好，否則兩次拖曳之間仍有空窗",
        )

    def test_success_latches_the_state_to_done(self):
        body = _function_body(self.content, "attemptInstall")
        success = re.search(r"if \(result\.status === 'success'\) \{(.*?)\n            \} else if",
                            body, re.DOTALL)
        self.assertIsNotNone(success, "找不到成功分支")
        self.assertIn("'done'", success.group(1))

    def test_failure_paths_release_the_state(self):
        """失敗、主程式執行中、檔案被鎖住這三條都會讓使用者重試（既有的
        closeRunningAppAndRetry／closeLockingProcessAndRetry），狀態必須
        清回可拖曳，不能把正當的重試一起擋掉。"""
        body = _function_body(self.content, "resetInstallUiAfterCancelOrFailure")
        self.assertIsNotNone(body, "找不到 resetInstallUiAfterCancelOrFailure()")
        self.assertIn(
            "'idle'", body,
            "失敗/取消的共用收尾沒有把安裝狀態清回可拖曳",
        )


class TestInFlightDragIsTerminatedWhenTheModalOpens(unittest.TestCase):
    def setUp(self):
        self.content = _read()

    def test_there_is_a_way_to_terminate_an_in_flight_drag(self):
        self.assertIn("cancelDragInProgress", self.content)

    def test_it_releases_the_pointer_capture(self):
        """捕獲中的指標事件不經過命中測試，覆蓋層擋不住它——一定要主動
        `releasePointerCapture()`，不能只靠彈窗蓋住圖示。實際的釋放在共用
        模組的 cancel() 裡。"""
        module = _read_module()
        body = re.search(r"function cancel\(\) \{.*?\n    \}", module, re.DOTALL)
        self.assertIsNotNone(body, "ui/drag_to_target.js 找不到 cancel()")
        self.assertIn("releasePointerCapture", body.group(0))

    def test_the_page_delegates_to_the_shared_cancel(self):
        body = _function_body(self.content, "cancelDragInProgress")
        self.assertIsNotNone(body, "找不到 cancelDragInProgress()")
        self.assertIn(".cancel()", body)

    def test_the_success_path_terminates_the_drag(self):
        body = _function_body(self.content, "attemptInstall")
        success = re.search(r"if \(result\.status === 'success'\) \{(.*?)\n            \} else if",
                            body, re.DOTALL)
        self.assertIsNotNone(success, "找不到成功分支")
        self.assertIn(
            "cancelDragInProgress", success.group(1),
            "顯示成功畫面時沒有終結進行中的拖曳，使用者手上的圖示會卡在半空中",
        )


if __name__ == "__main__":
    unittest.main()
