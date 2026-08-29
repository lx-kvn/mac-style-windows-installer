"""解除安裝端的拖曳改用跟安裝端同一套自繪手勢之後的結構性契約。

`ADR-0002` 記載了安裝端當初為什麼要從瀏覽器內建拖放改成自繪：拖曳過程中
跟著游標移動的那張影像由作業系統繪製，網頁端無法控制它的外觀、縮放、
透明度，也無法在放開時介入，所以做不到按下的即時回饋、沒命中的彈回、
命中的吸入——「整個專案的核心識別動作，剛好卡在整份介面裡最不能調整外觀
的機制上」。

**這段理由對解除安裝端一字不改地成立**，但它一直停在那個被換掉的機制上，
等於 ADR-0002 只做了一半。這份測試釘住換過來之後的結果。

附帶補上的無障礙缺口：原本 `#dragAppIcon` 沒有 `tabindex`、沒有 `role`、
沒有任何鍵盤事件——整個解除安裝介面完全無法用鍵盤操作，而拖曳是這個畫面
唯一的觸發點（ADR-0002 決定四：不另外提供「跳過拖曳」按鈕）。安裝端已經
解決過，`tests/test_ui_accessibility.py` 也釘成測試，但那份只解析
`ui/index.html`。

依 ADR-0002，手感本身無法自動化驗證（模擬的滑鼠事件送不進 WebView2 的
內容區），所以這裡只守「機制換掉了沒有」這類結構性判斷，不碰彈簧參數。
"""
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNINSTALL_HTML = os.path.join(REPO_ROOT, "ui", "uninstall.html")


def _read():
    with open(UNINSTALL_HTML, "r", encoding="utf-8") as f:
        return f.read()


def _element_tag(content, element_id):
    match = re.search(
        r"<[a-zA-Z]+[^>]*\bid=\"" + re.escape(element_id) + r"\"[^>]*>", content
    )
    if match is None:
        raise AssertionError(f'ui/uninstall.html 裡找不到 id="{element_id}" 的元素')
    return match.group(0)


class TestNativeDragAndDropIsGone(unittest.TestCase):
    def setUp(self):
        self.content = _read()

    def test_the_icon_is_no_longer_natively_draggable(self):
        tag = _element_tag(self.content, "dragAppIcon")
        self.assertNotIn(
            'draggable="true"', tag,
            "#dragAppIcon 還留著 draggable=\"true\"，瀏覽器內建拖曳沒有關掉：\n" + tag,
        )

    def test_no_leftover_native_drag_handlers(self):
        """殘留的原生拖放會跟自繪那套同時作用，變成兩套行為疊在一起。"""
        for leftover in ("dataTransfer", "'dragstart'", "'dragenter'",
                         "'dragover'", "'dragleave'", "'drop'"):
            self.assertNotIn(
                leftover, self.content,
                f"ui/uninstall.html 還留著原生拖放的殘骸：{leftover}",
            )


class TestSharedDragImplementationIsUsed(unittest.TestCase):
    def setUp(self):
        self.content = _read()

    def test_it_loads_the_shared_module(self):
        self.assertIn('src="drag_to_target.js"', self.content)

    def test_it_creates_a_controller_for_the_trash(self):
        match = re.search(r"createDragToTarget\(\{(.*?)\n        \}\);",
                          self.content, re.DOTALL)
        self.assertIsNotNone(match, "沒有用共用的拖曳實作建立控制器")
        body = match.group(1)
        self.assertIn("trashDropTarget", body, "目的地不是垃圾桶")
        self.assertIn("dragGhost", body, "沒有接上原位殘影")

    def test_the_trash_lid_reacts_to_hover_and_absorb(self):
        """垃圾桶蓋的開闔是這一端專屬的目的地回應（安裝端是資料夾吞一下），
        兩個時機都要接上：懸停時掀開、被吸進去時闔上。"""
        match = re.search(r"createDragToTarget\(\{(.*?)\n        \}\);",
                          self.content, re.DOTALL)
        self.assertIsNotNone(match)
        body = match.group(1)
        hover = re.search(r"onHoverChange:.*", body)
        absorb = re.search(r"onAbsorb:.*?\n            \},", body, re.DOTALL)
        self.assertIsNotNone(hover, "沒有接上懸停時的目的地回應")
        self.assertIsNotNone(absorb, "沒有接上命中時的目的地回應")
        self.assertIn("trashLidSpring", hover.group(0))
        self.assertIn("trashLidSpring", absorb.group(0))

    def test_the_original_slot_keeps_a_ghost(self):
        self.assertIn('id="dragGhost"', self.content)


