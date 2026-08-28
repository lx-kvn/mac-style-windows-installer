"""安裝畫面的鍵盤可操作性：主畫面的兩個圖示都是 <div> 加滑鼠事件，原本
都不在 Tab 鍵順序裡——

- `#drag-item`（App 圖示）：拖到資料夾圖示上放開才會開始安裝，是整個安裝
  流程唯一的觸發點。
- `#drop-target`（安裝目的地圖示）：點一下會開啟選擇資料夾對話框，是更改
  安裝路徑唯一的入口。

只靠鍵盤操作的使用者（含滑鼠故障、或本來就仰賴鍵盤/螢幕報讀器的人）既無
法改安裝路徑，也無法把這個安裝檔裝起來。

跟 `test_js_api_contract.py` 同樣的手法：靜態解析 `ui/index.html`，斷言
這些無障礙屬性/樣式/共用觸發路徑真的存在。這種缺陷平常只有實際拔掉滑鼠
用 Tab 鍵走一遍才發現得了，靠人工記得檢查並不可靠，所以釘成測試。

不另外提供一顆「跳過拖拽」按鈕，因為拖拽是這個專案的核心識別（macOS DMG
風格），另開一條平行捷徑等同於暗示使用者可以繞過核心體驗；採取的做法是
讓同一個拖拽目標本身就能用鍵盤操作。純粹要減少滑鼠動作的效率需求，由
靜默安裝（`/S`）涵蓋，不在 GUI 上另外疊加。
"""
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_HTML = os.path.join(REPO_ROOT, "ui", "index.html")


def _read_index_html():
    with open(INDEX_HTML, "r", encoding="utf-8") as f:
        return f.read()


def _element_tag(content, element_id):
    """回傳指定 id 的元素的開頭標籤原文。"""
    match = re.search(
        r"<[a-zA-Z]+[^>]*\bid=\"" + re.escape(element_id) + r"\"[^>]*>", content
    )
    if match is None:
        raise AssertionError(
            "ui/index.html 裡找不到 id=\"{}\" 的元素".format(element_id)
        )
    return match.group(0)


def _listener_body(content, target, event_name):
    """回傳指定元素上某個滑鼠事件監聽器的原文——使用者用滑鼠操作時實際
    跑的那段程式。"""
    match = re.search(
        re.escape(target) + r"\.addEventListener\(\s*'" + event_name + r"'.*?\n\s*\}\);",
        content,
        re.DOTALL,
    )
    if match is None:
        raise AssertionError(
            "ui/index.html 裡找不到 {} 的 {} 事件監聽器".format(target, event_name)
        )
    return match.group(0)


