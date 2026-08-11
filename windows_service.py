"""windows_service.py
--------------------
Windows 服務建立/移除原語，包裝 `sc.exe`。跟 `system_entries.py` 同一種
深模組風格：小介面、失敗吞例外回傳布林，不讓單一資源操作失敗中止整個
安裝/解除安裝流程。

用 `sc.exe`（subprocess）而不是 ctypes 直接呼叫 `OpenSCManager`/
`CreateService`：服務管理本來就是設計給一般管理員權限操作的 CLI 工具，
沒有 `taskkill.exe` 對 `explorer.exe` 那種 `SeDebugPrivilege` 權限坑
（見 `explorer_lock_release.py`），不需要改走底層 API。
"""
import subprocess


def create_service(service_name, exe_path, display_name=None, start_type="auto"):
    """`sc create <service_name> binPath= "<exe_path>" start= <start_type>`，
    有給 display_name 時加 `DisplayName= <display_name>`。回傳是否成功。
    """
    try:
        cmd = ["sc.exe", "create", service_name, "binPath=", exe_path, "start=", start_type]
        if display_name:
            cmd += ["DisplayName=", display_name]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            cmd, creationflags=creationflags, timeout=30,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except Exception:
        return False


def remove_service(service_name):
    """`sc delete <service_name>`。回傳是否成功。"""
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            ["sc.exe", "delete", service_name], creationflags=creationflags, timeout=30,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except Exception:
        return False
