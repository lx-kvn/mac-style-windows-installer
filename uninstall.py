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
"""

import os
import sys
import winreg
import shutil
import json
import ctypes
import time
import subprocess
from datetime import datetime
import file_assoc


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
        remove_from_path(manifest.get("install_path", current_dir))
        log_lines.append("已從 PATH 移除安裝路徑")

    print("[步驟 5] 正在刪除安裝目錄下的檔案...")
    files_to_remove = manifest.get("files")
    self_name = os.path.basename(sys.argv[0])

    # 這個旗標決定最後一步能不能連整個資料夾一起刪：
    # True = 資料夾裡確定只剩自己（uninstall.exe），可以安全整個清空；
    # False = 資料夾裡還留有其他東西，只能刪自己、把資料夾留著。
    safe_to_remove_whole_dir = False

    if files_to_remove:
        # 有清單：只刪清單內記錄的檔案，使用者事後自己產生的檔案不會被誤刪
        for rel in files_to_remove:
            if os.path.basename(rel) == self_name:
                continue  # 自己交給下面的自我刪除流程處理，執行中無法自刪
            item_path = os.path.join(current_dir, rel)
            try:
                if os.path.exists(item_path):
                    os.remove(item_path)
            except Exception as e:
                log_lines.append(f"[警告] 無法刪除 {rel}: {e}")

        # 清掉刪空的子目錄
        for root, dirs, files in os.walk(current_dir, topdown=False):
            for d in dirs:
                dpath = os.path.join(root, d)
                try:
                    if not os.listdir(dpath):
                        os.rmdir(dpath)
                except Exception:
                    pass
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

    # 安裝目錄即將被整個刪掉（或者只刪自己），log 改寫到 %TEMP%，
    # 不寫回安裝目錄底下，因為那個位置接下來可能會被清掉。
    try:
        log_dir = os.environ.get("TEMP", current_dir)
        with open(os.path.join(log_dir, f"{app_name}_uninstall_log.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
    except Exception:
        pass

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