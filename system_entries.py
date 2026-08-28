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

    回傳值語義（F04）：「這個函式結束之後，目標是否確實不存在」，不是
    「這次有沒有刪到東西」。兩個 hive 都拋 FileNotFoundError 代表目標
    本來就不存在，對呼叫端而言結果與「剛剛才刪掉」相同，回傳 True。
    只有 FileNotFoundError 以外的例外（權限不足、機碼底下還有子機碼）
    才代表目標仍在、移除失敗，回傳 False——即使另一個 hive 的移除成功，
    先前那個 hive 的殘留項目仍然存在，整體不算成功。

    這個語義是 uninstall.py 把失敗清單接上使用者畫面的前提：舊語義下
    「本來就沒有這個項目」與「移除失敗」回傳同一個值，接上畫面後會讓
    使用者手動清過捷徑／登錄表項目這類正常情境變成假警告。
    """
    reg_path = f"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{app_name}"
    scope = InstallScope(no_admin_install, registry=registry)
    primary_hive = scope.registry_hive
    other_hive = registry.HKEY_CURRENT_USER if primary_hive == registry.HKEY_LOCAL_MACHINE else registry.HKEY_LOCAL_MACHINE
    removal_failed = False
    for hive in (primary_hive, other_hive):
        try:
            registry.DeleteKey(hive, reg_path)
            return not removal_failed
        except FileNotFoundError:
            continue
        except Exception:
            removal_failed = True
    return not removal_failed


def remove_shortcut(app_name, desktop=False, no_admin_install=False, registry=_real_winreg):
    """回傳值語義同 remove_registry_entry()：檔案本來就不存在（使用者
    自己刪過、或安裝當時捷徑建立就失敗過——installer_core.py 的
    `_create_shortcut()` 失敗是可忽略的設計）視為成功，只有實際刪除
    失敗才回傳 False。

    F12：兩個位置（所有使用者共用的 Public Desktop／ProgramData 開始功能表，
    與目前使用者自己的桌面／開始功能表）都試，理由跟
    remove_registry_entry() 的雙 hive 探測完全相同——manifest 裡的
    no_admin_install 可能跟當初實際安裝時用的模式不符。原本只認 manifest
    推導出的那一個目錄，捷徑實際建在另一個位置時完全找不到，永遠留在
    使用者的開始功能表裡。兩個位置都是「同名應用程式自己的捷徑」，擴大
    嘗試範圍不會波及其他應用程式。
    """
    removal_failed = False
    for scope_flag in (bool(no_admin_install), not bool(no_admin_install)):
        base = InstallScope(scope_flag, registry=registry).shortcut_dir(desktop=desktop)
        path = os.path.join(base, f"{app_name}.lnk")
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            removal_failed = True
    return not removal_failed


def remove_from_path(install_path, no_admin_install=False, registry=_real_winreg):
    """把 install_path 從 PATH 環境變數移除，回傳是否成功。

    回傳值（F02）：這個函式原本整段包在一個 try/except: pass 裡且不回傳
    任何值，uninstall.py 的 PATH 移除步驟因此無條件記錄成功，實際失敗
    完全沒有出口。改成回傳布林值，語義與 remove_registry_entry()／
    remove_shortcut() 一致——PATH 這個值或整個機碼不存在時，安裝路徑
    當然也不在裡面，視為成功。

    環境變數變更廣播（SendMessageTimeoutW）不影響回傳值：登錄表已經寫
    成功、PATH 實際上已經清掉了，廣播沒送出只影響「已開啟的視窗何時看到
    新的 PATH」，該廣播在既有修正中已定性為 best-effort。

    F12：機器層級與使用者層級兩個 hive 都試，理由跟 remove_registry_entry()
    的雙 hive 探測完全相同——manifest 裡的 no_admin_install 可能跟當初實際
    安裝時用的模式不符，PATH 實際寫在另一個 hive 時完全找不到。

    每個 hive 先用唯讀開啟探一次，確認 install_path 真的在裡面才用寫入
    權限重開：沒有這一步的話，一般權限執行的解除安裝會在「另一個 hive」
    （機器層級的 Environment）拿到 PermissionError，變成一個假的失敗回報
    ——那個 hive 裡本來就沒有東西要清。順帶讓這個函式變成冪等的：不會把
    內容完全一樣的值再寫回去一次。
    """
    removal_failed = False
    changed_any = False
    for scope_flag in (bool(no_admin_install), not bool(no_admin_install)):
        hive, sub_key = InstallScope(scope_flag, registry=registry).path_env_hive_and_key
        try:
            key = registry.OpenKey(hive, sub_key, 0, registry.KEY_READ)
            current, reg_type = registry.QueryValueEx(key, "Path")
            registry.CloseKey(key)
        except FileNotFoundError:
            # 機碼或 Path 這個值不存在：安裝路徑當然也不在裡面，沒事可做。
            continue
        except Exception:
            # 讀不到就無從判斷這裡有沒有殘留，如實回報失敗。
            removal_failed = True
            continue

        parts = [p for p in current.split(";") if p and os.path.normcase(p) != os.path.normcase(install_path)]
        new_value = ";".join(parts)
        if new_value == current:
            continue

        try:
            key = registry.OpenKey(hive, sub_key, 0, registry.KEY_ALL_ACCESS)
            registry.SetValueEx(key, "Path", 0, reg_type, new_value)
            registry.CloseKey(key)
            changed_any = True
        except Exception:
            removal_failed = True

    if changed_any:
        try:
            HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG = 0xFFFF, 0x1A, 0x0002
            result = ctypes.c_long()
            ctypes.windll.user32.SendMessageTimeoutW(
                HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", SMTO_ABORTIFHUNG, 5000, ctypes.byref(result)
            )
        except Exception:
            pass
    return not removal_failed


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
