"""在虛擬機上實際操作安裝精靈的拖曳手勢，確認它真的會觸發安裝。

`run-test-vm` skill 原本的待辦：互動畫面沒有腳本，只有靜默路徑有。拖曳安裝
是這個專案的核心識別動作，而它一直沒有任何自動化的涵蓋——ADR-0002 記載那個
**手感**只能由人驗證，但「這個手勢會不會觸發安裝」是另一件事，那件事可以量。

## 為什麼要在客體端送真正的滑鼠事件

`run-installer-gui` 的說明記載，本機驅動送出的合成鍵盤事件進不了 WebView2 的
內容區。那是跨行程送給一個背景視窗的作法。這裡不同：腳本在客體桌面上以
`SendInput` 移動**真正的游標**並按下按鍵，走的是與使用者的手完全相同的輸入
路徑，WebView2 分辨不出差別。

## 這支工具不驗證手感

判準只看一件事：**檔案有沒有真的落地**。截圖仍然拍，但那是給人看的附件，
不是通過條件——把「有沒有拍到畫面」當成通過條件，等於用截圖代替驗證。

判準與腳本的性質由 `tests/test_drive_installer_gui.py` 釘住。
"""
import collections
import contextlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import packaging_core

from tools import vms


PASS = "pass"
FAIL = "fail"
INCONCLUSIVE = "inconclusive"

Result = collections.namedtuple("Result", "name verdict detail")

POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
GUEST_DIR = r"C:\Users\Public"
REPORT = GUEST_DIR + r"\drag_report.txt"

# 視窗標題由 installer_core 寫死（frameless 視窗，畫面上看不到它）。
WINDOW_TITLE = "安裝應用程式"

# 圖示與安裝目的地在視窗中的相對位置。量自實際畫面（732x478 的視窗，圖示
# 中心約 (188, 205)、目的地約 (545, 205)）。用比例而不是絕對座標：視窗大小
# 隨顯示縮放比例改變，寫死像素在 125% 的機器上會點到別的地方。
ICON_AT = (0.257, 0.429)
TARGET_AT = (0.745, 0.429)


@contextlib.contextmanager
def stage(name, log=print):
    """把一個階段的起訖印出來，並報出它花了多久。

    真實踩過：第一版整趟不出聲，跑了十分鐘看不出卡在哪，只能中止。最花時間
    的是把安裝檔複製進客體那一步，而那一步沒有任何外顯跡象——不報時間的話，
    連「它是不是卡住了」都判斷不了。

    失敗的階段也要報：否則最後一行永遠停在前一個成功的階段，看起來像卡在
    那裡。
    """
    log(f"→ {name}...")
    started = time.monotonic()
    try:
        yield
    finally:
        log(f"  {name}：{time.monotonic() - started:.1f} 秒")


def drag_path(start, end, steps=24):
    """從 start 拖到 end 要經過的座標。

    分成多段而不是一步到位：一步到位不會產生中間的位移事件，而前端要看到
    位移才會認定使用者正在拖曳（見 `ui/drag_to_target.js`）。只有按下與放開
    的話，那就不是一次拖曳。
    """
    steps = max(2, int(steps))
    x0, y0 = start
    x1, y1 = end
    points = []
    for index in range(steps + 1):
        ratio = index / steps
        points.append((int(round(x0 + (x1 - x0) * ratio)),
                       int(round(y0 + (y1 - y0) * ratio))))
    return points


