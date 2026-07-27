"""window_drag.py 的測試。

WindowDragController 現在是 gui_config.py（ConfigAPI）跟 installer_core.py
（InstallerAPI）兩邊共用的同一個深模組——這裡直接測這個共用實作本身，
不需要透過任一邊的 API class。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from window_drag import WindowDragController


class TestWindowDragController(unittest.TestCase):
    def test_drag_move_offsets_from_origin(self):
        window = mock.Mock(x=100, y=200)
        drag = WindowDragController()
        drag.start_drag(window, cursor_x=50, cursor_y=60)
        drag.drag_move(window, cursor_x=70, cursor_y=90)
        window.move.assert_called_once_with(120, 230)

    def test_drag_move_without_start_does_nothing(self):
        window = mock.Mock()
        drag = WindowDragController()
        drag.drag_move(window, cursor_x=70, cursor_y=90)
        window.move.assert_not_called()

    def test_end_drag_clears_origin_so_further_moves_are_ignored(self):
        window = mock.Mock(x=0, y=0)
        drag = WindowDragController()
        drag.start_drag(window, cursor_x=0, cursor_y=0)
        drag.end_drag()
        drag.drag_move(window, cursor_x=10, cursor_y=10)
        window.move.assert_not_called()

    def test_start_drag_with_no_window_is_a_no_op(self):
        drag = WindowDragController()
        drag.start_drag(None, cursor_x=0, cursor_y=0)
        # 沒有基準點，drag_move 就算給了真正的 window 也不該搬動它
        window = mock.Mock()
        drag.drag_move(window, cursor_x=10, cursor_y=10)
        window.move.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
