"""
uninstall.py
------------
解除安裝助手（業界規範版重寫）。

改動重點：
  - 不再無差別刪除安裝目錄下所有檔案，改成讀取安裝時寫入的 install_manifest.json，
    只刪除清單記錄的檔案，並清掉登錄表、開始功能表/桌面捷徑、檔案關聯、PATH。
    使用者事後在該目錄下自己產生的檔案（設定檔、輸出、使用紀錄）不會被誤刪。
    找不到清單時（例如舊版本安裝的、或清單被手動刪除）才退回整個資料夾清除，
    並在畫面上印出警告。
  - 新增 --silent 參數：供 installer_core.py 的「偵測到舊版本，更新覆蓋」流程呼叫，
    跳過確認彈窗，但仍然完整跑登錄表/捷徑/PATH/檔案關聯清理與 log。
  - 解除安裝前檢查主程式是否正在執行，執行中則中止並提示使用者先關閉程式，
    避免刪到一半被檔案鎖定卡住、留下殘骸。
  - log 檔改寫到 %TEMP%，因為安裝目錄本身即將被整個刪除，寫在裡面沒有意義。
  - 沿用上一輪修正：自我刪除前先 cd /d 明確定位到安裝目錄，避免刪錯路徑。

重要修正（這輪抓到的 bug）：
  原本清單式刪除做得很仔細（只刪清單記錄的檔案），但最後自我刪除那一步固定用
  `rmdir /s /q` 把整個安裝目錄無差別砍光，完全沒管前面清單式刪除刻意保留了什麼——
  等於前面的細心白做，使用者自己在安裝目錄裡產生的檔案照樣會被清單式刪除
  「跳過」之後、又被最後這個 rmdir 一次清掉，兩種做法混在一起實質上沒有差異。
  現在改成：清單式刪除跑完後，先確認資料夾裡是不是真的只剩自己（uninstall.exe）；
  真的清空了才整個 rmdir，資料夾裡還有其他東西（使用者自己的資料，或是找不到
  清單、退回整個清除的情況）就分開處理——保留資料夾的話只刪自己，
  真的要整個清空的話才連資料夾一起 rmdir。

新增：restart_explorer_on_update（打包時永遠內建開啟，不是選填項——見
packaging_core.py 的說明：最終要不要真的結束鎖定的程式，互動式解除安裝
一定會先跳確認對話框問過使用者，讓開發者關掉這個偵測反而只是徒增要理解
的設定項）。有些應用程式會在檔案總管的殼層擴充功能（Shell Extension DLL）
或其他背景進程裡持有安裝目錄下的檔案控制代碼，更新覆蓋安裝或解除安裝時
刪不掉、也跟系統管理員權限無關。解除安裝在刪除檔案前會用 Windows 官方的
Restart Manager API（restart_manager.py，跟 Windows Installer/PowerToys
File Locksmith 判斷「這個檔案被誰鎖住」用的是同一套機制）實際偵測是哪些
進程鎖住了要刪除的檔案，逐一結束那些進程（不是無差別假設一定是
explorer.exe），見 _kill_processes()。無人值守（--silent）情境直接套用；
互動式（使用者手動解除安裝）額外跳確認對話框，列出實際偵測到的進程
名稱，取得同意才真的結束，見 _confirm_kill_locking_processes()。

（曾經有一版在這裡結束掉 explorer.exe 後會主動呼叫
subprocess.Popen(["explorer.exe"]) 重啟它——後來拿掉了：實測發現這個
呼叫會跳出一個瀏覽視窗，代表呼叫當下 shell 其實已經被復原了，這一步
已經是多餘、還會多跳出一個使用者沒有要求的視窗，見下方修正紀錄。）

修正紀錄（真實抓到的 bug）：這個選項原本只在無人值守（--silent）情境套用，
互動式手動解除安裝一律略過，理由是「不該無預警把使用者的檔案總管視窗全部
關掉」——但這麼一來，只要應用程式有殼層擴充功能，使用者手動解除安裝永遠
不會釋放被鎖住的 DLL，檔案留在安裝目錄刪不掉（刪除失敗被靜默吞掉，只寫進
log），下次重新安裝到同一路徑覆寫這個仍被鎖住的 DLL 就會失敗，還顯示成
「權限不足」，容易誤導使用者以為要用系統管理員身分重試（其實無關）。現在
改成互動情境也套用同一個設定，只是額外跳確認對話框，而不是完全略過。

修正紀錄（真實抓到的 bug）：這個選項原本寫死「一定是 explorer.exe 鎖住」，
直接 taskkill /im explorer.exe，只涵蓋殼層擴充功能這一種特定情境——如果
卡住檔案的其實是別的進程，完全偵測不到也處理不了，治標不治本。現在改用
Restart Manager API 實際偵測是哪些進程持有這些檔案的控制代碼，逐一結束
真正的鎖定者；只有偵測到的進程剛好是 explorer.exe 才會自動重啟它，其他
進程（通常是使用者自己的應用程式）不會被自動重新開啟。

修正紀錄（真實抓到的 bug）：這個設定原本只看這支 exe 自己的
install_manifest.json，但「更新覆蓋安裝」情境下被呼叫的是舊版本的
uninstall.exe，讀到的是舊版本安裝當下寫的舊 manifest，跟使用者這次重新
打包時的新設定是兩回事，導致行為隨每次安裝嘗試留下的 manifest 版本不同而
時好時壞。現在新增 --restart-explorer 命令列旗標：由呼叫端（新版本的
installer_core.py）帶著自己這次的設定明確傳進來，覆蓋掉舊 manifest 的值；
沒帶這個旗標時（手動雙擊解除安裝、或不是被更新流程觸發的純靜默解除安裝）
才退回讀 manifest 自己的設定。

修正紀錄（真實抓到的 bug，導致「安裝程式回報成功，但檔案沒有複製完整」，
多發生在更新覆蓋安裝）：main() 尾端的自我刪除是 fire-and-forget（先前景
清理完，最後才呼叫不等待的 subprocess.Popen() 開一個背景 cmd.exe，
用 ping 製造約 1 秒延遲後才真正 del/rmdir）。installer_core.py 的
run_upgrade_uninstall() 是用 subprocess.run() 同步呼叫這支舊版
uninstall.exe，行程一結束就繼續複製新版本檔案——這時候背景那個延遲後
才會執行的 rmdir /s /q 根本還沒發生，如果複製時間跨過那個延遲視窗，
背景指令觸發時會把整個資料夾（含已經複製好的新檔案）砍掉。現在新增
--upgrade 命令列旗標：run_upgrade_uninstall() 呼叫時一律帶上，
self_delete.schedule_if_needed() 判斷不是 --upgrade 時才會排這段背景指令——
這支舊版 exe 的行程已經確定結束、不需要延遲刪除，資料夾跟 uninstall.exe
本身都會被新版本安裝流程直接複製覆蓋，不需要也不該被刪除或重建。
一般手動雙擊解除安裝、或不是被更新流程觸發的純靜默解除安裝，沒有這個
旗標，行為完全不變。
"""

