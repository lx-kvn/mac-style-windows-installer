"""
system_entries.py
------------------
安裝/解除安裝流程共用的系統層級「移除」原語：解除安裝登錄表項目 / 捷徑 /
PATH 環境變數這三種登錄表寫入的移除，加上跟登錄表無關的兩個原語——清掉
刪檔後留下的空目錄（`cleanup_empty_dirs()`）、強制關閉正在執行的主程式
（`kill_process_by_name()`）。原本這幾個函式只活在 uninstall.py 裡（或
installer_core.py 各自重複一份幾乎相同的實作），現在收斂成獨立模組，讓
installer_core.py 安裝失敗時的 rollback、以及安裝/解除安裝流程都能呼叫
同一份實作，不用另外維護邏輯幾乎一樣的複本。

跟 file_assoc.py 用同一種 registry seam：`registry` 參數預設是真正的
winreg 模組；測試直接把 tests/_fakes.py 的 FakeWinReg 當參數傳進去，
不需要 monkeypatch sys.modules 或模組屬性。

hive/目錄的判斷收在 install_scope.InstallScope，跟 installer_core.py
共用同一份規則（no_admin_install 開啟時走使用者層級位置）。
"""
import ctypes
import os
import subprocess
import winreg as _real_winreg

from install_scope import InstallScope


def remove_registry_entry(app_name, no_admin_install=False, registry=_real_winreg):
    """真實抓到的 bug：no_admin_install 從 manifest 讀出來的值可能跟舊
    版本實際安裝時用的模式對不上（manifest 遺失這個欄位時 uninstall.py
    預設回退成 False，或 manifest 被手動編輯過）——原本只查衍生出來的
    單一 hive，真正的登錄表項目在另一個 hive 時完全找不到，DeleteKey
    失敗、留下永久殘留在「已安裝的應用程式」清單裡，且失敗還被吞掉、
    連 log 都沒有。改成跟 check_existing_install() 一樣雙 hive 都試：
    優先試 no_admin_install 衍生出來的那個，找不到才試另一個。
    """
    reg_path = f"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{app_name}"
    scope = InstallScope(no_admin_install, registry=registry)
    primary_hive = scope.registry_hive
    other_hive = registry.HKEY_CURRENT_USER if primary_hive == registry.HKEY_LOCAL_MACHINE else registry.HKEY_LOCAL_MACHINE
    for hive in (primary_hive, other_hive):
        try:
            registry.DeleteKey(hive, reg_path)
            return True
        except Exception:
            continue
    return False


def remove_shortcut(app_name, desktop=False, no_admin_install=False, registry=_real_winreg):
    base = InstallScope(no_admin_install, registry=registry).shortcut_dir(desktop=desktop)
    path = os.path.join(base, f"{app_name}.lnk")
    try:
        if os.path.exists(path):
            os.remove(path)
            return True
    except Exception:
        pass
    return False


def remove_from_path(install_path, no_admin_install=False, registry=_real_winreg):
    try:
        hive, sub_key = InstallScope(no_admin_install, registry=registry).path_env_hive_and_key
        key = registry.OpenKey(hive, sub_key, 0, registry.KEY_ALL_ACCESS)
        current, reg_type = registry.QueryValueEx(key, "Path")
        parts = [p for p in current.split(";") if p and os.path.normcase(p) != os.path.normcase(install_path)]
        registry.SetValueEx(key, "Path", 0, reg_type, ";".join(parts))
        registry.CloseKey(key)

        HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG = 0xFFFF, 0x1A, 0x0002
        result = ctypes.c_long()
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", SMTO_ABORTIFHUNG, 5000, ctypes.byref(result)
        )
    except Exception:
        pass


def cleanup_empty_dirs(root_dir):
    """清掉 root_dir 底下因為刪檔而變空的子目錄（由裡到外），root_dir
    本身如果也空了就一併刪除。installer_core.py 安裝失敗 rollback 跟
    uninstall.py 解除安裝流程都走這個共用邏輯。"""
    try:
        for root, dirs, files in os.walk(root_dir, topdown=False):
            for d in dirs:
                dpath = os.path.join(root, d)
                try:
                    if not os.listdir(dpath):
                        os.rmdir(dpath)
                except Exception:
                    pass
        if os.path.exists(root_dir) and not os.listdir(root_dir):
            os.rmdir(root_dir)
    except Exception:
        pass


def kill_process_by_name(exe_name):
    """強制關閉指定檔名的行程（只取檔名，忽略路徑），使用者在「偵測到
    主程式正在執行」的彈窗按下「關閉並繼續」時，安裝/解除安裝流程都會
    呼叫這個。

    回傳值檢查 taskkill 的 returncode，不是「呼叫沒拋例外就一律回傳
    True」——找不到目標程序時 taskkill 會用非 0 的 returncode 表示
    失敗（stderr 導到 DEVNULL），這裡如實反映有沒有真的成功。
    """
    if not exe_name:
        return False
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            ["taskkill", "/f", "/im", os.path.basename(exe_name)],
            creationflags=creationflags, timeout=10,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except Exception:
        return False
