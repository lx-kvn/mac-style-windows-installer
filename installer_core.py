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
import ctypes
import subprocess
import threading
import zlib
import webview
from datetime import datetime
from window_drag import WindowDragController
from disk_space import required_install_size, check_drive_space
import file_assoc
import lang_detect
import restart_manager
import system_entries
import explorer_lock_release
import windows_service
import scheduled_task
import restore_point
import install_journal
import install_encryption
import progress_report
import dependency_install
import version_compare
import upgrade
from install_scope import InstallScope, local_appdata_root

# 目前介面 chrome（ui/index.html 裡固定的標籤、按鈕、提示文字）支援的語言，
# 跟 ui/index.html 內嵌的 I18N 翻譯表一一對應。EULA 文字語言不受此限制——
# 開發者可以自訂任意語言代碼，這裡只限制「安裝介面本身」的語言。
SUPPORTED_UI_LANGUAGES = ["zh-TW", "en"]
DEFAULT_UI_LANGUAGE = "zh-TW"

# 真實抓到的 bug：main() 真正建立 pywebview 視窗前，_report_progress()/
# _report_dependency_progress() 就可能被呼叫（例如 install_dependency()
# 在下載階段就會回報進度）。這裡明確給一個 None 預設值，讓
# progress_report.report_progress() 的 `if not window: return` 正常生效；
# 原本沒有這行，純粹是靠這兩個方法各自的 try/except Exception 意外吞掉
# 讀取未賦值全域變數會拋的 NameError，效果剛好一樣，但不是刻意設計的。
window = None


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


# 相依元件偵測/下載/靜默安裝子系統收在 dependency_install.py（見該模組
# 開頭的說明：跟 dependency_defs.py 之間「行為定義在哪」的落差就是這輪
# 收斂的動機）。DEPENDENCY_CHECKERS 保留這個名字重新匯出，向後相容既有
# 測試/呼叫端用 `installer_core.DEPENDENCY_CHECKERS` patch 的寫法。
DEPENDENCY_CHECKERS = dependency_install.DEPENDENCY_CHECKERS


