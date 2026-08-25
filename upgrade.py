"""
upgrade.py
----------
覆蓋安裝（更新既有安裝）子系統：偵測已安裝的舊版本、備份、靜默呼叫舊版
uninstall.exe 移除、必要時跨 UAC 呼叫、失敗時把備份復原。

真實抓到的架構問題：這 8 個方法原本各自獨立掛在 InstallerAPI 上，靠
InstallerAPI 的 6 個共享實例屬性（selected_path/_scope/
_upgrade_backup_path/_upgrade_backup_original_path 等）互相傳遞狀態。
內部複雜度是真的（踩過三輪真實 bug：dual-hive 版本偵測、CreateProcess
不會觸發 UAC、pending-delete 競態，見下面各方法的說明），但介面是散落的
8 個方法，沒有任何東西擋住 InstallerAPI 其他方法隨意伸手進備份路徑狀態。
收斂成 UpgradeCoordinator：備份路徑狀態收進物件內部（`backup_path`/
`backup_original_path` 兩個屬性，供 InstallerAPI.close_window() 判斷
「這次安裝流程有沒有待復原的備份」），對外只需要
`check_existing()`/`run()`/`restore_backup()`/`discard_backup()` 這幾個
呼叫；跨 UAC 提權的細節（ShellExecuteExW 那一段 ctypes）完全收在內部，
呼叫端不需要知道實作用的是哪個 Windows API。
"""
import ctypes
import os
import shutil
import subprocess
import tempfile
import time

import version_compare


def check_existing(app_name, version, scope):
    """檢查是否已安裝過同名應用程式（讀取解除安裝登錄表），並比較版本新舊。

    三種互斥的結果，讓前端可以分別顯示對應的提示樣式：
      - is_newer：這次要裝的版本比已安裝的新（本機是舊版，該問「是否要更新」）。
      - is_same：版本完全一致（單純重裝，維持原本的提示樣式）。
      - is_older：這次要裝的版本比已安裝的舊（本機版本比較新，該用警示樣式，
        明確告知使用者要裝的版本比較舊，讓使用者自己決定要不要繼續）。

    真實抓到的 bug：原本只查這次設定（no_admin_install）算出來的單一
    hive，如果舊版本是用不同的 no_admin_install 設定裝的（例如舊版本
    裝在需要管理員權限的 HKLM，這次改用免權限設定重新打包），會完全
    查不到舊版本的登錄表紀錄，誤判成「沒裝過」，跳過「是否要更新」的
    提示。改成兩邊 hive 都查（優先查這次設定對應的 hive），找到的話
    額外回報是在哪個 hive 找到的（"HKLM"/"HKCU"），供 run() 判斷要不要
    跨 UAC 呼叫舊版解除安裝程式。
    """
    import winreg
    reg_path = f"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{app_name}"
    primary_hive = scope.registry_hive
    other_hive = winreg.HKEY_CURRENT_USER if primary_hive == winreg.HKEY_LOCAL_MACHINE else winreg.HKEY_LOCAL_MACHINE
    for hive in (primary_hive, other_hive):
        try:
            with winreg.OpenKey(hive, reg_path) as key:
                try:
                    install_loc, _ = winreg.QueryValueEx(key, "InstallLocation")
                except Exception:
                    # 連 InstallLocation 都沒有，這個機碼沒有可用的資訊，
                    # 換下一個 hive 試。
                    continue
                try:
                    old_version, _ = winreg.QueryValueEx(key, "DisplayVersion")
                except Exception:
                    # 真實抓到的 bug：DisplayVersion 缺值（手動建立的
                    # 登錄表項目、舊版打包工具留下的、或損毀的登錄表）
                    # 原本會讓這整個機碼被最外層的 bare except 一併當成
                    # 「這個 hive 沒有這個項目」處理，兩邊 hive 都試完
                    # 就回報「沒裝過」——明明 InstallLocation 都還在，
                    # 卻整個升級偵測被跳過，新舊兩份安裝並存。缺版本號
                    # 時當成「未知的舊版本」處理（一律視為需要更新），
                    # 而不是讓整個偵測失效。
                    old_version = "0.0.0"
                comparison = version_compare.compare_versions(version, old_version)
                return {
                    "exists": True,
                    "install_path": install_loc,
                    "version": old_version,
                    "new_version": version,
                    "is_newer": comparison > 0,
                    "is_same": comparison == 0,
                    "is_older": comparison < 0,
                    "hive": "HKLM" if hive == winreg.HKEY_LOCAL_MACHINE else "HKCU",
                }
        except Exception:
            continue
    return {"exists": False}


