"""restart_manager.py
------------------
包裝 Windows Restart Manager API（Rstrtmgr.dll），用來偵測「目前是哪些
進程持有某些檔案的控制代碼（正在鎖定這些檔案）」。

真實情境（見 uninstall.py 的 restart_explorer_on_update）：應用程式如果
註冊了 Windows 檔案總管殼層擴充功能，explorer.exe 會把這支 DLL 常駐鎖住，
解除安裝/更新覆蓋時想刪除/覆寫會失敗，而且跟系統管理員權限無關。舊做法
是寫死「一定是 explorer.exe 鎖住」，直接 taskkill /im explorer.exe——這只
是針對這一種特定情境的權宜解法，如果卡住檔案的其實是別的進程（例如某個
背景服務、或另一個掛勾進來的應用程式），完全偵測不到也處理不了。

Restart Manager 是 Windows 官方提供給安裝程式用來解決這類問題的機制，
Windows Installer（MSI）判斷「這個檔案要不要重開機才能覆寫」、PowerToys
的 File Locksmith 判斷「這個檔案被誰鎖住」，用的都是同一套 API：

  1. RmStartSession()：開一個 session
  2. RmRegisterResources()：把要檢查的檔案路徑餵給它
  3. RmGetList()：問它「目前是哪些進程持有這些檔案的控制代碼」
  4. RmEndSession()：結束 session

只用 ctypes 呼叫 Windows 內建的 DLL，不需要額外安裝套件——跟這個專案其他
地方（winreg、user32、shell32）風格一致。
"""
import ctypes
from ctypes import wintypes

ERROR_SUCCESS = 0
ERROR_MORE_DATA = 234
_CCH_RM_SESSION_KEY = 32
_RM_MAX_APP_NAME = 255
_RM_MAX_SVC_NAME = 63


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _RM_UNIQUE_PROCESS(ctypes.Structure):
    _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", _FILETIME)]


class _RM_PROCESS_INFO(ctypes.Structure):
    _fields_ = [
        ("Process", _RM_UNIQUE_PROCESS),
        ("strAppName", ctypes.c_wchar * (_RM_MAX_APP_NAME + 1)),
        ("strServiceShortName", ctypes.c_wchar * (_RM_MAX_SVC_NAME + 1)),
        ("ApplicationType", ctypes.c_int),
        ("AppStatus", wintypes.ULONG),
        ("TSSessionId", wintypes.DWORD),
        ("bRestartable", wintypes.BOOL),
    ]


def _load_rstrtmgr():
    return ctypes.WinDLL("rstrtmgr")


def find_locking_processes(file_paths, rm_dll=None):
    """回傳 [(pid, app_name), ...]：目前鎖住 file_paths 裡任何一個檔案的
    進程清單（依 pid 去重）。app_name 是 Restart Manager 回傳的使用者
    友善名稱（例如 explorer.exe 常會顯示成「Windows 檔案總管」之類的
    localized 名稱，不是真正的執行檔檔名——要判斷是不是特定執行檔，
    呼叫端要另外用 pid 查真正的 image name，不能直接比對這個字串）。

    file_paths 只需要是路徑字串（不必真的存在——不存在的檔案 Restart
    Manager 會直接忽略，不會報錯）。任何一步 Windows API 呼叫失敗都
    best-effort 回傳空清單，不拋例外：對呼叫端來說，「找不到鎖定」跟
    「API 呼叫失敗」結果一樣——正常繼續刪除，刪不掉再照舊的重試/警告
    機制處理，不能因為這個輔助判斷本身出錯就讓整個解除安裝流程中斷。

    rm_dll 只給測試用：注入一個假的 DLL 物件（提供 RmStartSession /
    RmRegisterResources / RmGetList / RmEndSession 四個方法），繞過真正
    呼叫 Windows API，不需要在真實的鎖定檔案情境下才能測試這裡的流程
    （兩階段 RmGetList、buffer 依 needed 重新配置、pid 去重、各種失敗
    路徑的 best-effort 容錯）。
    """
    file_paths = [p for p in file_paths if p]
    if not file_paths:
        return []

    try:
        rstrtmgr = rm_dll if rm_dll is not None else _load_rstrtmgr()
    except Exception:
        return []

    session_handle = wintypes.DWORD()
    session_key = ctypes.create_unicode_buffer(_CCH_RM_SESSION_KEY + 1)
    try:
        result = rstrtmgr.RmStartSession(ctypes.byref(session_handle), 0, session_key)
    except Exception:
        return []
    if result != ERROR_SUCCESS:
        return []

    try:
        arr_type = ctypes.c_wchar_p * len(file_paths)
        filenames = arr_type(*file_paths)
        result = rstrtmgr.RmRegisterResources(
            session_handle, len(file_paths), filenames, 0, None, 0, None,
        )
        if result != ERROR_SUCCESS:
            return []

        proc_info_needed = wintypes.UINT(0)
        proc_info_count = wintypes.UINT(0)
        reboot_reasons = wintypes.DWORD(0)
        result = rstrtmgr.RmGetList(
            session_handle, ctypes.byref(proc_info_needed), ctypes.byref(proc_info_count),
            None, ctypes.byref(reboot_reasons),
        )
        if result not in (ERROR_SUCCESS, ERROR_MORE_DATA):
            return []
        if proc_info_needed.value == 0:
            return []

        proc_info_count = wintypes.UINT(proc_info_needed.value)
        proc_info_array = (_RM_PROCESS_INFO * proc_info_needed.value)()
        result = rstrtmgr.RmGetList(
            session_handle, ctypes.byref(proc_info_needed), ctypes.byref(proc_info_count),
            proc_info_array, ctypes.byref(reboot_reasons),
        )
        if result != ERROR_SUCCESS:
            return []

        seen_pids = set()
        processes = []
        for i in range(proc_info_count.value):
            info = proc_info_array[i]
            pid = info.Process.dwProcessId
            if pid in seen_pids:
                continue
            seen_pids.add(pid)
            processes.append((pid, info.strAppName))
        return processes
    except Exception:
        return []
    finally:
        try:
            rstrtmgr.RmEndSession(session_handle)
        except Exception:
            pass
