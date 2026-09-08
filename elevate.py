"""
elevate.py
-----------
以系統管理員權限啟動一支程式並等待它結束。

## 為什麼不是 `subprocess.run()`

Windows 的自動提權（跳出 UAC 詢問）只有走 `ShellExecute` 這條路徑才會被
認得。`subprocess.run()` 底層是 `CreateProcess`，它不會觸發提權，而是直接
用目前這個（未提權的）行程的權杖把子行程跑起來——子行程於是在需要權限的
那一步默默失敗，卻不會拋出任何例外，看起來像是正常執行完了。這是
`upgrade.py` 在更新覆蓋流程裡真實踩過的問題。

## 為什麼從 `upgrade.py` 抽出來

MSIX 的全機器佈建需要同一件事（ADR-0013 決定三：佈建交由提權的子行程
執行），但它需要的東西多一項：**要分得出「使用者按了取消」與「啟動
失敗」**。決定四把前者視為使用者的選擇——平靜地降級為當前使用者範圍；
後者則是故障，語氣不同。`upgrade.py` 原本兩者都拋出同一個例外，訊息裡
寫「使用者可能取消了 UAC 提示」，「可能」二字正是這裡要消掉的東西。

判斷依據是 `ShellExecuteExW` 失敗時系統設定的錯誤碼 `ERROR_CANCELLED`
(1223)，即 UAC 提示被拒絕時的固定回報值。讀不出那個錯誤碼時一律當成
故障：反過來猜「大概是使用者取消」會把真正的故障說成使用者的選擇。

## 回傳的是狀態，不是例外

`OK` 只代表**行程跑完了**，不代表它做成功了——結束碼的意義由呼叫端定義
（佈建的子行程以不同的非零值表達不同的失敗）。以例外表達的話，呼叫端得
從訊息字串反推發生了哪一種情形，那正是這個模組要避免的。

`shell32`／`kernel32` 是選填的注入點，與 `file_assoc.py`／
`system_entries.py` 的 `registry=` 是同一種形式：預設打真正的
`ctypes.windll`，測試換成假的 adapter。真實的 UAC 互動仍然無法在開發環境
重現，這個接縫只讓「成功／取消／逾時／非 0 結束碼」這幾條分支變得可測。
"""
import ctypes
from collections import namedtuple


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("fMask", ctypes.c_ulong),
        ("hwnd", ctypes.c_void_p),
        ("lpVerb", ctypes.c_wchar_p),
        ("lpFile", ctypes.c_wchar_p),
        ("lpParameters", ctypes.c_wchar_p),
        ("lpDirectory", ctypes.c_wchar_p),
        ("nShow", ctypes.c_int),
        ("hInstApp", ctypes.c_void_p),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", ctypes.c_wchar_p),
        ("hKeyClass", ctypes.c_void_p),
        ("dwHotKey", ctypes.c_ulong),
        ("hIcon", ctypes.c_void_p),
        ("hProcess", ctypes.c_void_p),
    ]


SEE_MASK_NOCLOSEPROCESS = 0x00000040
SW_HIDE = 0
WAIT_TIMEOUT = 0x00000102
ERROR_CANCELLED = 1223

OK = "ok"
DECLINED = "declined"
FAILED = "failed"
TIMEOUT = "timeout"

# 十分鐘。佈建本身是秒級的動作，但子行程是一顆 onefile 的安裝檔，啟動時要
# 先把自己解壓一次——GB 級的安裝檔在慢速磁碟上那一段就要好幾分鐘。逾時值
# 取的是「久到不可能是正常情形」而不是「正常情形的上限」。
DEFAULT_TIMEOUT_MS = 600000

Launch = namedtuple("Launch", "status exit_code detail")


def _last_error(kernel32):
    """讀系統的最後錯誤碼；讀不出整數就回傳 None。

    注入的假 adapter 可能回傳任何東西，而這個值會決定「使用者取消」與
    「故障」的分野——分不出來時交給呼叫端當故障處理。
    """
    try:
        value = kernel32.GetLastError()
    except Exception:
        return None
    return value if isinstance(value, int) else None


def _quote(argument):
    return f'"{argument}"' if " " in argument else argument


def run_elevated_and_wait(exe, args, timeout_ms=DEFAULT_TIMEOUT_MS,
                          shell32=None, kernel32=None):
    """提權啟動 `exe` 並等它結束，回傳 `Launch`。"""
    shell32 = shell32 if shell32 is not None else ctypes.windll.shell32
    kernel32 = kernel32 if kernel32 is not None else ctypes.windll.kernel32

    sei = SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(sei)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS
    sei.hwnd = None
    sei.lpVerb = "runas"
    sei.lpFile = exe
    sei.lpParameters = " ".join(_quote(a) for a in args)
    sei.lpDirectory = None
    # 子行程沒有介面，顯示出來的只會是一個空的黑框。
    sei.nShow = SW_HIDE

    if not shell32.ShellExecuteExW(ctypes.pointer(sei)):
        code = _last_error(kernel32)
        if code == ERROR_CANCELLED:
            return Launch(DECLINED, None, "")
        return Launch(FAILED, None,
                      f"無法以系統管理員權限啟動（錯誤碼 {code}）。")

    if not sei.hProcess:
        # `WaitForSingleObject(NULL, ...)` 回傳的是 WAIT_FAILED 而不是
        # WAIT_TIMEOUT，會被誤判成等待成功（`upgrade.py` 真實抓過的問題）。
        return Launch(FAILED, None, "啟動後沒有取得有效的行程控制代碼。")

    try:
        if kernel32.WaitForSingleObject(sei.hProcess, timeout_ms) == WAIT_TIMEOUT:
            return Launch(TIMEOUT, None, "等待逾時。")
        exit_code = ctypes.c_ulong(0)
        kernel32.GetExitCodeProcess(sei.hProcess, ctypes.pointer(exit_code))
        return Launch(OK, exit_code.value, "")
    finally:
        kernel32.CloseHandle(sei.hProcess)


def is_elevated(shell32=None):
    """目前這個行程是不是已經提權了。

    答不出來時回傳假：後果是多跳一次 UAC，而反過來猜錯的後果是佈建在系統
    那一端被拒，且錯誤訊息與真正的成因無關。
    """
    shell32 = shell32 if shell32 is not None else ctypes.windll.shell32
    try:
        return bool(shell32.IsUserAnAdmin())
    except Exception:
        return False
