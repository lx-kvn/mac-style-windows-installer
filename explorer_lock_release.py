"""
explorer_lock_release.py
-------------------------
釋放檔案總管鎖住安裝/解除安裝要處理的檔案時，分層處理：

  1. 先只關閉正在瀏覽目標路徑（或其子路徑）的檔案總管**視窗**，不動
     explorer.exe 這個殼層**行程**本身。工作管理員裡「應用程式」跟
     「Windows 處理程序」兩個 explorer.exe 項目行為不同就是這個道理：
     前者是單一視窗，關掉不影響桌面/工作列；後者才是整個殼層行程。
  2. 只有第 1 步關窗之後 explorer.exe 仍然鎖著檔案，才進到「強制關閉
     殼層」這條路：直接 taskkill 整個 explorer.exe 行程會讓桌面/工作列
     全部重啟，而且 Windows 的 AutoRestartShell 機制會讓它幾乎瞬間自動
     復活——如果它剛好開著目標資料夾，會在呼叫端的檔案操作還沒完成前
     就搶著把同一個檔案重新鎖住，這才是「看起來砍不掉」的真正原因。
     所以要先把 AutoRestartShell 暫時設成 "0"（保證它不會自動復活）
     再砍，回傳一個狀態物件給呼叫端保留，等檔案操作做完（不管成功或
     失敗）再呼叫 restore_after_lock_release() 手動重啟 explorer.exe、
     把 AutoRestartShell 寫回原值——這個值只有 explorer.exe 結束的那個
     瞬間才會被 Windows 讀取，寫回去本身不會觸發任何動作，所以順序上
     一定要「手動重啟」先做，不能只靠寫回登錄表值期待它自己醒過來。

跟 system_entries.py/restart_manager.py 同一種 seam 慣例：registry 參數
預設是真正的 winreg；shell_factory/find_locking_processes 只給測試注入
假物件用。
"""
import ctypes
from ctypes import wintypes
import os
import subprocess
import winreg as _real_winreg

import restart_manager

_WINLOGON_KEY = r"Software\Microsoft\Windows NT\CurrentVersion\Winlogon"
_DEFAULT_AUTO_RESTART_SHELL = "1"

_PROCESS_TERMINATE = 0x0001
_TOKEN_ADJUST_PRIVILEGES = 0x0020
_TOKEN_QUERY = 0x0008
_SE_PRIVILEGE_ENABLED = 0x00000002


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", _LUID), ("Attributes", wintypes.DWORD)]


class _TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [("PrivilegeCount", wintypes.DWORD), ("Privileges", _LUID_AND_ATTRIBUTES * 1)]


# 真實抓到的問題：這幾個 Windows API 呼叫原本完全沒宣告 restype/argtypes，
# ctypes 在沒有宣告的情況下預設把回傳值/參數當成 32-bit 的 c_int 處理——
# 在 64-bit Windows 上，GetCurrentProcess() 這類回傳 HANDLE（指標大小）的
# 函式，回傳值可能被截斷/錯誤解讀，導致後續拿著這個「壞掉」的參數去呼叫
# OpenProcessToken() 時直接被 Windows 拒絕（不會拋 Python 例外，只是回傳
# 失敗），實測重現：OpenProcessToken 每次都失敗，TerminateProcess 也跟著
# 失敗。明確宣告 HANDLE 大小的型別，讓 ctypes 正確處理指標寬度的參數/
# 回傳值，才是正確做法。
ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
ctypes.windll.kernel32.GetCurrentProcess.argtypes = []

ctypes.windll.advapi32.OpenProcessToken.restype = wintypes.BOOL
ctypes.windll.advapi32.OpenProcessToken.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
]

ctypes.windll.advapi32.LookupPrivilegeValueW.restype = wintypes.BOOL
ctypes.windll.advapi32.LookupPrivilegeValueW.argtypes = [
    wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(_LUID),
]

ctypes.windll.advapi32.AdjustTokenPrivileges.restype = wintypes.BOOL
ctypes.windll.advapi32.AdjustTokenPrivileges.argtypes = [
    wintypes.HANDLE, wintypes.BOOL, ctypes.POINTER(_TOKEN_PRIVILEGES),
    wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p,
]

ctypes.windll.kernel32.OpenProcess.restype = wintypes.HANDLE
ctypes.windll.kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]

ctypes.windll.kernel32.TerminateProcess.restype = wintypes.BOOL
ctypes.windll.kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]

ctypes.windll.kernel32.CloseHandle.restype = wintypes.BOOL
ctypes.windll.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

ctypes.windll.kernel32.GetLastError.restype = wintypes.DWORD
ctypes.windll.kernel32.GetLastError.argtypes = []


