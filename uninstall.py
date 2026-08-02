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

新增（打包時選填）：restart_explorer_on_update。有些應用程式會在檔案總管
的殼層擴充功能（Shell Extension DLL）或其他背景進程裡持有安裝目錄下的
檔案控制代碼，更新覆蓋安裝或解除安裝時刪不掉、也跟系統管理員權限無關。
勾選這個選項後，解除安裝在刪除檔案前會用 Windows 官方的 Restart Manager
API（restart_manager.py，跟 Windows Installer/PowerToys File Locksmith
判斷「這個檔案被誰鎖住」用的是同一套機制）實際偵測是哪些進程鎖住了要刪除
的檔案，逐一結束那些進程（不是無差別假設一定是 explorer.exe），刪除完畢
後如果結束掉的進程裡有 explorer.exe 才會自動重啟它，見
_kill_processes() / _restart_explorer()。無人值守（--silent）情境直接
套用；互動式（使用者手動解除安裝）額外跳確認對話框，列出實際偵測到的
進程名稱，取得同意才真的結束，見 _confirm_kill_locking_processes()。

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
_should_schedule_self_delete() 判斷為 True 時完全不排這段背景指令——
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
from datetime import datetime
import file_assoc
import restart_manager


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


def remove_registry_entry(app_name):
    reg_path = f"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{app_name}"
    try:
        winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, reg_path)
        return True
    except Exception:
        return False


def remove_shortcut(app_name, desktop=False):
    if desktop:
        base = "C:\\Users\\Public\\Desktop"
    else:
        base = os.path.join(
            os.environ.get("ProgramData", "C:\\ProgramData"),
            "Microsoft", "Windows", "Start Menu", "Programs",
        )
    path = os.path.join(base, f"{app_name}.lnk")
    try:
        if os.path.exists(path):
            os.remove(path)
            return True
    except Exception:
        pass
    return False


def _restart_explorer():
    """重新啟動 explorer.exe，搭配 _kill_locking_processes() 使用。"""
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(["explorer.exe"], creationflags=creationflags)
    except Exception:
        pass


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
    真正鎖住這些檔案的進程，不只是 explorer.exe。這是打包時的選填選項
    （見 install_manifest.json 的 restart_explorer_on_update），預設不啟用。

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


def _confirm_kill_locking_processes(app_name, processes):
    """互動式解除安裝在真的結束鎖定進程前，跳出確認對話框取得使用者同意。

    更新覆蓋安裝（無人值守）不需要問，因為那是使用者先前安裝新版本時就
    已經同意的流程。但手動解除安裝是使用者當下主動的操作，強制結束其他
    正在執行的程式是有感的副作用，選「否」的話解除安裝仍會繼續，只是
    被鎖住的檔案可能刪不掉。

    列出 restart_manager 實際偵測到的進程名稱，而不是像舊版一律寫死
    「檔案總管」——現在是真的偵測鎖定者是誰，不再假設一定是 explorer.exe。
    processes 為空（沒偵測到任何鎖定）時直接回傳 False，不用打擾使用者。
    """
    if not processes:
        return False
    names = "、".join(sorted({name for _pid, name in processes if name})) or "未知程式"
    MB_YESNO, IDYES, MB_ICONWARNING = 4, 6, 48
    result = ctypes.windll.user32.MessageBoxW(
        0,
        f"偵測到「{app_name}」的部分檔案目前被下列程式鎖定：{names}。\n"
        f"需要先結束這些程式才能完整移除相關檔案，是否繼續？\n\n"
        f"（選「否」解除安裝仍會繼續，但可能有檔案因為還在使用中而無法刪除。）",
        "解除安裝助手", MB_YESNO | MB_ICONWARNING,
    )
    return result == IDYES


def _is_upgrade_call(argv):
    """是否由 installer_core.py 的 run_upgrade_uninstall()（更新覆蓋安裝流程）
    呼叫，而不是一般的（互動式或企業批次靜默）解除安裝。見
    _should_schedule_self_delete() 的說明，這個旗標決定要不要完全跳過
    main() 尾端那段背景自我刪除指令。
    """
    return "--upgrade" in argv