def _is_process_running(exe_name):
    """用 tasklist 檢查指定檔名的行程是否正在執行

    修正紀錄：shell=True 會透過 cmd.exe 執行指令，在 --noconsole 的 GUI 程式裡
    呼叫會短暫跳出一個命令提示字元視窗。加上 CREATE_NO_WINDOW 徹底避免。

    F07：原本以 shell=True 搭配字串拼接組出指令。Windows 檔名允許 `&`、
    `|`、`^` 這些字元，打包端只驗證 main_exe 是否存在於來源資料夾，未限制
    字元——`My&App.exe` 這種檔名會讓 cmd.exe 把 `&` 之後的部分當成另一道
    指令，偵測結果不再對應到那支程式。改成傳入參數陣列並移除 shell=True，
    篩選字串整段當成一個參數交給 tasklist，不再經過 cmd.exe 的解析。同一支
    檔案裡其他 subprocess 呼叫已經是這個寫法。
    """
    if not exe_name:
        return False
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        output = subprocess.check_output(
            ["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/NH"],
            text=True, stderr=subprocess.DEVNULL, creationflags=creationflags,
        )
        return exe_name.lower() in output.lower()
    except Exception:
        return False


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
        self.dependencies_min_version = {}
        self.custom_dependencies = []
        self.bundle_dependencies = []
        self.file_associations = []
        self.doc_icon = ""
        self.doc_icons = {}
        self.add_to_path = False
        self.path_target_exe = ""
        self.local_appdata_files = []
        self.restart_explorer_on_update = False
        # 沒有這個欄位的既有安裝檔一律是傳統引擎，行為完全不變。
        self.install_engine = "traditional"
        # MSIX 引擎才有：內嵌的已簽章套件在資源裡的檔名。
        self.msix_package = ""
        self.no_admin_install = False
        self.custom_install_dir = ""
        self.pre_install_script = ""
        self.post_install_script = ""
        self.windows_service = {}
        self.scheduled_task = {}
        self.create_restore_point_before_install = False
        self.password_protected = False
        # verify_install_password() 成功後指向解密好的暫存資料夾；密碼
        # 驗證通過前一律是 None，_app_contents_dir() 靠這個判斷「還沒
        # 驗證密碼就想拿應用程式檔案」這種不該發生的呼叫順序。
        self._decrypted_payload_dir = None
        # 重入防護，見 trigger_installation()。用鎖而不是單純兩個布林值：
        # pywebview 的 JS API 呼叫各自在自己的執行緒上跑，「檢查旗標」跟
        # 「設定旗標」之間如果不是同一個不可分割的動作，兩個呼叫可能都
        # 通過檢查、然後都進到安裝流程。
        self._install_lock = threading.Lock()
        self._install_in_progress = False
        self._install_completed = False
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
        # 覆蓋安裝（更新既有安裝）的偵測/備份/跨 UAC 移除舊版本協定，
        # 見 upgrade.UpgradeCoordinator 的說明。
        self._upgrade = upgrade.UpgradeCoordinator()
        # close_locking_processes() 如果為了釋放鎖定而強制關過殼層（見
        # explorer_lock_release.py），回傳的狀態物件存在這裡，讓
        # trigger_installation() 結束時（不管成功、失敗、還是中途例外）
        # 都能補做「重啟 explorer.exe / 恢復 AutoRestartShell」。
        self._explorer_forced_down_state = None

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
                    self.dependencies_min_version = config.get("dependencies_min_version", {}) or {}
                    self.custom_dependencies = config.get("custom_dependencies", [])
                    self.bundle_dependencies = config.get("bundle_dependencies", [])
                    self.file_associations = config.get("file_associations", [])
                    self.doc_icon = config.get("doc_icon", "")
                    self.doc_icons = config.get("doc_icons", {})
                    self.add_to_path = bool(config.get("add_to_path", False))
                    self.path_target_exe = config.get("path_target_exe", "")
                    self.local_appdata_files = config.get("local_appdata_files", [])
                    self.restart_explorer_on_update = bool(config.get("restart_explorer_on_update", False))
                    self.install_engine = config.get("install_engine", "traditional") or "traditional"
                    self.msix_package = config.get("msix_package", "")
                    self.no_admin_install = bool(config.get("no_admin_install", False))
                    self.custom_install_dir = config.get("custom_install_dir", "")
                    self.pre_install_script = config.get("pre_install_script", "")
                    self.post_install_script = config.get("post_install_script", "")
                    self.windows_service = config.get("windows_service", {}) or {}
                    self.scheduled_task = config.get("scheduled_task", {}) or {}
                    self.create_restore_point_before_install = bool(config.get("create_restore_point_before_install", False))
                    self.password_protected = bool(config.get("password_protected", False))
        except Exception as e:
            print(f"[提示] 使用預設開發模式: {e}")

    def _build_dependency_checkers(self):
        """把內建的相依元件 checker 跟這次打包時透過 custom_dependencies
        自訂的相依元件合併成一份對照表，實際邏輯收在
        dependency_install.build_checkers()（見該模組說明）。"""
        return dependency_install.build_checkers(self.custom_dependencies, self.dependencies_min_version)

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

    def is_password_protected(self):
        """安裝密碼保護（見 CONTEXT.md「安裝密碼保護」一節）：前端在
        pywebviewready 一開始就要問這個，決定要不要在 EULA 之前插入密碼
        關卡。"""
        return self.password_protected

    def verify_install_password(self, password):
        """驗證密碼並把加密的 app_contents 解密到暫存資料夾，成功回傳
        True、密碼錯誤回傳 False。沒有密碼保護時直接回傳 True（呼叫端
        不需要先呼叫 is_password_protected() 才決定要不要驗證）。

        解密後的暫存資料夾存在 self._decrypted_payload_dir，後續
        _app_contents_dir()／_required_size() 都靠這個取得正確的複製
        來源，取代沒有密碼保護時直接用的 get_resource_path("app_contents")。
        """
        if not self.password_protected:
            return True
        encrypted_file = get_resource_path("app_contents.enc")
        dest_dir = tempfile.mkdtemp(prefix="mswi_payload_")
        try:
            install_encryption.decrypt_to_directory(encrypted_file, dest_dir, password)
        except install_encryption.WrongPasswordError:
            shutil.rmtree(dest_dir, ignore_errors=True)
            return False
        self._decrypted_payload_dir = dest_dir
        return True

    def _app_contents_dir(self):
        """這次安裝要複製的來源資料夾。沒有密碼保護時就是內嵌資源本身；
        有密碼保護時必須先呼叫過 verify_install_password() 成功，才會有
        解密後的暫存資料夾可用——漏掉這個前置呼叫直接拋例外，不要悄悄
        回傳一個不存在或是空的路徑，讓安裝流程用看起來正常、實際上沒有
        任何檔案的來源繼續跑下去。"""
        if self.password_protected:
            if not self._decrypted_payload_dir:
                raise RuntimeError("尚未通過密碼驗證，無法取得應用程式檔案")
            return self._decrypted_payload_dir
        return get_resource_path("app_contents")

    def get_dependency_warnings(self):
        """回傳目前系統缺少的相依元件清單（key + 顯示名稱 + 下載連結），
        不阻擋安裝。前端用 key 呼叫 install_dependency(key) 觸發自動安裝，
        url 保留給自動安裝失敗時的手動下載備援連結。
        """
        return dependency_install.get_warnings(self.dependencies, self._build_dependency_checkers())

    def _report_dependency_progress(self, percent, message):
        """相依元件自動安裝期間的進度推播，寫法比照 _report_progress()，
        但推到前端另一個獨立的進度條（window.updateDependencyInstallProgress），
        因為相依元件安裝跟主程式安裝是兩個不同的畫面，不能共用同一組進度條
        元素。
        """
        global window
        progress_report.report_progress(window, "updateDependencyInstallProgress", percent, message)

    def install_dependency(self, key):
        """使用者在相依元件彈窗按下「自動安裝」時，依序對每個缺少的元件呼叫
        這個方法：下載官方安裝檔到暫存目錄、靜默執行，結束後不管子程序的
        結束碼，一律重新呼叫這個元件自己的登錄表偵測函式確認「現在到底裝
        好了沒」才是最終依據。實際的下載/驗證/安裝協定收在
        dependency_install.install()（見該模組說明），這裡只負責把
        InstallerAPI 的實例狀態（custom_dependencies/bundle_dependencies/
        進度回報 callback）轉成明確參數傳進去。
        """
        return dependency_install.install(
            key, self._build_dependency_checkers(),
            custom_dependencies=self.custom_dependencies,
            bundle_dependencies=self.bundle_dependencies,
            resolve_resource_path=get_resource_path,
            on_progress=self._report_dependency_progress,
        )

    def open_url(self, url):
        """讓前端可以開啟預設瀏覽器前往下載頁"""
        try:
            import webbrowser
            webbrowser.open(url)
            return True
        except Exception:
            return False

    def check_existing_install(self):
        """檢查是否已安裝過同名應用程式並比較版本新舊，實際邏輯收在
        upgrade.check_existing()（見該模組說明）。"""
        return self._upgrade.check_existing(self.app_name, self.version, self._scope)

    def _restore_upgrade_backup(self):
        self._upgrade.restore_backup()

    def _discard_upgrade_backup(self):
        self._upgrade.discard_backup()

    def run_upgrade_uninstall(self, existing_info=None):
        """更新覆蓋安裝流程：先備份舊安裝資料夾，再靜默呼叫舊版本的解除
        安裝助手移除乾淨，之後才繼續安裝新版本。完整協定（備份/跨 UAC
        呼叫/pending-delete 重試/失敗復原）收在 upgrade.UpgradeCoordinator
        （見該模組說明），這裡只負責把 InstallerAPI 的實例狀態轉成明確
        參數傳進去。

        existing_info：呼叫端（trigger_installation()）剛查過的覆蓋安裝
        狀態，往下傳給 UpgradeCoordinator.run() 讓整個流程用同一份快照，
        見該方法的說明（F15）。
        """
        return self._upgrade.run(
            self.app_name, self.version, self._scope,
            self.selected_path, self.restart_explorer_on_update,
            existing_info=existing_info,
        )

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
        progress_report.report_progress(window, "updateInstallProgress", percent, message)

    def _required_size(self):
        """這次安裝的總容量（所有落地位置加總）。登錄表的
        EstimatedSize 欄位用這個值——那個欄位本來就只有一個數字，
        不區分落在哪顆磁碟。"""
        return required_install_size(self._app_contents_dir())

    def _required_size_by_destination(self):
        """把來源檔案依「實際會落到哪個目錄」分組加總，回傳
        `[(落地目錄, 位元組數), ...]`。

        F08：原本一律算在 `selected_path` 所在磁碟上，但
        `local_appdata_files` 指定的檔案實際落在 `_local_appdata_root()`
        （`%LOCALAPPDATA%\\Programs\\<folder_name>`），可能位於另一顆磁碟
        ——那顆磁碟從未被檢查，而目標磁碟的需求量同時被高估。分組的規則
        直接用 `_is_local_appdata_file()`，跟實際複製時決定落點的
        `_resolve_installed_path()` 是同一個判斷，不另外維護一份。
        """
        src_dir = self._app_contents_dir()
        sizes = {}
        for root, _dirs, files in os.walk(src_dir):
            for name in files:
                full_path = os.path.join(root, name)
                rel_path = os.path.relpath(full_path, src_dir)
                dest_dir = (
                    self._local_appdata_root() if self._is_local_appdata_file(rel_path)
                    else self.selected_path
                )
                sizes[dest_dir] = sizes.get(dest_dir, 0) + os.path.getsize(full_path)
        return list(sizes.items())

    def _check_disk_space(self, existing_install_path=""):
        """回傳 `(是否全部足夠, 各磁碟的檢查結果)`，見
        `disk_space.check_drive_space()`。

        existing_install_path：這次是覆蓋安裝時，舊安裝資料夾的位置。
        `upgrade.backup()` 會在移除舊版本之前把整份資料夾複製到 `%TEMP%`，
        那份需求原本完全沒有被計入——`%TEMP%` 所在磁碟不夠時，備份會失敗
        （備份失敗只回傳 None、不擋更新流程），使用者就此失去唯一的復原
        機會，而且事前沒有任何警告。

        密碼保護的安裝把解密內容展開到 `%TEMP%` 這件事不計入：那一步發生在
        `verify_install_password()`，早於整個 `trigger_installation()`，走到
        這裡時那份空間已經實際佔用成功了，再檢查一次沒有意義。
        """
        requirements = self._required_size_by_destination()
        if existing_install_path and os.path.isdir(existing_install_path):
            requirements.append(
                (tempfile.gettempdir(), required_install_size(existing_install_path))
            )
        return check_drive_space(requirements, self.default_path)

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
        #
        # winreg.CreateKey() 一呼叫就會立刻建立機碼，後面 11 個 SetValueEx
        # 是對同一個已存在機碼逐一寫值——如果中途某一個失敗（例如磁碟/登錄表
        # 配額問題），呼叫端的 registry_entry_created 旗標永遠不會被設成
        # True（因為這個函式沒有正常 return），導致 _rollback() 不知道要
        # 清掉這個只寫了一半的機碼，留下永久的孤兒登錄表項目。這裡用
        # try/except 保證：要嘛完整 11 個值都寫成功，要嘛什麼都不留下。
        try:
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
        except Exception:
            try:
                winreg.DeleteKey(hive, reg_path)
            except Exception:
                pass
            raise

    def _create_shortcut(self, desktop=False, log=None):
        """建立開始功能表或桌面捷徑（依賴 pywin32），成功回傳 True。

        跟其他登錄表寫入函式不同：捷徑建立失敗不影響安裝整體成敗，所以
        這裡吞例外、回傳 False，不讓例外往外拋觸發整個安裝回滾。

        回報管道的變化紀錄：最早是無效的 print()（這支 exe 是 --noconsole
        編譯），後來改成會寫進 install_log.txt 的 log()。F05 之後呼叫端
        還會把 False 併入回傳結果的 warnings，顯示在安裝完成畫面上——
        「安裝成功但沒有捷徑」對使用者是看得見的差別（開始功能表裡找不到
        這個應用程式），不該只留在使用者通常不會去看的紀錄檔裡。打包機器
        沒裝 pywin32 時這裡會一律失敗，那個情境本來就會在打包工具的環境
        檢查裡被標示出來（見 packaging_core.check_build_environment()）。
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
            try:
                current, reg_type = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                current, reg_type = "", winreg.REG_EXPAND_SZ
            parts = [p for p in current.split(";") if p]
            if not any(os.path.normcase(p) == os.path.normcase(target_dir) for p in parts):
                parts.append(target_dir)
                winreg.SetValueEx(key, "Path", 0, reg_type, ";".join(parts))
        finally:
            winreg.CloseKey(key)

        # 真實抓到的問題（B11）：上面 SetValueEx 那一步已經真的把值寫進
        # 登錄表了——這個廣播呼叫只是「通知其他視窗環境變數變了」的錦上
        # 添花，不是「PATH 有沒有寫入」的一部分。原本失敗會讓整個函式
        # 往外拋例外，導致呼叫端拿到的 path_directory 停在空字串，
        # _rollback() 因此誤判成「PATH 根本沒寫入」而略過移除，即使
        # 登錄表其實已經被改了，回滾後留下裝到一半的安裝路徑殘留在 PATH
        # 裡。改成 best-effort：失敗只吞掉，不影響 target_dir 的回傳。
        try:
            HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG = 0xFFFF, 0x1A, 0x0002
            result = ctypes.c_long()
            ctypes.windll.user32.SendMessageTimeoutW(
                HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", SMTO_ABORTIFHUNG, 5000, ctypes.byref(result)
            )
        except Exception:
            pass
        return target_dir

    # ------------------------------------------------------------------
    # 主安裝流程
    # ------------------------------------------------------------------

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
                  file_associations_registered=False, path_directory=None,
                  journal=None,
                  pre_existing_rel_paths=None):
        """安裝失敗時的回滾：把這次安裝已經寫入的東西清掉，盡量讓系統回到
        安裝前的乾淨狀態。只清掉『這次安裝這一輪自己寫入的部分』，不會動到
        selected_path（或 local_appdata_files 落地的 _local_appdata_root()）
        底下其他既有內容（例如使用者選了一個已經有東西的資料夾）。

        真實抓到的缺口：原本只清複製出去的檔案，但安裝流程後段還會依序寫入
        解除安裝登錄表項目/捷徑/檔案關聯/Windows 服務/排程工作/PATH——這幾步
        任何一步後面的步驟失敗，前面已經成功寫入的部分完全不會被回滾。這裡
        依「後寫的先復原」順序（跟安裝時 _register_uninstall_entry →
        _create_shortcut → file_assoc.register → windows_service.create_service
        → scheduled_task.create_scheduled_task → _add_to_path_env 的順序相反）
        補上這幾類。

        journal（A1 架構後續）：windows_service/scheduled_task 這兩類改用
        install_journal.InstallJournal 記錄「做了什麼 + 怎麼復原」，取代
        原本各自一個 windows_service_name/scheduled_task_name 旗標參數——
        呼叫端建立服務/排程工作成功的當下就把復原動作記進 journal，這裡
        只要呼叫 journal.unwind() 依相反順序復原，不用再為每一類新的
        系統資源多加一個旗標參數、多一段對應的 if。這兩項成功建立後完全
        沒有寫進任何 manifest（安裝在這裡失敗時，manifest 根本還沒寫），
        uninstall.py 永遠不會知道它們存在，是唯一還能清掉它們的地方，
        因此順序上要在 registry/shortcuts/file_associations 之前——跟
        registry_entry_created 等既有的旗標式回滾維持同一套順序，暫時
        不強行統一寫法（見對應的 ADR：架構稽核 A5）。

        pre_existing_rel_paths：真實抓到的問題（B7）——這個文件字串本身
        宣稱「不會動到 selected_path 底下其他既有內容」，但 copied_rel_paths
        記錄的是「這次安裝複製過的檔案」，不是「這次安裝新建立的檔案」。
        使用者選了一個已經有同名檔案的資料夾時，那個檔案會被複製覆蓋、
        一樣記進 copied_rel_paths，安裝失敗回滾時原本會被整個刪掉——
        使用者原本的檔案就這樣憑空消失，而不是「至少維持覆蓋後的內容」
        這種可以接受的次佳結果。這裡列出的相對路徑一律跳過刪除。
        """
        pre_existing_rel_paths = pre_existing_rel_paths or set()
        removed = 0
        for rel in copied_rel_paths:
            if rel in pre_existing_rel_paths:
                continue
            try:
                path = self._resolve_installed_path(rel)
                if os.path.exists(path):
                    os.remove(path)
                    removed += 1
            except Exception:
                pass
        system_entries.cleanup_empty_dirs(self.selected_path)
        if self.local_appdata_files:
            system_entries.cleanup_empty_dirs(self._local_appdata_root())
        if log:
            log(f"安裝失敗，已回滾刪除 {removed} 個已複製的檔案")

        if path_directory:
            system_entries.remove_from_path(path_directory, self.no_admin_install)
        if journal:
            journal.unwind(log=log)
        if file_associations_registered:
            file_assoc.unregister(self.file_associations, no_admin_install=self.no_admin_install)
        for desktop in (shortcuts_created or []):
            system_entries.remove_shortcut(self.app_name, desktop=desktop, no_admin_install=self.no_admin_install)
        if registry_entry_created:
            system_entries.remove_registry_entry(self.app_name, self.no_admin_install)

    def trigger_installation(self, create_desktop_shortcut=True, skip_process_check=False):
        """薄包裝：實際安裝邏輯在 _trigger_installation_impl()。這裡負責兩件
        跟安裝本身無關、但每一條出口都要照顧到的事。

        一、不管這次安裝成功、失敗、還是中途拋出未預期例外，只要之前
        close_locking_processes() 為了釋放鎖定強制關過殼層，最後都要補做
        重啟 explorer.exe / 恢復 AutoRestartShell，用 try/finally 涵蓋所有
        出口，不能散在各個 return 分支各自補一次、容易漏掉。

        二、重入防護。真實抓到的缺陷（使用者實測，2026-08-30）：安裝一開始
        主畫面的應用程式圖示就恢復可拖曳，使用者可以在安裝進行中、或成功
        彈窗出現之後再放一次，把安裝觸發第二次。前端會擋（見 ui/index.html
        的安裝狀態），但這裡也要擋——這是 JS API 的公開方法，前端狀態可以
        被繞過。

        兩種要擋的情況分別對應不同後果：
          - 安裝**進行中**再被呼叫：兩個安裝流程並行（pywebview 的 JS API
            呼叫各自在自己的執行緒上跑），同時複製檔案到同一個目錄、同時
            寫安裝清單、各自可能觸發回滾。單一實例鎖擋不到這個，它是行程
            層級、在程式啟動時取得一次。
          - 安裝**已成功**後再被呼叫：密碼保護的安裝會在成功後清掉解密出來
            的暫存資料夾，第二次走到 _app_contents_dir() 直接拋例外（使用者
            看到的「尚未通過密碼驗證」就是這個）；沒有密碼保護的安裝更糟，
            它不會報錯，會安靜地再裝一次。

        失敗、process_running、file_locked 都要放行重試：那是既有且正確的
        流程（前端的 closeRunningAppAndRetry／closeLockingProcessAndRetry
        會重新呼叫這個方法）。只有真正成功才latch住。
        """
        with self._install_lock:
            if self._install_in_progress:
                return {"status": "error", "message": "安裝正在進行中，請稍候。"}
            if self._install_completed:
                return {
                    "status": "error",
                    "message": "這個安裝程式已經完成安裝，如果需要重新安裝請重新開啟安裝程式。",
                }
            self._install_in_progress = True

        try:
            result = self._trigger_installation_impl(create_desktop_shortcut, skip_process_check)
            if isinstance(result, dict) and result.get("status") == "success":
                self._install_completed = True
            return result
        finally:
            self._install_in_progress = False
            explorer_lock_release.restore_after_lock_release(self._explorer_forced_down_state)
            self._explorer_forced_down_state = None

    def _trigger_installation_impl(self, create_desktop_shortcut=True, skip_process_check=False):
        log_lines = [f"=== {self.app_name} 安裝紀錄 {datetime.now().isoformat()} ==="]

        def log(msg):
            log_lines.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

        try:
            return self._trigger_installation_impl_inner(create_desktop_shortcut, skip_process_check, log_lines, log)
        finally:
            # 真實抓到的 bug（F24）：這份 log 原本只在安裝完全成功那條路徑
            # 才會寫出，十幾個提早失敗的 return 分支完全沒有寫——偏偏失敗
            # 時的診斷資訊才是這份 log 真正有用的時候，這支 exe 是
            # --noconsole 編譯的，使用者/事後排查完全沒有其他管道看到這些
            # 訊息。改成不管從哪個出口離開都會嘗試寫一份：優先寫進
            # selected_path（安裝成功、或失敗但目錄已經建立時，跟使用者
            # 直覺會去找的位置一致），selected_path 還不存在（或寫不進去、
            # 或被 _rollback() 清空刪掉）時 fallback 到 %TEMP%（跟
            # run_silent_install() 既有的 fallback 慣例一致）。
            content = "\n".join(log_lines)
            wrote = False
            try:
                if os.path.isdir(self.selected_path):
                    with open(os.path.join(self.selected_path, "install_log.txt"), "w", encoding="utf-8") as f:
                        f.write(content)
                    wrote = True
            except Exception:
                pass
            if not wrote:
                try:
                    fallback_path = os.path.join(tempfile.gettempdir(), f"{self.app_name}_install_log.txt")
                    with open(fallback_path, "w", encoding="utf-8") as f:
                        f.write(content)
                except Exception:
                    pass
            # 安裝密碼保護（見 CONTEXT.md）：verify_install_password() 解密出來
            # 的暫存資料夾裝的是明文應用程式檔案，不管這次安裝成功、失敗、
            # 還是中途拋出未預期例外，都不該讓這份明文留在磁碟上。
            if self._decrypted_payload_dir:
                shutil.rmtree(self._decrypted_payload_dir, ignore_errors=True)
                self._decrypted_payload_dir = None

    def _install_msix(self, log):
        """MSIX 引擎的安裝：把內嵌的已簽章套件交給 Windows 的套件引擎。

        流程的順序與理由在 msix_install.run()；這裡只負責把這個安裝檔手上
        的東西（內嵌套件的路徑、既有安裝的偵測與移除、進度回報）接上去。
        """
        import msix_deploy
        import msix_install

        package_path = get_resource_path(self.msix_package or "app.msix")

        def deploy(path, progress=None):
            return msix_deploy.deploy(path, progress=progress)

        def report_progress(percentage):
            # 第十一輪 CI 探針確認進度回報是真實百分比，因此這裡直接轉呈，
            # 不需要退化為不確定進度動畫（第二輪決議第六項的備案未被觸發）。
            self._report_progress(percentage, "正在安裝...")

        return msix_install.run(
            package_path,
            check_existing=self.check_existing_install,
            remove_existing=lambda info: self.run_upgrade_uninstall(info),
            deploy=deploy,
            progress=report_progress,
            log=log,
            package_must_exist=True,
        )

    def _trigger_installation_impl_inner(self, create_desktop_shortcut, skip_process_check, log_lines, log):
        # MSIX 引擎走另一條路徑。兩者幾乎不共用邏輯——傳統路徑做的是
        # 「自己複製檔案、寫登錄表、產生 uninstall.exe」，MSIX 路徑做的是
        # 「把已簽章的套件交給系統」——因此在最上層分流，而不是在傳統
        # 流程裡插判斷。走錯路徑的後果是兩種落地方式同時發生。
        if self.install_engine == "msix":
            return self._install_msix(log)

        # 這一整批都提前在最外層宣告：任何階段（複製迴圈開始前/開始後、
        # 登錄表/捷徑/檔案關聯/服務/排程工作/PATH 任一步之後）才失敗，
        # 下面兩個 except 區塊都要能安全參照，讓 _rollback() 知道哪些系統
        # 項目已經真的寫入、需要回滾（見 _rollback() 的說明）。
        #
        # 這些原本是以參數的形式從 _trigger_installation_impl()（外層
        # try/finally 包裝）傳進來，但外層那份從來沒有真的被讀取過——
        # copied_rel_paths 雖然透過 .append() 修改會反映到外層的同一個
        # list 物件，但外層本來就不會再讀它；current_copy_target/
        # registry_entry_created 等其餘的是用 `=` 整個重新賦值，對外層
        # 傳進來的參數完全沒有任何效果，是死參數。改成直接在這裡宣告，
        # 介面更誠實（外層真正需要的只有 log_lines，透過閉包的 log()
        # 就夠了，不需要這一整批）。
        warnings = []
        pre_existing_rel_paths = set()
        copied_rel_paths = []
        current_copy_target = None
        registry_entry_created = False
        shortcuts_created = []
        file_associations_registered = False
        path_directory = None
        windows_service_name = ""
        scheduled_task_name = ""
        journal = install_journal.InstallJournal()
        try:
            # F15：這裡（以及下面磁碟空間不足、主程式執行中兩處）原本都會
            # 先呼叫一次 _restore_upgrade_backup()。三處都位於「移除舊版本」
            # 之前，此時備份根本還沒建立（upgrade.backup() 是在
            # UpgradeCoordinator.run() 內部才呼叫的），必為空操作——留著只會
            # 讓讀的人以為這幾條路徑真的有東西要復原。備份建立之後才失敗的
            # 路徑仍然照舊復原，見下面兩個 except 區塊。
            src_dir = self._app_contents_dir()
            if not os.path.exists(src_dir):
                return {"status": "error", "message": "安裝失敗：找不到內建軟體資源！"}

            # 覆蓋安裝偵測提前到磁碟空間檢查之前：這是一次唯讀的登錄表查詢，
            # 沒有副作用，但磁碟空間檢查需要知道「這次會不會把舊安裝資料夾
            # 整份備份到 %TEMP%」才算得出完整的需求量（F08）。查詢結果留給
            # 下面真正執行移除舊版本的那一步共用，不重新查一次。
            existing = self.check_existing_install()
            existing_install_path = existing.get("install_path", "") if existing.get("exists") else ""

            # 磁碟空間檢查（依落地磁碟分組，可能不只一顆——見 _check_disk_space()）
            ok, drive_reports = self._check_disk_space(existing_install_path)
            if not ok:
                detail = "、".join(
                    f"{d['drive']} 需要約 {d['required'] // (1024 * 1024)} MB、"
                    f"剩餘 {d['free'] // (1024 * 1024)} MB"
                    for d in drive_reports if not d["sufficient"]
                )
                return {"status": "error", "message": f"磁碟空間不足：{detail}。"}
            if drive_reports:
                log(
                    "磁碟空間檢查通過（"
                    + "、".join(
                        f"{d['drive']} 約需 {d['required'] // (1024 * 1024)} MB" for d in drive_reports
                    )
                    + "）"
                )
            else:
                # 需求量全為 0，或所有落地磁碟都查不到用量——沒有東西可以
                # 回報，但也不是失敗（見 disk_space.check_drive_space()）。
                log("磁碟空間檢查通過（沒有可回報的磁碟需求量）")

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
                return {
                    "status": "process_running",
                    "message": f"偵測到「{self.main_exe}」正在執行中。\n請先關閉程式後再繼續安裝。",
                }

            # 覆蓋安裝：使用者在拖曳圖示前的彈窗只是「確認要不要繼續」，真正
            # 刪除舊版本檔案的動作延後到這裡——使用者已經實際拖曳圖示、確定要
            # 安裝了才動手，而不是彈窗一按確認鈕、使用者都還沒觸發安裝就先刪。
            # run_upgrade_uninstall() 內部會先備份舊安裝資料夾，失敗時自己復原。
            # existing 沿用上面磁碟空間檢查前查到的那一次結果，不重查。
            if existing.get("exists"):
                self._report_progress(3, "正在移除舊版本...")
                upgrade_result = self.run_upgrade_uninstall(existing_info=existing)
                if upgrade_result.get("status") == "error":
                    return {"status": "error", "message": upgrade_result.get("message")}

            if not os.path.exists(self.selected_path):
                os.makedirs(self.selected_path)
            log(f"安裝目標路徑: {self.selected_path}")

            # 真實抓到的問題（B15）：系統還原點建立原本在磁碟空間檢查之後
            # 就立刻執行，發生在「主程式執行中」「舊版本移除失敗」這幾個
            # 仍然會整個中止安裝的檢查之前——使用者被要求先關閉程式再重試
            # 時，已經憑空多了一個其實對應不到任何真實安裝的還原點，白白
            # 消耗還原點的儲存空間（VSS 儲存是有限的環狀空間，可能因此
            # 擠掉一個真正有用的舊還原點）。改成延後到這裡：這些還會中止
            # 安裝的檢查都已經通過、真正要開始動手（安裝目錄已建立）之前。
            if self.create_restore_point_before_install:
                # 措辭刻意不寫「已建立系統還原點」：Windows 8 之後如果
                # 24 小時內已經建立過還原點，SRSetRestorePoint 會略過真的
                # 建立新的一份，但仍然回傳成功（見 restore_point.py 的
                # 已知限制說明）——這裡的回傳值只能保證「呼叫成功、系統上
                # 有近期可用的還原點」，不能保證這次真的新建了一個。
                if restore_point.create_restore_point(f"安裝 {self.app_name} {self.version}"):
                    log("系統還原點已就緒（可能是新建立的，也可能是 24 小時內的既有還原點）")
                else:
                    # F05：這種「失敗但不中止安裝」的情況原本只寫進 log 檔，
                    # 跟服務/排程工作建立失敗（B17 已改成併入 warnings）性質
                    # 相同、嚴重性相當，處理方式卻分成兩類。使用者以為安裝前
                    # 已經有還原點可以回頭，實際上沒有——這正是 B17 當初要
                    # 解決的那種「使用者不會知道」的情況。
                    msg = "建立系統還原點失敗（不影響安裝結果）"
                    log(f"[警告] {msg}")
                    warnings.append(msg)

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
                if os.path.exists(dest_f):
                    pre_existing_rel_paths.add(rel)
                current_copy_target = dest_f
                # 真實抓到的問題（B10）：rel 原本要等 shutil.copy2() 完全
                # 成功、完整性驗證都跑完才會被記進 copied_rel_paths——
                # copy2() 如果中途拋例外（例如磁碟滿了）但已經在目的地
                # 寫入部分內容，這個部分檔案就不會被記錄，_rollback()
                # 也就不會嘗試清掉它，留下孤兒殘留檔案。改成在複製之前
                # 就先記錄；_rollback() 本來就用 os.path.exists() 判斷要
                # 不要刪，複製根本沒開始寫的情況一樣安全（no-op）。
                copied_rel_paths.append(rel)
                shutil.copy2(src_f, dest_f)

                # 完整性驗證：先比大小（快），大小一致才進一步比 checksum（較慢但更可靠，
                # 抓得出「大小剛好一樣但內容其實壞掉」這種 size 比對抓不到的情況）。
                if os.path.getsize(src_f) != os.path.getsize(dest_f):
                    integrity_errors.append(rel)
                elif _file_checksum(src_f) != _file_checksum(dest_f):
                    integrity_errors.append(rel)

                percent = int((i + 1) / total * 80)  # 複製階段佔整體流程的 0-80%
                if percent != last_reported:
                    self._report_progress(percent, f"正在複製檔案 ({i + 1}/{total})...")
                    last_reported = percent

            if integrity_errors:
                log(f"完整性驗證失敗的檔案: {integrity_errors}")
                self._rollback(copied_rel_paths, log, pre_existing_rel_paths=pre_existing_rel_paths)
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
            # F05：捷徑建立失敗同樣併入 warnings。沒有主程式時本來就沒有
            # 捷徑可以建立，那不是失敗，不進 warnings。
            if self.main_exe:
                if self._create_shortcut(desktop=False, log=log):
                    shortcuts_created.append(False)
                else:
                    msg = "建立開始功能表捷徑失敗（不影響安裝結果）"
                    log(f"[警告] {msg}")
                    warnings.append(msg)
                if create_desktop_shortcut:
                    if self._create_shortcut(desktop=True, log=log):
                        shortcuts_created.append(True)
                    else:
                        msg = "建立桌面捷徑失敗（不影響安裝結果）"
                        log(f"[警告] {msg}")
                        warnings.append(msg)
            if self.file_associations:
                main_exe_path = self._resolve_installed_path(self.main_exe)
                icon_refs = self._resolve_doc_icon_refs(main_exe_path)
                try:
                    file_assoc.register(
                        self.file_associations, main_exe_path, self.app_name, icon_refs, log=log,
                        no_admin_install=self.no_admin_install,
                    )
                    file_associations_registered = True
                except Exception as e:
                    raise RuntimeError(f"檔案關聯註冊失敗：{e}") from e
                log(f"已註冊檔案關聯: {self.file_associations}")
            if self.windows_service.get("service_name") and self.windows_service.get("exe_relative_path"):
                service_exe_path = self._resolve_installed_path(self.windows_service["exe_relative_path"])
                created = windows_service.create_service(
                    self.windows_service["service_name"], service_exe_path,
                    display_name=self.windows_service.get("display_name"),
                    start_type=self.windows_service.get("start_type", "auto"),
                )
                if created:
                    windows_service_name = self.windows_service["service_name"]
                    journal.record(
                        f"Windows 服務: {windows_service_name}",
                        lambda name=windows_service_name: windows_service.remove_service(name),
                    )
                    log(f"已建立 Windows 服務: {windows_service_name}")
                else:
                    msg = f"建立 Windows 服務「{self.windows_service['service_name']}」失敗（不影響安裝結果）"
                    log(f"[警告] {msg}")
                    warnings.append(msg)

            if self.scheduled_task.get("task_name") and self.scheduled_task.get("exe_relative_path"):
                task_exe_path = self._resolve_installed_path(self.scheduled_task["exe_relative_path"])
                created = scheduled_task.create_scheduled_task(
                    self.scheduled_task["task_name"], task_exe_path,
                    trigger=self.scheduled_task.get("trigger", "onlogon"),
                )
                if created:
                    scheduled_task_name = self.scheduled_task["task_name"]
                    journal.record(
                        f"排程工作: {scheduled_task_name}",
                        lambda name=scheduled_task_name: scheduled_task.remove_scheduled_task(name),
                    )
                    log(f"已建立排程工作: {scheduled_task_name}")
                else:
                    msg = f"建立排程工作「{self.scheduled_task['task_name']}」失敗（不影響安裝結果）"
                    log(f"[警告] {msg}")
                    warnings.append(msg)

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
                ok, script_error = self._run_install_script(self.post_install_script)
                if ok:
                    log("已執行安裝後置腳本")
                else:
                    # F05：跟服務/排程工作建立失敗同一種處理方式，不再只寫 log。
                    msg = f"安裝後置腳本執行失敗（不影響安裝結果）: {script_error}"
                    log(f"[警告] {msg}")
                    warnings.append(msg)

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
                "windows_service_name": windows_service_name,
                "scheduled_task_name": scheduled_task_name,
                "installed_at": datetime.now().isoformat(),
            }
            with open(os.path.join(self.selected_path, "install_manifest.json"), "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)

            # install_log.txt 由外層 _trigger_installation_impl() 的 finally
            # 統一寫出（見該處說明），這裡不用再寫一次。

            self._report_progress(100, "安裝完成")
            self._discard_upgrade_backup()

            main_exe_path = self._resolve_installed_path(self.main_exe) if self.main_exe else ""
            return {
                "status": "success", "message": "安裝成功", "main_exe_path": main_exe_path,
                "warnings": warnings,
            }

        except OSError as e:
            self._rollback(
                copied_rel_paths, log,
                registry_entry_created=registry_entry_created, shortcuts_created=shortcuts_created,
                file_associations_registered=file_associations_registered, path_directory=path_directory,
                journal=journal,
                pre_existing_rel_paths=pre_existing_rel_paths,
            )
            self._restore_upgrade_backup()
            message = self._describe_install_os_error(e, current_copy_target)
            if self._is_lock_violation(e) and current_copy_target:
                processes = restart_manager.find_locking_processes([current_copy_target])
                if processes:
                    return {
                        "status": "file_locked", "message": message,
                        "processes": [{"pid": pid, "name": name} for pid, name in processes],
                        "path": current_copy_target,
                    }
            return {"status": "error", "message": message}
        except Exception as e:
            self._rollback(
                copied_rel_paths, log,
                registry_entry_created=registry_entry_created, shortcuts_created=shortcuts_created,
                file_associations_registered=file_associations_registered, path_directory=path_directory,
                journal=journal,
                pre_existing_rel_paths=pre_existing_rel_paths,
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
                f"請先關閉相關程式後再重試安裝。\n"
                f"若按下「關閉此程式」後問題持續發生，也可能是防毒/安全軟體攔截了"
                f"終止系統關鍵行程（例如檔案總管）的動作，請確認相關防護設定是否允許此操作。"
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

    def _log_explorer_lock_release(self, msg):
        """explorer_lock_release.py 各步驟的 log(msg) callback 落地成一個
        固定、事後可以翻閱的除錯紀錄檔（%TEMP% 底下，累加寫入）。

        真實抓到的問題：一旦「按下關閉此程式後，砍 explorer.exe 看起來
        沒效果」，如果完全沒有留下任何痕跡，事後根本無從判斷是「這個 pid
        沒被正確解析成 explorer.exe」「taskkill 有跑但失敗」還是其他原因，
        只能靠猜——所以這裡一定要把每一步實際發生的事寫下來，供下次重現
        問題時直接翻紀錄檔，而不是繼續憑空推測。best-effort，寫入失敗
        （例如 %TEMP% 不可寫）不影響安裝流程本身。
        """
        try:
            log_path = os.path.join(tempfile.gettempdir(), "mswi_explorer_lock_debug.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().isoformat()}] {msg}\n")
        except Exception:
            pass

    def close_locking_processes(self, processes, path=None):
        """使用者在安裝失敗跳出的『檔案使用中』畫面按下「關閉此程式」時
        呼叫：processes 是前端原封不動把 trigger_installation() 回傳的
        file_locked 狀態裡的 processes 傳回來的 [{"pid":.., "name":..}, ...]，
        path 是同一個狀態裡的 path（目前正在寫入、被鎖住的檔案路徑）。

        實際的釋放邏輯（先只關閉瀏覽 path 的檔案總管視窗，不夠才暫停
        AutoRestartShell、強制關殼層）收在 explorer_lock_release.py，這裡
        只是薄包裝——把回傳的 forced_down 狀態存起來，讓 trigger_installation()
        之後補做「重啟 explorer.exe / 恢復 AutoRestartShell」，並把
        explorer_lock_release.py 內部的 log(msg) 接到 _log_explorer_lock_release()。
        """
        self._explorer_forced_down_state = explorer_lock_release.release_locking_processes(
            processes, path=path, log=self._log_explorer_lock_release,
        )

    def close_running_main_exe(self):
        """使用者在「偵測到主程式執行中」的彈窗按下「關閉程式並繼續安裝」時
        呼叫：強制關閉正在執行的主程式，讓前端可以接著重新呼叫
        trigger_installation()。跟 uninstall.py 同樣情境共用
        system_entries.kill_process_by_name()。
        """
        return system_entries.kill_process_by_name(self.main_exe)

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
        if self._upgrade.backup_path:
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
        /PASSWORD=密碼           安裝密碼保護（見 CONTEXT.md）開啟時，靜默
                                 安裝用這個帶密碼——比照 Inno Setup 既有的
                                 /PASSWORD= 慣例。

    回傳 (silent: bool, install_dir: str|None, create_desktop_shortcut: bool, log_path: str|None, password: str|None)
    """
    silent = False
    install_dir = None
    create_desktop_shortcut = True
    log_path = None
    password = None
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
        elif upper.startswith("/PASSWORD="):
            password = arg[len("/PASSWORD="):].strip('"')
    return silent, install_dir, create_desktop_shortcut, log_path, password