def _last_error_message():
    """呼叫端剛執行完一個回傳失敗的 Win32 API 之後立刻呼叫：把
    GetLastError() 的錯誤碼轉成人看得懂的訊息（ctypes.WinError() 底層會呼叫
    FormatMessage）。真實抓到的問題：原本 OpenProcess/TerminateProcess
    失敗時只記一句「失敗」，看不出 Windows 給的實際原因（例如是不是
    ERROR_ACCESS_DENIED），下次要繼續追查時又要重新加一輪 log 才看得到。
    """
    code = ctypes.windll.kernel32.GetLastError()
    try:
        return f"錯誤碼 {code}：{ctypes.WinError(code).strerror}"
    except Exception:
        return f"錯誤碼 {code}"


def _noop_log(_msg):
    pass


def close_windows_browsing_path(target_path, shell_factory=None, log=None):
    """關閉所有目前瀏覽 target_path 本身或其子路徑的檔案總管視窗（呼叫
    Shell.Application 視窗物件的 .Quit()，不動 explorer.exe 這個行程）。
    回傳關閉的視窗數量。

    依賴 pywin32（win32com.client），跟 installer_core.py._create_shortcut()
    一樣是選用依賴——任何一步失敗（沒裝 pywin32、COM 呼叫例外）都
    best-effort 回傳 0，不拋例外：這只是「盡量避免要重啟殼層」的優化，
    不是關鍵路徑，失敗了正常走後面的強制關殼層流程就好。
    """
    log = log or _noop_log
    if not target_path:
        return 0
    try:
        if shell_factory is not None:
            shell = shell_factory()
        else:
            import win32com.client
            shell = win32com.client.Dispatch("Shell.Application")
        windows = list(shell.Windows())
    except Exception as e:
        log(f"[explorer_lock_release] 無法列舉檔案總管視窗（Shell.Application 不可用）: {e}")
        return 0

    log(f"[explorer_lock_release] 目前開啟的檔案總管/瀏覽器視窗共 {len(windows)} 個，目標路徑: {target_path}")
    normalized_target = os.path.normcase(os.path.normpath(target_path))
    closed = 0
    for window in windows:
        try:
            folder_path = window.Document.Folder.Self.Path
        except Exception as e:
            log(f"[explorer_lock_release] 無法取得某個視窗的瀏覽路徑，略過: {e}")
            continue
        normalized_folder = os.path.normcase(os.path.normpath(folder_path))
        if normalized_folder != normalized_target and not normalized_folder.startswith(
            normalized_target + os.sep
        ):
            continue
        try:
            window.Quit()
            closed += 1
            log(f"[explorer_lock_release] 已關閉瀏覽 {folder_path} 的視窗")
        except Exception as e:
            log(f"[explorer_lock_release] 關閉視窗 {folder_path} 失敗: {e}")
    log(f"[explorer_lock_release] 關窗步驟結束，共關閉 {closed} 個視窗")
    return closed


def _resolve_image_name(pid, log=_noop_log):
    """回傳 pid 對應的執行檔檔名（例如 "explorer.exe"），查不到回傳空字串。

    Restart Manager 回傳的是使用者友善名稱（explorer.exe 常會顯示成
    「Windows 檔案總管」之類的 localized 字串），不能拿來判斷「這是不是
    explorer.exe」，要另外用 pid 查真正的執行檔檔名。

    真實抓到的問題：這一步如果解析失敗（tasklist 呼叫例外、pid 當下已經
    不存在、輸出格式跟預期不同）會直接回傳空字串，呼叫端會把它當成
    「不是 explorer.exe」處理，整個強制關殼層的保護機制就不會啟動，卻
    完全沒有任何痕跡可以事後追查——所以這裡一定要把失敗原因記下來。
    """
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        output = subprocess.check_output(
            f'tasklist /FI "PID eq {pid}" /NH /FO CSV',
            shell=True, text=True, stderr=subprocess.DEVNULL, creationflags=creationflags,
        )
        first_line = output.strip().splitlines()[0] if output.strip() else ""
        image_name = first_line.split(",")[0].strip('"')
        if not image_name:
            log(f"[explorer_lock_release] pid={pid} 的 tasklist 輸出是空字串，無法解析真正的執行檔名稱（原始輸出: {output!r}）")
        return image_name
    except Exception as e:
        log(f"[explorer_lock_release] pid={pid} 查詢真正的執行檔名稱失敗，無法解析: {e}")
        return ""