import os
import sys
import winreg
import shutil
import json
import ctypes
import tempfile
import time
import subprocess
import webview
from datetime import datetime
import file_assoc
import restart_manager
import lang_detect
import self_delete
import system_entries
from window_drag import WindowDragController

# 跟 installer_core.py 的介面語言支援範圍一致：解除安裝助手的畫面 chrome
# 只認這兩種，跟安裝端同一套規則。
SUPPORTED_UI_LANGUAGES = ["zh-TW", "en"]
DEFAULT_UI_LANGUAGE = "zh-TW"


def get_resource_path(relative_path):
    """獲取資源絕對路徑，相容 PyInstaller 單一檔案打包環境（照抄
    installer_core.py 的版本——這支 exe 現在也需要找到內嵌的
    ui/uninstall.html）。"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def is_process_running(exe_name):
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


# 登錄表項目/捷徑/PATH 的實際移除邏輯收在 system_entries.py（installer_core.py
# 安裝失敗時的 rollback 也共用同一份實作）。這裡包一層薄的呼叫，明確把這個
# 檔案最上面 import 的那個 winreg 名字（測試用 mock.patch.object(un, "winreg",
# fake) 直接換掉）當 registry 參數傳進去，不能讓 system_entries 自己另外
# import 一份真的 winreg。
def remove_registry_entry(app_name, no_admin_install=False):
    return system_entries.remove_registry_entry(app_name, no_admin_install, registry=winreg)


def remove_shortcut(app_name, desktop=False, no_admin_install=False):
    return system_entries.remove_shortcut(app_name, desktop, no_admin_install, registry=winreg)


def _process_image_name(pid):
    """回傳 pid 對應的執行檔檔名（例如 "explorer.exe"），查不到回傳空字串。

    Restart Manager（見 restart_manager.find_locking_processes()）回傳的
    是使用者友善名稱（explorer.exe 常會顯示成「Windows 檔案總管」之類的
    localized 字串），不能拿來判斷「這是不是 explorer.exe」，要另外用
    pid 查真正的執行檔檔名。
    """
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        output = subprocess.check_output(
            f'tasklist /FI "PID eq {pid}" /NH /FO CSV',
            shell=True, text=True, stderr=subprocess.DEVNULL, creationflags=creationflags,
        )
        first_line = output.strip().splitlines()[0] if output.strip() else ""
        return first_line.split(",")[0].strip('"')
    except Exception:
        return ""


def _kill_processes(processes):
    """逐一 taskkill /f /pid 結束 processes（通常來自
    restart_manager.find_locking_processes()，[(pid, friendly_name), ...]，
    真正是誰鎖住就結束誰，不是無差別地假設一定是 explorer.exe）。

    真實情境：應用程式如果註冊了 Windows 檔案總管殼層擴充功能（Shell
    Extension DLL，例如右鍵選單擴充），只要 explorer.exe 還活著就會把這支
    DLL 常駐載入在自己的記憶體裡，刪除/覆寫會被擋下來——這跟系統管理員
    權限完全無關，重試也沒用，只能真的把持有它的處理程序關掉。舊做法是
    寫死 taskkill /im explorer.exe，只涵蓋這一種情境；改用
    restart_manager（包 Windows Restart Manager API）實際偵測，涵蓋任何
    真正鎖住這些檔案的進程，不只是 explorer.exe。這是打包時永遠內建開啟
    的能力（見 install_manifest.json 的 restart_explorer_on_update），
    最終要不要真的結束偵測到的進程還是由使用者在確認對話框裡決定。

    回傳被結束的進程清單 [(pid, image_name), ...]（image_name 用
    _process_image_name() 另外查真正的執行檔檔名，不是 Restart Manager
    回傳的使用者友善名稱），供呼叫端決定後續要不要重啟：目前只有
    explorer.exe 值得自動重啟（殺掉它會讓使用者的桌面/工作列消失），其他
    被偵測到鎖定檔案的進程通常是使用者自己的應用程式（或其他跟這次解除
    安裝無關的第三方程式），不該自動幫使用者重新開啟。
    """
    killed = []
    for pid, _friendly_name in processes:
        image_name = _process_image_name(pid)
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.run(
                ["taskkill", "/f", "/pid", str(pid)],
                creationflags=creationflags, timeout=10,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            killed.append((pid, image_name))
        except Exception:
            pass
    if killed:
        time.sleep(1)  # 給處理程序終止、控制代碼釋放一點緩衝時間
    return killed


def _wants_lock_release(manifest, argv):
    """決定這次解除安裝『設定上』要不要偵測並結束鎖定檔案的進程——不分
    互動或無人值守，只回答「這個應用程式有沒有勾選需要」，實際互動情境下
    要不要真的執行、要不要先跟使用者確認，是呼叫端（main()）自己的事。

    優先看呼叫端有沒有透過 --restart-explorer 命令列旗標明確指定——這是
    installer_core.py 的 run_upgrade_uninstall()（更新覆蓋安裝情境）會用的
    傳遞方式，帶的是新版本這次的設定，不是這支舊版 uninstall.exe 自己那份
    可能過期的 install_manifest.json。真實抓到的 bug：原本只看 manifest，
    但更新覆蓋安裝呼叫的是舊版本的 uninstall.exe，讀到的是舊版本安裝當下的
    設定，跟使用者這次重新打包的新設定是兩回事，導致行為隨每次安裝嘗試留下
    的 manifest 版本不同而時好時壞。沒帶這個旗標時才退回讀 manifest 自己的設定。

    真實抓到的另一個 bug：這裡原本只要不是 --silent（互動式手動解除安裝）
    就直接回傳 False，理由是「不該無預警把使用者的檔案總管視窗全部關掉」——
    但這麼一來，只要應用程式有鎖定相關的檔案，使用者手動解除安裝永遠不會
    真的把它釋放掉，檔案留在安裝目錄刪不掉（刪除失敗被靜默吞掉，只寫進
    log），下次重新安裝到同一個路徑覆寫這個仍被鎖住的檔案就會失敗，而且
    錯誤訊息顯示成「權限不足」，容易誤導使用者以為要用系統管理員身分重試
    （其實無關，重試也沒用）。現在改成不分互動或無人值守，只要設定上想要
    就回傳 True；互動情境該不該先問過使用者，改由 main() 呼叫
    _confirm_kill_locking_processes() 額外把關。
    """
    return ("--restart-explorer" in argv) or bool(manifest.get("restart_explorer_on_update", False))


def _compute_locking_processes(ctx, argv):
    """偵測目前有哪些程式鎖定了這次要刪除的檔案（見 restart_manager.py）。

    `_wants_lock_release(manifest, argv)` 為 False（打包時沒勾選這個選項，
    也不是被 --restart-explorer 明確要求）時直接回傳空清單，不用真的去
    偵測——維持原本「這個功能是選用的」行為。

    互動式流程（UninstallerAPI.get_locking_process_names()）跟無人值守
    流程（run_silent_uninstall()）共用這個函式；互動式會把結果快取起來，
    問過使用者「要不要結束這些程式」之後才呼叫 _perform_uninstall_steps()，
    無人值守則直接視為同意（維持原本「更新覆蓋安裝不用問」的行為）。
    """
    manifest = ctx["manifest"]
    if not _wants_lock_release(manifest, argv):
        return []

    current_dir = ctx["current_dir"]
    files_to_remove = manifest.get("files")
    self_name = os.path.basename(argv[0])
    is_local_appdata_file = _local_appdata_resolver(manifest)
    local_appdata_dir = manifest.get("local_appdata_dir") or ""

    def _target_path(rel):
        base_dir = local_appdata_dir if (local_appdata_dir and is_local_appdata_file(rel)) else current_dir
        return os.path.join(base_dir, rel)

    if files_to_remove:
        candidate_paths = [_target_path(rel) for rel in files_to_remove if os.path.basename(rel) != self_name]
    else:
        # 找不到安裝清單的舊版遺留情況：沒有明確的檔案清單可以餵給鎖定偵測，
        # 退回掃描整個安裝目錄，盡量維持跟有清單時同等的偵測涵蓋範圍。
        candidate_paths = [
            os.path.join(current_dir, f)
            for f in os.listdir(current_dir)
            if f != self_name and os.path.isfile(os.path.join(current_dir, f))
        ]
    return restart_manager.find_locking_processes(candidate_paths)


def _cli_log_path(argv):
    """解析 /LOG=路徑 命令列參數，讓解除安裝的紀錄檔路徑可以自訂，
    不帶就回傳 None（呼叫端 fallback 回原本的 %TEMP% 路徑）。"""
    for raw_arg in argv[1:]:
        arg = raw_arg.strip()
        if arg.upper().startswith("/LOG="):
            return arg[5:].strip('"')
    return None


def _local_appdata_resolver(manifest):
    """回傳一個函式：給定安裝清單裡的相對路徑，判斷它是不是打包時被指定
    落地到 `%LOCALAPPDATA%\\Programs\\<folder_name>`（而不是主安裝目錄）
    的檔案（見 installer_core.py 的 `local_appdata_files`/
    `_local_appdata_root()`）。manifest 沒有這個欄位（舊版本安裝、或
    這次打包沒用到這個功能）時一律回傳 False，行為維持原樣。
    """
    local_appdata_files = manifest.get("local_appdata_files") or []
    normed = {os.path.normcase(os.path.normpath(f)) for f in local_appdata_files}

    def is_local_appdata_file(rel_path):
        return os.path.normcase(os.path.normpath(rel_path)) in normed

    return is_local_appdata_file


def _cleanup_empty_dirs(root_dir):
    """清掉 root_dir 底下因為刪檔而變空的子目錄（由裡到外），
    root_dir 本身如果也空了就一併刪除。"""
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


def _path_removal_target(manifest, current_dir):
    """算出解除安裝時要從 PATH 移除的目錄字串。

    `path_directory` 是安裝當下 installer_core.py 實際加進 PATH 的目錄：預設
    是整個安裝目錄，但如果開發者打包時指定了 path_target_exe（例如跟主程式
    分開的 CLI 工具），加進 PATH 的其實是那支執行檔所在的子目錄——必須刪
    一模一樣的字串才能真的移除乾淨，刪 install_path 反而會刪不掉。
    沒有這個欄位（舊版本安裝的 manifest，這個功能還不存在時寫入的）就退回
    install_path，維持原本「整個安裝目錄」的行為。
    """
    return manifest.get("path_directory") or manifest.get("install_path", current_dir)


def remove_from_path(install_path, no_admin_install=False):
    system_entries.remove_from_path(install_path, no_admin_install, registry=winreg)


def _load_uninstall_context(argv):
    """讀 sys.argv[0] 所在目錄的 install_manifest.json/installer_config.json，
    算出這次解除安裝需要的基本資訊。main() 的靜默路徑跟互動路徑
    （UninstallerAPI.__init__）共用這個函式，不重複寫兩份。
    """
    current_dir = os.path.dirname(os.path.abspath(argv[0]))
    manifest_path = os.path.join(current_dir, "install_manifest.json")
    config_path = os.path.join(current_dir, "installer_config.json")

    manifest = {}
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

    app_name = manifest.get("app_name")
    if not app_name and os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                app_name = json.load(f).get("app_name")
        except Exception:
            pass
    app_name = app_name or "DefaultApp"

    return {
        "current_dir": current_dir,
        "manifest": manifest,
        "app_name": app_name,
        "main_exe": manifest.get("main_exe", ""),
        # no_admin_install 開啟時整個安裝流程（含解除安裝）完全不要求系統
        # 管理員權限——manifest 找不到這個欄位（舊版本安裝、或找不到清單的
        # 情況）就 fallback 成 False，維持原本一律要求管理員權限的行為。
        "no_admin_install": bool(manifest.get("no_admin_install", False)),
    }


def _write_uninstall_log(log_lines, app_name, argv):
    """安裝目錄即將被整個刪掉（或者只刪自己），log 改寫到 %TEMP%（或使用者
    用 /LOG= 指定的路徑），不寫回安裝目錄底下，因為那個位置接下來可能會
    被清掉。互動/靜默兩條路徑共用。
    """
    custom_log_path = _cli_log_path(argv)
    if custom_log_path:
        try:
            os.makedirs(os.path.dirname(custom_log_path) or ".", exist_ok=True)
            with open(custom_log_path, "w", encoding="utf-8") as f:
                f.write("\n".join(log_lines))
            return
        except Exception as e:
            log_lines.append(f"[警告] 無法寫入指定的紀錄路徑 {custom_log_path}：{e}，改用預設路徑。")
    try:
        # tempfile.gettempdir() 比自己讀 os.environ.get("TEMP", ...) 穩固：
        # 後者只有 TEMP 這個環境變數整個不存在時才會用預設值，存在但是空字串
        # 的情況（實測會在某些提權執行的情境下發生）不會被擋下來，算出來的
        # 路徑會不小心變成相對路徑。
        log_dir = tempfile.gettempdir()
        with open(os.path.join(log_dir, f"{app_name}_uninstall_log.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
    except Exception:
        pass


# 自我刪除（.bat 產生＋重試邏輯，踩過三輪真實 bug）收在 self_delete.py，
# 見該檔案的 schedule_if_needed()——呼叫端只要給 argv/current_dir/
# exe_path/safe_to_remove_whole_dir，要不要排程的前置判斷也收在裡面。


def _perform_uninstall_steps(ctx, locking_processes, kill_locking_processes, log, report_progress=None):
    """實際的解除安裝步驟：登錄表 → 捷徑 → 檔案關聯 → PATH →（視情況結束
    鎖定檔案的程式）→ 清單式刪除。互動流程（UninstallerAPI.run_uninstall()）
    跟無人值守流程（run_silent_uninstall()）共用同一份，差別只在
    kill_locking_processes 從哪裡決定（互動由使用者在確認對話框回答；
    無人值守一律視為同意，維持原本「更新覆蓋安裝不用問」的行為）跟
    report_progress 要不要真的推播進度給前端。

    回傳 safe_to_remove_whole_dir（bool）：True 表示資料夾裡確定只剩自己
    （uninstall.exe），呼叫端可以安全整個清空；False 表示資料夾裡還留有
    其他東西，呼叫端只能刪自己、把資料夾留著。
    """
    app_name = ctx["app_name"]
    manifest = ctx["manifest"]
    current_dir = ctx["current_dir"]
    no_admin_install = ctx["no_admin_install"]

    def progress(percent, message):
        log(message)
        if report_progress:
            report_progress(percent, message)

    progress(10, "正在清理 Windows 註冊表...")
    if remove_registry_entry(app_name, no_admin_install):
        log("已移除解除安裝登錄表項目")

    progress(25, "正在移除捷徑...")
    if manifest.get("start_menu_shortcut"):
        remove_shortcut(app_name, desktop=False, no_admin_install=no_admin_install)
        log("已移除開始功能表捷徑")
    if manifest.get("desktop_shortcut"):
        remove_shortcut(app_name, desktop=True, no_admin_install=no_admin_install)
        log("已移除桌面捷徑")

    file_associations = manifest.get("file_associations") or []
    if file_associations:
        progress(40, "正在移除檔案關聯...")
        file_assoc.unregister(file_associations)
        log(f"已移除檔案關聯: {file_associations}")

    if manifest.get("path_added"):
        progress(50, "正在從環境變數 PATH 移除安裝路徑...")
        remove_from_path(_path_removal_target(manifest, current_dir), no_admin_install)
        log("已從 PATH 移除安裝路徑")

    progress(65, "正在刪除安裝目錄下的檔案...")
    files_to_remove = manifest.get("files")
    self_name = os.path.basename(sys.argv[0])

    safe_to_remove_whole_dir = False
    is_local_appdata_file = _local_appdata_resolver(manifest)
    local_appdata_dir = manifest.get("local_appdata_dir") or ""

    def _target_path(rel):
        base_dir = local_appdata_dir if (local_appdata_dir and is_local_appdata_file(rel)) else current_dir
        return os.path.join(base_dir, rel)

    if kill_locking_processes and locking_processes:
        log("正在結束鎖定安裝檔案的程式，以釋放檔案（例如殼層擴充功能 DLL）...")
        _kill_processes(locking_processes)

    if files_to_remove:
        # 有清單：只刪清單內記錄的檔案，使用者事後自己產生的檔案不會被誤刪。
        # 部分檔案打包時可能被指定落地到 %LOCALAPPDATA%\Programs\<folder_name>
        # （見 installer_core.py 的 local_appdata_files），要從那邊刪，
        # 不是安裝目錄（current_dir）。
        for rel in files_to_remove:
            if os.path.basename(rel) == self_name:
                continue  # 自己交給下面的自我刪除流程處理，執行中無法自刪
            item_path = _target_path(rel)
            try:
                if os.path.exists(item_path):
                    os.remove(item_path)
            except Exception as e:
                log(f"[警告] 無法刪除 {rel}: {e}")

        # 清掉刪空的子目錄（安裝目錄與別位的 local_appdata 目錄分開清）
        for root, dirs, files in os.walk(current_dir, topdown=False):
            for d in dirs:
                dpath = os.path.join(root, d)
                try:
                    if not os.listdir(dpath):
                        os.rmdir(dpath)
                except Exception:
                    pass
        if local_appdata_dir:
            _cleanup_empty_dirs(local_appdata_dir)
        log(f"已依安裝清單刪除 {len(files_to_remove)} 個檔案")

        # 清單刪完之後，看看資料夾裡除了自己還剩什麼——這才是真正決定能不能
        # 整個 rmdir 的依據，不能像原本那樣不管三七二十一直接砍。
        remaining = [item for item in os.listdir(current_dir) if item != self_name]
        if remaining:
            log(
                f"安裝目錄內還有清單之外的 {len(remaining)} 個項目（可能是使用者自行產生的資料），"
                f"保留資料夾，只刪除解除安裝程式本身：{remaining}"
            )
            safe_to_remove_whole_dir = False
        else:
            safe_to_remove_whole_dir = True
    else:
        log("[警告] 找不到安裝清單，改為整個安裝目錄清除")
        for item in os.listdir(current_dir):
            item_path = os.path.join(current_dir, item)
            try:
                if item == self_name:
                    continue
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                else:
                    os.remove(item_path)
            except Exception as e:
                log(f"[警告] 無法刪除 {item}: {e}")
        # 找不到清單這個分支，設計上本來就是要整個清空，維持原行為
        safe_to_remove_whole_dir = True

    progress(95, "正在寫入解除安裝紀錄...")
    return safe_to_remove_whole_dir


def run_silent_uninstall(ctx, argv):
    """command-line 靜默解除安裝：完全不開視窗，給 installer_core.py 的
    run_upgrade_uninstall()（更新覆蓋安裝）跟企業批次部署（登入腳本/MDM/
    群組原則）用。回傳值直接當這支 exe 的 process exit code。

    既定行為（維持修改前的原樣）：不彈確認、偵測到鎖定檔案的程式直接視為
    同意結束（不用像互動式那樣先問過使用者），跑完寫 log、排自我刪除。
    """
    app_name = ctx["app_name"]
    main_exe = ctx["main_exe"]
    log_lines = [f"=== {app_name} 解除安裝紀錄 {datetime.now().isoformat()} ==="]

    def log(msg):
        log_lines.append(msg)

    if is_process_running(main_exe):
        log(f"[錯誤] {main_exe} 正在執行中，中止解除安裝。")
        _write_uninstall_log(log_lines, app_name, argv)
        return 1

    locking_processes = _compute_locking_processes(ctx, argv)
    safe_to_remove_whole_dir = _perform_uninstall_steps(
        ctx, locking_processes, kill_locking_processes=True, log=log, report_progress=None,
    )

    _write_uninstall_log(log_lines, app_name, argv)

    # 更新覆蓋安裝流程呼叫的是「舊版本」的 uninstall.exe，接下來
    # installer_core.py 馬上就要在同一個目錄裡寫入新版本的檔案（包含
    # 覆蓋這支 uninstall.exe 自己）——要不要排背景自我刪除指令的判斷
    # 收在 self_delete.schedule_if_needed() 裡（看 argv 裡有沒有
    # --upgrade），這裡不用再自己先判斷一次。
    self_delete.schedule_if_needed(argv, ctx["current_dir"], argv[0], safe_to_remove_whole_dir)
    return 0


class UninstallerAPI:
    """互動式解除安裝的 pywebview JS API，寫法比照 installer_core.py 的
    InstallerAPI：畫面（ui/uninstall.html）驅動流程，這個 class 只負責
    回答「目前狀態怎樣」跟「執行實際的解除安裝動作」，取代原本一路線性
    執行到底、靠原生 MessageBoxW 中斷的 main()。
    """

    def __init__(self, ctx):
        self.current_dir = ctx["current_dir"]
        self.manifest = ctx["manifest"]
        self.app_name = ctx["app_name"]
        self.main_exe = ctx["main_exe"]
        self.no_admin_install = ctx["no_admin_install"]
        self.ui_language = lang_detect.detect_system_language(SUPPORTED_UI_LANGUAGES, DEFAULT_UI_LANGUAGE)
        self._drag = WindowDragController()
        self._locking_processes = []
        self._safe_to_remove_whole_dir = True
        self._log_lines = [f"=== {self.app_name} 解除安裝紀錄 {datetime.now().isoformat()} ==="]

    def start_drag(self, cursor_x, cursor_y):
        global window
        self._drag.start_drag(window, cursor_x, cursor_y)

    def drag_move(self, cursor_x, cursor_y):
        global window
        self._drag.drag_move(window, cursor_x, cursor_y)

    def end_drag(self):
        self._drag.end_drag()

    def get_app_name(self):
        return self.app_name

    def get_ui_language(self):
        return self.ui_language

    def check_main_exe_running(self):
        return is_process_running(self.main_exe)

    def close_running_main_exe(self):
        """使用者在『偵測到程式正在執行』畫面按下「關閉應用程式並繼續
        解除安裝」時呼叫，寫法比照 installer_core.py 既有的
        close_running_main_exe()：taskkill /f、CREATE_NO_WINDOW、檢查
        returncode 決定回傳值（不是呼叫沒拋例外就一律回傳 True）。
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

    def get_locking_process_names(self):
        """算一次鎖定檔案的程式清單，快取在 self._locking_processes 供
        run_uninstall() 使用（避免使用者已經在畫面上看過一次名單、按了
        是/否之後，run_uninstall() 又重新掃一次、可能得到不一致的結果）。
        """
        ctx = {"current_dir": self.current_dir, "manifest": self.manifest}
        self._locking_processes = _compute_locking_processes(ctx, sys.argv)
        return sorted({name for _pid, name in self._locking_processes if name})

    def _report_progress(self, percent, message):
        global window
        safe_msg = json.dumps(message, ensure_ascii=False)
        try:
            if window:
                window.evaluate_js(f"window.updateUninstallProgress({percent}, {safe_msg})")
        except Exception:
            pass

    def run_uninstall(self, kill_locking_processes):
        ctx = {
            "current_dir": self.current_dir, "manifest": self.manifest,
            "app_name": self.app_name, "main_exe": self.main_exe,
            "no_admin_install": self.no_admin_install,
        }

        def log(msg):
            self._log_lines.append(msg)

        try:
            self._safe_to_remove_whole_dir = _perform_uninstall_steps(
                ctx, self._locking_processes, kill_locking_processes, log,
                report_progress=self._report_progress,
            )
            return {"status": "success"}
        except Exception as e:
            log(f"[錯誤] {e}")
            return {"status": "error", "message": str(e)}

    def finish_and_exit(self):
        """使用者在完成畫面按下「完成」：寫 log、排自我刪除，然後立刻硬結束
        行程（`os._exit()`，不是關閉視窗再等它自然結束）。

        真實抓到的 bug：原本是呼叫 `window.destroy()` 請 WinForms 關閉視窗，
        指望 `webview.start()` 內部的 `Application.Run()` 訊息迴圈自己返回、
        行程自然結束。舊版（純 console，沒有 GUI 框架、沒有 WebView2）
        跑完 `main()` 就是行程結束，這個假設在那個版本上成立；但這支 exe
        現在內嵌了 WebView2 runtime，`Application.Run()`/WebView2 瀏覽器
        行程的收尾不保證會在合理時間內真的返回——只要這個行程還活著，
        它自己的 exe 檔案就會一直被鎖住，背景那個自我刪除的重試迴圈
        （見 `self_delete.py`）再怎麼重試都不會成功，這正是解除
        安裝「完成」了卻永遠刪不掉自己的根本原因。修法：background 自我
        刪除指令一排上去（這一步不依賴視窗或 GUI 框架，獨立行程），就直接
        `os._exit(0)` 讓整個行程在作業系統層級立刻終止，不等待、也不
        依賴任何框架的優雅關閉流程。

        真實抓到的 bug（第二輪）：改成 `os._exit(0)` 之後，行程本身確實立刻
        終止，但使用者實際感受到的是「按下按鈕後畫面卡住一兩秒才消失」——
        WebView2 是硬體加速合成的畫面，行程被終止的當下，Windows 桌面合成
        器（DWM）不一定會立刻回收那個視窗殘留的最後一幀畫面，視覺上就會
        像是「沒反應、過一陣子才突然消失」，容易讓使用者誤以為程式卡住。
        修法：先呼叫 `window.hide()`（WinForms 的 `Form.Hide()`，單純把視窗
        設成不可見，不牽涉 WebView2 收尾，執行很快）讓視窗立刻從畫面上消失、
        給使用者「按下去馬上有反應」的回饋，再繼續原本的收尾動作（寫 log、
        排自我刪除）跟 `os._exit(0)`——即使行程真正終止還要花一兩秒，使用者
        已經看不到那個視窗了。
        """
        global window
        try:
            window.hide()
        except Exception:
            pass
        _write_uninstall_log(self._log_lines, self.app_name, sys.argv)
        self_delete.schedule_if_needed(sys.argv, self.current_dir, sys.argv[0], self._safe_to_remove_whole_dir)
        os._exit(0)

    def cancel(self):
        """使用者在確認畫面按下 X 關閉，或在「主程式正在執行中」畫面按下
        「了解」：不做任何刪除動作。跟 finish_and_exit() 一樣，先
        `window.hide()` 讓視窗立刻消失，再 `os._exit(0)` 硬結束行程，理由
        同 finish_and_exit() 的說明（避免 WebView2 畫面收尾的視覺延遲讓
        使用者誤以為卡住）。"""
        global window
        try:
            window.hide()
        except Exception:
            pass
        os._exit(0)