def guest_script(setup_path, app_name, install_dir=None, main_exe="app.exe",
                 steps=24, click_before_drag=None):
    """產生客體端要跑的 PowerShell。

    先等視窗出現並取得它的位置，才開始碰滑鼠——視窗還沒出現就移動並按下，
    點到的是桌面，而報告上會看起來像「拖了但沒有反應」。

    `click_before_drag`：拖曳之前先在視窗的這個相對位置點一下，形式與
    `ICON_AT` 相同（視窗矩形的比例）。授權合約頁的驗證用它按下「同意並
    繼續」——那一頁擋在拖曳畫面之前，不先過它就拖不到東西
    （見 `tools/verify_eula_gate.py`）。送滑鼠事件的那段 C# 因此只留這
    一份，不在別處複製。
    """
    install_dir = install_dir or (r"$env:LOCALAPPDATA\Programs\\" + app_name)
    icon_x, icon_y = ICON_AT
    target_x, target_y = TARGET_AT
    pre_click = ""
    if click_before_drag:
        click_x, click_y = click_before_drag
        pre_click = f"""
# 先按下一顆按鈕再拖曳。位置與 icon/target 一樣是視窗矩形的比例，因此要等
# 上面量到 $rect 之後才算得出來。
$clickX = $rect.Left + [int]($w * {click_x})
$clickY = $rect.Top  + [int]($h * {click_y})
[Mouse]::MoveTo($clickX, $clickY)
Start-Sleep -Milliseconds 300
[Mouse]::Down()
Start-Sleep -Milliseconds 120
[Mouse]::Up()
Note 'pre_click_sent' 'True'
Note 'pre_click_at' "$clickX,$clickY"
# 按下之後畫面要換頁，馬上拖曳會拖在還沒消失的那一頁上。
Start-Sleep -Seconds 3
"""

    return f"""$ErrorActionPreference = 'Continue'
$report = '{REPORT}'
Set-Content -Path $report -Value '# 拖曳手勢實測' -Encoding UTF8

function Note($key, $value) {{
    Add-Content -Path $report -Value ("{{0}}={{1}}" -f $key, $value) -Encoding UTF8
}}

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Mouse {{
    [StructLayout(LayoutKind.Sequential)]
    public struct MOUSEINPUT {{
        public int dx; public int dy; public uint mouseData;
        public uint dwFlags; public uint time; public IntPtr dwExtraInfo;
    }}
    [StructLayout(LayoutKind.Sequential)]
    public struct INPUT {{ public uint type; public MOUSEINPUT mi; }}

    public const uint MOUSEEVENTF_MOVE = 0x0001;
    public const uint MOUSEEVENTF_ABSOLUTE = 0x8000;
    public const uint MOUSEEVENTF_LEFTDOWN = 0x0002;
    public const uint MOUSEEVENTF_LEFTUP = 0x0004;

    [DllImport("user32.dll", SetLastError = true)]
    public static extern uint SendInput(uint n, INPUT[] inputs, int size);
    [DllImport("user32.dll")]
    public static extern int GetSystemMetrics(int index);
    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern IntPtr FindWindow(string cls, string title);
    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT {{ public int Left; public int Top; public int Right; public int Bottom; }}

    // 找不到目標視窗時，把桌面上實際有哪些視窗列出來。沒有這份清單就分不出
    // 「還沒開」與「標題對不上」，而兩者的處置完全不同。
    public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")]
    public static extern bool EnumWindows(EnumProc callback, IntPtr lParam);
    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetWindowTextW(IntPtr hWnd, System.Text.StringBuilder text, int count);

    // 以列舉方式比對標題，不用 FindWindow。真實抓到：FindWindow 以精確標題
    // 尋找 `安裝應用程式` 連續 150 秒都回傳 0，而同一時間 EnumWindows 列出的
    // 清單裡就有那個標題。改用列舉，順便容得下標題前後的空白。
    public static IntPtr FindByTitle(string wanted) {{
        IntPtr found = IntPtr.Zero;
        EnumWindows((hWnd, lParam) => {{
            if (!IsWindowVisible(hWnd)) return true;
            var buffer = new System.Text.StringBuilder(300);
            GetWindowTextW(hWnd, buffer, buffer.Capacity);
            if (buffer.ToString().Trim() == wanted) {{ found = hWnd; return false; }}
            return true;
        }}, IntPtr.Zero);
        return found;
    }}

    public static string VisibleTitles() {{
        var titles = new System.Collections.Generic.List<string>();
        EnumWindows((hWnd, lParam) => {{
            if (!IsWindowVisible(hWnd)) return true;
            var buffer = new System.Text.StringBuilder(300);
            GetWindowTextW(hWnd, buffer, buffer.Capacity);
            string title = buffer.ToString().Trim();
            if (title.Length > 0) titles.Add(title);
            return true;
        }}, IntPtr.Zero);
        return string.Join(" / ", titles.ToArray());
    }}

    static void Send(uint flags, int x, int y) {{
        INPUT[] input = new INPUT[1];
        input[0].type = 0;                       // INPUT_MOUSE
        input[0].mi.dwFlags = flags;
        input[0].mi.dx = x;
        input[0].mi.dy = y;
        SendInput(1, input, Marshal.SizeOf(typeof(INPUT)));
    }}

    public static void MoveTo(int x, int y) {{
        // SendInput 的絕對座標是 0..65535 的normalised 值，不是像素。
        int w = GetSystemMetrics(0);
        int h = GetSystemMetrics(1);
        Send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE,
             (x * 65535) / w, (y * 65535) / h);
    }}
    public static void Down() {{ Send(MOUSEEVENTF_LEFTDOWN, 0, 0); }}
    public static void Up() {{ Send(MOUSEEVENTF_LEFTUP, 0, 0); }}
}}
"@

Start-Process -FilePath '{setup_path}'

# 等視窗出現。視窗還沒出現就開始移動游標並按下，點到的是桌面，而報告上會
# 看起來像「拖了但沒有反應」。
#
# 等待上限給得寬：剛還原快照開機的機器上，這顆安裝檔要先把內嵌內容解壓到
# 暫存目錄、再啟動 WebView2。實測第一版的 30 秒不夠——回報「沒找到視窗」，
# 而同時拍的截圖裡視窗好端端地開著。
$waitSeconds = 90
$hwnd = [IntPtr]::Zero
$waited = 0
for ($i = 0; $i -lt ($waitSeconds * 2); $i++) {{
    $hwnd = [Mouse]::FindByTitle('{WINDOW_TITLE}')
    if ($hwnd -ne [IntPtr]::Zero) {{ break }}
    Start-Sleep -Milliseconds 500
    $waited = $i / 2
}}
Note 'window_found' ($hwnd -ne [IntPtr]::Zero)
Note 'window_wait_seconds' $waited
if ($hwnd -eq [IntPtr]::Zero) {{
    # 找不到就把桌面上實際有的視窗列出來——「還沒開」與「標題對不上」要
    # 分得出來。
    Note 'windows_seen' ([Mouse]::VisibleTitles())
    exit 1
}}

Start-Sleep -Seconds 3          # 讓 WebView2 把內容畫完

$rect = New-Object Mouse+RECT
[void][Mouse]::GetWindowRect($hwnd, [ref]$rect)
$w = $rect.Right - $rect.Left
$h = $rect.Bottom - $rect.Top
Note 'window_rect' "$($rect.Left),$($rect.Top),$w,$h"
{pre_click}
$iconX   = $rect.Left + [int]($w * {icon_x})
$iconY   = $rect.Top  + [int]($h * {icon_y})
$targetX = $rect.Left + [int]($w * {target_x})
$targetY = $rect.Top  + [int]($h * {target_y})

[Mouse]::MoveTo($iconX, $iconY)
Start-Sleep -Milliseconds 300
[Mouse]::Down()
Start-Sleep -Milliseconds 200

# 分段移動：一步到位不會產生中間的位移事件，前端不會認定那是一次拖曳。
$steps = {steps}
for ($i = 1; $i -le $steps; $i++) {{
    $x = $iconX + [int](($targetX - $iconX) * $i / $steps)
    $y = $iconY + [int](($targetY - $iconY) * $i / $steps)
    [Mouse]::MoveTo($x, $y)
    Start-Sleep -Milliseconds 25
}}
Start-Sleep -Milliseconds 300
[Mouse]::Up()
Note 'drag_sent' 'True'

# 安裝需要一點時間，而且結果畫面出現之後才算走完。
Start-Sleep -Seconds 12

$installDir = "{install_dir}"
Note 'install_dir_exists' (Test-Path $installDir)
Note 'main_exe_exists' (Test-Path (Join-Path $installDir '{main_exe}'))
Note 'result_screen' ([Mouse]::FindByTitle('{WINDOW_TITLE}') -ne [IntPtr]::Zero)
Note 'done' 'True'
"""


