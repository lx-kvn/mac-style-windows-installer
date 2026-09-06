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
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
                 steps=24):
    """產生客體端要跑的 PowerShell。

    先等視窗出現並取得它的位置，才開始碰滑鼠——視窗還沒出現就移動並按下，
    點到的是桌面，而報告上會看起來像「拖了但沒有反應」。
    """
    install_dir = install_dir or (r"$env:LOCALAPPDATA\Programs\\" + app_name)
    icon_x, icon_y = ICON_AT
    target_x, target_y = TARGET_AT

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
$hwnd = [IntPtr]::Zero
for ($i = 0; $i -lt 60; $i++) {{
    $hwnd = [Mouse]::FindWindow($null, '{WINDOW_TITLE}')
    if ($hwnd -ne [IntPtr]::Zero) {{ break }}
    Start-Sleep -Milliseconds 500
}}
Note 'window_found' ($hwnd -ne [IntPtr]::Zero)
if ($hwnd -eq [IntPtr]::Zero) {{ exit 1 }}

Start-Sleep -Seconds 3          # 讓 WebView2 把內容畫完

$rect = New-Object Mouse+RECT
[void][Mouse]::GetWindowRect($hwnd, [ref]$rect)
$w = $rect.Right - $rect.Left
$h = $rect.Bottom - $rect.Top
Note 'window_rect' "$($rect.Left),$($rect.Top),$w,$h"

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
Note 'result_screen' ([Mouse]::FindWindow($null, '{WINDOW_TITLE}') -ne [IntPtr]::Zero)
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
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        found[key.strip()] = value.strip()
    return found


def run(vm, setup_path, app_name, work_dir, main_exe="app.exe",
        screenshot=None):
    """把安裝檔送進客體、在桌面上實際拖一次、取回結果。

    腳本必須以 `interactive=True` 執行：拖曳要發生在使用者看得到的桌面工作
    階段上，工作階段 0 沒有可以操作的桌面。
    """
    remote_setup = GUEST_DIR + "\\" + os.path.basename(setup_path)
    vm.copy_in(setup_path, remote_setup)

    local_script = os.path.join(work_dir, "drive_installer.ps1")
    vms.write_guest_script(local_script,
                           guest_script(remote_setup, app_name, main_exe=main_exe))
    remote_script = GUEST_DIR + "\\" + os.path.basename(local_script)
    vm.copy_in(local_script, remote_script)

    vm.run_program(POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-File", remote_script, interactive=True, check=False)

    if screenshot:
        # 給人看的附件，不列入判準。
        try:
            vm.capture_screen(screenshot)
        except Exception as error:
            print("截圖失敗：" + str(error), file=sys.stderr)

    local_report = os.path.join(work_dir, "drag_report.txt")
    text = ""
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

    parser = argparse.ArgumentParser(
        description="在虛擬機上實際拖一次，確認拖曳手勢會觸發安裝。")
    parser.add_argument("setup", help="要測的 Setup_...exe")
    parser.add_argument("--app-name", default="mac-style-windows-installer")
    parser.add_argument("--main-exe", default="mswi-gui.exe")
    parser.add_argument("--machine", default="win11")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--work-dir", default=None)
    args = parser.parse_args(argv)

    work_dir = args.work_dir or os.path.dirname(os.path.abspath(args.setup))
    os.makedirs(work_dir, exist_ok=True)
    shot = os.path.join(work_dir, "drag_screen.png")

    vm = vms.connect(args.machine, profile=args.profile,
                     purpose="拖曳手勢實測：" + os.path.basename(args.setup))
    try:
        with vms.preserved_tab(vm.machine.vmx):
            vms.fresh_boot(vm, gui=True)   # 拖曳要有桌面，畫面開著
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
