"""驗證配置精靈：填完表單按下編譯，真的產出一顆安裝檔。

配置精靈（`gui_config.py` + `ui/config.html`）是這個專案唯一沒有被任何
自動化碰過的介面。CLI（`builder_cli.py`）走的是另一條路，CI 用的也是那
一條——「使用者實際會用的那個畫面」在此之前沒有驗過。

## 一輪做完的事

1. 把 Python 與 `requirements.txt` 的每一項離線裝進客體
   （`tools/provision_build_env.py`，實測約五分鐘）。
2. 開一次配置精靈，**不補 PATH**：環境檢查的遮罩應該跳出來。
3. 關掉，補上 PATH 再開一次：遮罩不該跳。
4. 填五個文字欄位、選應用程式資料夾、挑主要執行檔、選 PNG 與 ICO。
5. 按下「開始編譯安裝檔」，等產物出現。

第 2、3 步是一組對照：兩次量到同一個值的話，「環境檢查通過」這件事與環境
無關，它說什麼都不算數。成本幾乎是零——同一輪、同一台客體，只是多開關一
次視窗，不必多跑一輪虛擬機。

## 介面元素怎麼指名

**文字欄位靠 placeholder。** 輔助使用介面把網頁輸入框的名字報成它的
placeholder（實測 `例如: MyCustomApp` 之類），而 `ui/config.html` 裡標籤
與輸入框沒有繫結（沒有 `for=`），所以標籤文字指不到東西。代價是改了
`ui/config.html` 的 placeholder 這裡就找不到元素，因此
`tests/test_verify_config_wizard.py` 會比對兩邊。

**網頁裡的按鈕用 Invoke，不用座標。** 網頁內容的元素矩形與送滑鼠事件那段
的座標體系對不上，而比例沒辦法從視窗本身算出來（經過見
`docs/investigations/驗證覆蓋率的稽核與補強.md`）。

**原生對話框的確認鈕只能用點的。** 「選擇資料夾」與「開啟」這兩個對話框
的確認鈕在輔助使用介面上以 `Pane` 露出、不支援 Invoke，但那個位置底下是
真的按鈕（實測 `WindowFromPoint` 回報 `Button "選擇資料夾"`）。要點得準，
腳本行程必須先設成認得高解析度顯示：不設的話 `GetSystemMetrics` 與
`GetCursorPos` 回報的是縮放過的邏輯像素（實測 2046x952），而元素矩形是
實體像素（2558x1190），點下去落在 1.25 倍遠的地方。設定有沒有生效看得
出來——螢幕尺寸會從前者變成後者。

**兩個對話框的路徑都不能用寫的。** 它們裡面唯一的真輸入框是搜尋方塊
——選資料夾那個的「資料夾:」、開啟檔案那個的「檔案名稱(N):」都只以
`Pane` 露出。因此選資料夾走網址列（Alt+D → 路徑 → Enter），選檔案則是
點一下標籤右邊那格（那格是輸入框，標籤本身只有 85x19）再打字。

**送鍵盤之前一定要先確認前景視窗是誰。** 不確認的話輸入會落在當下有焦點
的地方：實測有一輪落在檔案清單上，路徑被當成「重新命名」，跳出「檔案
名稱不可以包含下列任意字元」，整段停在那裡二十四分鐘。找不到該打的欄位
時就停下來回報，不送出「賭賭看」的輸入。

## 判準

判準由 `tests/test_verify_config_wizard.py` 釘住；驅動虛擬機的部分不進
測試，真的跑一輪要二十分鐘。
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import packaging_core

from tools import drive_installer_gui as drive
from tools import provision_build_env as provision

PASS = drive.PASS
FAIL = drive.FAIL
INCONCLUSIVE = drive.INCONCLUSIVE

Result = collections.namedtuple("Result", "name verdict detail")
Field = collections.namedtuple("Field", "key label placeholder value")

WINDOW_TITLE = "配置精靈"

# 五個文字欄位。placeholder 與 ui/config.html 一致，由測試釘住。
TEXT_FIELDS = (
    Field("app_name", "應用程式名稱", "例如: MyCustomApp", "DemoApp"),
    Field("folder_name", "安裝資料夾名稱", "留空則沿用上方的應用程式名稱",
          "DemoApp"),
    Field("version", "應用程式版本號", "example: 1.0.0", "1.0.0"),
    Field("publisher", "軟體發行者", "例如: CustomPublisher", "VerifyBot"),
    Field("exe_name", "輸出的安裝檔名稱", "例如: Setup_MyCustomApp",
          "Setup_DemoApp"),
)

GUEST_APP_DIR = r"C:\Users\Public\demo_app"
GUEST_MAIN_EXE = "app.exe"
GUEST_PNG = r"C:\Users\Public\icon.png"
GUEST_ICO = r"C:\Users\Public\icon.ico"
GUEST_WIZARD = r"C:\Users\Public\wizard.exe"
GUEST_REPORT = r"C:\Users\Public\wizard_report.txt"

# 產物落在 packaging_core.default_workspace_dir() 底下的 dist/。
SETUP_IN_GUEST = (r"$env:LOCALAPPDATA\mac-style-windows-installer"
                  r"\workspace\dist\Setup_DemoApp.exe")

# 一顆安裝檔至少這麼大。實測那顆是 39 MB；幾 KB 的東西比較像中途寫壞的
# 殘檔，而「檔案存在」對那種情形也會是綠的。
MIN_SETUP_BYTES = 5 * 1024 * 1024


def evaluate_environment(report):
    """環境檢查：沒補 PATH 要跳遮罩，補了不該跳。"""
    name = "環境檢查跟著環境走"
    missing = [k for k in ("env_modal_without_path", "env_modal_with_path")
               if k not in report]
    if missing:
        return Result(name, INCONCLUSIVE, "客體沒有回報：" + "、".join(missing))
    without = report["env_modal_without_path"]
    with_path = report["env_modal_with_path"]
    # 先判紅燈再判對照組：環境已經到齊卻還在喊缺東西，那是產品的缺陷，
    # 不因為對照組那一半也不合格就降級成「無法判定」。對照組存在的目的是
    # 擋住假的綠燈，不是擋住紅燈。
    if with_path != "False":
        return Result(name, FAIL,
                      "環境已經到齊，配置精靈仍然說缺少編譯安裝檔所需的環境。")
    if without != "True":
        return Result(name, INCONCLUSIVE,
                      "環境不完整時也沒有跳出遮罩——這條檢查與環境無關，"
                      "它的「通過」不算數。")
    return Result(name, PASS,
                  "環境不完整時跳出遮罩、到齊之後不跳。")


def evaluate_form(report):
    """表單：每一格都要讀得回來，顯示區的預設字樣都要被換掉。"""
    name = "表單填得進去"
    if "folder_dialog_closed" not in report:
        return Result(name, INCONCLUSIVE, "客體沒有回報選擇資料夾那一步。")
    if report["folder_dialog_closed"] != "True":
        return Result(name, INCONCLUSIVE,
                      "選擇資料夾的對話框沒有關掉——它是強制回應的視窗，"
                      "後面每一步都動不了，這一輪量不到東西。")
    for field in TEXT_FIELDS:
        key = field.key + "_readback"
        if key not in report:
            return Result(name, INCONCLUSIVE, "客體沒有回報 " + field.label)
        if report[key] != field.value:
            return Result(name, FAIL,
                          field.label + " 沒有填進去（讀回來是 "
                          + repr(report[key]) + "）。")
    # 對話框關掉了不等於路徑進去了。證據是頁面上那行預設字樣被換掉。
    if report.get("page_still_says_none") != "False":
        return Result(name, FAIL,
                      "選完資料夾之後，頁面上仍然寫著「未選擇資料夾」。")
    if report.get("page_still_says_no_file") != "False":
        return Result(name, FAIL,
                      "選完圖示之後，頁面上仍然寫著「未選擇檔案」。")
    before = report.get("main_exe_before", "")
    after = report.get("main_exe_after", "")
    if not after or after == before:
        return Result(name, FAIL,
                      "主要執行檔沒有選起來（前後都是 " + repr(before)
                      + "）——它是必填，按下編譯只會得到一個提醒。")
    return Result(name, PASS, "五個欄位、資料夾、主要執行檔與兩張圖示都進去了。")


def evaluate_build(report):
    """產物：按下編譯之前不存在，之後存在，而且大小像一顆安裝檔。"""
    name = "按下編譯真的產出安裝檔"
    if "setup_exists_before" not in report:
        return Result(name, INCONCLUSIVE, "沒有量到按下編譯之前的狀態。")
    if report["setup_exists_before"] != "False":
        # 檔案本來就在的話，「產出來了」不需要任何人動手就成立。
        return Result(name, INCONCLUSIVE,
                      "按下編譯之前那顆安裝檔就已經存在，這一輪的「產出來了」"
                      "證明不了任何事。")
    form = evaluate_form(report)
    if report.get("setup_exists") != "True" and form.verdict != PASS:
        return Result(name, INCONCLUSIVE,
                      "表單沒有填完就按下編譯，得到的是一個提醒的彈窗"
                      "——沒有產物不代表編譯壞了。")
    if report.get("setup_exists") != "True":
        return Result(name, FAIL, "按下編譯之後沒有產出安裝檔。")
    try:
        size = int(report.get("setup_size", "0"))
    except ValueError:
        size = 0
    if size < MIN_SETUP_BYTES:
        return Result(name, FAIL,
                      "產出的檔案只有 " + str(size) + " 位元組，不像一顆安裝檔"
                      "——比較像中途寫壞的殘檔。")
    return Result(name, PASS,
                  "編譯前不存在、編譯後產出 " + str(size) + " 位元組的安裝檔。")


def guest_script():
    """客體端要跑的那段腳本。"""
    fills = "".join(
        "SetText '%s' '%s' '%s'\n" % (f.placeholder, f.value, f.key)
        for f in TEXT_FIELDS)
    return _SCRIPT % {
        "path_prefix": provision.PATH_PREFIX,
        "wizard": GUEST_WIZARD,
        "title": WINDOW_TITLE,
        "fills": fills,
        "app_dir": GUEST_APP_DIR,
        "main_exe": GUEST_MAIN_EXE,
        "png": GUEST_PNG,
        "ico": GUEST_ICO,
        "report": GUEST_REPORT,
        "setup": SETUP_IN_GUEST,
    }


_SCRIPT = r"""$ErrorActionPreference = 'Continue'
$report = '%(report)s'
function Note($k, $v) { Add-Content -Path $report -Value ("{0}={1}" -f $k, $v) -Encoding UTF8 }
Set-Content -Path $report -Value '# 配置精靈' -Encoding UTF8

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class W {
    public const uint MOVE = 0x0001; public const uint ABS = 0x8000;
    public const uint DOWN = 0x0002; public const uint UP = 0x0004;
    [StructLayout(LayoutKind.Sequential)]
    public struct MI { public int dx; public int dy; public uint data;
        public uint flags; public uint time; public IntPtr extra; }
    [StructLayout(LayoutKind.Sequential)]
    public struct IN { public uint type; public MI mi; }
    [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X; public int Y; }
    [DllImport("user32.dll")] public static extern uint SendInput(uint n, IN[] i, int size);
    [DllImport("user32.dll")] public static extern int GetSystemMetrics(int i);
    [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT p);
    [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT p);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr c);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    public delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetWindowTextW(IntPtr h, System.Text.StringBuilder s, int n);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetClassNameW(IntPtr h, System.Text.StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    // 不設的話 GetSystemMetrics／GetCursorPos 回報邏輯像素、輔助使用介面
    // 回報實體像素，點下去落在 1.25 倍遠的地方（見模組說明）。
    public static bool BecomeDpiAware() {
        try { if (SetProcessDpiAwarenessContext((IntPtr)(-4))) return true; } catch { }
        try { return SetProcessDPIAware(); } catch { }
        return false;
    }
    public static POINT Cursor() { POINT p; GetCursorPos(out p); return p; }
    public static IntPtr Find(string wanted) {
        IntPtr found = IntPtr.Zero;
        EnumWindows((h, l) => {
            if (!IsWindowVisible(h)) return true;
            var b = new System.Text.StringBuilder(300);
            GetWindowTextW(h, b, b.Capacity);
            if (b.ToString().Trim() == wanted) { found = h; return false; }
            return true;
        }, IntPtr.Zero);
        return found;
    }
    // 認視窗類別不認標題：標題會被在地化，而且桌面上隨時有別的視窗冒出來
    // （實測比對「新出現的標題」時抓到過一個 MSN 小工具的視窗）。
    public static IntPtr[] Dialogs() {
        var found = new System.Collections.Generic.List<IntPtr>();
        EnumWindows((h, l) => {
            if (!IsWindowVisible(h)) return true;
            var c = new System.Text.StringBuilder(64);
            GetClassNameW(h, c, c.Capacity);
            if (c.ToString() == "#32770") found.Add(h);
            return true;
        }, IntPtr.Zero);
        return found.ToArray();
    }
    public static string Describe(IntPtr h) {
        if (h == IntPtr.Zero) return "(none)";
        var t = new System.Text.StringBuilder(300);
        GetWindowTextW(h, t, t.Capacity);
        var c = new System.Text.StringBuilder(64);
        GetClassNameW(h, c, c.Capacity);
        return c.ToString() + " \"" + t.ToString() + "\"";
    }
    static void Send(uint f, int x, int y) {
        IN[] i = new IN[1]; i[0].type = 0; i[0].mi.flags = f; i[0].mi.dx = x; i[0].mi.dy = y;
        SendInput(1, i, Marshal.SizeOf(typeof(IN)));
    }
    public static void MoveTo(int x, int y) {
        int w = GetSystemMetrics(0); int h = GetSystemMetrics(1);
        Send(MOVE | ABS, (x * 65535) / w, (y * 65535) / h);
    }
    public static uint Click() {
        IN[] i = new IN[2];
        i[0].type = 0; i[0].mi.flags = DOWN;
        i[1].type = 0; i[1].mi.flags = UP;
        return SendInput(2, i, Marshal.SizeOf(typeof(IN)));
    }
}
"@

