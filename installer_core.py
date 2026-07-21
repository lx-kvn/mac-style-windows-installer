"""
installer_core.py
------------------
主安裝檔（打包後的 exe）內部執行的安裝邏輯。

這一輪新增的「安裝精靈該有的步驟」：
  - 單一實例鎖（Mutex）：避免使用者手滑點兩次，同時跑兩個安裝流程互相干擾。
  - 覆蓋安裝偵測：透過登錄表判斷是否已裝過，讓前端跳出「更新覆蓋 / 取消」選擇。
  - 磁碟空間檢查：裝之前先確認目標磁碟剩餘空間夠不夠。
  - 相依元件偵測（VC++ Redist / .NET Desktop Runtime）：只做偵測 + 提示官方下載連結，
    不做靜默安裝（那需要額外綁定官方安裝檔，風險與體積都大，先不做）。
  - 主程式執行中偵測：避免複製到一半被檔案鎖定卡住。
  - 真實複製進度：以「已複製檔案數 / 總檔案數」回報百分比，取代原本的假文字。
  - 複製後完整性驗證：比對來源與目的地檔案大小是否一致。
  - 桌面捷徑（安裝端小勾選）、開始功能表捷徑（沿用上一輪）。
  - 檔案關聯、加入環境變數 PATH（依製作工具端的設定執行）。
  - 安裝紀錄 log 檔（install_log.txt）。
  - 安裝清單（install_manifest.json）：記錄實際安裝了哪些檔案/捷徑/登錄項目，
    讓解除安裝可以「照清單刪」而不是整個資料夾血洗，對應 uninstall.py 的重寫。
  - 安裝完成可勾選「立即執行程式」（安裝端前端呼叫 launch_app()）。
"""

import os
import sys
import json
import shutil
import time
import ctypes
import subprocess
import zlib
import webview
from datetime import datetime


def _file_checksum(path, chunk_size=1024 * 1024):
    """算檔案的 CRC32 checksum，用來做複製後的完整性驗證。

    用 CRC32 而不是 MD5/SHA256：這裡的目的只是抓「複製過程中檔案是不是壞掉了」，
    不是防止惡意竄改，不需要密碼學等級的雜湊，CRC32 快很多，安裝一個內容量大的
    軟體時，多一輪讀檔驗證不會拖太久。
    """
    crc = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            crc = zlib.crc32(chunk, crc)
    return crc