def _should_schedule_self_delete(is_upgrade):
    """決定要不要排出 main() 尾端那段延遲執行的背景自我刪除指令
    （`ping` 製造延遲 + `del` 刪除自己的 exe + 視情況 `rmdir /s /q` 整個
    安裝目錄）。

    真實抓到的 bug：這段指令是 fire-and-forget（`subprocess.Popen()`
    呼叫完立刻回傳，不等待），`installer_core.py` 的
    `run_upgrade_uninstall()` 是用 `subprocess.run()` **同步**呼叫這支
    舊版 uninstall.exe，行程一結束就繼續往下跑，這時候背景那個延遲約
    1 秒後才會真正執行的 `rmdir /s /q` 根本還沒發生。新版本安裝流程
    緊接著就會開始把檔案複製進同一個目錄——如果複製時間跨過那個延遲
    視窗，背景指令觸發時會把「整個資料夾」（含已經複製好的新檔案）
    砍掉，導致安裝程式回報成功、但實際檔案沒有複製完整，且只發生在
    更新覆蓋安裝（唯一會同時存在「舊版本背景自刪」跟「新版本複製檔案」
    競爭同一個資料夾的情境）。

    修法：`is_upgrade`（呼叫端帶了 `--upgrade` 旗標）為真時，完全不排
    這段背景指令——這支舊版 uninstall.exe 的行程本身已經確定執行完畢
    即將結束，它自己的 exe 檔案已經沒有任何行程占用，不需要靠延遲的
    背景指令才能刪除；`installer_core.py` 稍後本來就會把新版本自己的
    `uninstall.exe` 複製到同一個路徑蓋過去，資料夾本身也會被新版本
    重用，不需要也不該被刪除或重建。一般手動雙擊解除安裝、或不是被
    更新流程觸發的純靜默解除安裝，`is_upgrade` 是 False，行為不變。
    """
    return not is_upgrade


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


def remove_from_path(install_path):
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            0, winreg.KEY_ALL_ACCESS,
        )
        current, reg_type = winreg.QueryValueEx(key, "Path")
        parts = [p for p in current.split(";") if p and os.path.normcase(p) != os.path.normcase(install_path)]
        winreg.SetValueEx(key, "Path", 0, reg_type, ";".join(parts))
        winreg.CloseKey(key)

        HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG = 0xFFFF, 0x1A, 0x0002
        result = ctypes.c_long()
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", SMTO_ABORTIFHUNG, 5000, ctypes.byref(result)
        )
    except Exception:
        pass