Note 'metrics_before' "$([W]::GetSystemMetrics(0))x$([W]::GetSystemMetrics(1))"
Note 'dpi_aware_ok' ([W]::BecomeDpiAware())
Note 'metrics_after' "$([W]::GetSystemMetrics(0))x$([W]::GetSystemMetrics(1))"

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$UIA = [System.Windows.Automation.AutomationElement]
$SCOPE = [System.Windows.Automation.TreeScope]::Descendants
$wshell = New-Object -ComObject WScript.Shell
$ENV_MARK = '缺少編譯安裝檔所需的環境'

function Elements($hwnd) {
    $e = $UIA::FromHandle($hwnd)
    if (-not $e) { return @() }
    return $e.FindAll($SCOPE, [System.Windows.Automation.Condition]::TrueCondition)
}

function PageText($hwnd) {
    $n = @(); foreach ($e in (Elements $hwnd)) { $n += $e.Current.Name }
    return ($n -join ' | ')
}

function Named($hwnd, $name, $kind) {
    # 同名的元素不只一個時取第一個符合型別的：「選擇資料夾」在頁面上有兩顆
    # 按鈕（應用程式資料夾、編譯工作目錄），要的是前者。
    foreach ($n in (Elements $hwnd)) {
        if ($n.Current.Name -ne $name) { continue }
        $k = $n.Current.ControlType.ProgrammaticName -replace 'ControlType\.', ''
        if ($kind -eq '' -or $k -eq $kind) { return $n }
    }
    return $null
}

