"""
window_drag.py
---------------
無邊框視窗的自訂拖曳邏輯，`gui_config.py`（ConfigAPI）跟 `installer_core.py`
（InstallerAPI）兩支各自獨立的 pywebview 視窗都要用到，行為完全一樣：
不用 pywebview 內建的 pywebview-drag-region——那個機制在拖曳開始瞬間會讓視窗
往左上方跳一下才跟上游標，100% 縮放下也會發生，判斷是機制本身的問題（不是
DPI 造成的）。改成完全自己算位移量、呼叫 window.move()，徹底繞開這個問題。

兩邊原本各寫了一份幾乎一樣的 start_drag/drag_move/end_drag，這裡收斂成一個
小類別，兩邊都改成持有一個 WindowDragController 實例、把目前的 window 物件
傳進去，不用再各自維護一份拖曳狀態機。
"""


class WindowDragController:
    def __init__(self):
        self._drag_origin = None

    def start_drag(self, window, cursor_x, cursor_y):
        """拖曳開始：記錄按下當下的滑鼠螢幕座標與視窗當下座標，作為位移量的計算基準。"""
        if window:
            self._drag_origin = (cursor_x, cursor_y, window.x, window.y)

    def drag_move(self, window, cursor_x, cursor_y):
        """拖曳中：用目前滑鼠螢幕座標相對於按下當下的位移量搬動視窗。"""
        if window and self._drag_origin:
            start_cx, start_cy, start_wx, start_wy = self._drag_origin
            dx = cursor_x - start_cx
            dy = cursor_y - start_cy
            window.move(start_wx + dx, start_wy + dy)

    def end_drag(self):
        """拖曳結束：清掉基準點。"""
        self._drag_origin = None