def evaluate(report):
    """判準只看檔案有沒有落地。

    視窗沒出現時判為無法判定而不是失敗：那種情況下拖曳這件事根本沒有被測到
    （缺 WebView2、安裝檔沒啟動、標題改了都會這樣），把它算成失敗會讓人去找
    一個不存在的手勢缺陷。
    """
    name = "拖曳手勢觸發安裝"
    required = ("window_found", "drag_sent", "install_dir_exists", "main_exe_exists")
    missing = [key for key in required if key not in report]
    if missing:
        return Result(name, INCONCLUSIVE, "客體沒有回報：" + "、".join(missing))

    if report["window_found"] != "True":
        return Result(name, INCONCLUSIVE,
                      "安裝精靈的視窗沒有出現，這一輪沒有測到拖曳。")
    if report["drag_sent"] != "True":
        return Result(name, INCONCLUSIVE, "滑鼠事件沒有送出。")

    problems = [key for key in ("install_dir_exists", "main_exe_exists")
                if report[key] != "True"]
    if problems:
        return Result(name, FAIL,
                      "拖曳送出了，但安裝沒有發生：" + "、".join(problems))
    return Result(name, PASS, "拖曳觸發了安裝，檔案已落地。")


def parse_report(text):
    found = {}
    # 客體端建立檔案時會寫入位元組順序標記。這裡第一行剛好是註解、會被跳過，
    # 但那是巧合而不是保護——`tools/measure_msix_scope.py` 就因為第一行是資料
    # 而踩到，第一筆紀錄的鍵變成 `﻿step`。
    text = text.lstrip("﻿")
    for line in text.splitlines():
        line = line.strip().lstrip("﻿")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        found[key.strip()] = value.strip()
    return found