def main():
    silent = "--silent" in sys.argv
    ctx = _load_uninstall_context(sys.argv)

    if not ctx["no_admin_install"] and not is_admin():
        # webview 視窗還沒建立，用原生 MessageBoxW 是既有的接受方案
        # （跟 installer_core.py 單一實例鎖對話框同樣的例外）。
        _lang = lang_detect.detect_system_language(SUPPORTED_UI_LANGUAGES, DEFAULT_UI_LANGUAGE)
        if _lang == "en":
            _title, _msg = "Uninstaller", "Please run this program as an administrator."
        else:
            _title, _msg = "解除安裝助手", "請以系統管理員身分執行此程式。"
        ctypes.windll.user32.MessageBoxW(0, _msg, _title, 0x10)
        return

    if silent:
        sys.exit(run_silent_uninstall(ctx, sys.argv))

    # 讓 Windows 在非 100% 縮放比例下不要把整個視窗畫面當點陣圖拉伸，避免
    # 版面尺寸跟視窗實際像素尺寸對不上（跟 installer_core.py 同一個坑，
    # 這裡沿用完全一樣的宣告方式）。
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    global window
    api = UninstallerAPI(ctx)
    html_path = get_resource_path(os.path.join("ui", "uninstall.html"))
    # 視窗尺寸/版面直接沿用 installer_core.py 主安裝畫面的規格（見規格文件
    # §8.29），不要為解除安裝助手另外發明一套尺寸標準。
    window = webview.create_window(
        title="解除安裝", url=html_path, js_api=api,
        width=600, height=420, resizable=False, frameless=True, easy_drag=False,
    )
    webview.start(debug=False)
    sys.exit(0)


if __name__ == "__main__":
    main()