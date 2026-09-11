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

# `tools.vms` 不在最上層匯入：它要求 `vm_lease`，而那個套件由另一個 repo
# 提供、只裝在開發機上。放在最上層的話，凡是匯入這個模組的測試在 CI 上連
# 載入都會失敗——判準那幾十項因此一項都跑不到，而那些正是「拿到客體回報
# 之後怎麼判定」的全部保障（2026-09-09 實際踩到，理由同
# tools/verify_msix_all_users.py）。


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


def escape_for_sendkeys(text):
    """把 `SendKeys` 會當成控制字元的那幾個字包起來。

    `+^%~(){}[]` 原樣送出去打進去的是別的東西（`+` 是 Shift、`~` 是 Enter、
    `(` 開始一個群組）。密碼欄位對這種錯誤只會回報「密碼錯誤」，看不出是
    送法的問題。`{{}}` 要先處理，否則後面幾個補上的大括號會再被跳脫一次。
    """
    text = text.replace("{", "{{}").replace("}", "{}}")
    for char in "+^%~()[]":
        text = text.replace(char, "{" + char + "}")
    return text.replace("'", "''")


def _uia_snippet(key):
    """讀出視窗裡（含 WebView2 網頁內容）所有節點的名稱。

    走輔助使用介面：WebView2 把網頁內容的文字掛在那棵樹上，因此讀到的是
    使用者眼睛看到的字，不是程式碼裡的常數。
    """
    return _UIA_READ + """
Note '""" + key + """' ($seen -join ' | ')
"""


# 讀一次輔助使用樹，結果留在 $seen。抽出來的理由是它有三個用途：等頁面畫好、
# 拖曳前讀一次、拖曳後再讀一次。
_UIA_READ = """
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$element = [System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
$seen = @()
if ($element) {
    $found = $element.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($node in $found) {
        $label = $node.Current.Name
        if ($label -and $seen -notcontains $label) { $seen += $label }
    }
}
"""


def _wait_for_page(wanted, seconds=90):
    """等到輔助使用樹上出現指定的那串字為止。

    視窗出現不等於那一頁畫好了。真實抓到（2026-09-09）：視窗第 13.5 秒出現，
    固定等 3 秒之後就開始拖，而那時候樹上只有視窗外框的那幾個名稱——網頁內容
    還沒掛上去，那一輪因此「拖了但什麼都沒發生」。應用程式的名字在兩種語言下
    都一樣，拿它當判準不受介面語言影響。擋在前面的那幾頁（授權合約、密碼）
    上面沒有那個名字，呼叫端可以改指定別的字串——不改的話這一步會白等
    到上限（實測每輪 90 秒）。
    """
    return f"""
$pageWaitSeconds = {seconds}
$pageReady = $false
$pageWaited = 0
for ($i = 0; $i -lt ($pageWaitSeconds * 2); $i++) {{
{_UIA_READ}
    if ($seen -join ' | ' -like '*{wanted}*') {{ $pageReady = $true; break }}
    Start-Sleep -Milliseconds 500
    $pageWaited = $i / 2
}}
Note 'page_ready' $pageReady
Note 'page_wait_seconds' $pageWaited
"""


def _press_by_name(name, label):
    """找到叫這個名字的按鈕並按下去。**不用座標。**

    為什麼不用座標（2026-09-12 實測）：**視窗層級**的矩形與 `GetWindowRect`
    是同一個空間——拖曳那一段一直正常就是因為這樣——但**網頁內容裡**的元素
    回報的是實體像素，而 `[Mouse]::MoveTo` 以 `GetSystemMetrics` 正規化，
    拿到的是邏輯像素（量到 2046x952，而畫面實際是 2558x1190）。同一個 y 座標
    因此被當成不同比例，游標落到按鈕下方一百多像素的地方。

    沒有任何一個「從視窗算出來的比例」能同時修好兩者：拿那個視窗的兩種矩形
    相除得到 1（兩者同空間），拿桌面與 `GetSystemMetrics` 相除也得到 1。兩種
    都試過、都量過。因此改成根本不碰座標，用輔助使用介面的 `Invoke`。

    當初排除 `Invoke` 的理由是它丟例外時腳本仍會往下走，而下一行照樣把
    「按到了」記成 True——那條回報因此永遠成立。包起來、把例外記下來就沒有
    這個問題，而那本來就該做。

    找不到那顆按鈕時，把畫面上看得到的名稱一併回報：「按不到」與「按了沒
    作用」的處置完全不同，分不出來的話查不下去。
    """
    return _UIA_READ + f"""
$target = $null
if ($element) {{
    $wanted = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::NameProperty, '{name}')
    $target = $element.FindFirst(
        [System.Windows.Automation.TreeScope]::Descendants, $wanted)
}}
Note '{label}_found' ($target -ne $null)
if ($target -ne $null) {{
    $box = $target.Current.BoundingRectangle
    Note '{label}_rect' "$($box.X),$($box.Y),$($box.Width),$($box.Height)"
    if ($box.Width -gt 0 -and $box.Height -gt 0) {{
        # 不碰座標（見這個函式的說明）。例外要記下來——不記的話下一行的
        # 「按到了」就是一條永遠成立的回報。
        $failed = ''
        try {{
            $pattern = $target.GetCurrentPattern(
                [System.Windows.Automation.InvokePattern]::Pattern)
            $pattern.Invoke()
        }} catch {{
            $failed = $_.Exception.Message
        }}
        Note '{label}_error' $failed
        Note '{label}_pressed' ($failed -eq '')
        Start-Sleep -Seconds 5
    }} else {{
        # 沒有面積的元素按不到——那通常表示它其實不在畫面上。
        Note '{label}_pressed' 'False'
        Note '{label}_seen' ($seen -join ' | ')
    }}
}} else {{
    Note '{label}_pressed' 'False'
    Note '{label}_seen' ($seen -join ' | ')
}}
"""


