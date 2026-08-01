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
import tempfile
import time
import ctypes
import subprocess
import zlib
import webview
from datetime import datetime
from window_drag import WindowDragController
from disk_space import required_install_size, check_disk_space
import file_assoc
import lang_detect

# 目前介面 chrome（ui/index.html 裡固定的標籤、按鈕、提示文字）支援的語言，
# 跟 ui/index.html 內嵌的 I18N 翻譯表一一對應。EULA 文字語言不受此限制——
# 開發者可以自訂任意語言代碼，這裡只限制「安裝介面本身」的語言。
SUPPORTED_UI_LANGUAGES = ["zh-TW", "en"]
DEFAULT_UI_LANGUAGE = "zh-TW"


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
        self.eula_texts = {}
        self.eula_default_lang = ""
        self.dependencies = []
        self.file_associations = []
        self.doc_icon = ""
        self.add_to_path = False
        self.path_target_exe = ""
        self.restart_explorer_on_update = False
        self.ui_language = lang_detect.detect_system_language(SUPPORTED_UI_LANGUAGES, DEFAULT_UI_LANGUAGE)

        program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
        self.default_path = os.path.join(program_files, self.app_name)
        self.selected_path = self.default_path
        self.load_config()
        # default_path 改用 folder_name（安裝路徑用的名稱，建議英數字），
        # 不再用 app_name（顯示名稱，可能是中文）組路徑，兩者職責分開。
        # folder_name 沒填的話 load_config() 已經 fallback 成 app_name，行為不變。
        self.default_path = os.path.join(program_files, self.folder_name or self.app_name)
        self.selected_path = self.default_path
        self._drag = WindowDragController()
        # 更新覆蓋安裝的復原用備份：run_upgrade_uninstall() 靜默刪掉舊版本前，
        # 會把舊安裝資料夾複製到這裡；使用者事後取消，或新版本安裝失敗，
        # 都要能把這份備份搬回原位，見 _backup_existing_install() / _restore_upgrade_backup()。
        self._upgrade_backup_path = None
        self._upgrade_backup_original_path = None

    def start_drag(self, cursor_x, cursor_y):
        global window
        self._drag.start_drag(window, cursor_x, cursor_y)

    def drag_move(self, cursor_x, cursor_y):
        global window
        self._drag.drag_move(window, cursor_x, cursor_y)

    def end_drag(self):
        self._drag.end_drag()

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
                    self.eula_texts = config.get("eula_texts", {})
                    self.eula_default_lang = config.get("eula_default_lang", "")
                    self.dependencies = config.get("dependencies", [])
                    self.file_associations = config.get("file_associations", [])
                    self.doc_icon = config.get("doc_icon", "")
                    self.add_to_path = bool(config.get("add_to_path", False))
                    self.path_target_exe = config.get("path_target_exe", "")
                    self.restart_explorer_on_update = bool(config.get("restart_explorer_on_update", False))
        except Exception as e:
            print(f"[提示] 使用預設開發模式: {e}")

    # ------------------------------------------------------------------
    # 供前端查詢的基本資訊
    # ------------------------------------------------------------------

    def get_app_name(self):
        return self.app_name

    def get_default_path(self):
        return self.default_path

    def get_ui_language(self):
        return self.ui_language

    def get_eula_text(self):
        """回傳目前語言對應的 EULA 文字。

        回退順序：偵測到的系統語言完全對到開發者提供的語言 → 開發者打包時
        指定的「預設/回退語言」→ 字典裡第一筆（保底，避免開發者忘記設定
        預設語言時 EULA 整個消失不見）→ 空字串（維持既有「空字串 = 跳過
        EULA 頁」的行為）。
        """
        if not self.eula_texts:
            return ""
        text = self.eula_texts.get(self.ui_language)
        if text:
            return text
        text = self.eula_texts.get(self.eula_default_lang)
        if text:
            return text
        return next(iter(self.eula_texts.values()), "")

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

        三種互斥的結果，讓前端可以分別顯示對應的提示樣式：
          - is_newer：這次要裝的版本比已安裝的新（本機是舊版，該問「是否要更新」）。
          - is_same：版本完全一致（單純重裝，維持原本的提示樣式）。
          - is_older：這次要裝的版本比已安裝的舊（本機版本比較新，該用警示樣式，
            明確告知使用者要裝的版本比較舊，讓使用者自己決定要不要繼續）。
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
                    "is_same": comparison == 0,
                    "is_older": comparison < 0,
                }
        except Exception:
            return {"exists": False}

    def _backup_existing_install(self, install_path):
        """更新覆蓋安裝前，把舊安裝資料夾整份複製到暫存區。

        run_upgrade_uninstall() 接下來會靜默呼叫舊版本的 uninstall.exe 把這個
        資料夾整個刪掉，這份備份是唯一的復原機會：使用者事後取消、或這次新版本
        安裝失敗，都靠它把舊檔案搬回原位（見 _restore_upgrade_backup()）。

        失敗只回傳 None、不拋例外：沒有備份頂多是沒辦法復原，不該因此擋住
        合法的更新流程。

        修正紀錄（真實抓到的 bug）：原本用 os.environ.get("TEMP", ".") 算暫存
        路徑，`.get()` 只有在 TEMP 這個環境變數整個不存在時才會用預設值 "."；
        如果它存在但是空字串（實測發生在某些提權執行的情境下，環境變數區塊
        沒有正確帶出 TEMP），會直接算出一個相對路徑，實際落點變成「這個安裝
        程式執行當下的工作目錄」——如果使用者剛好把新安裝檔放在舊安裝目錄
        本身執行更新，備份資料夾會被建立在 install_path 底下，變成
        shutil.copytree() 對自己複製（複製到自己的子資料夾），越複製越亂，
        最後多半是拋例外收場，留下一個爛尾的子資料夾，而且完全沒有真的
        備份到東西。改用 tempfile.gettempdir()：這是標準函式庫保證一定回傳
        真實存在、絕對路徑的系統暫存資料夾的做法，不會有空字串/相對路徑這種
        陷阱。另外加一道保險：算出來的路徑如果還是落在 install_path 底下，
        直接拒絕備份，不要冒險對自己複製。
        """
        backup_path = os.path.join(
            tempfile.gettempdir(), f"{self.app_name}_upgrade_backup_{os.getpid()}",
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

    def _restore_upgrade_backup(self):
        """把 _backup_existing_install() 備份的舊安裝資料夾搬回原位。

        使用者在更新覆蓋安裝途中取消（close_window()），或這次新版本
        trigger_installation() 失敗時呼叫，盡量讓系統回到更新前的狀態。
        沒有備份（例如備份當初就失敗、或這次根本不是更新流程）時是no-op。
        """
        if not self._upgrade_backup_path or not os.path.exists(self._upgrade_backup_path):
            self._upgrade_backup_path = None
            self._upgrade_backup_original_path = None
            return
        try:
            if os.path.exists(self._upgrade_backup_original_path):
                shutil.rmtree(self._upgrade_backup_original_path, ignore_errors=True)
            shutil.move(self._upgrade_backup_path, self._upgrade_backup_original_path)
        except Exception:
            pass
        finally:
            self._upgrade_backup_path = None
            self._upgrade_backup_original_path = None

    def _discard_upgrade_backup(self):
        """新版本安裝成功後，備份已經沒用了，清掉暫存區避免留垃圾。"""
        if self._upgrade_backup_path and os.path.exists(self._upgrade_backup_path):
            shutil.rmtree(self._upgrade_backup_path, ignore_errors=True)
        self._upgrade_backup_path = None
        self._upgrade_backup_original_path = None

    def _wait_for_selected_path_writable(self, timeout=10, interval=0.5):
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
        rmdir，run_upgrade_uninstall() 呼叫完全不保證那個背景流程已經真的
        跑完——這其實是個更嚴重的競態（那個背景 rmdir 事後觸發時會把新版本
        已經複製好的檔案一起砍掉，導致「安裝回報成功但檔案不完整」），已經
        改用 --upgrade 命令列旗標請舊版 uninstall.exe 完全不排這段背景指令
        來根治（見 uninstall.py 的 _should_schedule_self_delete()），不是
        靠這裡的重試等待解決。這個函式現在只需要處理殘餘的、影響小得多的
        單一檔案層級 pending-delete 情況。
        """
        deadline = time.time() + timeout
        while True:
            try:
                os.makedirs(self.selected_path, exist_ok=True)
                return
            except PermissionError:
                if time.time() >= deadline:
                    return
                time.sleep(interval)

    def run_upgrade_uninstall(self):
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
        詳細根因見 uninstall.py 的 _should_schedule_self_delete()。

        已知限制：如果目前安裝的舊版本本身是用更早、還沒有 --upgrade 支援
        的舊版 uninstall.exe，這個旗標對它沒有意義（舊版 exe 根本不認得
        這個參數，會被當成一般未知引數忽略，仍然照舊排出那段有競態風險的
        背景自我刪除指令）——跟 --restart-explorer 的已知限制是同一類情況，
        第一次從這麼舊的版本更新可能仍會遇到這個問題，等新版本安裝完成、
        往後再次更新時才會是「新版本呼叫新版本」，這個修正才能確實生效。
        """
        info = self.check_existing_install()
        if not info.get("exists"):
            return {"status": "skipped"}

        install_path = info["install_path"]
        uninstall_exe = os.path.join(install_path, "uninstall.exe")
        if not os.path.exists(uninstall_exe):
            return {"status": "error", "message": "找不到舊版本的解除安裝程式，請先手動移除舊版本後再安裝。"}

        self._upgrade_backup_original_path = install_path
        self._upgrade_backup_path = self._backup_existing_install(install_path)

        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            # --upgrade：告訴舊版 uninstall.exe 這是更新覆蓋安裝呼叫的，
            # 不要排出它尾端那段延遲執行的背景自我刪除／整個資料夾 rmdir
            # 指令，避免那段非同步指令事後把這次新複製的檔案一起砍掉
            # （見 _wait_for_selected_path_writable() docstring 與
            # uninstall.py 的 _should_schedule_self_delete()）。
            cmd = [uninstall_exe, "--silent", "--upgrade"]
            if self.restart_explorer_on_update:
                cmd.append("--restart-explorer")
            subprocess.run(cmd, timeout=30, creationflags=creationflags)
            self._wait_for_selected_path_writable()
            return {"status": "success"}
        except Exception as e:
            self._restore_upgrade_backup()
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
        return required_install_size(get_resource_path("app_contents"))

    def _check_disk_space(self):
        return check_disk_space(self._required_size(), self.selected_path, self.default_path)

    def _register_uninstall_entry(self):
        import winreg
        uninstall_exe = os.path.join(self.selected_path, "uninstall.exe")
        main_exe_path = os.path.join(self.selected_path, self.main_exe) if self.main_exe else uninstall_exe
        reg_path = f"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{self.app_name}"
        try:
            estimated_size_kb = self._required_size() // 1024
        except Exception:
            estimated_size_kb = 0
        # 不吞例外：這支 exe 是 --noconsole 編譯，print() 沒有任何地方會顯示
        # （同一類問題見規格文件 §8.7），失敗時直接讓例外往外拋，交給
        # trigger_installation() 既有的外層 except 處理（回滾 + 回報使用者）。
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

    def _create_shortcut(self, desktop=False, log=None):
        """建立開始功能表或桌面捷徑（依賴 pywin32，未安裝時靜默略過）。

        跟其他登錄表寫入函式不同：捷徑建立失敗是刻意設計成「可忽略」，
        不影響安裝整體成敗，所以這裡維持吞例外、回傳 False 的行為，
        只是把回報管道從無效的 print() 換成真正會寫進 install_log.txt 的 log()。
        """
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
            if log:
                log(f"[提示] 未建立捷徑（可忽略）: {e}")
            return False

    def _resolve_doc_icon_ref(self, main_exe_path):
        """決定檔案關聯要用哪個圖示：有自訂就指向安裝時複製過去的那顆 ico，
        沒有就直接沿用主程式圖示。原本完全沒寫 DefaultIcon 時，檔案總管會顯示
        Windows 給「不知道用什麼圖示」的檔案類型的通用預設圖示，不是預期的樣子。
        """
        if self.doc_icon:
            return os.path.join(self.selected_path, self.doc_icon)
        return f"{main_exe_path},0"

    def _path_target_dir(self):
        """算出「加入 PATH」實際要加的目錄。

        預設加整個安裝目錄（跟原本行為一致）；如果開發者在打包時另外指定了
        一支執行檔（`path_target_exe`，例如跟主程式分開的 CLI 工具），改成
        只加那支執行檔所在的目錄——如果它就在安裝根目錄，結果跟預設行為
        相同；如果在子目錄，只有那個子目錄會被加進 PATH，不會讓整個安裝
        目錄下所有 exe 都變成全域可呼叫。
        """
        if self.path_target_exe:
            target_dir = os.path.dirname(os.path.join(self.selected_path, self.path_target_exe))
            if target_dir:
                return target_dir
        return self.selected_path

    def _add_to_path_env(self):
        import winreg
        target_dir = self._path_target_dir()
        # 不吞例外：理由同上，讓 PATH 寫入失敗時整個安裝流程失敗回滾。
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
        if not any(os.path.normcase(p) == os.path.normcase(target_dir) for p in parts):
            parts.append(target_dir)
            winreg.SetValueEx(key, "Path", 0, reg_type, ";".join(parts))
        winreg.CloseKey(key)

        HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG = 0xFFFF, 0x1A, 0x0002
        result = ctypes.c_long()
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", SMTO_ABORTIFHUNG, 5000, ctypes.byref(result)
        )
        return target_dir

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
                self._restore_upgrade_backup()
                return {"status": "error", "message": "安裝失敗：找不到內建軟體資源！"}

            # 磁碟空間檢查
            ok, free, required = self._check_disk_space()
            if not ok:
                self._restore_upgrade_backup()
                return {
                    "status": "error",
                    "message": f"磁碟空間不足：本次安裝約需 {required // (1024 * 1024)} MB，"
                                f"目標磁碟剩餘 {free // (1024 * 1024)} MB。",
                }
            log(f"磁碟空間檢查通過（需要約 {required // (1024 * 1024)} MB）")

            # 主程式執行中偵測
            if self.main_exe and _is_process_running(os.path.basename(self.main_exe)):
                self._restore_upgrade_backup()
                return {
                    "status": "error",
                    "message": f"安裝失敗：偵測到「{self.main_exe}」正在執行，請先關閉程式後再安裝。",
                }

            # 覆蓋安裝：使用者在拖曳圖示前的彈窗只是「確認要不要繼續」，真正
            # 刪除舊版本檔案的動作延後到這裡——使用者已經實際拖曳圖示、確定要
            # 安裝了才動手，而不是彈窗一按確認鈕、使用者都還沒觸發安裝就先刪。
            # run_upgrade_uninstall() 內部會先備份舊安裝資料夾，失敗時自己復原。
            existing = self.check_existing_install()
            if existing.get("exists"):
                self._report_progress(3, "正在移除舊版本...")
                upgrade_result = self.run_upgrade_uninstall()
                if upgrade_result.get("status") == "error":
                    return {"status": "error", "message": upgrade_result.get("message")}

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
                self._restore_upgrade_backup()
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
                self._restore_upgrade_backup()
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
            try:
                self._register_uninstall_entry()
            except Exception as e:
                raise RuntimeError(f"寫入解除安裝登錄表失敗：{e}") from e
            self._create_shortcut(desktop=False, log=log)
            if create_desktop_shortcut:
                self._create_shortcut(desktop=True, log=log)
            if self.file_associations:
                main_exe_path = os.path.join(self.selected_path, self.main_exe)
                icon_ref = self._resolve_doc_icon_ref(main_exe_path)
                try:
                    file_assoc.register(self.file_associations, main_exe_path, self.app_name, icon_ref, log=log)
                except Exception as e:
                    raise RuntimeError(f"檔案關聯註冊失敗：{e}") from e
                log(f"已註冊檔案關聯: {self.file_associations}")
            path_directory = ""
            if self.add_to_path:
                try:
                    path_directory = self._add_to_path_env()
                except Exception as e:
                    raise RuntimeError(f"加入環境變數 PATH 失敗：{e}") from e
                log(f"已將 {path_directory} 加入環境變數 PATH")

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
                "path_directory": path_directory,
                "restart_explorer_on_update": self.restart_explorer_on_update,
                "installed_at": datetime.now().isoformat(),
            }
            with open(os.path.join(self.selected_path, "install_manifest.json"), "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)

            with open(os.path.join(self.selected_path, "install_log.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(log_lines))

            self._report_progress(100, "安裝完成")
            self._discard_upgrade_backup()

            main_exe_path = os.path.join(self.selected_path, self.main_exe) if self.main_exe else ""
            return {"status": "success", "message": "安裝成功", "main_exe_path": main_exe_path}

        except PermissionError:
            self._rollback(copied_rel_paths, log)
            self._restore_upgrade_backup()
            return {
                "status": "error",
                "message": "安裝失敗：權限不足。請安裝到桌面或 D 槽，或以管理員身分執行。\n"
                           "（若已經是系統管理員身分仍失敗，也可能是舊版本尚未移除完畢，"
                           "請關閉安裝程式稍後再試一次。）",
            }
        except Exception as e:
            self._rollback(copied_rel_paths, log)
            self._restore_upgrade_backup()
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
        # 更新覆蓋安裝流程跑到一半（舊版本檔案已刪、新版本還沒裝完）就關視窗，
        # 等於使用者取消了這次安裝，要把備份的舊版本檔案搬回去，避免兩頭落空。
        if self._upgrade_backup_path:
            self._restore_upgrade_backup()
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
            log_path = os.path.join(tempfile.gettempdir(), f"{app_name}_silent_install_log.txt")
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
        # 實際的移除/備份/復原都交給 trigger_installation() 內部處理（跟 GUI
        # 流程共用同一份邏輯），這裡只負責記錄，避免同一個舊版本被刪兩次。
        log(f"[資訊] 偵測到已安裝版本 {existing.get('version')}，這次安裝版本 {api.version}，將靜默更新覆蓋...")

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
        # 這個對話框在 webview 視窗建立之前就跳出，沒辦法用 ui/index.html 的
        # JS 翻譯表套用語言，直接在這裡用偵測結果從兩種語言的文字二選一。
        _lock_dialog_lang = lang_detect.detect_system_language(SUPPORTED_UI_LANGUAGES, DEFAULT_UI_LANGUAGE)
        if _lock_dialog_lang == "en":
            _lock_title, _lock_message = "Installer", f'"{api.app_name}" installer is already running.'
        else:
            _lock_title = "安裝應用程式"
            _lock_message = f"「{api.app_name}」安裝程式已經在執行中。"
        ctypes.windll.user32.MessageBoxW(0, _lock_message, _lock_title, 0x30)
        sys.exit(0)

    html_path = get_resource_path(os.path.join('ui', 'index.html'))

    window = webview.create_window(
        title='安裝應用程式', url=html_path, js_api=api,
        width=600, height=420, resizable=False, frameless=True, easy_drag=False,
    )
    webview.start(debug=False)