def run(vm, setup_path, app_name, work_dir, main_exe="app.exe",
        screenshot=None, log=print, click_before_drag=None):
    """把安裝檔送進客體、在桌面上實際拖一次、取回結果。

    腳本必須以 `interactive=True` 執行：拖曳要發生在使用者看得到的桌面工作
    階段上，工作階段 0 沒有可以操作的桌面。
    """
    remote_setup = GUEST_DIR + "\\" + os.path.basename(setup_path)
    size_mb = os.path.getsize(setup_path) / (1024 * 1024)
    with stage(f"把安裝檔送進客體（{size_mb:.0f} MB）", log=log):
        # 這一步最花時間，而且沒有任何外顯跡象——不報出來就看不出是不是卡住。
        vm.copy_in(setup_path, remote_setup)

    local_script = os.path.join(work_dir, "drive_installer.ps1")
    vms.write_guest_script(local_script,
                           guest_script(remote_setup, app_name, main_exe=main_exe,
                                        click_before_drag=click_before_drag))
    remote_script = GUEST_DIR + "\\" + os.path.basename(local_script)
    with stage("送入腳本", log=log):
        vm.copy_in(local_script, remote_script)

    with stage("在桌面上實際拖一次（含等視窗、等安裝）", log=log):
        vm.run_program(POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                       "-File", remote_script, interactive=True, check=False)

    if screenshot:
        # 給人看的附件，不列入判準。
        with stage("截圖", log=log):
            try:
                vm.capture_screen(screenshot)
            except Exception as error:
                print("截圖失敗：" + str(error), file=sys.stderr)

    local_report = os.path.join(work_dir, "drag_report.txt")
    text = ""
    with stage("取回報告", log=log):
        try:
            vm.copy_out(REPORT, local_report)
            with open(local_report, encoding="utf-8") as f:
                text = f.read()
        except Exception as error:
            print("取回報告失敗：" + str(error), file=sys.stderr)

    report = parse_report(text)
    return report, evaluate(report)


def main(argv=None):
    import argparse

    # 客體寫回來的文字不受這支工具控制，其中可能含有主控台編不出來的
    # 字元（實際踩過：客體讀錯編碼寫回一段亂碼）。少了這一行，一輪已經
    # 跑完的量測會在印出報告的那一步崩潰，結果完全拿不到。
    packaging_core.make_console_forgiving(sys.stdout)
    packaging_core.make_console_forgiving(sys.stderr)

    parser = argparse.ArgumentParser(
        description="在虛擬機上實際拖一次，確認拖曳手勢會觸發安裝。")
    parser.add_argument("setup", help="要測的 Setup_...exe")
    parser.add_argument("--app-name", default="mac-style-windows-installer")
    parser.add_argument("--main-exe", default="mswi-gui.exe")
    parser.add_argument("--machine", default="win11")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--work-dir", default=None)
    parser.add_argument("--gui", action="store_true",
                        help="開著虛擬機的畫面跑（預設無畫面；截圖兩種模式都拍得到）")
    args = parser.parse_args(argv)

    work_dir = args.work_dir or os.path.dirname(os.path.abspath(args.setup))
    os.makedirs(work_dir, exist_ok=True)
    shot = os.path.join(work_dir, "drag_screen.png")

    vm = vms.connect(args.machine, profile=args.profile,
                     purpose="拖曳手勢實測：" + os.path.basename(args.setup))
    try:
        with vms.preserved_tab(vm.machine.vmx):
            # 拖曳需要的是客體端的桌面工作階段，不是主機這邊看不看得到它，
            # 因此預設無畫面。截圖兩種模式都拍得到。
            with stage("還原快照並開機", log=print):
                vms.fresh_boot(vm, gui=args.gui)
            report, result = run(vm, args.setup, args.app_name, work_dir,
                                 main_exe=args.main_exe, screenshot=shot)
            vm.stop()
    finally:
        vms.release(args.machine)

    for key, value in report.items():
        print(f"  {key} = {value}")
    print(f"[{result.verdict}] {result.name}：{result.detail}")
    print(f"截圖（給人看的附件，不列入判準）：{shot}")
    return 0 if result.verdict == PASS else 1


if __name__ == "__main__":
    sys.exit(main())