def run_silent_install(install_dir=None, create_desktop_shortcut=True, log_path=None, password=None):
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

    # 安裝密碼保護（見 CONTEXT.md「安裝密碼保護」一節）：靜默模式不能跳出
    # 任何視窗、不能卡住等輸入，密碼缺少或錯誤直接中止並回傳非 0 exit
    # code，原因寫進這份靜默安裝 log——跟一般靜默安裝錯誤走同一套回報
    # 管道，不是另外開一個特殊分支。
    if api.password_protected and not api.verify_install_password(password or ""):
        log("[錯誤] 這個安裝檔有密碼保護，命令列缺少 /PASSWORD= 或密碼錯誤。")
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
        # F01：安裝過程收集到的非致命失敗（服務/排程工作/還原點/後置腳本/
        # 捷徑）原本只存在於回傳結果裡，靜默安裝完全沒有讀取，等於這些
        # 警告在無人值守情境下沒有任何出口。跟靜默解除安裝同一種做法：
        # 至少寫進這份紀錄檔，不要讓 exit code 0 掩蓋掉「其實有幾項沒成功」。
        install_warnings = result.get("warnings") or []
        for w in install_warnings:
            log(f"[警告] {w}")
        return write_log_and_return(api.app_name, 0)
    else:
        log(f"[錯誤] {result.get('message')}")
        return write_log_and_return(api.app_name, 1)


if __name__ == '__main__':
    _silent, _cli_install_dir, _cli_desktop_shortcut, _cli_log_path, _cli_password = _parse_cli_args()

    if _silent:
        sys.exit(run_silent_install(_cli_install_dir, _cli_desktop_shortcut, _cli_log_path, _cli_password))

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