function Press($el, $label) {
    if (-not $el) { Note "${label}_found" 'False'; return $false }
    $failed = ''
    try {
        $el.GetCurrentPattern(
            [System.Windows.Automation.InvokePattern]::Pattern).Invoke()
    } catch { $failed = $_.Exception.Message }
    if ($failed) { Note "${label}_error" $failed }
    return ($failed -eq '')
}

function ClickElement($el, $label) {
    if (-not $el) { Note "${label}_found" 'False'; return $false }
    $b = $el.Current.BoundingRectangle
    if ($b.Width -le 0 -or $b.Height -le 0) { return $false }
    [W]::MoveTo([int]($b.X + $b.Width / 2), [int]($b.Y + $b.Height / 2))
    Start-Sleep -Milliseconds 400
    # 問另一個來源：那個點上的視窗是誰。這條與送出去的座標無關，瞄歪了它
    # 會講——拿「滑鼠停在我叫它去的地方」當證據的話永遠成立。
    Note "${label}_under_cursor" ([W]::Describe([W]::WindowFromPoint([W]::Cursor())))
    [void][W]::Click()
    Start-Sleep -Milliseconds 800
    return $true
}

function ClickUntilGone($gone, $el, $label, $tries) {
    # 點一下、檢查那個視窗還在不在，還在就再點。實測同一顆按鈕在不同的送法
    # 與不同的間隔下結果不同，而是哪一項造成的沒有查到底；與其賭一個說法，
    # 不如讓呼叫端自己確認結果。
    for ($t = 1; $t -le $tries; $t++) {
        [void](ClickElement $el "${label}_try$t")
        Start-Sleep -Seconds 2
        if (& $gone) { Note "${label}_closed_on_try" $t; return $true }
    }
    return $false
}