class KeyboardOperableElementMixin:
    """一個原本只有滑鼠事件的 <div> 要能用鍵盤操作，需要同時滿足的四件事。
    兩個圖示各自套一次，避免其中一個被改動時另一個悄悄退化。

    子類必須設定：
      ELEMENT_ID     — 要檢查的元素 id
      MOUSE_TARGET   — 對應滑鼠事件監聽器掛在哪個 JS 變數上
      MOUSE_EVENT    — 對應的滑鼠事件名稱（鍵盤要跑同一段邏輯的那個）
      PURPOSE        — 這個元素負責的動作，用在失敗訊息裡
    """

    ELEMENT_ID = None
    MOUSE_TARGET = None
    MOUSE_EVENT = None
    PURPOSE = None

    def test_is_reachable_by_tab_key(self):
        """沒有 tabindex 的 <div> 不在 Tab 鍵順序裡，鍵盤使用者根本選不到它。"""
        tag = _element_tag(_read_index_html(), self.ELEMENT_ID)
        self.assertIn(
            'tabindex="0"', tag,
            "#{} 沒有 tabindex=\"0\"，Tab 鍵會整個跳過{}：\n{}".format(
                self.ELEMENT_ID, self.PURPOSE, tag),
        )

    def test_is_announced_as_a_button(self):
        """螢幕報讀器要唸得出這是一個可以按的東西，而不是一張裝飾用的圖。"""
        tag = _element_tag(_read_index_html(), self.ELEMENT_ID)
        self.assertIn(
            'role="button"', tag,
            "#{} 沒有 role=\"button\"，螢幕報讀器不會把它當成可操作的控制項：\n{}".format(
                self.ELEMENT_ID, tag),
        )

    def test_responds_to_enter_and_space(self):
        """原生 <button> 會自動處理 Enter/空白鍵，<div> 不會，要自己補。"""
        tag = _element_tag(_read_index_html(), self.ELEMENT_ID)
        self.assertIn("onkeydown", tag, "#{} 沒有掛鍵盤事件：\n{}".format(self.ELEMENT_ID, tag))
        self.assertIn(
            "'Enter'", tag,
            "#{} 的鍵盤事件沒有處理 Enter 鍵：\n{}".format(self.ELEMENT_ID, tag),
        )
        self.assertIn(
            "' '", tag,
            "#{} 的鍵盤事件沒有處理空白鍵：\n{}".format(self.ELEMENT_ID, tag),
        )

    def test_keyboard_trigger_reuses_the_same_code_path_as_the_mouse(self):
        """鍵盤觸發跟滑鼠操作必須跑同一段邏輯——不能各寫一份，否則其中
        一邊之後被改動時，另一邊會悄悄跟著行為分岔。"""
        content = _read_index_html()
        tag = _element_tag(content, self.ELEMENT_ID)
        handler = re.search(r"onkeydown=\"([^\"]*)\"", tag)
        self.assertIsNotNone(handler, "#{} 沒有 onkeydown 內容：\n{}".format(self.ELEMENT_ID, tag))

        called = {
            name for name in re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", handler.group(1))
            if name not in ("if", "preventDefault")
        }
        self.assertTrue(
            called,
            "#{} 的鍵盤事件沒有實際呼叫任何函式：{}".format(self.ELEMENT_ID, handler.group(1)),
        )

        mouse_body = _listener_body(content, self.MOUSE_TARGET, self.MOUSE_EVENT)
        shared = {name for name in called if name + "(" in mouse_body.replace(" ", "")}
        self.assertTrue(
            shared,
            "#{} 鍵盤事件呼叫的 {} 沒有出現在 {} 的 {} 監聽器裡——兩條路徑"
            "各自寫了一份邏輯：\n{}".format(
                self.ELEMENT_ID, sorted(called), self.MOUSE_TARGET,
                self.MOUSE_EVENT, mouse_body),
        )

    def test_has_a_keyboard_focus_ring(self):
        """看不到焦點在哪的鍵盤操作等於盲按。跟 .nice-btn / .custom-close-btn
        用同一套 :focus-visible 視覺（只在鍵盤導覽時出現，滑鼠點擊不顯示，
        滑鼠使用者看到的畫面完全不變）。"""
        content = _read_index_html()
        rules = re.findall(
            r"#" + re.escape(self.ELEMENT_ID) + r":focus-visible[^{]*\{([^}]*)\}", content
        )
        self.assertTrue(
            rules,
            "ui/index.html 裡沒有 #{}:focus-visible 的焦點框樣式".format(self.ELEMENT_ID),
        )
        outlined = [
            r for r in rules
            if "outline: 2px solid #0071e3" in r and "outline-offset: 2px" in r
        ]
        self.assertTrue(
            outlined,
            "#{}:focus-visible 沒有畫出跟其他控制項一致的藍色焦點框：{}".format(
                self.ELEMENT_ID, rules),
        )


class TestDragItemKeyboardAccessibility(KeyboardOperableElementMixin, unittest.TestCase):
    """App 圖示：鍵盤按下去要等同於「拖到資料夾圖示上放開」，直接開始安裝。"""

    ELEMENT_ID = "drag-item"
    MOUSE_TARGET = None
    MOUSE_EVENT = None
    PURPOSE = "這個安裝觸發點"

    def test_keyboard_trigger_reuses_the_same_code_path_as_the_mouse(self):
        """拖曳安裝改成自繪之後，滑鼠那條路徑不再是一個「drop 事件監聽器」，
        而是拖曳結束、判定命中之後才呼叫的一段程式。這裡改成從結果反推：
        鍵盤呼叫的那個函式必須真的存在、而且不是只有鍵盤在用（至少還有另一
        個呼叫點，也就是滑鼠命中那條路徑）；同時整份檔案只能有一個地方去讀
        桌面捷徑的勾選狀態——出現第二個，就代表有人在別處複製了一份安裝
        觸發邏輯，兩條路徑遲早會分岔。"""
        content = _read_index_html()
        tag = _element_tag(content, self.ELEMENT_ID)
        handler = re.search(r"onkeydown=\"([^\"]*)\"", tag)
        self.assertIsNotNone(handler, "#drag-item 沒有 onkeydown 內容：\n" + tag)

        called = {
            name for name in re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", handler.group(1))
            if name not in ("if", "preventDefault")
        }
        self.assertTrue(
            called, "#drag-item 的鍵盤事件沒有實際呼叫任何函式：" + handler.group(1),
        )

        shared = set()
        for name in called:
            defined = re.search(
                r"(function\s+" + re.escape(name) + r"\s*\(|(?:const|let|var)\s+"
                + re.escape(name) + r"\s*=)", content)
            if not defined:
                continue
            call_sites = re.findall(r"\b" + re.escape(name) + r"\s*\(", content)
            # 定義處本身也會被算進去，所以「鍵盤 + 定義 + 滑鼠」至少是三次
            if len(call_sites) >= 3:
                shared.add(name)

        self.assertTrue(
            shared,
            "#drag-item 鍵盤事件呼叫的 {} 不是定義好、而且滑鼠那條路徑也會走的"
            "共用函式——鍵盤可能自己另外寫了一份安裝邏輯".format(sorted(called)),
        )

        reads = re.findall(r"getElementById\('desktopShortcutCheck'\)\.checked", content)
        self.assertEqual(
            len(reads), 1,
            "讀取桌面捷徑勾選狀態的地方有 {} 處，應該只有共用的那一個函式會讀——"
            "多出來的那份代表安裝觸發邏輯被複製了".format(len(reads)),
        )


