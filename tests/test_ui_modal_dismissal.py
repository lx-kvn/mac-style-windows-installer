"""說明彈窗點外面就關掉。

點空白處關閉是說明類彈窗的標準行為：使用者打開它是為了讀一段文字，讀完想
離開時不該還要去找那顆關閉鈕。

**只有說明類的可以這樣做。** 安裝端的四個彈窗（偵測到已安裝的版本、建議先
安裝以下元件、偵測到程式正在執行、檔案使用中）都是在問使用者一個問題，
點外面關掉等於那個問題沒有被回答，而後續流程需要那個答案。這裡的測試同時
釘住「說明類有」與「決定類沒有」兩件事——只釘前者的話，日後有人「順手統一
一下」把它加到決定類彈窗上，不會有任何東西叫。
"""
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_HTML = os.path.join(REPO_ROOT, "ui", "config.html")
INDEX_HTML = os.path.join(REPO_ROOT, "ui", "index.html")

# 說明類：打開只為了讀，關掉不會遺失任何回答。
DISMISSIBLE = ["helpModal", "langCodeHelpModal"]

# 決定類：關掉等於問題沒被回答。
DECISION_MODALS = ["upgradeModal", "depModal", "processRunningModal", "fileLockedModal"]


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


class TheHelpModalsCanBeDismissedByClickingOutside(unittest.TestCase):
    def setUp(self):
        self.html = _read(CONFIG_HTML)

    def test_there_is_a_shared_helper(self):
        """兩個說明彈窗各寫一份的話，日後只會有一個被修到。"""
        self.assertRegex(self.html, r"function\s+makeDismissibleByBackdrop\s*\(")

    def test_each_help_modal_is_registered(self):
        for modal_id in DISMISSIBLE:
            self.assertRegex(
                self.html, rf"makeDismissibleByBackdrop\(\s*'{modal_id}'",
                f"{modal_id} 沒有註冊點外面關閉")

    def test_it_only_reacts_to_the_backdrop_itself(self):
        """點在彈窗內容上不能關掉——使用者選取文字時滑鼠會落在內容裡，
        那個動作若被當成「點外面」，選到一半就會被關掉。"""
        body = re.search(r"function\s+makeDismissibleByBackdrop[\s\S]*?\n        \}\n",
                         self.html)
        self.assertIsNotNone(body)
        self.assertIn("event.target", body.group(0))

    def test_a_selection_that_ends_outside_does_not_close_it(self):
        """說明彈窗是一大段可捲動的文字，使用者會拖曳選取。從彈窗內開始拖、
        放開時滑鼠落到外面時，`click` 事件會落在兩者的共同祖先——也就是遮罩
        ——選到一半就被關掉。

        只看 `click` 的 target 擋不住這個情形：那個事件的 target 確實就是
        遮罩。要看的是**按下**的當下在哪裡，因此也要聽 `mousedown`。
        """
        body = re.search(r"function\s+makeDismissibleByBackdrop[\s\S]*?\n        \}\n",
                         self.html)
        self.assertIsNotNone(body)
        self.assertIn("mousedown", body.group(0),
                      "只聽 click 的話，從彈窗內拖曳選取到外面會誤關")

    def test_it_closes_through_the_same_path_as_the_button(self):
        """關閉鈕與點外面要走同一條路，否則兩者日後會分岔（例如關閉鈕多做了
        一件事而點外面沒做）。"""
        body = re.search(r"function\s+makeDismissibleByBackdrop[\s\S]*?\n        \}\n",
                         self.html)
        self.assertRegex(body.group(0), r"classList\.remove|onClose|close")


class DecisionModalsAreNotDismissible(unittest.TestCase):
    """安裝端的四個彈窗都在問問題，後續流程需要那個答案。"""

    def setUp(self):
        self.html = _read(INDEX_HTML)

    def test_no_decision_modal_is_registered(self):
        for modal_id in DECISION_MODALS:
            self.assertNotRegex(
                self.html, rf"makeDismissibleByBackdrop\(\s*'{modal_id}'",
                f"{modal_id} 是決定類彈窗，不該點外面就關掉")

    def test_the_installer_has_no_such_helper_at_all(self):
        """安裝端沒有任何說明類彈窗，因此連這個機制都不需要存在——存在就會
        有人拿去用在決定類上。"""
        self.assertNotIn("makeDismissibleByBackdrop", self.html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