def _enable_debug_privilege(log=_noop_log):
    """啟用目前行程權杖裡的 SeDebugPrivilege。

    真實抓到的問題：即使這支安裝程式本身已經是用系統管理員權限執行
    （--uac-admin 提權），admin token 裡雖然帶著 SeDebugPrivilege，但
    Windows 預設是「停用」狀態，要自己呼叫 AdjustTokenPrivileges 才會
    真的生效——這正是工作管理員（會自己啟用這個權限）能終止
    explorer.exe，但外部呼叫 taskkill.exe（沒有啟用這個權限）卻會回報
    「拒絕存取」而失敗的真正差異。best-effort：這裡任何一步失敗都只記
    log 繼續往下走，讓 OpenProcess/TerminateProcess 自己決定最終是否
    真的因為權限不足而失敗。
    """
    token = wintypes.HANDLE()
    try:
        if not ctypes.windll.advapi32.OpenProcessToken(
            ctypes.windll.kernel32.GetCurrentProcess(),
            _TOKEN_ADJUST_PRIVILEGES | _TOKEN_QUERY,
            ctypes.byref(token),
        ):
            log(f"[explorer_lock_release] OpenProcessToken 失敗，SeDebugPrivilege 可能無法啟用，{_last_error_message()}")
            return
        try:
            luid = _LUID()
            if not ctypes.windll.advapi32.LookupPrivilegeValueW(None, "SeDebugPrivilege", ctypes.byref(luid)):
                log(f"[explorer_lock_release] LookupPrivilegeValueW 失敗，SeDebugPrivilege 可能無法啟用，{_last_error_message()}")
                return
            tp = _TOKEN_PRIVILEGES()
            tp.PrivilegeCount = 1
            tp.Privileges[0].Luid = luid
            tp.Privileges[0].Attributes = _SE_PRIVILEGE_ENABLED
            if not ctypes.windll.advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None):
                log(f"[explorer_lock_release] AdjustTokenPrivileges 失敗，SeDebugPrivilege 可能無法啟用，{_last_error_message()}")
        finally:
            ctypes.windll.kernel32.CloseHandle(token)
    except Exception as e:
        log(f"[explorer_lock_release] 啟用 SeDebugPrivilege 時發生例外: {e}")


def _terminate_process(pid, log=_noop_log):
    """直接呼叫 Windows API（OpenProcess + TerminateProcess）終止行程，
    取代原本外部呼叫 taskkill.exe。

    真實抓到的問題（實測重現）：taskkill.exe 預設不會啟用
    SeDebugPrivilege，對 explorer.exe 這類跑在不同登入 session 的行程，
    即使呼叫端本身已經是系統管理員權限執行，taskkill 仍會回報
    「returncode=1，ERROR: 無法終止 PID xxxx 的處理程序。原因: 存取被拒。」
    而終止失敗——這正是工作管理員能砍得掉、taskkill.exe 卻不行的真正
    差異。改成跟工作管理員一樣：先啟用 SeDebugPrivilege，再直接
    OpenProcess + TerminateProcess。
    """
    _enable_debug_privilege(log=log)
    try:
        handle = ctypes.windll.kernel32.OpenProcess(_PROCESS_TERMINATE, False, pid)
    except Exception as e:
        log(f"[explorer_lock_release] OpenProcess(pid={pid}) 呼叫例外: {e}")
        return
    if not handle:
        log(f"[explorer_lock_release] OpenProcess(pid={pid}) 失敗，無法取得終止用的控制代碼，{_last_error_message()}")
        return
    try:
        if ctypes.windll.kernel32.TerminateProcess(handle, 1):
            log(f"[explorer_lock_release] TerminateProcess(pid={pid}) 成功")
        else:
            log(f"[explorer_lock_release] TerminateProcess(pid={pid}) 失敗，{_last_error_message()}")
    except Exception as e:
        log(f"[explorer_lock_release] TerminateProcess(pid={pid}) 呼叫例外: {e}")
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _get_auto_restart_shell(registry):
    try:
        key = registry.OpenKey(registry.HKEY_CURRENT_USER, _WINLOGON_KEY, 0, registry.KEY_ALL_ACCESS)
        value, _ = registry.QueryValueEx(key, "AutoRestartShell")
        registry.CloseKey(key)
        return value
    except Exception:
        return None


def _set_auto_restart_shell(value, registry):
    try:
        key = registry.CreateKey(registry.HKEY_CURRENT_USER, _WINLOGON_KEY)
        registry.SetValueEx(key, "AutoRestartShell", 0, registry.REG_SZ, value)
        registry.CloseKey(key)
    except Exception:
        pass


