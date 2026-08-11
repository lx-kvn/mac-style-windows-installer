"""
system_entries.py
------------------
解除安裝登錄表項目 / 捷徑 / PATH 環境變數這三種系統層級寫入的「移除」
原語。原本這幾個函式只活在 uninstall.py 裡（真正解除安裝時才會用到）；
現在收斂成獨立模組，讓 installer_core.py 安裝失敗時的 rollback 也能呼叫
同一份實作，清掉這次安裝已經寫入的部分，不用另外維護一份邏輯幾乎一樣
的複本。

跟 file_assoc.py 用同一種 registry seam：`registry` 參數預設是真正的
winreg 模組；測試直接把 tests/_fakes.py 的 FakeWinReg 當參數傳進去，
不需要 monkeypatch sys.modules 或模組屬性。

hive/目錄的判斷收在 install_scope.InstallScope，跟 installer_core.py
共用同一份規則（no_admin_install 開啟時走使用者層級位置）。
"""
import ctypes
import os
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