class UpgradeCoordinator:
    def __init__(self):
        # 更新覆蓋安裝的復原用備份：run() 靜默刪掉舊版本前，會把舊安裝
        # 資料夾複製到 backup_path；使用者事後取消，或新版本安裝失敗，
        # 都要能把這份備份搬回 backup_original_path，見 restore_backup()。
        self.backup_path = None
        self.backup_original_path = None

    def check_existing(self, app_name, version, scope):
        return check_existing(app_name, version, scope)

    def backup(self, install_path):
        """更新覆蓋安裝前，把舊安裝資料夾整份複製到暫存區。

        run() 接下來會靜默呼叫舊版本的 uninstall.exe 把這個資料夾整個
        刪掉，這份備份是唯一的復原機會：使用者事後取消、或這次新版本
        安裝失敗，都靠它把舊檔案搬回原位（見 restore_backup()）。

        失敗只回傳 None、不拋例外：沒有備份頂多是沒辦法復原，不該因此
        擋住合法的更新流程。

        修正紀錄（真實抓到的 bug）：原本用 os.environ.get("TEMP", ".")
        算暫存路徑，`.get()` 只有在 TEMP 這個環境變數整個不存在時才會用
        預設值 "."；如果它存在但是空字串（實測發生在某些提權執行的
        情境下，環境變數區塊沒有正確帶出 TEMP），會直接算出一個相對
        路徑，實際落點變成「這個安裝程式執行當下的工作目錄」——如果
        使用者剛好把新安裝檔放在舊安裝目錄本身執行更新，備份資料夾會被
        建立在 install_path 底下，變成 shutil.copytree() 對自己複製
        （複製到自己的子資料夾），越複製越亂，最後多半是拋例外收場，
        留下一個爛尾的子資料夾，而且完全沒有真的備份到東西。改用
        tempfile.gettempdir()：這是標準函式庫保證一定回傳真實存在、
        絕對路徑的系統暫存資料夾的做法，不會有空字串/相對路徑這種
        陷阱。另外加一道保險：算出來的路徑如果還是落在 install_path
        底下，直接拒絕備份，不要冒險對自己複製。
        """
        backup_path = os.path.join(
            tempfile.gettempdir(), f"mswi_upgrade_backup_{os.getpid()}",
        )
        install_path_abs = os.path.abspath(install_path)
        backup_path_abs = os.path.abspath(backup_path)
        if backup_path_abs == install_path_abs or backup_path_abs.startswith(install_path_abs + os.sep):
            return None
        try:
            if os.path.exists(backup_path):
                shutil.rmtree(backup_path, ignore_errors=True)
            shutil.copytree(install_path, backup_path)
            return backup_path
        except Exception:
            return None

    def restore_backup(self):
        """把 backup() 備份的舊安裝資料夾搬回原位。

        使用者在更新覆蓋安裝途中取消（InstallerAPI.close_window()），或
        這次新版本 trigger_installation() 失敗時呼叫，盡量讓系統回到
        更新前的狀態。沒有備份（例如備份當初就失敗、或這次根本不是更新
        流程）時是 no-op。
        """
        if not self.backup_path or not os.path.exists(self.backup_path):
            self.backup_path = None
            self.backup_original_path = None
            return
        try:
            if os.path.exists(self.backup_original_path):
                shutil.rmtree(self.backup_original_path, ignore_errors=True)
            shutil.move(self.backup_path, self.backup_original_path)
        except Exception:
            pass
        finally:
            self.backup_path = None
            self.backup_original_path = None

    def discard_backup(self):
        """新版本安裝成功後，備份已經沒用了，清掉暫存區避免留垃圾。"""
        if self.backup_path and os.path.exists(self.backup_path):
            shutil.rmtree(self.backup_path, ignore_errors=True)
        self.backup_path = None
        self.backup_original_path = None

    def wait_for_path_writable(self, path, timeout=10, interval=0.5):
        """更新覆蓋安裝後，安裝目標路徑理論上偶爾仍可能短暫卡在 Windows 的
        pending-delete 狀態（例如防毒軟體正在掃描剛被刪除/剛被觸碰的檔案、
        NTFS 中繼資料更新有些微延遲），這時候在同一個路徑建立新目錄會被
        系統擋下來、丟出 PermissionError，而且這跟是不是系統管理員身分
        完全無關（不是權限被拒絕，是那個目錄物件還沒真的從檔案系統移除）。

        這裡用短暫重試等它真的釋放，取代原本賭一個固定等待時間夠不夠的做法。
        重試期間遇到的 PermissionError 都是預期中的過渡狀態，吞掉繼續等即可；
        逾時還是不行，就不再攔，讓後面真正的複製流程去踢出原本的失敗處理
        （回滾 + 回報使用者）。

        修正紀錄：這裡原本的主要成因是舊版本 uninstall.exe 用背景、不等待
        （fire-and-forget）的 cmd.exe 延遲自我刪除、視情況把整個資料夾一起
        rmdir，run() 呼叫完全不保證那個背景流程已經真的跑完——這其實是個
        更嚴重的競態（那個背景 rmdir 事後觸發時會把新版本已經複製好的
        檔案一起砍掉，導致「安裝回報成功但檔案不完整」），已經改用
        --upgrade 命令列旗標請舊版 uninstall.exe 完全不排這段背景指令
        來根治（見 self_delete.py），不是靠這裡的重試等待解決。這個函式
        現在只需要處理殘餘的、影響小得多的單一檔案層級 pending-delete
        情況。
        """
        deadline = time.time() + timeout
        while True:
            try:
                os.makedirs(path, exist_ok=True)
                return
            except PermissionError:
                if time.time() >= deadline:
                    return
                time.sleep(interval)

    def is_current_process_elevated(self):
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    def run_uninstall_exe_elevated(self, uninstall_exe, args, timeout_ms=30000, shell32=None, kernel32=None):
        """透過 ShellExecuteExW + "runas" 動詞啟動舊版 uninstall.exe 並等待
        完成，取代 subprocess.run() 在需要跨 UAC 情境下的角色。

        真實抓到的問題：Windows 的 manifest 自動提權（跳 UAC 詢問）只有
        透過 ShellExecute 這條路徑才會被認得；subprocess.run() 底層是
        CreateProcess，不會觸發提權，而是直接用目前（未提權）行程的權杖
        把子行程跑起來——如果舊版 uninstall.exe 本身需要管理員權限（例如
        裝在 Program Files、登錄表寫在 HKLM），子行程會在寫入/刪除這些
        受保護的位置時默默失敗，卻不會拋出任何例外，看起來像是「正常
        執行完了」，實際上舊版本根本沒清乾淨。

        `shell32`/`kernel32` 選填注入點：預設用真正的
        `ctypes.windll.shell32`/`ctypes.windll.kernel32`，跟
        file_assoc.py/system_entries.py 的 `registry=` 是同一種 seam
        模式，只是這裡注入的是 shell32/kernel32 形狀的物件——測試可以
        換成假的「提權後行程」adapter，不需要透過 mock.patch 改寫
        `ctypes.windll` 這個行程全域共用物件的屬性。真實 UAC 互動本身
        仍然沒辦法在開發環境重現，這個 seam 只讓「成功／逾時／非 0
        回傳」這幾條分支變得可測。
        """
        shell32 = shell32 if shell32 is not None else ctypes.windll.shell32
        kernel32 = kernel32 if kernel32 is not None else ctypes.windll.kernel32

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

        params = " ".join(f'"{a}"' if " " in a else a for a in args)
        sei = SHELLEXECUTEINFOW()
        sei.cbSize = ctypes.sizeof(sei)
        sei.fMask = SEE_MASK_NOCLOSEPROCESS
        sei.hwnd = None
        sei.lpVerb = "runas"
        sei.lpFile = uninstall_exe
        sei.lpParameters = params
        sei.lpDirectory = None
        sei.nShow = SW_HIDE

        ok = shell32.ShellExecuteExW(ctypes.pointer(sei))
        if not ok:
            raise OSError("無法以系統管理員權限啟動舊版解除安裝程式（使用者可能取消了 UAC 提示）。")

        # 真實抓到的問題：SEE_MASK_NOCLOSEPROCESS 模式下，如果沒有真的
        # 產生一個行程（hProcess 是 NULL），WaitForSingleObject(NULL, ...)
        # 實際上會回傳 WAIT_FAILED，不是這裡原本唯一判斷的 WAIT_TIMEOUT，
        # 會被誤判成「等待成功」繼續往下跑，而不是明確的錯誤。
        if not sei.hProcess:
            raise OSError("啟動舊版解除安裝程式失敗：沒有取得有效的行程控制代碼。")

        try:
            wait_result = kernel32.WaitForSingleObject(sei.hProcess, timeout_ms)
            if wait_result == WAIT_TIMEOUT:
                raise TimeoutError("舊版解除安裝程式執行逾時。")
            # 真實抓到的問題（B6）：結束碼原本完全沒有被檢查——等到行程
            # 結束就直接視為成功，不管它實際上是不是真的執行成功。跟
            # uninstall.py 自己的慣例一致：0=成功、非 0=失敗。
            exit_code = ctypes.c_ulong(0)
            kernel32.GetExitCodeProcess(sei.hProcess, ctypes.pointer(exit_code))
            if exit_code.value != 0:
                raise RuntimeError(f"舊版解除安裝程式回報失敗（結束碼 {exit_code.value}）。")
        finally:
            kernel32.CloseHandle(sei.hProcess)

    def run(self, app_name, version, scope, selected_path, restart_explorer_on_update):
        """更新覆蓋安裝流程：先備份舊安裝資料夾，再靜默呼叫舊版本的解除安裝
        助手移除乾淨，之後才繼續安裝新版本。

        修正紀錄（真實抓到的 bug）：這裡呼叫的是「舊版本」的 uninstall.exe，
        它是否會在刪除前關閉檔案總管，原本完全依賴舊版本自己那份
        install_manifest.json 裡的 restart_explorer_on_update 欄位——這個欄位
        記錄的是「舊版本被安裝當下」的打包設定，跟「這次重新打包、使用者剛
        勾選的新設定」是兩回事，新設定完全影響不到「拿舊版本的 uninstall.exe
        來移除它自己」這個當下的行為。實測會導致「時好時壞」：每次安裝嘗試
        （不論成功失敗）都可能在磁碟上留下不同版本的 install_manifest.json，
        這次移除到底會不會關檔案總管，取決於上一輪剛好留下哪份 manifest，
        不是使用者這次的選擇。修正做法：把這次（新版本）的
        restart_explorer_on_update 設定透過命令列參數明確傳給舊版
        uninstall.exe，覆蓋掉它自己那份可能過期的 manifest 設定
        （見 uninstall.py 對 --restart-explorer 的處理）。

        修正紀錄（真實抓到的 bug，導致「安裝回報成功但檔案沒有複製完整」，
        只發生在更新覆蓋安裝）：呼叫舊版 uninstall.exe 時一律加上 --upgrade
        旗標，讓它跳過尾端那段延遲執行、不等待的背景自我刪除／整個資料夾
        rmdir 指令——那段指令原本會在這裡的 subprocess.run() 已經返回、
        新版本已經開始複製檔案之後的某個時間點才真正觸發，如果複製時間
        跨過那個延遲視窗，會把整個資料夾（含新複製好的檔案）一起砍掉。
        詳細根因見 self_delete.py。

        已知限制：如果目前安裝的舊版本本身是用更早、還沒有 --upgrade 支援
        的舊版 uninstall.exe，這個旗標對它沒有意義（舊版 exe 根本不認得
        這個參數，會被當成一般未知引數忽略，仍然照舊排出那段有競態風險的
        背景自我刪除指令）——跟 --restart-explorer 的已知限制是同一類情況，
        第一次從這麼舊的版本更新可能仍會遇到這個問題，等新版本安裝完成、
        往後再次更新時才會是「新版本呼叫新版本」，這個修正才能確實生效。
        """
        info = self.check_existing(app_name, version, scope)
        if not info.get("exists"):
            return {"status": "skipped"}

        install_path = info["install_path"]
        uninstall_exe = os.path.join(install_path, "uninstall.exe")
        if not os.path.exists(uninstall_exe):
            return {"status": "error", "message": "找不到舊版本的解除安裝程式，請先手動移除舊版本後再安裝。"}

        # 真實抓到的安全性問題：HKCU 是一般使用者身分就寫得進去的登錄表
        # 位置。如果目前這個安裝程式行程已經持有系統管理員權杖，執行從
        # HKCU 找到的 uninstall.exe——不管是透過 subprocess.run 還是
        # ShellExecute，子行程都會繼承呼叫端目前的權杖——等於讓任何能以
        # 同一個使用者身分寫入 HKCU 的人，都能讓這支已提權的安裝程式代為
        # 執行任意程式碼，還帶著系統管理員權限。這個組合（hive=HKCU 且
        # 目前行程已經提權）沒有正當情境會出現：正常的免權限安裝在免權限
        # 行程底下執行；只有攻擊者刻意偽造 HKCU 項目、剛好又遇到使用者
        # 這次改用需要管理員權限的方式重新打包，才會踩進這個組合。直接
        # 拒絕自動執行，不嘗試用簽章或路徑白名單這類還沒有基礎建設支撐
        # 的方式去區分「這個 HKCU 項目到底可不可信」。
        if info.get("hive") == "HKCU" and self.is_current_process_elevated():
            return {
                "status": "error",
                "message": "偵測到舊版本的登錄表項目位於使用者層級（HKCU），但目前安裝程式"
                           "正以系統管理員權限執行。為了安全，不會自動代為執行來源不受信任的"
                           "舊版解除安裝程式，請先手動移除舊版本後再安裝。",
            }

        self.backup_original_path = install_path
        self.backup_path = self.backup(install_path)

        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            # --upgrade：告訴舊版 uninstall.exe 這是更新覆蓋安裝呼叫的，
            # 不要排出它尾端那段延遲執行的背景自我刪除／整個資料夾 rmdir
            # 指令，避免那段非同步指令事後把這次新複製的檔案一起砍掉
            # （見 wait_for_path_writable() docstring 與 self_delete.py）。
            args = ["--silent", "--upgrade"]
            if restart_explorer_on_update:
                args.append("--restart-explorer")
            # 舊版本裝在需要管理員權限的位置（登錄表在 HKLM），但目前這個
            # 行程本身沒有提權：subprocess.run() 不會觸發 UAC，直接呼叫會
            # 因為權限不足而默默失敗。改用 ShellExecuteExW + "runas" 跨 UAC
            # 呼叫（見 run_uninstall_exe_elevated() 的說明）。
            if info.get("hive") == "HKLM" and not self.is_current_process_elevated():
                self.run_uninstall_exe_elevated(uninstall_exe, args, timeout_ms=30000)
            else:
                # 真實抓到的問題（B6）：這裡的回傳值原本連變數都沒接，
                # 舊版 uninstall.exe 的結束碼完全被忽略。uninstall.py 自己
                # 的慣例是 0=成功、非 0=失敗（見 run_silent_uninstall()）——
                # 舊版本如果因為檔案被鎖住、manifest 損毀等原因回報失敗，
                # 這裡完全偵測不到，會誤以為舊版本已經清乾淨，繼續往下
                # 覆蓋安裝，實際上舊版本殘留的檔案可能還在。
                result = subprocess.run([uninstall_exe] + args, timeout=30, creationflags=creationflags)
                if result.returncode != 0:
                    raise RuntimeError(f"舊版解除安裝程式回報失敗（結束碼 {result.returncode}）。")
            self.wait_for_path_writable(selected_path)
            return {"status": "success"}
        except Exception as e:
            self.restore_backup()
            return {"status": "error", "message": f"移除舊版本失敗: {e}"}