def release_locking_processes(processes, path=None, registry=_real_winreg,
                               shell_factory=None, find_locking_processes=None, log=None):
    """釋放鎖定的主流程，見模組說明。processes 是
    [{"pid":.., "name":..}, ...]（name 是 Restart Manager 回傳的 localized
    名稱，不用來判斷是不是 explorer.exe）。

    有給 path 也有給 find_locking_processes：先關窗，再重新查一次「實際
    還鎖著」的 process 有哪些，取代呼叫端傳進來的（可能已經過期的）
    processes 清單——關窗如果已經解開鎖，這裡查到的就會是空清單，後面
    完全不需要動殼層。沒給 find_locking_processes 就直接沿用 processes，
    不重查（例如呼叫端沒辦法/不需要重新偵測的情境）。

    回傳值：這次有沒有強制關過殼層。None 代表沒有（不管是關窗就解決了、
    還是本來就沒有 explorer.exe 在鎖），呼叫端不需要事後處理；不是 None
    就是一個狀態物件，呼叫端要在檔案操作完成後傳給 restore_after_lock_release()。

    log 是選用的 log(msg) callback（沒給就是 no-op）：這一整條流程完全
    沒有任何痕跡的話，一旦「砍了但看起來沒效果」，事後完全沒辦法判斷是
    「根本沒被判斷成 explorer.exe」「taskkill 有跑但失敗」還是別的原因。
    """
    log = log or _noop_log
    log(f"[explorer_lock_release] release_locking_processes 開始：processes={processes} path={path}")
    if path:
        closed = close_windows_browsing_path(path, shell_factory=shell_factory, log=log)
        if find_locking_processes is not None:
            remaining = find_locking_processes([path])
            log(f"[explorer_lock_release] 關窗 {closed} 個之後重新偵測，剩餘鎖定: {remaining}")
            processes = [{"pid": pid, "name": name} for pid, name in remaining]

    # 第二層：關窗解決不了時，先試著用 Restart Manager 請支援它的應用程式
    # 自己存檔、優雅關閉（不是砍行程），比直接強制終止更客氣，也是
    # Windows Installer 本身處理這類情境的做法。這層解不開鎖，才落到
    # 下面既有的「強制關殼層」邏輯。
    if path and processes:
        session = restart_manager.RestartManagerSession([path])
        if session.is_open:
            log(f"[explorer_lock_release] 嘗試 Restart Manager 優雅關閉，path={path}")
            if session.shutdown():
                if find_locking_processes is not None:
                    remaining = find_locking_processes([path])
                else:
                    remaining = session.list_locking_processes()
                log(f"[explorer_lock_release] Restart Manager 優雅關閉後重新偵測，剩餘鎖定: {remaining}")
                session.restart()
                if not remaining:
                    session.close()
                    log("[explorer_lock_release] Restart Manager 優雅關閉已解開鎖，不需要強制關殼層")
                    return None
                processes = [{"pid": pid, "name": name} for pid, name in remaining]
            else:
                log("[explorer_lock_release] Restart Manager 優雅關閉呼叫失敗或沒有可關閉的應用程式")
        else:
            log("[explorer_lock_release] Restart Manager session 開不起來，略過優雅關閉這層")
        session.close()

    explorer_procs = []
    other_procs = []
    for proc in processes:
        image_name = _resolve_image_name(proc["pid"], log=log)
        log(f"[explorer_lock_release] pid={proc['pid']} (Restart Manager 名稱={proc.get('name')!r}) "
            f"解析出的真正執行檔名稱={image_name!r}")
        if image_name.lower() == "explorer.exe":
            explorer_procs.append(proc)
        else:
            other_procs.append(proc)

    for proc in other_procs:
        _terminate_process(proc["pid"], log=log)

    if not explorer_procs:
        log("[explorer_lock_release] 沒有偵測到 explorer.exe，不強制關殼層")
        return None

    previous_value = _get_auto_restart_shell(registry)
    log(f"[explorer_lock_release] 偵測到 explorer.exe 仍鎖著檔案，AutoRestartShell 目前值={previous_value!r}，改為 \"0\"")
    _set_auto_restart_shell("0", registry)
    for proc in explorer_procs:
        _terminate_process(proc["pid"], log=log)

    return {"previous_auto_restart_shell": previous_value}


def restore_after_lock_release(forced_down_state, registry=_real_winreg, log=None):
    """呼叫端在後續檔案操作完成（不管成功或失敗）之後呼叫。forced_down_state
    是 None 就什麼都不做；不是 None 就手動重啟 explorer.exe（Popen，
    best-effort，吞例外），並把 AutoRestartShell 寫回讀到的舊值（沒讀到
    就用預設值 "1"）。"""
    log = log or _noop_log
    if forced_down_state is None:
        return
    log(f"[explorer_lock_release] restore_after_lock_release：重啟 explorer.exe，還原 AutoRestartShell={forced_down_state}")
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(["explorer.exe"], creationflags=creationflags)
    except Exception as e:
        log(f"[explorer_lock_release] 重啟 explorer.exe 失敗: {e}")
    previous_value = forced_down_state.get("previous_auto_restart_shell") or _DEFAULT_AUTO_RESTART_SHELL
    _set_auto_restart_shell(previous_value, registry)