function OpenWizard($seconds) {
    Start-Process -FilePath '%(wizard)s'
    for ($i = 0; $i -lt ($seconds * 2); $i++) {
        $h = [W]::Find('%(title)s')
        if ($h -ne [IntPtr]::Zero) { return $h }
        Start-Sleep -Milliseconds 500
    }
    return [IntPtr]::Zero
}

function WaitRendered($hwnd) {
    for ($i = 0; $i -lt 120; $i++) {
        if ((PageText $hwnd) -like '*開始編譯安裝檔*') { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

# ---- 對照組的前半：不補 PATH ----
# 這一輪才裝的 Python 不在桌面工作階段的 PATH 裡（登入時就定下來了），
# 因此配置精靈看不到它，環境檢查的遮罩應該跳出來。
$first = OpenWizard 90
Note 'first_window_found' ($first -ne [IntPtr]::Zero)
if ($first -ne [IntPtr]::Zero) {
    Note 'first_rendered' (WaitRendered $first)
    Note 'env_modal_without_path' ((PageText $first) -like "*$ENV_MARK*")
    [void](Press (Named $first '關閉' 'Button') 'first_close')
    Start-Sleep -Seconds 3
}
Get-Process -Name 'wizard' -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 3

# ---- 對照組的後半：補上 PATH ----
$env:PATH = '%(path_prefix)s;' + $env:PATH
$hwnd = OpenWizard 90
Note 'wizard_window_found' ($hwnd -ne [IntPtr]::Zero)
if ($hwnd -eq [IntPtr]::Zero) { Note 'done' 'True'; exit 1 }
Note 'rendered' (WaitRendered $hwnd)
Note 'env_modal_with_path' ((PageText $hwnd) -like "*$ENV_MARK*")
if ((PageText $hwnd) -like "*$ENV_MARK*") {
    [void](Press (Named $hwnd '我知道了' 'Button') 'dismiss')
    Start-Sleep -Seconds 2
}

# ---- 文字欄位 ----
function SetText($name, $text, $label) {
    $el = Named $hwnd $name 'Edit'
    if (-not $el) { Note "${label}_readback" '(找不到欄位)'; return }
    try {
        $el.GetCurrentPattern(
            [System.Windows.Automation.ValuePattern]::Pattern).SetValue($text)
    } catch { Note "${label}_error" $_.Exception.Message }
    Start-Sleep -Milliseconds 400
    # 讀回來才算填進去了：SetValue 沒有拋例外不等於網頁那邊真的收到。
    $back = ''
    try {
        $back = (Named $hwnd $name 'Edit').GetCurrentPattern(
            [System.Windows.Automation.ValuePattern]::Pattern).Current.Value
    } catch { }
    Note "${label}_readback" $back
}

%(fills)s
# ---- 應用程式資料夾 ----
Note 'browse_pressed' (Press (Named $hwnd '選擇資料夾' 'Button') 'browse')
$dialog = [IntPtr]::Zero
for ($i = 0; $i -lt 60; $i++) {
    $dialog = [W]::Find('選擇資料夾')
    if ($dialog -ne [IntPtr]::Zero) { break }
    Start-Sleep -Milliseconds 500
}
Note 'folder_dialog_found' ($dialog -ne [IntPtr]::Zero)
if ($dialog -ne [IntPtr]::Zero) {
    [void][W]::SetForegroundWindow($dialog)
    Start-Sleep -Milliseconds 500
    # 路徑走網址列：這個對話框裡唯一的輸入框是搜尋方塊。
    $wshell.SendKeys('%%d')
    Start-Sleep -Milliseconds 800
    $wshell.SendKeys('%(app_dir)s')
    Start-Sleep -Seconds 1
    $wshell.SendKeys('{ENTER}')
    Start-Sleep -Seconds 3
    $addr = ''
    foreach ($n in (Elements $dialog)) {
        if ($n.Current.Name -like '位址:*') { $addr = $n.Current.Name }
    }
    Note 'folder_address' $addr
    [void](ClickUntilGone { [W]::Find('選擇資料夾') -eq [IntPtr]::Zero } `
        (Named $dialog '選擇資料夾' 'Pane') 'folder_confirm' 4)
}
Note 'folder_dialog_closed' ([W]::Find('選擇資料夾') -eq [IntPtr]::Zero)
# 對話框關掉了不等於路徑進去了：它還開著的時候，整棵樹裡本來就找得到自己
# 打進去的路徑，拿那個當證據的話怎樣都會過。
Note 'page_still_says_none' ((PageText $hwnd) -like '*未選擇資料夾*')

# ---- 主要執行檔 ----
Start-Sleep -Seconds 2
$combo = $null
foreach ($n in (Elements $hwnd)) {
    $k = $n.Current.ControlType.ProgrammaticName -replace 'ControlType\.', ''
    if ($k -eq 'ComboBox' -and $n.Current.Name -ne 'Language') { $combo = $n }
}
function ComboValue($c) {
    if (-not $c) { return '' }
    try {
        return $c.GetCurrentPattern(
            [System.Windows.Automation.ValuePattern]::Pattern).Current.Value
    } catch { return '' }
}
Note 'main_exe_before' (ComboValue $combo)
if ($combo) {
    try {
        $combo.GetCurrentPattern(
            [System.Windows.Automation.ValuePattern]::Pattern).SetValue('%(main_exe)s')
    } catch { Note 'main_exe_setvalue_error' $_.Exception.Message }
    Start-Sleep -Milliseconds 800
    if ((ComboValue $combo) -notlike '*%(main_exe)s*') {
        try {
            $combo.GetCurrentPattern(
                [System.Windows.Automation.ExpandCollapsePattern]::Pattern).Expand()
            Start-Sleep -Seconds 1
            foreach ($n in (Elements $hwnd)) {
                $k = $n.Current.ControlType.ProgrammaticName -replace 'ControlType\.', ''
                if ($k -eq 'ListItem' -and $n.Current.Name -like '*%(main_exe)s*') {
                    $n.GetCurrentPattern(
                        [System.Windows.Automation.SelectionItemPattern]::Pattern).Select()
                    break
                }
            }
        } catch { Note 'main_exe_expand_error' $_.Exception.Message }
        Start-Sleep -Seconds 1
    }
}
Note 'main_exe_after' (ComboValue $combo)

# ---- PNG 與 ICO ----
function PickFile($buttonName, $path, $label) {
    $before = [W]::Dialogs()
    [void](Press (Named $hwnd $buttonName 'Button') "${label}_btn")
    $dlg = [IntPtr]::Zero
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        foreach ($h in [W]::Dialogs()) {
            if ($before -notcontains $h) { $dlg = $h; break }
        }
        if ($dlg -ne [IntPtr]::Zero) { break }
    }
    Note "${label}_dialog_found" ($dlg -ne [IntPtr]::Zero)
    if ($dlg -eq [IntPtr]::Zero) { return }
    [void][W]::SetForegroundWindow($dlg)
    Start-Sleep -Milliseconds 800
    # 檔名欄位跟選資料夾那個對話框一樣，只以 Pane 露出、寫不進去（整個對話
    # 框裡唯一的真輸入框是搜尋方塊）。所以用點的：「檔案名稱(N):」那個 Pane
    # 量到的是那四個字本身（實測 85x19），輸入框緊接在它右邊。
    # 兩個元素都先抓在手上再動手。打完路徑之後檔名格會跳出自動完成的候選
    # 清單，那當下整棵樹讀不到東西——實測就是在那時候找不到「開啟」，而
    # 症狀是「按鈕不見了」，看不出跟打字有關。
    $nameField = $null
    $open = $null
    foreach ($n in (Elements $dlg)) {
        if (-not $nameField -and $n.Current.Name -like '檔案名稱*') { $nameField = $n }
        if (-not $open -and ($n.Current.Name -like '開啟*' -or
                             $n.Current.Name -like 'Open*')) { $open = $n }
    }
    Note "${label}_name_field_found" ($nameField -ne $null)
    Note "${label}_open_found" ($open -ne $null)
    if (-not $nameField -or -not $open) { return }
    $lb = $nameField.Current.BoundingRectangle
    [W]::MoveTo([int]($lb.X + $lb.Width + 200), [int]($lb.Y + $lb.Height / 2))
    Start-Sleep -Milliseconds 400
    Note "${label}_name_field_under_cursor" `
        ([W]::Describe([W]::WindowFromPoint([W]::Cursor())))
    [void][W]::Click()
    Start-Sleep -Milliseconds 600
    # 打字之前確認前景視窗真的是這個對話框。不確認就打的話，鍵盤會落在
    # 當下有焦點的地方——實測落在檔案清單上，路徑被當成「重新命名」，
    # 跳出「檔案名稱不可以包含下列任意字元」然後整段卡死。
    $fg = [W]::GetForegroundWindow()
    Note "${label}_foreground_is_dialog" ($fg -eq $dlg)
    if ($fg -ne $dlg) {
        Note "${label}_foreground" ([W]::Describe($fg))
        return
    }
    $wshell.SendKeys($path)
    Start-Sleep -Seconds 1
    [void](ClickUntilGone { [W]::Dialogs() -notcontains $dlg } $open `
        "${label}_open" 4)
    Note "${label}_dialog_closed" ([W]::Dialogs() -notcontains $dlg)
}

PickFile '選擇 PNG 圖示' '%(png)s' 'png'
PickFile '選擇 ICO 檔案' '%(ico)s' 'ico'
Note 'page_still_says_no_file' ((PageText $hwnd) -like '*未選擇檔案*')

# ---- 編譯 ----
$dist = "%(setup)s"
# 編譯之前先量一次：檔案本來就在的話，「產出來了」不需要任何人動手就成立。
Note 'setup_exists_before' (Test-Path $dist)
Note 'submit_pressed' (Press (Named $hwnd '開始編譯安裝檔' 'Button') 'submit')
Note 'submitted_at' (Get-Date -Format 'HH:mm:ss')

$progress = @()
for ($i = 0; $i -lt 48; $i++) {
    Start-Sleep -Seconds 15
    $progress += ("{0}`t{1}`t{2}" -f (Get-Date -Format 'HH:mm:ss'),
        (Test-Path $dist), (PageText $hwnd))
    Set-Content -Path 'C:\Users\Public\build_progress.txt' `
        -Value ($progress -join "`n") -Encoding UTF8
    if (Test-Path $dist) { break }
}
Note 'setup_exists' (Test-Path $dist)
if (Test-Path $dist) {
    Note 'setup_size' (Get-Item $dist).Length
    Note 'setup_path' $dist
}
Note 'finished_at' (Get-Date -Format 'HH:mm:ss')
Note 'done' 'True'
"""

_PREP = (
    "New-Item -ItemType Directory -Force -Path '%s' | Out-Null\n"
    "Copy-Item 'C:\\Windows\\System32\\cmd.exe' '%s\\%s' -Force\n"
    % (GUEST_APP_DIR, GUEST_APP_DIR, GUEST_MAIN_EXE))


def run(vm, wizard_exe, png, ico, work_dir, log=print, timeout_seconds=1500):
    """跑完一輪，回傳客體寫回來的報告。"""
    import time

    from tools import vms

    os.makedirs(work_dir, exist_ok=True)
    provision.provision(vm, work_dir, log=log)

    with drive.stage("送材料進客體", log=log):
        vm.copy_in(wizard_exe, GUEST_WIZARD)
        vm.copy_in(png, GUEST_PNG)
        vm.copy_in(ico, GUEST_ICO)
        prep = os.path.join(work_dir, "prep.ps1")
        vms.write_guest_script(prep, _PREP)
        vm.copy_in(prep, r"C:\Users\Public\prep.ps1")
        vm.run_program(drive.POWERSHELL, "-NoProfile", "-ExecutionPolicy",
                       "Bypass", "-File", r"C:\Users\Public\prep.ps1",
                       check=False)

    script = os.path.join(work_dir, "wizard.ps1")
    vms.write_guest_script(script, guest_script())
    vm.copy_in(script, r"C:\Users\Public\wizard.ps1")
    with drive.stage("填表單並編譯", log=log):
        # 這一段要有看得見的桌面：配置精靈是視窗程式，而且它彈出的原生對話框
        # 只在使用者的工作階段裡存在。
        vm.run_program(drive.POWERSHELL, "-NoProfile", "-ExecutionPolicy",
                       "Bypass", "-File", r"C:\Users\Public\wizard.ps1",
                       interactive=True, no_wait=True, check=False)

    local = os.path.join(work_dir, "wizard_report.txt")
    text = ""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        time.sleep(15)
        try:
            vm.copy_out(GUEST_REPORT, local)
            with open(local, encoding="utf-8") as handle:
                text = handle.read()
            if "done=True" in text:
                break
        except Exception:
            pass
    vm.capture_screen(os.path.join(work_dir, "screen.png"))
    try:
        vm.copy_out(r"C:\Users\Public\build_progress.txt",
                    os.path.join(work_dir, "build_progress.txt"))
    except Exception:
        pass
    return provision.parse_report(text)


def main(argv=None):
    import argparse

    packaging_core.make_console_forgiving(sys.stdout)
    packaging_core.make_console_forgiving(sys.stderr)

    from tools import vms

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(
        description="驗證配置精靈填完表單按下編譯真的產出安裝檔。")
    parser.add_argument("wizard", help="要測的配置精靈 GUI exe")
    parser.add_argument("--png", default=os.path.join(repo, "branding", "icon.png"))
    parser.add_argument("--ico", default=os.path.join(repo, "branding", "icon.ico"))
    parser.add_argument("--machine", default="win11")
    parser.add_argument("--work-dir", default=None)
    args = parser.parse_args(argv)

    work_dir = args.work_dir or os.path.join(
        os.path.dirname(os.path.abspath(args.wizard)), "config_wizard_run")

    vm = vms.connect(args.machine, purpose="驗證配置精靈", lock_minutes=45)
    try:
        with vms.preserved_tab(vm.machine.vmx):
            vms.fresh_boot(vm)
            report = run(vm, args.wizard, args.png, args.ico, work_dir)
            vm.stop()
    finally:
        vms.release(args.machine)

    print("=== 客體的回報 ===")
    for key in sorted(report):
        print(key + " = " + report[key])
    print()

    print("=== 判定 ===")
    worst = PASS
    for result in (evaluate_environment(report), evaluate_form(report),
                   evaluate_build(report)):
        print("[" + result.verdict + "] " + result.name + "：" + result.detail)
        if result.verdict != PASS:
            worst = result.verdict
    return 0 if worst == PASS else 1


if __name__ == "__main__":
    sys.exit(main())