class TestDragToInstallInteraction(unittest.TestCase):
    """拖曳安裝改成自繪之後的結構性契約。這些是「機制換掉了沒有」的判斷，
    不涉及彈簧參數本身（那些數值是調出來的觀感，釘死在測試裡只會擋住往後
    的微調）。"""

    def test_native_html5_drag_and_drop_is_no_longer_used(self):
        """瀏覽器內建的拖曳會由作業系統畫出一張半透明拖曳影像，外觀完全
        不受控——換成自繪就是為了拿回這個控制權，殘留的原生拖曳會同時
        觸發兩套行為。"""
        content = _read_index_html()
        tag = _element_tag(content, "drag-item")
        self.assertNotIn(
            'draggable="true"', tag,
            "#drag-item 還留著 draggable=\"true\"，瀏覽器內建拖曳沒有關掉：\n" + tag,
        )
        for leftover in ("dataTransfer", "'dragstart'", "'dragover'", "'dragleave'", "'drop'"):
            self.assertFalse(
                leftover in content,
                "ui/index.html 還留著原生拖曳的殘骸：{}".format(leftover),
            )

    def test_drag_is_driven_by_pointer_events(self):
        """滑鼠、觸控筆、觸控都走同一套 pointer 事件；pointercancel 是
        系統中途接管手勢（例如觸控被捲動搶走）時的收尾，漏了會讓圖示卡在
        半路。"""
        content = _read_index_html()
        for event_name in ("pointerdown", "pointermove", "pointerup", "pointercancel"):
            self.assertTrue(
                ("'" + event_name + "'") in content,
                "ui/index.html 沒有處理 {} 事件".format(event_name),
            )
        self.assertTrue(
            "setPointerCapture" in content,
            "沒有用 setPointerCapture，游標移出圖示範圍時拖曳會斷掉",
        )

    def test_original_slot_keeps_a_ghost_while_dragging(self):
        """原位殘影：拖走之後原本的位置要留下替身，表達「這東西仍然屬於
        這裡」，同時當作沒命中時彈回去的目的地。"""
        content = _read_index_html()
        self.assertTrue(
            'id="dragGhost"' in content,
            "找不到原位殘影元素（id=\"dragGhost\"）",
        )

    def test_reduced_motion_is_respected(self):
        """系統要求減少動態效果時，自己會跑的動畫要收掉。跟手的位移不算
        在內（那是使用者的手在動，不是介面自己在動）。"""
        content = _read_index_html()
        self.assertTrue(
            "prefers-reduced-motion" in content,
            "ui/index.html 沒有處理 prefers-reduced-motion",
        )


class TestDropTargetKeyboardAccessibility(KeyboardOperableElementMixin, unittest.TestCase):
    """安裝目的地圖示：鍵盤按下去要等同於滑鼠點它，開啟選擇資料夾對話框。"""

    ELEMENT_ID = "drop-target"
    MOUSE_TARGET = "dropTarget"
    MOUSE_EVENT = "click"
    PURPOSE = "這個更改安裝路徑的入口"


if __name__ == "__main__":
    unittest.main()