def main():
    silent = "--silent" in sys.argv
    is_upgrade = _is_upgrade_call(sys.argv)

    if not is_admin():
        print("[錯誤] 請以系統管理員身分執行此程式。")
        if not silent:
            os.system("pause")
        return

    current_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
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
    main_exe = manifest.get("main_exe", "")

    if not silent:
        MB_YESNO, IDYES, MB_ICONQUESTION = 4, 6, 32
        confirm = ctypes.windll.user32.MessageBoxW(
            0, f"您確定要刪除「{app_name}」及其所有組件嗎？",
            "解除安裝助手", MB_YESNO | MB_ICONQUESTION,
        )
        if confirm != IDYES:
            print("[資訊] 使用者已取消解除安裝。")
            return

    # 主程式執行中偵測：避免刪除一半被鎖住的檔案卡住
    if is_process_running(main_exe):
        if not silent:
            ctypes.windll.user32.MessageBoxW(
                0, f"「{app_name}」目前正在執行中，請先關閉程式後再解除安裝。",
                "解除安裝助手", 0x10,
            )
        print(f"[錯誤] {main_exe} 正在執行中，中止解除安裝。")
        return

    log_lines = [f"=== {app_name} 解除安裝紀錄 {datetime.now().isoformat()} ==="]

    print("[步驟 1] 正在清理 Windows 註冊表...")
    if remove_registry_entry(app_name):
        log_lines.append("已移除解除安裝登錄表項目")

    print("[步驟 2] 正在移除捷徑...")
    if manifest.get("start_menu_shortcut"):
        remove_shortcut(app_name, desktop=False)
        log_lines.append("已移除開始功能表捷徑")
    if manifest.get("desktop_shortcut"):
        remove_shortcut(app_name, desktop=True)
        log_lines.append("已移除桌面捷徑")

    file_associations = manifest.get("file_associations") or []
    if file_associations:
        print("[步驟 3] 正在移除檔案關聯...")
        file_assoc.unregister(file_associations)
        log_lines.append(f"已移除檔案關聯: {file_associations}")

    if manifest.get("path_added"):
        print("[步驟 4] 正在從環境變數 PATH 移除安裝路徑...")
        remove_from_path(_path_removal_target(manifest, current_dir))
        log_lines.append("已從 PATH 移除安裝路徑")

    print("[步驟 5] 正在刪除安裝目錄下的檔案...")
    files_to_remove = manifest.get("files")
    self_name = os.path.basename(sys.argv[0])

    # 這個旗標決定最後一步能不能連整個資料夾一起刪：
    # True = 資料夾裡確定只剩自己（uninstall.exe），可以安全整個清空；
    # False = 資料夾裡還留有其他東西，只能刪自己、把資料夾留著。
    safe_to_remove_whole_dir = False

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

    # 打包時勾選了這個選項，才需要偵測並結束鎖定檔案的進程；無人值守
    # （--silent，來自更新覆蓋流程或企業批次部署）偵測到就直接結束，
    # 互動式（使用者手動解除安裝）額外跳確認對話框，列出實際偵測到的
    # 進程名稱，取得同意後才真的結束，避免無預警強制關閉使用者正在用的
    # 其他程式。
    killed_processes = []
    if _wants_lock_release(manifest, sys.argv):
        locking_processes = restart_manager.find_locking_processes(candidate_paths)
        proceed = silent or _confirm_kill_locking_processes(app_name, locking_processes)
        if locking_processes and proceed:
            print("[提示] 正在結束鎖定安裝檔案的程式，以釋放檔案（例如殼層擴充功能 DLL）...")
            killed_processes = _kill_processes(locking_processes)

    try:
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
                    log_lines.append(f"[警告] 無法刪除 {rel}: {e}")

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
            log_lines.append(f"已依安裝清單刪除 {len(files_to_remove)} 個檔案")

            # 清單刪完之後，看看資料夾裡除了自己還剩什麼——這才是真正決定能不能
            # 整個 rmdir 的依據，不能像原本那樣不管三七二十一直接砍。
            remaining = [item for item in os.listdir(current_dir) if item != self_name]
            if remaining:
                log_lines.append(
                    f"安裝目錄內還有清單之外的 {len(remaining)} 個項目（可能是使用者自行產生的資料），"
                    f"保留資料夾，只刪除解除安裝程式本身：{remaining}"
                )
                safe_to_remove_whole_dir = False
            else:
                safe_to_remove_whole_dir = True
        else:
            print("[警告] 找不到安裝清單，將移除整個安裝目錄（含目錄下所有檔案）。")
            log_lines.append("[警告] 找不到安裝清單，改為整個安裝目錄清除")
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
                    log_lines.append(f"[警告] 無法刪除 {item}: {e}")
            # 找不到清單這個分支，設計上本來就是要整個清空，維持原行為
            safe_to_remove_whole_dir = True
    finally:
        # 只有真的結束掉的進程裡剛好有 explorer.exe，才自動重啟它——殺掉它
        # 會讓使用者的桌面/工作列消失；其他被偵測到鎖定檔案而結束掉的進程
        # 通常是使用者自己的應用程式，不該自動幫使用者重新開啟。
        if any(image_name.lower() == "explorer.exe" for _pid, image_name in killed_processes):
            _restart_explorer()

    # 安裝目錄即將被整個刪掉（或者只刪自己），log 改寫到 %TEMP%，
    # 不寫回安裝目錄底下，因為那個位置接下來可能會被清掉。
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

    if not _should_schedule_self_delete(is_upgrade):
        # 更新覆蓋安裝流程呼叫的是「舊版本」的 uninstall.exe，接下來
        # installer_core.py 馬上就要在同一個目錄裡寫入新版本的檔案
        # （包含覆蓋這支 uninstall.exe 自己）——不排背景自我刪除指令，
        # 見 _should_schedule_self_delete() 的完整說明。
        print("\n" + "=" * 40)
        print("解除安裝完成（更新覆蓋安裝流程），交由新版本接手安裝。")
        print("=" * 40)
        return

    print("\n" + "=" * 40)
    print("解除安裝完成！")
    if safe_to_remove_whole_dir:
        print("提示：本視窗即將關閉，並將自動刪除本助手檔案與所屬資料夾。")
    else:
        print("提示：本視窗即將關閉並刪除本助手檔案；安裝目錄內還有其他檔案，資料夾會保留。")
    print("=" * 40)

    if not silent:
        time.sleep(1.5)

    # 自我刪除：先明確 cd /d 切到安裝目錄，確保刪的一定是安裝目錄本身，
    # 而不是 cmd.exe 繼承來的、可能不確定的工作目錄。
    # 只有確認資料夾裡真的清空了（safe_to_remove_whole_dir）才會連資料夾一起
    # rmdir；還有剩東西的話，只刪自己，資料夾跟裡面剩下的內容保留給使用者。
    exe_path = sys.argv[0]
    if safe_to_remove_whole_dir:
        cmd_command = (
            f'cd /d "{current_dir}" && '
            f'ping 127.0.0.1 -n 2 > nul & '
            f'del /f /q "{exe_path}" & '
            f'cd .. & '
            f'rmdir /s /q "{current_dir}"'
        )
    else:
        cmd_command = (
            f'cd /d "{current_dir}" && '
            f'ping 127.0.0.1 -n 2 > nul & '
            f'del /f /q "{exe_path}"'
        )
    subprocess.Popen(
        cmd_command,
        shell=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()