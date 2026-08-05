"""
installer_core.py
------------------
主安裝檔（打包後的 exe）內部執行的安裝邏輯。

這一輪新增的「安裝精靈該有的步驟」：
  - 單一實例鎖（Mutex）：避免使用者手滑點兩次，同時跑兩個安裝流程互相干擾。
  - 覆蓋安裝偵測：透過登錄表判斷是否已裝過，讓前端跳出「更新覆蓋 / 取消」選擇。
  - 磁碟空間檢查：裝之前先確認目標磁碟剩餘空間夠不夠。
  - 相依元件偵測（VC++ Redist / .NET Desktop Runtime）：偵測缺少時，使用者可以選擇
    直接從官方下載點自動下載＋靜默安裝，也可以略過（不阻擋主程式安裝），見
    install_dependency() 與 DEPENDENCY_CHECKERS 開頭的說明。
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
import urllib.request
import zlib
import webview
from datetime import datetime
from window_drag import WindowDragController
from disk_space import required_install_size, check_disk_space
import file_assoc
import lang_detect
import restart_manager
import dependency_defs
import system_entries
from install_scope import InstallScope, local_appdata_root

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

_REGISTRY_HIVES = None  # 延後初始化：只有用得到時才 import winreg（跨平台開發機也能載入這個模組）


def _registry_hive(name):
    """把設定檔裡的字串（"HKLM"/"HKCU"）轉成 winreg 的 hive 常數。
    只支援這兩個，相依元件偵測跟免 UAC 模式用得到的就這兩種。
    """
    global _REGISTRY_HIVES
    import winreg
    if _REGISTRY_HIVES is None:
        _REGISTRY_HIVES = {
            "HKLM": winreg.HKEY_LOCAL_MACHINE,
            "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
            "HKCU": winreg.HKEY_CURRENT_USER,
            "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
        }
    return _REGISTRY_HIVES.get(str(name).strip().upper(), winreg.HKEY_LOCAL_MACHINE)


def _generic_registry_check(hive, path, value_name=None, expected=None):
    """泛用的「這個相依元件裝了沒」登錄表偵測，取代原本每個相依元件各自寫
    一個檢查函式的做法（見規格文件對應章節：自訂相依元件功能）。

    value_name 留空：只確認這個機碼本身存在（.NET Desktop Runtime 沒有明確
    的版本值可查，用機碼是否存在當作「有沒有裝」的依據）。
    value_name 有給：機碼底下這個值要等於 expected 才算已安裝。
    """
    import winreg
    try:
        key = winreg.OpenKey(_registry_hive(hive), path)
        if value_name is None:
            return True
        val, _ = winreg.QueryValueEx(key, value_name)
        return val == expected
    except Exception:
        return False


def _check_vcredist_x64():
    return _generic_registry_check(
        "HKLM", r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64", "Installed", 1,
    )


def _check_dotnet_desktop():
    return _generic_registry_check(
        "HKLM", r"SOFTWARE\WOW6432Node\dotnet\Setup\InstalledVersions\x64\sharedfx\Microsoft.WindowsDesktop.App",
    )


def _make_custom_dependency_checker(reg_check):
    """把 custom_dependencies 裡一筆 registry_check 設定，包成跟內建相依元件
    同樣簽章的 check_fn（不吃參數、回傳布林），供 _build_dependency_checkers()
    統一放進同一個 dict 使用。用 default 參數把當下這筆設定值綁進閉包，避免
    for 迴圈裡常見的「閉包晚繫結」陷阱（迴圈跑完後所有 checker 都指到最後
    一筆設定）。
    """
    def _check(reg_check=reg_check):
        return _generic_registry_check(
            reg_check.get("hive", "HKLM"),
            reg_check.get("path", ""),
            reg_check.get("value_name"),
            reg_check.get("expected"),
        )
    return _check


# {key: (check_fn, 顯示名稱, 官方靜默安裝檔下載連結, 靜默安裝命令列參數)}。
# 下載連結目前都指向可以直接執行的安裝檔（不是網頁），install_dependency()
# 靠這個直接下載＋執行。顯示名稱/連結/靜默參數本體定義在 dependency_defs.py，
# 跟 builder.py 的 bundle_dependencies（打包時內嵌相依元件安裝檔）共用同一份，
# 避免兩邊分別維護一份 URL 悄悄不同步（見 dependency_defs.py 開頭的說明，
# 包含 dotnet_desktop 版本號需要定期手動更新的已知限制）。
DEPENDENCY_CHECKERS = {
    "vcredist_x64": (
        _check_vcredist_x64,
        dependency_defs.BUILT_IN_DEPENDENCIES["vcredist_x64"]["display_name"],
        dependency_defs.BUILT_IN_DEPENDENCIES["vcredist_x64"]["download_url"],
        dependency_defs.BUILT_IN_DEPENDENCIES["vcredist_x64"]["silent_args"],
    ),
    "dotnet_desktop": (
        _check_dotnet_desktop,
        dependency_defs.BUILT_IN_DEPENDENCIES["dotnet_desktop"]["display_name"],
        dependency_defs.BUILT_IN_DEPENDENCIES["dotnet_desktop"]["download_url"],
        dependency_defs.BUILT_IN_DEPENDENCIES["dotnet_desktop"]["silent_args"],
    ),
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
        self.custom_dependencies = []
        self.bundle_dependencies = []
        self.file_associations = []
        self.doc_icon = ""
        self.doc_icons = {}
        self.add_to_path = False
        self.path_target_exe = ""
        self.local_appdata_files = []
        self.restart_explorer_on_update = False
        self.no_admin_install = False
        self.custom_install_dir = ""
        self.pre_install_script = ""
        self.post_install_script = ""
        self.ui_language = lang_detect.detect_system_language(SUPPORTED_UI_LANGUAGES, DEFAULT_UI_LANGUAGE)

        program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
        self.default_path = os.path.join(program_files, self.app_name)
        self.selected_path = self.default_path
        self.load_config()
        # no_admin_install 這個布林值衍生出的「用哪個 hive/目錄」判斷，收在
        # install_scope.InstallScope 裡（見下面 _scope property），
        # installer_core.py 跟 uninstall.py 共用同一份規則，不用各自重新
        # 推導。folder_name 沒填的話 load_config() 已經 fallback 成
        # app_name，行為不變。
        self.default_path = self._compute_default_path()
        self.selected_path = self.default_path
        self._drag = WindowDragController()
        # 更新覆蓋安裝的復原用備份：run_upgrade_uninstall() 靜默刪掉舊版本前，
        # 會把舊安裝資料夾複製到這裡；使用者事後取消，或新版本安裝失敗，
        # 都要能把這份備份搬回原位，見 _backup_existing_install() / _restore_upgrade_backup()。
        self._upgrade_backup_path = None
        self._upgrade_backup_original_path = None

    @property
    def _scope(self):
        """真實抓到的問題：一開始把這個算好存成 self._scope（建構時算一次
        就存住），但測試（跟部分呼叫端）會在建構完成後才直接
        `api.no_admin_install = True` 這樣改屬性，存住的 _scope 就跟
        no_admin_install 對不起來、吃到舊值。改成 property，每次存取都
        用當下的 self.no_admin_install 重新算，InstallScope 本身只是包一個
        布林值，重新建構沒有額外成本。"""
        return InstallScope(self.no_admin_install)

    def _compute_default_path(self):
        """算出這次安裝的預設安裝路徑：`custom_install_dir` 有值時優先
        套用（用 os.path.expandvars() 展開 %APPDATA% 這類環境變數寫法，
        照『使用者這台電腦當下』的環境變數解析，不是打包當下開發者電腦的
        值），否則照 no_admin_install 決定 Program Files 還是
        %LOCALAPPDATA%\\Programs\\<folder>（見 InstallScope）。

        寫成獨立方法而不是只在 __init__ 裡算一次：跟 _scope property 同一個
        理由——測試（跟部分呼叫端）會在 InstallerAPI() 建構完成後才直接用
        setattr 覆蓋 custom_install_dir/no_admin_install，方法每次呼叫都
        重新算才不會吃到建構當下的舊值。
        """
        if self.custom_install_dir:
            return os.path.expandvars(self.custom_install_dir)
        return self._scope.default_install_root(self.app_name, self.folder_name)

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
                    self.custom_dependencies = config.get("custom_dependencies", [])
                    self.bundle_dependencies = config.get("bundle_dependencies", [])
                    self.file_associations = config.get("file_associations", [])
                    self.doc_icon = config.get("doc_icon", "")
                    self.doc_icons = config.get("doc_icons", {})
                    self.add_to_path = bool(config.get("add_to_path", False))
                    self.path_target_exe = config.get("path_target_exe", "")
                    self.local_appdata_files = config.get("local_appdata_files", [])
                    self.restart_explorer_on_update = bool(config.get("restart_explorer_on_update", False))
                    self.no_admin_install = bool(config.get("no_admin_install", False))
                    self.custom_install_dir = config.get("custom_install_dir", "")
                    self.pre_install_script = config.get("pre_install_script", "")
                    self.post_install_script = config.get("post_install_script", "")
        except Exception as e:
            print(f"[提示] 使用預設開發模式: {e}")

    def _build_dependency_checkers(self):
        """把內建的 DEPENDENCY_CHECKERS 跟這次打包時透過 custom_dependencies
        自訂的相依元件合併成一份對照表。

        每次呼叫都重新組（不是在 __init__ 時算好快取起來）：一來合併成本
        很低（最多幾筆），二來讓 DEPENDENCY_CHECKERS 在執行當下被覆蓋/patch
        （例如測試情境）時，這裡永遠讀到當下最新的內容，不會因為快取在
        InstallerAPI 建構當時就定案而讀到過期的版本。custom_dependencies 的
        key 如果跟內建的撞名，直接以自訂設定覆蓋（打包時 packaging_core
        已經擋掉撞名，這裡再處理一次是最後一道防線，不視為錯誤，沿用
        「儘量讓安裝繼續」的原則）。
        """
        checkers = dict(DEPENDENCY_CHECKERS)
        for entry in self.custom_dependencies:
            key = str(entry.get("key", "")).strip()
            if not key:
                continue
            checkers[key] = (
                _make_custom_dependency_checker(entry.get("registry_check", {}) or {}),
                str(entry.get("display_name", key)),
                str(entry.get("download_url", "")),
                list(entry.get("silent_args", []) or []),
            )
        return checkers

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
        """回傳目前系統缺少的相依元件清單（key + 顯示名稱 + 下載連結），
        不阻擋安裝。前端用 key 呼叫 install_dependency(key) 觸發自動安裝，
        url 保留給自動安裝失敗時的手動下載備援連結。
        """
        checkers = self._build_dependency_checkers()
        warnings = []
        for key in self.dependencies:
            checker = checkers.get(key)
            if not checker:
                continue
            check_fn, display_name, url, _silent_args = checker
            if not check_fn():
                warnings.append({"key": key, "name": display_name, "url": url})
        return warnings

    def _report_dependency_progress(self, percent, message):
        """相依元件自動安裝期間的進度推播，寫法比照 _report_progress()，
        但推到前端另一個獨立的進度條（window.updateDependencyInstallProgress），
        因為相依元件安裝跟主程式安裝是兩個不同的畫面，不能共用同一組進度條
        元素。
        """
        global window
        safe_msg = json.dumps(message, ensure_ascii=False)
        try:
            if window:
                window.evaluate_js(f"window.updateDependencyInstallProgress({percent}, {safe_msg})")
        except Exception:
            pass

    def install_dependency(self, key):
        """使用者在相依元件彈窗按下「自動安裝」時，依序對每個缺少的元件呼叫
        這個方法：下載官方安裝檔到暫存目錄、靜默執行，結束後不管子程序的
        結束碼，一律重新呼叫這個元件自己的登錄表偵測函式（check_fn）確認
        「現在到底裝好了沒」才是最終依據。

        真實情境：Visual C++ Redistributable 的官方文件明講，如果偵測到
        機器上已經裝了更新版本，`/quiet` 模式下的子程序會回傳非 0 的錯誤碼，
        但這其實不是真正的失敗——只看結束碼判斷會誤判成失敗。這支安裝程式
        本身是 --uac-admin 編譯、執行到這裡一定已經是系統管理員權杖，子
        程序繼承同一個權杖直接靜默安裝，不會再跳一次 UAC。
        """
        checker = self._build_dependency_checkers().get(key)
        if not checker:
            return {"status": "error", "message": "未知的相依元件。"}
        check_fn, display_name, url, silent_args = checker

        # bundle_dependencies：打包時已經把這個相依元件的安裝檔內嵌進安裝檔裡
        # （見 packaging_core.py/builder.py 的 bundle_dependencies 處理），
        # 直接執行內嵌檔案即可，不用再連網下載——適合「不確定目標機器有沒有
        # 網路」的情境。掛載路徑固定是 dependencies/<key>.exe，跟打包端的
        # 命名規則一致。
        bundled_path = get_resource_path(os.path.join("dependencies", f"{key}.exe"))
        run_path = None
        if key in self.bundle_dependencies and os.path.exists(bundled_path):
            run_path = bundled_path
            self._report_dependency_progress(55, f"正在安裝 {display_name}...")
        else:
            tmp_path = os.path.join(tempfile.gettempdir(), f"dep_installer_{key}.exe")
            try:
                self._report_dependency_progress(5, f"正在下載 {display_name}...")
                with urllib.request.urlopen(url, timeout=30) as resp:
                    total = resp.getheader("Content-Length")
                    total = int(total) if total else None
                    downloaded = 0
                    with open(tmp_path, "wb") as f:
                        while True:
                            chunk = resp.read(1024 * 256)
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total:
                                percent = 5 + int(downloaded / total * 50)  # 下載階段佔 5%-55%
                                self._report_dependency_progress(percent, f"正在下載 {display_name}...")
            except Exception as e:
                return {"status": "error", "message": f"下載 {display_name} 失敗：{e}"}
            run_path = tmp_path

        try:
            self._report_dependency_progress(60, f"正在安裝 {display_name}...")
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.run([run_path] + silent_args, creationflags=creationflags, timeout=600)
        except Exception as e:
            return {"status": "error", "message": f"執行 {display_name} 安裝程式失敗：{e}"}
        finally:
            # 內嵌檔案是這次安裝檔自己的資源（PyInstaller 解壓出來的暫存內容
            # 或開發模式下的原始檔案），不是我們自己下載到 %TEMP% 的暫存檔，
            # 不能刪除；只清掉真的是我們下載出來的那份。
            if run_path != bundled_path:
                try:
                    os.remove(run_path)
                except Exception:
                    pass

        self._report_dependency_progress(95, "正在確認安裝結果...")
        if check_fn():
            return {"status": "success", "name": display_name}
        return {
            "status": "error",
            "message": f"{display_name} 安裝流程已經結束，但仍偵測不到已安裝——"
                       f"可能需要手動安裝，或重新啟動電腦後再試一次。",
        }

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
        hive = self._scope.registry_hive
        try:
            with winreg.OpenKey(hive, reg_path) as key:
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
        來根治（見 self_delete.py），不是
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
        詳細根因見 self_delete.py。

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
            # self_delete.py）。
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
        main_exe_path = self._resolve_installed_path(self.main_exe) if self.main_exe else uninstall_exe
        reg_path = f"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{self.app_name}"
        # no_admin_install 開啟時整個安裝流程完全不要求提權，解除安裝登錄表
        # 也對應寫進 HKEY_CURRENT_USER（每個使用者各自的解除安裝清單），
        # 不是需要系統管理員權限才能寫入的 HKLM——見 install_scope.InstallScope。
        hive = self._scope.registry_hive
        try:
            estimated_size_kb = self._required_size() // 1024
        except Exception:
            estimated_size_kb = 0
        # 不吞例外：這支 exe 是 --noconsole 編譯，print() 沒有任何地方會顯示
        # （同一類問題見規格文件 §8.7），失敗時直接讓例外往外拋，交給
        # trigger_installation() 既有的外層 except 處理（回滾 + 回報使用者）。
        with winreg.CreateKey(hive, reg_path) as key:
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

            # no_admin_install 開啟時，捷徑改建在「目前使用者自己」的桌面/開始
            # 功能表，預設（需要系統管理員權限的安裝）維持原本「所有使用者
            # 共用」的位置，這樣裝一次全部使用者都看得到捷徑——見
            # install_scope.InstallScope.shortcut_dir()。
            base = self._scope.shortcut_dir(desktop=desktop)
            os.makedirs(base, exist_ok=True)
            shortcut_path = os.path.join(base, f"{self.app_name}.lnk")
            main_exe_path = self._resolve_installed_path(self.main_exe)

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

    def _resolve_doc_icon_ref(self, main_exe_path, ext=None):
        """決定某個副檔名要用哪個圖示，依序 fallback：
        1. `ext` 自己在 `doc_icons` 裡的專屬圖示（例如 .a 跟 .b 各自不同的 ICO）
        2. 所有副檔名共用的 `doc_icon`
        3. 主程式圖示

        原本完全沒寫 DefaultIcon 時，檔案總管會顯示 Windows 給「不知道用
        什麼圖示」的檔案類型的通用預設圖示，不是預期的樣子。`ext` 留空
        （沒有個別副檔名這個概念的呼叫情境）就跳過第 1 層，行為等同原本
        只有「共用圖示 / 主程式圖示」兩層 fallback 的版本。
        """
        per_ext_icon = self.doc_icons.get(ext) if ext else None
        if per_ext_icon:
            return os.path.join(self.selected_path, per_ext_icon)
        if self.doc_icon:
            return os.path.join(self.selected_path, self.doc_icon)
        return f"{main_exe_path},0"

    def _resolve_doc_icon_refs(self, main_exe_path):
        """幫 self.file_associations 裡每個副檔名各自算好要用的圖示，
        組成 file_assoc.register() 需要的 {副檔名: icon_ref} 字典。
        """
        return {ext: self._resolve_doc_icon_ref(main_exe_path, ext) for ext in self.file_associations}

    def _local_appdata_root(self):
        """`local_appdata_files` 指定的檔案要落地的目錄：
        `%LOCALAPPDATA%\\Programs\\<folder_name>`。

        用意是讓某幾支執行檔（典型案例：CLI 工具）改裝到使用者層級、
        不需要系統管理員權限就能寫入的目錄，跟主安裝目錄（可能在
        Program Files，需要管理員權限）分開——這樣使用者事後單純「執行」
        這支工具不需要提權，只有「安裝/解除安裝」這個動作本身仍然需要
        系統管理員權限（因為要寫登錄表、PATH 等系統層級項目）。

        已知限制：如果安裝程式是用跟目前登入者不同的系統管理員帳號提權
        執行（例如切換帳號的 UAC 提示），這裡解析出來的 %LOCALAPPDATA%
        會是那個提權帳號的，不是原本操作安裝程式那個使用者的——多數情境
        下 UAC 沿用同一個帳號的提權權杖，不會踩到這個邊界案例。
        """
        return local_appdata_root(self.folder_name or self.app_name)

    def _is_local_appdata_file(self, rel_path):
        if not rel_path:
            return False
        norm = os.path.normcase(os.path.normpath(rel_path))
        return any(
            os.path.normcase(os.path.normpath(f)) == norm for f in self.local_appdata_files
        )

    def _resolve_installed_path(self, rel_path):
        """把一個相對路徑（相對於 app_dir/安裝內容）解析成實際安裝到磁碟上的
        絕對路徑：`local_appdata_files` 裡列出的檔案落在
        `_local_appdata_root()`，其餘維持原本行為，落在 `self.selected_path`。
        """
        if self._is_local_appdata_file(rel_path):
            return os.path.join(self._local_appdata_root(), rel_path)
        return os.path.join(self.selected_path, rel_path)

    def _path_target_dir(self):
        """算出「加入 PATH」實際要加的目錄。

        預設加整個安裝目錄（跟原本行為一致）；如果開發者在打包時另外指定了
        一支執行檔（`path_target_exe`，例如跟主程式分開的 CLI 工具），改成
        只加那支執行檔所在的目錄——如果它就在安裝根目錄，結果跟預設行為
        相同；如果在子目錄，只有那個子目錄會被加進 PATH，不會讓整個安裝
        目錄下所有 exe 都變成全域可呼叫。`path_target_exe` 如果同時也列在
        `local_appdata_files` 裡，這裡會自動改成加 `_local_appdata_root()`
        （或它底下的子目錄），不需要另外設定。
        """
        if self.path_target_exe:
            target_dir = os.path.dirname(self._resolve_installed_path(self.path_target_exe))
            if target_dir:
                return target_dir
        return self.selected_path

    def _add_to_path_env(self):
        import winreg
        target_dir = self._path_target_dir()
        # no_admin_install 時寫使用者層級的 PATH，否則維持原本的系統層級
        # PATH——見 install_scope.InstallScope.path_env_hive_and_key。
        hive, sub_key = self._scope.path_env_hive_and_key
        # 不吞例外：理由同上，讓 PATH 寫入失敗時整個安裝流程失敗回滾。
        key = winreg.OpenKey(hive, sub_key, 0, winreg.KEY_ALL_ACCESS)
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

    @staticmethod
    def _cleanup_empty_dirs(root_dir):
        """清掉 root_dir 底下因為刪檔而變空的子目錄（由裡到外），
        root_dir 本身如果也空了就一併刪除。main install 目錄跟
        `_local_appdata_root()` 的回滾清理都走這個共用邏輯。
        """
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

    def _run_install_script(self, script_rel, timeout=120):
        """執行打包時內嵌的 pre/post-install 腳本（見 pre_install_script/
        post_install_script 設定欄位）。腳本以這支安裝程式當下的權限層級
        執行（一般是系統管理員，或 no_admin_install 開啟時是一般使用者），
        跟主程式檔案複製本來就有的能力等級一致，不是新增的風險類別。

        回傳 (ok: bool, message: str)：ok 是「有跑且結束碼是 0」；找不到
        腳本檔案（例如打包時沒有設定這個欄位）視為 no-op、直接回傳成功，
        呼叫端不需要另外判斷「這個欄位到底有沒有設定」。
        """
        if not script_rel:
            return True, ""
        script_path = get_resource_path(script_rel)
        if not os.path.exists(script_path):
            return True, ""
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            result = subprocess.run(
                [script_path], creationflags=creationflags, timeout=timeout,
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                tail = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()[-500:]
                return False, f"腳本結束碼 {result.returncode}：{tail}"
            return True, ""
        except Exception as e:
            return False, str(e)

    def _rollback(self, copied_rel_paths, log=None, *,
                  registry_entry_created=False, shortcuts_created=None,
                  file_associations_registered=False, path_directory=None):
        """安裝失敗時的回滾：把這次安裝已經寫入的東西清掉，盡量讓系統回到
        安裝前的乾淨狀態。只清掉『這次安裝這一輪自己寫入的部分』，不會動到
        selected_path（或 local_appdata_files 落地的 _local_appdata_root()）
        底下其他既有內容（例如使用者選了一個已經有東西的資料夾）。

        真實抓到的缺口：原本只清複製出去的檔案，但安裝流程後段還會依序寫入
        解除安裝登錄表項目/捷徑/檔案關聯/PATH——這幾步任何一步後面的步驟
        失敗，前面已經成功寫入的部分完全不會被回滾。這裡依「後寫的先復原」
        順序（跟安裝時 _register_uninstall_entry → _create_shortcut →
        file_assoc.register → _add_to_path_env 的順序相反）補上這四類。
        """
        removed = 0
        for rel in copied_rel_paths:
            try:
                path = self._resolve_installed_path(rel)
                if os.path.exists(path):
                    os.remove(path)
                    removed += 1
            except Exception:
                pass
        self._cleanup_empty_dirs(self.selected_path)
        if self.local_appdata_files:
            self._cleanup_empty_dirs(self._local_appdata_root())
        if log:
            log(f"安裝失敗，已回滾刪除 {removed} 個已複製的檔案")

        if path_directory:
            system_entries.remove_from_path(path_directory, self.no_admin_install)
        if file_associations_registered:
            file_assoc.unregister(self.file_associations)
        for desktop in (shortcuts_created or []):
            system_entries.remove_shortcut(self.app_name, desktop=desktop, no_admin_install=self.no_admin_install)
        if registry_entry_created:
            system_entries.remove_registry_entry(self.app_name, self.no_admin_install)

    def trigger_installation(self, create_desktop_shortcut=True, skip_process_check=False):
        log_lines = [f"=== {self.app_name} 安裝紀錄 {datetime.now().isoformat()} ==="]
        copied_rel_paths = []  # 提前宣告：任何階段失敗都要能安全參照這個變數做回滾
        current_copy_target = None  # 目前正在寫入的目的地路徑，複製失敗時用來查是誰鎖住它
        # 這幾個一樣提前宣告：登錄表/捷徑/檔案關聯/PATH 任何一步之後才失敗，
        # 都要能安全參照這幾個變數，讓 _rollback() 知道哪些系統項目已經真的
        # 寫入、需要回滾（見 _rollback() 的說明）。
        registry_entry_created = False
        shortcuts_created = []
        file_associations_registered = False
        path_directory = None

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

            # 主程式執行中偵測：回傳獨立於一般 "error" 的狀態值，讓前端可以
            # 跳出「關閉程式並繼續安裝／取消」的互動選擇，而不是直接判定
            # 安裝失敗、逼使用者自己手動關閉程式再重新來一次。
            # skip_process_check：保底選項，給「偵測卡死關不掉」的邊緣情況
            # 用（例如某支程式在系統匣選單 callback 裡崩潰，留下 Windows
            # 行程表裡真的還存在、但工作管理員已經看不到的殭屍行程，重開機
            # 前 tasklist 都會回報還在執行，taskkill 也殺不掉——這是 Windows
            # 本身的行程狀態問題，不是這裡偵測邏輯的 bug，需要一個讓使用者
            # 不會被卡死的出口）。
            if not skip_process_check and self.main_exe and _is_process_running(os.path.basename(self.main_exe)):
                self._restore_upgrade_backup()
                return {
                    "status": "process_running",
                    "message": f"偵測到「{self.main_exe}」正在執行中。\n請先關閉程式後再繼續安裝。",
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

            # pre-install 腳本：檔案還沒複製之前執行，失敗視為安裝失敗中止——
            # 主程式可能依賴這個腳本先做的事（例如停用某個會鎖住待複製檔案
            # 的服務），腳本沒成功執行完，後面的複製流程不該假裝沒事繼續跑。
            if self.pre_install_script:
                self._report_progress(2, "正在執行安裝前置腳本...")
                ok, msg = self._run_install_script(self.pre_install_script)
                if not ok:
                    log(f"[錯誤] 安裝前置腳本執行失敗: {msg}")
                    self._restore_upgrade_backup()
                    return {"status": "error", "message": f"安裝失敗：安裝前置腳本執行失敗。{msg}"}
                log("已執行安裝前置腳本")

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
                dest_f = self._resolve_installed_path(rel)
                os.makedirs(os.path.dirname(dest_f), exist_ok=True)
                current_copy_target = dest_f
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
                current_copy_target = os.path.join(self.selected_path, "uninstall.exe")
                shutil.copy2(uninstall_src, current_copy_target)
                copied_rel_paths.append("uninstall.exe")

            config_src = get_resource_path("installer_config.json")
            if os.path.exists(config_src):
                current_copy_target = os.path.join(self.selected_path, "installer_config.json")
                shutil.copy2(config_src, current_copy_target)
                copied_rel_paths.append("installer_config.json")

            if self.doc_icon:
                doc_icon_src = get_resource_path(self.doc_icon)
                if os.path.exists(doc_icon_src):
                    current_copy_target = os.path.join(self.selected_path, self.doc_icon)
                    shutil.copy2(doc_icon_src, current_copy_target)
                    copied_rel_paths.append(self.doc_icon)
                else:
                    log(f"[警告] 找不到內嵌的文件圖示 {self.doc_icon}，檔案關聯將沿用主程式圖示。")
                    self.doc_icon = ""

            for ext, icon_rel in list(self.doc_icons.items()):
                icon_src = get_resource_path(icon_rel)
                if os.path.exists(icon_src):
                    current_copy_target = os.path.join(self.selected_path, icon_rel)
                    shutil.copy2(icon_src, current_copy_target)
                    copied_rel_paths.append(icon_rel)
                else:
                    log(f"[警告] 找不到內嵌的文件圖示 {icon_rel}（副檔名 {ext}），改用共用的文件圖示或主程式圖示。")
                    del self.doc_icons[ext]

            # 登錄表 + 捷徑 + 檔案關聯 + PATH
            self._report_progress(90, "正在註冊系統項目...")
            try:
                self._register_uninstall_entry()
                registry_entry_created = True
            except Exception as e:
                raise RuntimeError(f"寫入解除安裝登錄表失敗：{e}") from e
            if self._create_shortcut(desktop=False, log=log):
                shortcuts_created.append(False)
            if create_desktop_shortcut and self._create_shortcut(desktop=True, log=log):
                shortcuts_created.append(True)
            if self.file_associations:
                main_exe_path = self._resolve_installed_path(self.main_exe)
                icon_refs = self._resolve_doc_icon_refs(main_exe_path)
                try:
                    file_assoc.register(self.file_associations, main_exe_path, self.app_name, icon_refs, log=log)
                    file_associations_registered = True
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

            # post-install 腳本：主程式已經裝好、登錄表/捷徑都寫完之後執行，
            # 失敗只記錄警告、不讓整體安裝回報失敗——此時主程式已經是可用
            # 狀態，不該因為收尾腳本（例如額外的環境設定）失敗就整個作廢。
            if self.post_install_script:
                self._report_progress(96, "正在執行安裝後置腳本...")
                ok, msg = self._run_install_script(self.post_install_script)
                if ok:
                    log("已執行安裝後置腳本")
                else:
                    log(f"[警告] 安裝後置腳本執行失敗（不影響安裝結果）: {msg}")

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
                "local_appdata_files": self.local_appdata_files,
                "local_appdata_dir": self._local_appdata_root() if self.local_appdata_files else "",
                "restart_explorer_on_update": self.restart_explorer_on_update,
                "no_admin_install": self.no_admin_install,
                "installed_at": datetime.now().isoformat(),
            }
            with open(os.path.join(self.selected_path, "install_manifest.json"), "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)

            with open(os.path.join(self.selected_path, "install_log.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(log_lines))

            self._report_progress(100, "安裝完成")
            self._discard_upgrade_backup()

            main_exe_path = self._resolve_installed_path(self.main_exe) if self.main_exe else ""
            return {"status": "success", "message": "安裝成功", "main_exe_path": main_exe_path}

        except OSError as e:
            self._rollback(
                copied_rel_paths, log,
                registry_entry_created=registry_entry_created, shortcuts_created=shortcuts_created,
                file_associations_registered=file_associations_registered, path_directory=path_directory,
            )
            self._restore_upgrade_backup()
            message = self._describe_install_os_error(e, current_copy_target)
            if self._is_lock_violation(e) and current_copy_target:
                processes = restart_manager.find_locking_processes([current_copy_target])
                if processes:
                    return {
                        "status": "file_locked", "message": message,
                        "processes": [{"pid": pid, "name": name} for pid, name in processes],
                    }
            return {"status": "error", "message": message}
        except Exception as e:
            self._rollback(
                copied_rel_paths, log,
                registry_entry_created=registry_entry_created, shortcuts_created=shortcuts_created,
                file_associations_registered=file_associations_registered, path_directory=path_directory,
            )
            self._restore_upgrade_backup()
            return {"status": "error", "message": f"發生未知錯誤：\n{str(e)}"}

    def _describe_install_os_error(self, error, dest_file=None):
        """把安裝過程中複製/寫入檔案時真正發生的 OSError 轉換成使用者看得懂
        的訊息，取代原本「不管什麼原因，一律歸類成權限不足」的做法。

        真實情境：這支安裝程式本身是用 PyInstaller 的 --uac-admin 編譯的，
        Windows 執行時已經先跳出 UAC 要求使用者同意用系統管理員身分執行，
        執行到這裡的程式碼一定已經是系統管理員權杖——換句話說，這裡真的
        遇到 PermissionError，幾乎不可能是「使用者忘記用系統管理員身分
        執行」（Windows 根本不會讓它跑到這裡）。實際上最常見的原因是檔案
        正被其他進程鎖住（Windows 的 sharing violation，Python 一樣會包成
        PermissionError，但成因跟系統管理員權限完全無關，「以管理員身分
        重試」這個建議對這種情況沒有用，只會誤導使用者反覆做沒有用的事）。

        用 error.winerror（Windows 特有，OSError 的 errno 之外還會帶原始
        的 Win32 錯誤碼）分辨真正的成因：
          - ERROR_SHARING_VIOLATION (32) / ERROR_LOCK_VIOLATION (33)：
            檔案被其他進程開著——用 restart_manager（跟 uninstall.py 解除
            安裝時偵測鎖定進程用的是同一套 Restart Manager API）實際查出
            是哪個進程鎖住的，能查到就直接點名，讓使用者知道要關掉什麼。
          - ERROR_ACCESS_DENIED (5)：安裝程式已經是系統管理員身分，還被
            拒絕存取，比較可能是防毒軟體、Windows 防勒索軟體的「受控
            資料夾存取」，或企業原則限制了這個安裝路徑，不是使用者能單靠
            「重新以管理員身分執行」解決的。
          - ERROR_WRITE_PROTECT (19)：目標磁碟或媒體本身是唯讀狀態。
          - 其他/查不到 winerror：退回一個沒有過度承諾特定原因的通用訊息，
            仍然明確提醒使用者這不是系統管理員權限的問題。
        """
        winerror = getattr(error, "winerror", None)

        if winerror in (32, 33):
            locker_hint = ""
            if dest_file:
                processes = restart_manager.find_locking_processes([dest_file])
                if processes:
                    names = "、".join(sorted({name for _pid, name in processes if name})) or "未知程式"
                    locker_hint = f"目前偵測到鎖定這個檔案的程式：{names}。"
            file_label = f"「{os.path.basename(dest_file)}」" if dest_file else "某個檔案"
            return (
                f"安裝失敗：{file_label}正被其他程式使用中，暫時無法覆寫。{locker_hint}"
                f"請先關閉相關程式後再重試安裝。"
            )
        if winerror == 5:
            return (
                "安裝失敗：存取被拒。這支安裝程式已經是以系統管理員身分執行，"
                "通常不是「權限不足」造成的，比較可能是防毒軟體、Windows 防勒索軟體的"
                "「受控資料夾存取」，或企業網域原則限制了這個安裝路徑的寫入權限。\n"
                "請暫時停用相關防護，或改安裝到其他路徑（例如桌面或 D 槽）後再試。"
            )
        if winerror == 19:
            return "安裝失敗：目標磁碟或媒體目前是唯讀（寫入保護）狀態，請改安裝到其他磁碟。"
        if isinstance(error, PermissionError):
            return (
                "安裝失敗：權限不足，但這支安裝程式已經是以系統管理員身分執行，"
                "不太可能是使用者權限的問題（可能是舊版本尚未移除完畢，請關閉安裝程式"
                "稍後再試一次；或安裝路徑有其他特殊的存取限制）。"
            )
        return f"安裝失敗：{error}"

    def _is_lock_violation(self, error):
        """判斷是不是「檔案被其他程式鎖住」這一類 OSError（Windows 的
        sharing violation / lock violation），跟 _describe_install_os_error()
        共用同一組 winerror 判斷，抽出來避免兩處各自寫一份魔法數字。"""
        return getattr(error, "winerror", None) in (32, 33)

    def close_locking_processes(self, processes):
        """使用者在安裝失敗跳出的『檔案使用中』畫面按下「關閉此程式」時
        呼叫：processes 是前端原封不動把 trigger_installation() 回傳的
        file_locked 狀態裡的 processes 傳回來的 [{"pid":.., "name":..}, ...]。
        逐一 taskkill /f /pid（寫法比照 close_running_main_exe()：
        CREATE_NO_WINDOW、吞例外、不做分層重試）。

        真實抓到的問題：原本如果關閉的裡面有 explorer.exe，會額外呼叫
        subprocess.Popen(["explorer.exe"]) 主動重啟它——實測發現這個呼叫
        會跳出一個瀏覽視窗，代表呼叫當下 shell 其實已經被復原了，這一步
        是多餘的，還會多跳出一個使用者沒有要求的視窗，所以拿掉了。
        """
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        for proc in processes:
            try:
                subprocess.run(
                    ["taskkill", "/f", "/pid", str(proc["pid"])],
                    creationflags=creationflags, timeout=10,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except Exception:
                pass

    def close_running_main_exe(self):
        """使用者在「偵測到主程式執行中」的彈窗按下「關閉程式並繼續安裝」時
        呼叫：強制關閉正在執行的主程式，讓前端可以接著重新呼叫
        trigger_installation()。寫法比照 uninstall.py 既有的慣例
        （taskkill /f、CREATE_NO_WINDOW、吞例外回傳布林值），不做「先禮貌
        關閉、失敗才強制」這種分層。

        回傳值檢查 taskkill 的 returncode，不是「呼叫沒拋例外就一律回傳
        True」——找不到目標程序時 taskkill 會用非 0 的 returncode 表示
        失敗（stderr 導到 DEVNULL，呼叫端原本從沒檢查過），這裡改成
        如實反映有沒有真的成功。
        """
        if not self.main_exe:
            return False
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            result = subprocess.run(
                ["taskkill", "/f", "/im", os.path.basename(self.main_exe)],
                creationflags=creationflags, timeout=10,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return result.returncode == 0
        except Exception:
            return False

    def launch_app(self):
        """安裝完成後「立即執行程式」"""
        if not self.main_exe:
            return False
        try:
            main_exe_path = self._resolve_installed_path(self.main_exe)
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
        /LOG=路徑                指定靜默安裝紀錄檔要寫到哪裡（不帶就維持原本
                                 的 %TEMP%\\<AppName>_silent_install_log.txt）

    回傳 (silent: bool, install_dir: str|None, create_desktop_shortcut: bool, log_path: str|None)
    """
    silent = False
    install_dir = None
    create_desktop_shortcut = True
    log_path = None
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
        elif upper.startswith("/LOG="):
            log_path = arg[5:].strip('"')
    return silent, install_dir, create_desktop_shortcut, log_path


def run_silent_install(install_dir=None, create_desktop_shortcut=True, log_path=None):
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
        # log_path（/LOG= 帶進來的路徑）優先；沒帶或寫入失敗（例如指定的
        # 資料夾不存在、沒有寫入權限）都 fallback 回原本的 %TEMP% 路徑，
        # 不讓「紀錄寫不進去」變成整個靜默安裝失敗。
        target_path = log_path
        if target_path:
            try:
                os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)
                with open(target_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(log_lines))
                return exit_code
            except Exception as e:
                log_lines.append(f"[警告] 無法寫入指定的紀錄路徑 {target_path}：{e}，改用預設路徑。")
        try:
            fallback_path = os.path.join(tempfile.gettempdir(), f"{app_name}_silent_install_log.txt")
            with open(fallback_path, "w", encoding="utf-8") as f:
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
    _silent, _cli_install_dir, _cli_desktop_shortcut, _cli_log_path = _parse_cli_args()

    if _silent:
        sys.exit(run_silent_install(_cli_install_dir, _cli_desktop_shortcut, _cli_log_path))

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