def get_resource_path(relative_path):
    """獲取資源絕對路徑，完美相容 PyInstaller 單一檔案打包環境"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


# ---------------------------------------------------------------------------
# 相依元件偵測：只做「登錄表存在與否」的簡易判斷，非百分之百精準，
# 但足以提醒使用者「可能缺少某個執行環境」。
# ---------------------------------------------------------------------------

def _check_vcredist_x64():
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64")
        val, _ = winreg.QueryValueEx(key, "Installed")
        return val == 1
    except Exception:
        return False


def _check_dotnet_desktop():
    import winreg
    base = r"SOFTWARE\WOW6432Node\dotnet\Setup\InstalledVersions\x64\sharedfx\Microsoft.WindowsDesktop.App"
    try:
        winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
        return True
    except Exception:
        return False


DEPENDENCY_CHECKERS = {
    "vcredist_x64": (_check_vcredist_x64, "Visual C++ Redistributable (x64)", "https://aka.ms/vs/17/release/vc_redist.x64.exe"),
    "dotnet_desktop": (_check_dotnet_desktop, ".NET Desktop Runtime", "https://dotnet.microsoft.com/download/dotnet"),
}


def _is_process_running(exe_name):
    """用 tasklist 檢查指定檔名的行程是否正在執行

    修正紀錄：shell=True 會透過 cmd.exe 執行指令，在 --noconsole 的 GUI 程式裡
    呼叫會短暫跳出一個命令提示字元視窗。加上 CREATE_NO_WINDOW 徹底避免。
    """
    if not exe_name:
        return False
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        output = subprocess.check_output(
            f'tasklist /FI "IMAGENAME eq {exe_name}" /NH',
            shell=True, text=True, stderr=subprocess.DEVNULL, creationflags=creationflags,
        )
        return exe_name.lower() in output.lower()
    except Exception:
        return False


def _parse_version(v):
    """把版本字串拆成數字 tuple，例如 "1.10.2" -> (1, 10, 2)，
    這樣才能正確比較「1.10.0 > 1.2.0」，單純字串比較會誤判成 1.10.0 < 1.2.0。
    非數字的部分（例如 "1.0.0-beta"）只取數字部分，忽略後綴。
    """
    parts = []
    for p in str(v).split('.'):
        digits = ''.join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _compare_versions(v1, v2):
    """回傳 1 表示 v1 > v2，0 表示相等，-1 表示 v1 < v2"""
    t1, t2 = _parse_version(v1), _parse_version(v2)
    length = max(len(t1), len(t2))
    t1 = t1 + (0,) * (length - len(t1))
    t2 = t2 + (0,) * (length - len(t2))
    if t1 > t2:
        return 1
    if t1 < t2:
        return -1
    return 0


def _acquire_single_instance_lock(app_name):
    """建立具名 Mutex，回傳 True 表示成功取得鎖（沒有其他安裝程式實例在跑）"""
    mutex_name = f"Global\\{app_name}_installer_mutex"
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
    already_running = ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
    return (not already_running), handle


class InstallerAPI:
    def __init__(self):
        self.app_name = "應用程式"
        self.folder_name = ""
        self.version = "1.0.0"
        self.publisher = "Unknown"
        self.main_exe = ""
        self.eula_text = ""
        self.dependencies = []
        self.file_associations = []
        self.doc_icon = ""
        self.add_to_path = False

        program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
        self.default_path = os.path.join(program_files, self.app_name)
        self.selected_path = self.default_path
        self.load_config()
        # default_path 改用 folder_name（安裝路徑用的名稱，建議英數字），
        # 不再用 app_name（顯示名稱，可能是中文）組路徑，兩者職責分開。
        # folder_name 沒填的話 load_config() 已經 fallback 成 app_name，行為不變。
        self.default_path = os.path.join(program_files, self.folder_name or self.app_name)
        self.selected_path = self.default_path
        self._drag_origin = None

    def start_drag(self, cursor_x, cursor_y):
        """自訂拖曳開始：記錄按下當下的滑鼠螢幕座標與視窗當下座標，作為位移量的計算基準。

        不用 pywebview 內建的 pywebview-drag-region：那個機制在拖曳開始瞬間會讓視窗
        往左上方跳一下才跟上游標，100% 縮放下也會發生，判斷是機制本身的問題。
        改成完全自己算位移量、呼叫 window.move()，徹底繞開這個問題。
        """
        global window
        if window:
            self._drag_origin = (cursor_x, cursor_y, window.x, window.y)

    def drag_move(self, cursor_x, cursor_y):
        """拖曳中：用目前滑鼠螢幕座標相對於按下當下的位移量搬動視窗。"""
        global window
        if window and self._drag_origin:
            start_cx, start_cy, start_wx, start_wy = self._drag_origin
            dx = cursor_x - start_cx
            dy = cursor_y - start_cy
            window.move(start_wx + dx, start_wy + dy)

    def end_drag(self):
        """拖曳結束：清掉基準點。"""
        self._drag_origin = None

    def load_config(self):
        try:
            config_path = get_resource_path("installer_config.json")
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    self.app_name = config.get("app_name", self.app_name)
                    self.folder_name = config.get("folder_name", "") or self.app_name
                    self.version = config.get("version", self.version)
                    self.publisher = config.get("publisher", self.publisher)
                    self.main_exe = config.get("main_exe", "")
                    self.eula_text = config.get("eula_text", "")
                    self.dependencies = config.get("dependencies", [])
                    self.file_associations = config.get("file_associations", [])
                    self.doc_icon = config.get("doc_icon", "")
                    self.add_to_path = bool(config.get("add_to_path", False))
        except Exception as e:
            print(f"[提示] 使用預設開發模式: {e}")

    # ------------------------------------------------------------------
    # 供前端查詢的基本資訊
    # ------------------------------------------------------------------

    def get_app_name(self):
        return self.app_name

    def get_default_path(self):
        return self.default_path

    def get_eula_text(self):
        return self.eula_text

    def get_dependency_warnings(self):
        """回傳目前系統缺少的相依元件清單（顯示名稱 + 下載連結），不阻擋安裝"""
        warnings = []
        for key in self.dependencies:
            checker = DEPENDENCY_CHECKERS.get(key)
            if not checker:
                continue
            check_fn, display_name, url = checker
            if not check_fn():
                warnings.append({"name": display_name, "url": url})
        return warnings

    def open_url(self, url):
        """讓前端可以開啟預設瀏覽器前往下載頁"""
        try:
            import webbrowser
            webbrowser.open(url)
            return True
        except Exception:
            return False

    def check_existing_install(self):
        """檢查是否已安裝過同名應用程式（讀取解除安裝登錄表），並比較版本新舊。

        原本只檢查「有沒有裝過」，現在加上版本比對：is_newer 為 True 代表這次要裝的
        版本比已安裝的新（該問「是否更新」）；is_same_or_older 為 True 代表這次要裝的
        版本跟已安裝的一樣新或更舊（該提示使用者「目前安裝的版本比較新/一樣新」，
        而不是照舊講「有更新可以裝」這種容易誤導的話）。
        """
        import winreg
        reg_path = f"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{self.app_name}"
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path) as key:
                install_loc, _ = winreg.QueryValueEx(key, "InstallLocation")
                old_version, _ = winreg.QueryValueEx(key, "DisplayVersion")
                comparison = _compare_versions(self.version, old_version)
                return {
                    "exists": True,
                    "install_path": install_loc,
                    "version": old_version,
                    "new_version": self.version,
                    "is_newer": comparison > 0,
                    "is_same_or_older": comparison <= 0,
                }
        except Exception:
            return {"exists": False}

    def run_upgrade_uninstall(self):
        """更新覆蓋安裝流程：靜默呼叫舊版本的解除安裝助手，移除乾淨後再繼續安裝"""
        info = self.check_existing_install()
        if not info.get("exists"):
            return {"status": "skipped"}

        uninstall_exe = os.path.join(info["install_path"], "uninstall.exe")
        if not os.path.exists(uninstall_exe):
            return {"status": "error", "message": "找不到舊版本的解除安裝程式，請先手動移除舊版本後再安裝。"}

        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.run([uninstall_exe, "--silent"], timeout=30, creationflags=creationflags)
            # uninstall.exe 結束後會用背景指令延遲刪除自己所在資料夾，
            # 這裡多等一下讓那個背景流程有時間跑完，避免馬上寫入同一個路徑時互相搶檔案。
            time.sleep(3)
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "message": f"移除舊版本失敗: {e}"}

    def select_folder(self):
        global window
        try:
            res = window.create_file_dialog(webview.FOLDER_DIALOG)
            if res and len(res) > 0:
                self.selected_path = res[0]
                return self.selected_path
            return None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # 安裝流程輔助函式
    # ------------------------------------------------------------------

    def _report_progress(self, percent, message):
        global window
        safe_msg = json.dumps(message, ensure_ascii=False)
        try:
            if window:
                window.evaluate_js(f"window.updateInstallProgress({percent}, {safe_msg})")
        except Exception:
            pass

    def _required_size(self):
        src = get_resource_path("app_contents")
        total = 0
        for root, dirs, files in os.walk(src):
            for f in files:
                total += os.path.getsize(os.path.join(root, f))
        return total

    def _check_disk_space(self):
        required = self._required_size()
        drive = os.path.splitdrive(self.selected_path)[0] or os.path.splitdrive(self.default_path)[0] or "C:"
        usage = shutil.disk_usage(drive + "\\")
        # 保留 10% 緩衝空間
        return usage.free >= required * 1.1, usage.free, required

    def _register_uninstall_entry(self):
        import winreg
        uninstall_exe = os.path.join(self.selected_path, "uninstall.exe")
        main_exe_path = os.path.join(self.selected_path, self.main_exe) if self.main_exe else uninstall_exe
        reg_path = f"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{self.app_name}"
        try:
            estimated_size_kb = self._required_size() // 1024
        except Exception:
            estimated_size_kb = 0
        try:
            with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, reg_path) as key:
                winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, self.app_name)
                winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, f'"{uninstall_exe}"')
                # QuietUninstallString：Windows「設定 > 已安裝的應用程式」偵測到這個欄位時，
                # 會優先用它做靜默解除安裝（例如企業用 MDM/群組原則批次移除），
                # 沒有這個欄位的話，系統只能呼叫一般的 UninstallString，會跳出確認視窗。
                winreg.SetValueEx(key, "QuietUninstallString", 0, winreg.REG_SZ, f'"{uninstall_exe}" --silent')
                winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, self.selected_path)
                winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, self.publisher)
                winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, self.version)
                winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, main_exe_path)
                winreg.SetValueEx(key, "InstallDate", 0, winreg.REG_SZ, datetime.now().strftime("%Y%m%d"))
                winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, estimated_size_kb)
                winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
        except Exception as e:
            print(f"[警告] 寫入解除安裝登錄表失敗: {e}")

    def _create_shortcut(self, desktop=False):
        """建立開始功能表或桌面捷徑（依賴 pywin32，未安裝時靜默略過）"""
        if not self.main_exe:
            return False
        try:
            import win32com.client

            if desktop:
                base = "C:\\Users\\Public\\Desktop"
            else:
                base = os.path.join(
                    os.environ.get("ProgramData", "C:\\ProgramData"),
                    "Microsoft", "Windows", "Start Menu", "Programs",
                )
            os.makedirs(base, exist_ok=True)
            shortcut_path = os.path.join(base, f"{self.app_name}.lnk")
            main_exe_path = os.path.join(self.selected_path, self.main_exe)

            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortCut(shortcut_path)
            shortcut.Targetpath = main_exe_path
            shortcut.WorkingDirectory = os.path.dirname(main_exe_path)
            shortcut.IconLocation = main_exe_path
            shortcut.save()
            return True
        except Exception as e:
            print(f"[提示] 未建立捷徑（可忽略）: {e}")
            return False

    def _register_file_associations(self):
        import winreg
        if not self.file_associations or not self.main_exe:
            return
        main_exe_path = os.path.join(self.selected_path, self.main_exe)
        # 文件圖示：有自訂就指向安裝時複製過去的那顆 ico，沒有就直接沿用主程式圖示。
        # 原本這裡完全沒寫 DefaultIcon，檔案總管會顯示 Windows 給「不知道用什麼
        # 圖示」的檔案類型的通用預設圖示，不是預期的樣子。
        if self.doc_icon:
            icon_ref = os.path.join(self.selected_path, self.doc_icon)
        else:
            icon_ref = f"{main_exe_path},0"
        for ext in self.file_associations:
            prog_id = f"AppFile{ext.replace('.', '')}"
            try:
                with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, f"Software\\Classes\\{ext}") as key:
                    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, prog_id)
                with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, f"Software\\Classes\\{prog_id}") as key:
                    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f"{self.app_name} File")
                with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, f"Software\\Classes\\{prog_id}\\shell\\open\\command") as key:
                    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{main_exe_path}" "%1"')
                with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, f"Software\\Classes\\{prog_id}\\DefaultIcon") as key:
                    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, icon_ref)
            except Exception as e:
                print(f"[警告] 註冊檔案關聯 {ext} 失敗: {e}")
        try:
            ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0, None, None)  # SHCNE_ASSOCCHANGED
        except Exception:
            pass

    def _add_to_path_env(self):
        import winreg
        try:
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                0, winreg.KEY_ALL_ACCESS,
            )
            try:
                current, reg_type = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                current, reg_type = "", winreg.REG_EXPAND_SZ
            parts = [p for p in current.split(";") if p]
            if not any(os.path.normcase(p) == os.path.normcase(self.selected_path) for p in parts):
                parts.append(self.selected_path)
                winreg.SetValueEx(key, "Path", 0, reg_type, ";".join(parts))
            winreg.CloseKey(key)

            HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG = 0xFFFF, 0x1A, 0x0002
            result = ctypes.c_long()
            ctypes.windll.user32.SendMessageTimeoutW(
                HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", SMTO_ABORTIFHUNG, 5000, ctypes.byref(result)
            )
        except Exception as e:
            print(f"[警告] 加入環境變數 PATH 失敗: {e}")

    # ------------------------------------------------------------------
    # 主安裝流程
    # ------------------------------------------------------------------

    def _rollback(self, copied_rel_paths, log=None):
        """安裝失敗時的回滾：把這次安裝已經複製出去的檔案清掉，盡量讓系統回到
        安裝前的乾淨狀態。只清掉『這次安裝自己複製出去的檔案』，不會動到
        selected_path 底下其他既有內容（例如使用者選了一個已經有東西的資料夾）。
        """
        removed = 0
        for rel in copied_rel_paths:
            try:
                path = os.path.join(self.selected_path, rel)
                if os.path.exists(path):
                    os.remove(path)
                    removed += 1
            except Exception:
                pass
        # 清掉因此變空的子目錄（由裡到外）
        try:
            for root, dirs, files in os.walk(self.selected_path, topdown=False):
                for d in dirs:
                    dpath = os.path.join(root, d)
                    try:
                        if not os.listdir(dpath):
                            os.rmdir(dpath)
                    except Exception:
                        pass
            if os.path.exists(self.selected_path) and not os.listdir(self.selected_path):
                os.rmdir(self.selected_path)
        except Exception:
            pass
        if log:
            log(f"安裝失敗，已回滾刪除 {removed} 個已複製的檔案")

    def trigger_installation(self, create_desktop_shortcut=True):
        log_lines = [f"=== {self.app_name} 安裝紀錄 {datetime.now().isoformat()} ==="]
        copied_rel_paths = []  # 提前宣告：任何階段失敗都要能安全參照這個變數做回滾

        def log(msg):
            log_lines.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

        try:
            src_dir = get_resource_path("app_contents")
            if not os.path.exists(src_dir):
                return {"status": "error", "message": "安裝失敗：找不到內建軟體資源！"}

            # 磁碟空間檢查
            ok, free, required = self._check_disk_space()
            if not ok:
                return {
                    "status": "error",
                    "message": f"磁碟空間不足：本次安裝約需 {required // (1024 * 1024)} MB，"
                                f"目標磁碟剩餘 {free // (1024 * 1024)} MB。",
                }
            log(f"磁碟空間檢查通過（需要約 {required // (1024 * 1024)} MB）")

            # 主程式執行中偵測
            if self.main_exe and _is_process_running(os.path.basename(self.main_exe)):
                return {
                    "status": "error",
                    "message": f"安裝失敗：偵測到「{self.main_exe}」正在執行，請先關閉程式後再安裝。",
                }

            if not os.path.exists(self.selected_path):
                os.makedirs(self.selected_path)
            log(f"安裝目標路徑: {self.selected_path}")

            # 收集要複製的檔案清單（先算總數，才能算出真實百分比）
            file_list = []
            for root, dirs, files in os.walk(src_dir):
                for f in files:
                    file_list.append(os.path.relpath(os.path.join(root, f), src_dir))

            total = len(file_list)
            if total == 0:
                return {"status": "error", "message": "安裝失敗：打包的資源資料夾是空的。"}

            integrity_errors = []
            last_reported = -1
            for i, rel in enumerate(file_list):
                src_f = os.path.join(src_dir, rel)
                dest_f = os.path.join(self.selected_path, rel)
                os.makedirs(os.path.dirname(dest_f) or self.selected_path, exist_ok=True)
                shutil.copy2(src_f, dest_f)

                # 完整性驗證：先比大小（快），大小一致才進一步比 checksum（較慢但更可靠，
                # 抓得出「大小剛好一樣但內容其實壞掉」這種 size 比對抓不到的情況）。
                if os.path.getsize(src_f) != os.path.getsize(dest_f):
                    integrity_errors.append(rel)
                elif _file_checksum(src_f) != _file_checksum(dest_f):
                    integrity_errors.append(rel)
                copied_rel_paths.append(rel)

                percent = int((i + 1) / total * 80)  # 複製階段佔整體流程的 0-80%
                if percent != last_reported:
                    self._report_progress(percent, f"正在複製檔案 ({i + 1}/{total})...")
                    last_reported = percent

            if integrity_errors:
                log(f"完整性驗證失敗的檔案: {integrity_errors}")
                self._rollback(copied_rel_paths, log)
                return {
                    "status": "error",
                    "message": f"安裝失敗：{len(integrity_errors)} 個檔案複製後驗證不通過，"
                               f"安裝資源可能已損壞。已自動清除本次安裝複製的檔案。",
                }
            log(f"已複製 {len(copied_rel_paths)} 個檔案，完整性驗證通過")

            # 複製反安裝助手與設定檔
            self._report_progress(85, "正在寫入設定與解除安裝助手...")
            uninstall_src = get_resource_path("uninstall.exe")
            if os.path.exists(uninstall_src):
                shutil.copy2(uninstall_src, os.path.join(self.selected_path, "uninstall.exe"))
                copied_rel_paths.append("uninstall.exe")

            config_src = get_resource_path("installer_config.json")
            if os.path.exists(config_src):
                shutil.copy2(config_src, os.path.join(self.selected_path, "installer_config.json"))
                copied_rel_paths.append("installer_config.json")

            if self.doc_icon:
                doc_icon_src = get_resource_path(self.doc_icon)
                if os.path.exists(doc_icon_src):
                    shutil.copy2(doc_icon_src, os.path.join(self.selected_path, self.doc_icon))
                    copied_rel_paths.append(self.doc_icon)
                else:
                    log(f"[警告] 找不到內嵌的文件圖示 {self.doc_icon}，檔案關聯將沿用主程式圖示。")
                    self.doc_icon = ""

            # 登錄表 + 捷徑 + 檔案關聯 + PATH
            self._report_progress(90, "正在註冊系統項目...")
            self._register_uninstall_entry()
            self._create_shortcut(desktop=False)
            if create_desktop_shortcut:
                self._create_shortcut(desktop=True)
            if self.file_associations:
                self._register_file_associations()
                log(f"已註冊檔案關聯: {self.file_associations}")
            if self.add_to_path:
                self._add_to_path_env()
                log("已加入環境變數 PATH")

            # 寫入安裝清單，供解除安裝時「照清單刪」使用
            self._report_progress(97, "正在寫入安裝紀錄...")
            manifest = {
                "app_name": self.app_name,
                "version": self.version,
                "publisher": self.publisher,
                "main_exe": self.main_exe,
                "install_path": self.selected_path,
                "files": copied_rel_paths + ["install_manifest.json", "install_log.txt"],
                "desktop_shortcut": bool(create_desktop_shortcut),
                "start_menu_shortcut": True,
                "file_associations": self.file_associations,
                "path_added": self.add_to_path,
                "installed_at": datetime.now().isoformat(),
            }
            with open(os.path.join(self.selected_path, "install_manifest.json"), "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)

            with open(os.path.join(self.selected_path, "install_log.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(log_lines))

            self._report_progress(100, "安裝完成")

            main_exe_path = os.path.join(self.selected_path, self.main_exe) if self.main_exe else ""
            return {"status": "success", "message": "安裝成功", "main_exe_path": main_exe_path}

        except PermissionError:
            self._rollback(copied_rel_paths, log)
            return {"status": "error", "message": "安裝失敗：權限不足。請安裝到桌面或 D 槽，或以管理員身分執行。"}
        except Exception as e:
            self._rollback(copied_rel_paths, log)
            return {"status": "error", "message": f"發生未知錯誤：\n{str(e)}"}

    def launch_app(self):
        """安裝完成後「立即執行程式」"""
        if not self.main_exe:
            return False
        try:
            main_exe_path = os.path.join(self.selected_path, self.main_exe)
            subprocess.Popen([main_exe_path], cwd=os.path.dirname(main_exe_path))
            return True
        except Exception as e:
            print(f"[警告] 啟動程式失敗: {e}")
            return False

    def close_window(self):
        global window
        window.destroy()


def _show_starting_cursor():
    """讓滑鼠游標在視窗還沒開出來之前，顯示 Windows 內建的「載入中」游標
    （箭頭 + 小圈圈，IDC_APPSTARTING），取代原本考慮過的完整 splash 視窗。
    使用者要求安裝檔本身盡量簡潔，這是成本最低、干擾最小的做法：
    不開額外視窗，游標本身就是回饋，視窗一出現、滑鼠移到視窗上就會被
    pywebview 原生游標接管，不需要另外寫程式碼去關掉它。

    只能從「Python 程式碼開始執行」這一刻起生效：PyInstaller onefile
    自我解壓縮那一小段時間（Python 都還沒開始跑）沒辦法覆蓋到，
    這點跟先前 splash.py 遇到的限制是一樣的道理。
    """
    try:
        IDC_APPSTARTING = 32650
        hcursor = ctypes.windll.user32.LoadCursorW(None, IDC_APPSTARTING)
        ctypes.windll.user32.SetCursor(hcursor)
    except Exception:
        pass


def _parse_cli_args():
    """解析命令列參數，給企業批次部署用的靜默安裝模式。

    支援的參數（不分大小寫）：
        /S, /SILENT, /QUIET     靜默模式，不開任何視窗
        /D=路徑 或 /DIR=路徑     指定安裝路徑（覆蓋預設的 Program Files 路徑）
        /NODESKTOPSHORTCUT       靜默安裝時不要建立桌面捷徑（預設會建立）

    回傳 (silent: bool, install_dir: str|None, create_desktop_shortcut: bool)
    """
    silent = False
    install_dir = None
    create_desktop_shortcut = True
    for raw_arg in sys.argv[1:]:
        arg = raw_arg.strip()
        upper = arg.upper()
        if upper in ("/S", "/SILENT", "/QUIET", "/VERYSILENT"):
            silent = True
        elif upper.startswith("/D="):
            install_dir = arg[3:].strip('"')
        elif upper.startswith("/DIR="):
            install_dir = arg[5:].strip('"')
        elif upper == "/NODESKTOPSHORTCUT":
            create_desktop_shortcut = False
    return silent, install_dir, create_desktop_shortcut


def run_silent_install(install_dir=None, create_desktop_shortcut=True):
    """command-line 靜默安裝：完全不開視窗，給企業批次部署（登入腳本、MDM、
    群組原則）用。回傳值直接當這支 exe 的 process exit code：
    0 = 成功，非 0 = 失敗，部署腳本可以直接檢查 errorlevel（cmd）或
    $LASTEXITCODE（PowerShell）判斷結果。exit code 是背景的數字訊號，
    不會自己顯示在畫面上，要在執行完後緊接著查（例如 cmd 打 echo %errorlevel%）。

    靜默模式的既定行為（都是業界慣例）：
      - 不顯示 EULA 同意頁，執行 /S 視同已經同意（跟大多數靜默安裝工具一致）。
      - 偵測到舊版本會自動靜默更新覆蓋，不會跳出選擇視窗。
      - 相依元件缺少只會記錄警告、不會阻擋安裝（畢竟沒有視窗可以顯示提示）。

    修正紀錄：這支 exe 是用 --noconsole 編譯的（GUI 拖曳安裝模式需要這樣），
    即使用命令列帶 /S 執行，也沒有任何主控台視窗可以顯示文字——原本這裡用
    print() 輸出訊息，實際上等於印給空氣看，不會出現在呼叫端的 cmd 視窗裡，
    是設計疏漏，不是「還沒做」。現在改成把所有訊息收集起來，最後統一寫進
    一份 log 檔（%TEMP% 底下），這才是部署腳本或人工事後真正查得到細節的管道；
    exit code 本身不受這個問題影響，一直都是正確的。
    """
    log_lines = [f"=== {datetime.now().isoformat()} 靜默安裝 ==="]

    def log(msg):
        log_lines.append(msg)

    def write_log_and_return(app_name, exit_code):
        try:
            log_path = os.path.join(os.environ.get("TEMP", "."), f"{app_name}_silent_install_log.txt")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("\n".join(log_lines))
        except Exception:
            pass
        return exit_code

    api = InstallerAPI()
    if install_dir:
        api.selected_path = install_dir

    got_lock, _mutex_handle = _acquire_single_instance_lock(api.app_name)
    if not got_lock:
        log(f"[錯誤] {api.app_name} 安裝程式已經在執行中。")
        return write_log_and_return(api.app_name, 1)

    existing = api.check_existing_install()
    if existing.get("exists"):
        log(f"[資訊] 偵測到已安裝版本 {existing.get('version')}，這次安裝版本 {api.version}，正在靜默更新覆蓋...")
        upgrade_result = api.run_upgrade_uninstall()
        if upgrade_result.get("status") == "error":
            log(f"[錯誤] 移除舊版本失敗: {upgrade_result.get('message')}")
            return write_log_and_return(api.app_name, 1)

    warnings = api.get_dependency_warnings()
    for w in warnings:
        log(f"[警告] 建議先安裝：{w.get('name')}（{w.get('url')}）")

    result = api.trigger_installation(create_desktop_shortcut=create_desktop_shortcut)
    if result.get("status") == "success":
        log(f"[成功] {result.get('message')}")
        return write_log_and_return(api.app_name, 0)
    else:
        log(f"[錯誤] {result.get('message')}")
        return write_log_and_return(api.app_name, 1)


if __name__ == '__main__':
    _silent, _cli_install_dir, _cli_desktop_shortcut = _parse_cli_args()

    if _silent:
        sys.exit(run_silent_install(_cli_install_dir, _cli_desktop_shortcut))

    # 讓 Windows 在非 100% 縮放比例下不要把整個視窗畫面當點陣圖拉伸，避免文字模糊。
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE（改用系統層級，跟 pywebview 原生視窗拖曳交接的相容性較好，避免拖曳瞬間跳動）
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    _show_starting_cursor()

    api = InstallerAPI()

    # 單一實例鎖：避免使用者手滑重複開啟安裝程式，兩個流程同時寫同一個目錄
    got_lock, _mutex_handle = _acquire_single_instance_lock(api.app_name)
    if not got_lock:
        ctypes.windll.user32.MessageBoxW(
            0, f"「{api.app_name}」安裝程式已經在執行中。", "安裝應用程式", 0x30,
        )
        sys.exit(0)

    html_path = get_resource_path(os.path.join('ui', 'index.html'))

    window = webview.create_window(
        title='安裝應用程式', url=html_path, js_api=api,
        width=600, height=420, resizable=False, frameless=True, easy_drag=False,
    )
    webview.start(debug=False)