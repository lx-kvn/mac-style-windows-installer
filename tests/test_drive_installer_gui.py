"""tools/drive_installer_gui.py 的測試：在虛擬機上實際操作安裝精靈的畫面。

`run-test-vm` skill 的待辦：互動畫面（拖曳手勢、彈窗）沒有腳本，只有靜默
路徑有。這是這個專案最大的一塊測試缺口——拖曳安裝是它的核心識別動作，而
ADR-0002 明載那個手感只能由人在真實視窗上驗證。

**這支工具不宣稱驗證手感**，它驗證的是比手感更基本的一件事：**那個手勢真的
會觸發安裝**。作法是在客體端以 `SendInput` 送出真正的滑鼠事件——不是網頁層
的合成事件——因此走的是與使用者按下滑鼠完全相同的路徑。本機的驅動程式做不到
這件事（`run-installer-gui` 的說明記載，合成的鍵盤事件送不進 WebView2 的內容
區），差別在於那邊是跨行程送給一個背景視窗，這裡是在客體桌面上移動真正的
游標。

判準的核心是**不以截圖為證據**：畫面看起來對不對由人看，程式要判的是「檔案
有沒有真的落地」。截圖仍然拍，但那是給人看的附件，不是通過條件。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# vm_lease 由另一個 repo 提供，理由與跳過方式見 tests/test_vms.py 開頭。
try:
    import vm_lease  # noqa: F401
except ImportError as exc:  # pragma: no cover - 取決於執行機器裝了什麼
    raise unittest.SkipTest(
        "vm_lease 未安裝，跳過虛擬機驅動的測試（見 tests/test_vms.py 的說明）"
    ) from exc

from tools import drive_installer_gui as drive


class TheDragPath(unittest.TestCase):
    """游標要走一條「像人拖的」路徑，不是瞬移。"""

    def test_it_starts_at_the_icon_and_ends_at_the_destination(self):
        points = drive.drag_path((100, 200), (400, 220), steps=10)
        self.assertEqual(points[0], (100, 200))
        self.assertEqual(points[-1], (400, 220))

    def test_it_moves_in_more_than_one_hop(self):
        """一步到位的移動不會產生 pointermove，而拖曳的判定倚賴那些事件——
        前端要看到位移才會認為使用者在拖，瞬移等於只有按下與放開。"""
        points = drive.drag_path((0, 0), (300, 0), steps=12)
        self.assertGreater(len(points), 10)

    def test_every_point_is_an_integer_pixel(self):
        """SendInput 收的是整數座標；浮點數會在客體端被截斷成別的位置。"""
        for x, y in drive.drag_path((0, 0), (101, 51), steps=7):
            self.assertIsInstance(x, int)
            self.assertIsInstance(y, int)

    def test_a_zero_length_drag_still_produces_both_ends(self):
        points = drive.drag_path((50, 50), (50, 50), steps=5)
        self.assertEqual(points[0], (50, 50))
        self.assertEqual(points[-1], (50, 50))


class TheGuestScript(unittest.TestCase):
    def test_it_sends_real_mouse_input(self):
        """要走作業系統的輸入路徑，不是網頁層的合成事件——後者證明不了
        「使用者真的拖得動」。"""
        script = drive.guest_script(r"C:\Users\Tester\Setup.exe", "TestApp")
        self.assertIn("SendInput", script)
        self.assertIn("MOUSEEVENTF", script)

    def test_it_finds_the_window_before_touching_the_mouse(self):
        """視窗還沒出現就開始點，點到的是桌面，而報告上會看起來像「拖了但
        沒有反應」。

        比對的是**呼叫**的先後，不是宣告——那一段 P/Invoke 宣告裡兩個名字
        都會出現，順序沒有意義（第一版的這條測試就是比到宣告，它其實什麼
        都沒有檢查到）。
        """
        script = drive.guest_script(r"C:\Users\Tester\Setup.exe", "TestApp")
        self.assertIn("[Mouse]::GetWindowRect(", script)
        self.assertLess(script.index("[Mouse]::GetWindowRect("),
                        script.index("[Mouse]::MoveTo("))
        self.assertLess(script.index("[Mouse]::GetWindowRect("),
                        script.index("[Mouse]::Down()"))

    def test_it_writes_the_report_line_by_line(self):
        script = drive.guest_script(r"C:\Users\Tester\Setup.exe", "TestApp")
        self.assertIn("Add-Content", script)

    def test_the_installer_path_is_quoted(self):
        script = drive.guest_script(r"C:\Users\Tester\Setup My App.exe", "My App")
        self.assertIn("'C:\\Users\\Tester\\Setup My App.exe'", script)


class TheVerdict(unittest.TestCase):
    def _report(self, **overrides):
        report = {
            "window_found": "True",
            "drag_sent": "True",
            "install_dir_exists": "True",
            "main_exe_exists": "True",
            "result_screen": "True",
        }
        report.update({k: str(v) for k, v in overrides.items()})
        return report

    def test_a_completed_drag_that_installed_passes(self):
        self.assertEqual(drive.evaluate(self._report()).verdict, drive.PASS)

    def test_the_gesture_failing_to_install_is_a_failure(self):
        """視窗開了、滑鼠也送了，但什麼都沒裝——這正是這支工具要抓的東西。"""
        result = drive.evaluate(self._report(install_dir_exists="False",
                                             main_exe_exists="False"))
        self.assertEqual(result.verdict, drive.FAIL)
        self.assertIn("install_dir_exists", result.detail)

    def test_a_window_that_never_appeared_is_inconclusive(self):
        """視窗沒出現時，拖曳這件事根本沒有被測到，不能算它失敗。"""
        result = drive.evaluate(self._report(window_found="False"))
        self.assertEqual(result.verdict, drive.INCONCLUSIVE)

    def test_an_empty_report_is_inconclusive(self):
        self.assertEqual(drive.evaluate({}).verdict, drive.INCONCLUSIVE)

    def test_the_screenshot_is_not_part_of_the_verdict(self):
        """截圖是給人看的附件。把它列入通過條件，等於用「有沒有拍到」代替
        「有沒有裝起來」。"""
        passed = drive.evaluate(self._report())
        self.assertEqual(passed.verdict, drive.PASS)
        self.assertNotIn("screenshot", passed.detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