def guest_script(setup_path, app_name, install_dir=None, main_exe="app.exe",
                 steps=24, click_before_drag=None, window_title=None,
                 icon_at=None, target_at=None, settle_seconds=12,
                 type_before_drag=None, click_after_typing=None,
                 click_after_settle=None, after_finish_seconds=20,
                 dump_text=False, dump_text_after=False,
                 invoke_before_drag=None, wait_for_text=None):
    """產生客體端要跑的 PowerShell。

    先等視窗出現並取得它的位置，才開始碰滑鼠——視窗還沒出現就移動並按下，
    點到的是桌面，而報告上會看起來像「拖了但沒有反應」。

    `click_before_drag`：拖曳之前先在視窗的這個相對位置點一下，形式與
    `ICON_AT` 相同（視窗矩形的比例）。授權合約頁的驗證用它按下「同意並
    繼續」——那一頁擋在拖曳畫面之前，不先過它就拖不到東西
    （見 `tools/verify_eula_gate.py`）。送滑鼠事件的那段 C# 因此只留這
    一份，不在別處複製。

    `window_title`／`icon_at`／`target_at`：解除安裝那一端的拖曳（把圖示拖到
    垃圾桶）走的是同一套機制，差別只有要找哪個視窗、以及兩個端點在視窗裡的
    位置（見 `tools/verify_uninstall_drag.py`）。留空即沿用安裝那一端的值。

    落地判準不在這裡：這個函式只回報 `install_dir_exists`／`main_exe_exists`
    這些事實，安裝要的是「出現」、解除安裝要的是「消失」，方向由呼叫端自己
    的判準函式決定。
    """
    install_dir = install_dir or (r"$env:LOCALAPPDATA\Programs\\" + app_name)
    window_title = window_title or WINDOW_TITLE
    finish = ""
    if click_after_settle:
        finish_x, finish_y = click_after_settle
        finish = f"""
# 結果畫面上那顆按鈕。解除安裝的整個目錄要按下「完成」之後才會被背景指令
# 刪掉（見 uninstall.py 的 finish_and_exit()），不按的話量到的是「檔案
# 都不見了、資料夾還在」那個中間狀態。
$finishX = $rect.Left + [int]($w * {finish_x})
$finishY = $rect.Top  + [int]($h * {finish_y})
[Mouse]::MoveTo($finishX, $finishY)
Start-Sleep -Milliseconds 300
[Mouse]::Down()
Start-Sleep -Milliseconds 120
[Mouse]::Up()
Note 'finish_click_at' "$finishX,$finishY"
# 背景那段指令自己還帶一段延遲才動手。
Start-Sleep -Seconds {after_finish_seconds}
Note 'install_dir_after_finish' (Test-Path $installDir)
Note 'main_exe_after_finish' (Test-Path (Join-Path $installDir '{main_exe}'))
"""
    wait_for_page = _wait_for_page(wait_for_text or app_name)
    read_text = ""
    if dump_text:
        # 位置在點擊與拖曳之前——那些動作會換頁，之後讀到的是別一頁的字。
        read_text = _uia_snippet("window_text")
    read_after = _uia_snippet("window_text_after") if dump_text_after else ""
    icon_x, icon_y = icon_at or ICON_AT
    target_x, target_y = target_at or TARGET_AT
    if invoke_before_drag:
        # 更新／降版那幾個對話框是安裝檔一開起來就問的，不是拖完才問（見
        # `tools/verify_msix_dialogs.py`）。先按過它才輪得到拖曳——順序反過來
        # 的話，那一下拖在對話框的遮罩上，而按完按鈕之後沒有人再拖一次。
        read_text = read_text + _press_by_name(invoke_before_drag,
                                               "before_drag")
    pre_click = read_text
    if click_before_drag:
        click_x, click_y = click_before_drag
        pre_click += f"""
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
        if type_before_drag is not None:
            # 打字接在點擊之後：輸入框要先拿到焦點，否則按鍵送到別的地方，
            # 而那一頁只會回報「密碼錯誤」，看不出是送法的問題。這一段因此
            # 併進 pre_click，順序由結構保證而不是靠呼叫端記得。
            pre_click += f"""