class TestUninstallIconKeyboardAccessibility(unittest.TestCase):
    """這顆圖示是整個解除安裝流程唯一的觸發點，原本完全不在 Tab 順序裡。"""

    def setUp(self):
        self.content = _read()
        self.tag = _element_tag(self.content, "dragAppIcon")

    def test_is_reachable_by_tab_key(self):
        self.assertIn('tabindex="0"', self.tag,
                      "#dragAppIcon 沒有 tabindex=\"0\"，Tab 鍵會整個跳過它：\n" + self.tag)

    def test_is_announced_as_a_button(self):
        self.assertIn('role="button"', self.tag,
                      "#dragAppIcon 沒有 role=\"button\"，螢幕報讀器不會把它當成可操作的控制項")

    def test_responds_to_enter_and_space(self):
        self.assertIn("onkeydown", self.tag, "#dragAppIcon 沒有掛鍵盤事件")
        self.assertIn("'Enter'", self.tag)
        self.assertIn("' '", self.tag)

    def test_has_a_keyboard_focus_ring(self):
        """outline: none 之類的重設會讓鍵盤使用者看不出目前選到哪裡。"""
        rules = re.findall(r"#dragAppIcon:focus-visible\s*\{[^}]*\}", self.content)
        self.assertTrue(rules, "#dragAppIcon 沒有鍵盤焦點框")
        self.assertTrue(
            any("outline:" in r and "none" not in r.split("outline:")[1][:20] for r in rules),
            f"#dragAppIcon:focus-visible 沒有畫出焦點框：{rules}",
        )

    def test_keyboard_and_mouse_share_the_same_trigger(self):
        """鍵盤不能自己另外寫一份解除安裝邏輯——兩條路徑遲早會分岔。"""
        handler = re.search(r'onkeydown="([^"]*)"', self.tag)
        self.assertIsNotNone(handler)
        called = set(re.findall(r"(\w+)\(\)", handler.group(1)))
        called.discard("preventDefault")
        self.assertTrue(called, "鍵盤事件沒有呼叫任何函式")

        for name in called:
            self.assertIn(
                f"function {name}(", self.content,
                f"鍵盤呼叫的 {name}() 不是定義好的函式",
            )
            # 滑鼠命中那條路徑（onHit）也要走同一個函式
            self.assertIn(
                f"onHit: () => {{ {name}(); }}", self.content,
                f"滑鼠命中沒有走跟鍵盤同一個函式（{name}）",
            )


class TestDragIsGatedByUninstallState(unittest.TestCase):
    """理由同安裝端：解除安裝進行中或已完成都不該再被拖一次。這一端原本
    「剛好」沒事，是因為畫面切換會把整個 confirmView 藏起來——但那是副作用
    不是設計，而且已經取得指標捕獲的拖曳不經過命中測試。"""

    def setUp(self):
        self.content = _read()

    def test_there_is_an_explicit_state(self):
        self.assertIn("uninstallState", self.content)

    def test_the_controller_asks_the_state(self):
        match = re.search(r"canDrag:\s*([^\n,]+)", self.content)
        self.assertIsNotNone(match, "沒有把可否拖曳的判斷交給共用模組")
        self.assertIn("uninstallState", match.group(1))

    def test_switching_views_cancels_an_in_flight_drag(self):
        body = re.search(r"function showView\(id\) \{.*?\n        \}",
                         self.content, re.DOTALL)
        self.assertIsNotNone(body, "找不到 showView()")
        self.assertIn(
            ".cancel()", body.group(0),
            "切換畫面時沒有終結進行中的拖曳——藏起來擋不住已經取得指標捕獲的拖曳",
        )

    def test_undo_returns_to_draggable(self):
        """反悔倒數期間按「復原」會回到確認畫面，狀態要跟著清回可拖曳，
        不然使用者回到主畫面卻發現圖示拖不動。"""
        body = re.search(r"function onUndoDelete\(\) \{.*?\n        \}",
                         self.content, re.DOTALL)
        self.assertIsNotNone(body, "找不到 onUndoDelete()")
        self.assertIn("'idle'", body.group(0))


if __name__ == "__main__":
    unittest.main()
