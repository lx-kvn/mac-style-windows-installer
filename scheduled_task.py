"""scheduled_task.py
-------------------
Windows 排程工作建立/移除原語，包裝 `schtasks.exe`。跟 `windows_service.py`
同一種深模組風格：小介面、失敗吞例外回傳布林，不讓單一資源操作失敗中止
整個安裝/解除安裝流程。
"""
import subprocess


def create_scheduled_task(task_name, exe_path, trigger="onlogon"):
    """`schtasks /create /tn <task_name> /tr <exe_path> /sc <trigger> /f`
    （`/f` 強制覆蓋同名的既有工作，避免互動式確認提示卡住無人值守的安裝
    流程）。回傳是否成功。
    """
    try:
        cmd = ["schtasks.exe", "/create", "/tn", task_name, "/tr", exe_path, "/sc", trigger, "/f"]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            cmd, creationflags=creationflags, timeout=30,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except Exception:
        return False


def remove_scheduled_task(task_name):
    """`schtasks /delete /tn <task_name> /f`。回傳是否成功。"""
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            ["schtasks.exe", "/delete", "/tn", task_name, "/f"], creationflags=creationflags, timeout=30,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except Exception:
        return False