$wshell = New-Object -ComObject WScript.Shell
$wshell.SendKeys('{escape_for_sendkeys(type_before_drag)}')
Note 'typed' 'True'
Start-Sleep -Milliseconds 500
"""
            if click_after_typing:
                post_x, post_y = click_after_typing
                pre_click += f"""
$postX = $rect.Left + [int]($w * {post_x})
$postY = $rect.Top  + [int]($h * {post_y})
[Mouse]::MoveTo($postX, $postY)
Start-Sleep -Milliseconds 300
[Mouse]::Down()
Start-Sleep -Milliseconds 120
[Mouse]::Up()
Note 'post_click_at' "$postX,$postY"
# 送出之後畫面要換頁，馬上拖曳會拖在還沒消失的那一頁上。
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

# 路徑用雙引號，讓 `$env:LOCALAPPDATA` 這種寫法在客體端展開。解除安裝那一端
# 要啟動的是安裝目錄底下的 uninstall.exe，而那個目錄的絕對位置取決於客體的
# 使用者名稱——寫死使用者名稱會讓這支工具綁在某一台機器上。
Start-Process -FilePath "{setup_path}"

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
    $hwnd = [Mouse]::FindByTitle('{window_title}')
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

{wait_for_page}
$rect = New-Object Mouse+RECT
[void][Mouse]::GetWindowRect($hwnd, [ref]$rect)
$w = $rect.Right - $rect.Left
$h = $rect.Bottom - $rect.Top
Note 'window_rect' "$($rect.Left),$($rect.Top),$w,$h"

# 拖曳之前先量一次同一個表達式。只量後面那次的話，兩端都有一個永遠成立的
# 讀法：安裝端的「目錄在」有可能是這台機器本來就裝著，解除安裝端的「目錄
# 不見了」在從來沒裝過的機器上無條件為真。
$installDir = "{install_dir}"
Note 'install_dir_before' (Test-Path $installDir)
Note 'main_exe_before' (Test-Path (Join-Path $installDir '{main_exe}'))
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

# 落地要一點時間，而且結果畫面出現之後才算走完。解除安裝還要把整個目錄
# 拿掉、再把自己刪掉，比安裝久，因此這段等待由呼叫端決定。
Start-Sleep -Seconds {settle_seconds}

Note 'install_dir_exists' (Test-Path $installDir)
Note 'main_exe_exists' (Test-Path (Join-Path $installDir '{main_exe}'))
Note 'result_screen' ([Mouse]::FindByTitle('{window_title}') -ne [IntPtr]::Zero)
{read_after}
{finish}
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
    if report.get("install_dir_before") == "True":
        # 拖曳之前就已經裝著的話，拖曳之後「還在」不是這個手勢的功勞。
        return Result(name, INCONCLUSIVE,
                      "拖曳之前安裝目錄就已經存在，這一輪量不出手勢的效果。")

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
        screenshot=None, log=print, click_before_drag=None,
        remote_target=None, window_title=None, icon_at=None, target_at=None,
        settle_seconds=12, type_before_drag=None, click_after_typing=None,
        click_after_settle=None, dump_text=False, dump_text_after=False,
        after_finish_seconds=20, invoke_before_drag=None, wait_for_text=None):
    """把安裝檔送進客體、在桌面上實際拖一次、取回結果。

    腳本必須以 `interactive=True` 執行：拖曳要發生在使用者看得到的桌面工作
    階段上，工作階段 0 沒有可以操作的桌面。

    `remote_target`：要啟動的程式已經在客體裡（例如安裝完才存在的
    `uninstall.exe`），這時候 `setup_path` 不會被送進去。
    """
    from tools import vms

    if remote_target:
        remote_setup = remote_target
    else:
        remote_setup = GUEST_DIR + "\\" + os.path.basename(setup_path)
        size_mb = os.path.getsize(setup_path) / (1024 * 1024)
        with stage(f"把安裝檔送進客體（{size_mb:.0f} MB）", log=log):
            # 這一步最花時間，而且沒有任何外顯跡象——不報出來就看不出是不是
            # 卡住。
            vm.copy_in(setup_path, remote_setup)

    local_script = os.path.join(work_dir, "drive_installer.ps1")
    vms.write_guest_script(local_script,
                           guest_script(remote_setup, app_name, main_exe=main_exe,
                                        click_before_drag=click_before_drag,
                                        window_title=window_title,
                                        icon_at=icon_at, target_at=target_at,
                                        settle_seconds=settle_seconds,
                                        type_before_drag=type_before_drag,
                                        click_after_typing=click_after_typing,
                                        click_after_settle=click_after_settle,
                                        dump_text=dump_text,
                                        dump_text_after=dump_text_after,
                                        after_finish_seconds=after_finish_seconds,
                                        invoke_before_drag=invoke_before_drag,
                                        wait_for_text=wait_for_text))
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

    from tools import vms

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
