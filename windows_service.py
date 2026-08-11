"""windows_service.py
--------------------
Windows 服務建立/移除原語，包裝 `sc.exe`。

用 `sc.exe`（subprocess）而不是 ctypes 直接呼叫 `OpenSCManager`/
`CreateService`：服務管理本來就是設計給一般管理員權限操作的 CLI 工具，
沒有 `taskkill.exe` 對 `explorer.exe` 那種 `SeDebugPrivilege` 權限坑
(見 `explorer_lock_release.py`)，不需要改走底層 API。

`remove_service()` 收斂了服務移除的完整生命週期（查詢目前狀態 -> 執行中
先 stop 並輪詢確認真的停止 -> delete -> 再查詢一次確認真的消失），不是
單純轉呼叫 `sc delete` 就回傳它的 returncode。真實抓到的 bug：`sc delete`
對一個仍在執行中的服務會回傳 0（成功），但只是把它標記成
`DELETE_PENDING`，不是真的從服務控制管理員資料庫移除——服務仍然存在，
指向的執行檔也還被鎖著刪不掉，只看 `sc delete` 的 returncode 會讓呼叫端
誤報「已移除」。這個介面對外仍然只有一個 `create_service()`/
`remove_service()`，把 `sc.exe` 的這些狀態機細節都吸收在實作裡，呼叫端
只要問「移除了沒」，不需要自己搞懂 STOPPED/DELETE_PENDING 這些狀態碼。
"""
import subprocess
import time

# sc query 輸出的 STATE 欄位數值（節錄自 SERVICE_STATUS 結構的 dwCurrentState）。
# 完整列表：1=STOPPED 2=START_PENDING 3=STOP_PENDING 4=RUNNING
# 5=CONTINUE_PENDING 6=PAUSE_PENDING 7=PAUSED，這裡只需要判斷「是不是已停止」。
_SERVICE_STATE_STOPPED = 1

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# sc.exe `start=` 參數支援的值（節錄自這裡實際用到的子集，完整列表還有
# boot/system，這個工具用不到）。這是這個模組對外公開的介面一部分——
# packaging_core.py 的打包前驗證要判斷使用者填的 start_type 合不合法，
# 應該從這裡讀，而不是自己另外寫死一份會悄悄跟這裡脫鉤的常數
# （架構稽核 A3：config schema 單一真實來源）。
VALID_START_TYPES = frozenset({"auto", "demand", "disabled"})


def create_service(service_name, exe_path, display_name=None, start_type="auto"):
    """`sc create <service_name> binPath= "<exe_path>" start= <start_type>`，
    有給 display_name 時加 `DisplayName= <display_name>`。回傳是否成功。

    真實抓到的 bug（unquoted service path，CWE-428）：`exe_path` 必須用
    引號字元包起來才能傳給 `binPath=`。命令列傳遞給 sc.exe 時，因為值裡
    有空白，Python 的 subprocess 會自動幫這個 argv 元素加一層引號——但那
    只是讓 sc.exe 的 C runtime 正確解析出「這是一個參數」，sc.exe 收到的
    字串值本身（拿掉那層引號之後）仍然是不含引號的原始路徑，寫進登錄表
    ImagePath 的也是這個不含引號的值。Service Control Manager 之後解析
    ImagePath 啟動服務時，如果沒有引號會依序嘗試每個以空白分隔的前綴當
    可執行檔（例如 `C:\\Program Files\\MyApp\\app.exe` 會先試
    `C:\\Program.exe`）——這裡明確把字面上的引號字元包進傳給 `binPath=`
    的值本身，才會真的寫進 ImagePath。
    """
    try:
        cmd = ["sc.exe", "create", service_name, "binPath=", f'"{exe_path}"', "start=", start_type]
        if display_name:
            cmd += ["DisplayName=", display_name]
        result = subprocess.run(
            cmd, creationflags=_CREATE_NO_WINDOW, timeout=30,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except Exception:
        return False


def _query_service_state(service_name):
    """回傳 (exists, state_code)。服務不存在時 `sc query` 回傳非 0 exit
    code，這裡一律當成 (False, None)；exists=True 但解析不出 STATE 那行
    （理論上不會發生，保留當防呆）時 state_code 是 None。"""
    try:
        result = subprocess.run(
            ["sc.exe", "query", service_name], creationflags=_CREATE_NO_WINDOW, timeout=30,
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return False, None
        for line in (result.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("STATE"):
                after_colon = line.split(":", 1)[1].strip()
                digits = after_colon.split()[0] if after_colon else ""
                return True, (int(digits) if digits.isdigit() else None)
        return True, None
    except Exception:
        return False, None


def _stop_and_wait(service_name, timeout=30, poll_interval=0.5):
    """呼叫 `sc stop`，輪詢 `sc query` 直到狀態變成 STOPPED 或逾時。
    回傳是否確認已停止——逾時仍回傳 False，讓呼叫端自行決定要不要繼續
    嘗試 delete（delete 之後的 verify 查詢會抓到刪不掉的情況）。"""
    try:
        subprocess.run(
            ["sc.exe", "stop", service_name], creationflags=_CREATE_NO_WINDOW, timeout=30,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        exists, state = _query_service_state(service_name)
        if not exists or state == _SERVICE_STATE_STOPPED:
            return True
        time.sleep(poll_interval)
    return False


def remove_service(service_name, stop_timeout=30):
    """移除一個 Windows 服務：確認目前狀態 -> 執行中先停止並等待真的
    停止 -> `sc delete` -> 再查詢一次確認真的消失了。回傳是否確認移除
    成功——不是相信 `sc delete` 呼叫本身的 returncode（見模組docstring）。
    """
    exists, state = _query_service_state(service_name)
    if not exists:
        return True

    if state != _SERVICE_STATE_STOPPED:
        _stop_and_wait(service_name, timeout=stop_timeout)

    try:
        subprocess.run(
            ["sc.exe", "delete", service_name], creationflags=_CREATE_NO_WINDOW, timeout=30,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        return False

    exists_after, _ = _query_service_state(service_name)
    return not exists_after
