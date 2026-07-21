"""
splash.py
---------
統一的啟動載入畫面介面。

改版紀錄（取代原本 PyInstaller --splash 靜態圖片的做法）：
  - 原本 exe 型態用 pyi_splash（PyInstaller bootloader 內建功能）顯示一張
    靜態圖片，優點是能蓋住 onefile 解壓縮的空窗期，缺點是只能放圖片，
    做不出「真的在轉」的進度條。
  - 現在統一改用「系統上真正的 python 直譯器」去執行一支極簡的獨立腳本
    splash_helper.py，開出一個貨真價實的 Tkinter 視窗，跑一個跟實際進度
    無關、持續轉動的進度條，直到主視窗準備好被 close() 關閉。
  - 不管目前是 .py 直接執行還是打包成 onefile exe，都是同一套邏輯：因為
    用的是系統 python 直譯器，不是重新呼叫（可能包了 PyQt5/pythonnet 等
    重量級依賴、很肥大的）主程式 exe 本身，不會有 onefile 重新解壓縮拖慢
    速度的問題。這個工具本身也已經要求機器上要有能動的 python + pyinstaller
    （builder.py 執行時會呼叫 pyinstaller 編譯安裝檔），所以「系統上有 python
    可用」這個前提本來就成立，不是新增的額外要求。
  - 唯一的取捨：exe 剛雙擊、bootloader 正在解壓縮自己的那一兩秒空窗期，
    這個做法沒辦法覆蓋（因為 splash 視窗要等 Python 程式碼開始執行、
    呼叫 show() 之後才會跳出來）。如果要蓋住那段空窗期，只能退回原本
    pyi_splash 的靜態圖片做法，兩者無法兼得，這次是照需求選擇「真的會轉的
    視窗」而不是「時機更早的靜態圖片」。

呼叫端用法：
    import splash
    splash.set_dpi_aware()       # 建立任何視窗之前呼叫
    splash.show("正在啟動...")   # 主程式一開始、還沒 import webview 等重量級套件前呼叫
    splash.close()                # 主視窗準備好、要顯示時呼叫
"""

import sys
import os
import shutil
import subprocess
import tempfile

_process = None


def set_dpi_aware():
    """讓行程對 Windows 顯示縮放比例（DPI）有感知，避免視窗被系統以點陣圖方式拉伸造成模糊。"""
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE（改用系統層級，跟 pywebview 原生視窗拖曳交接的相容性較好，避免拖曳瞬間跳動）
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()  # 較舊版 Windows 的退回方案
    except Exception:
        pass  # 非 Windows 環境或呼叫失敗都不應該讓程式掛掉


def _get_resource_path(relative_path):
    """取得 splash_helper.py 的實際路徑，相容 .py 直接執行與 frozen exe 兩種型態。"""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


def _resolve_helper_script():
    """回傳可以直接拿去執行的 splash_helper.py 路徑。

    .py 直接執行：splash_helper.py 跟 splash.py 放在同一層，直接用。
    frozen exe：splash_helper.py 被內嵌在 exe 裡（build_config_tool.py 打包時
    加了 --add-data=splash_helper.py;.），每次執行都覆蓋寫一份到系統暫存目錄。

    修正紀錄：原本「只在 TEMP 沒有這個檔案時才複製」，代表升級到新版
    InstallerBuilder.exe 之後，TEMP 裡卡著的舊版 splash_helper.py 永遠不會被換掉，
    會一直執行到過期的版本。這個檔案很小，一律覆蓋的 I/O 成本可以忽略，
    不值得為了省這點效能冒著執行到舊程式碼的風險。
    """
    if not hasattr(sys, "_MEIPASS"):
        return _get_resource_path("splash_helper.py")

    dest = os.path.join(tempfile.gettempdir(), "installer_builder_splash_helper.py")
    try:
        shutil.copy2(_get_resource_path("splash_helper.py"), dest)
        return dest
    except Exception:
        return None


def show(text="正在載入中..."):
    """啟動載入畫面。應在程式最開頭、還沒 import webview 等重量級套件前呼叫。

    修正紀錄：原本用 shutil.which("python") 優先找到的通常是 python.exe（主控台版本），
    透過 subprocess.Popen 呼叫它會讓 Windows 短暫跳出一個命令提示字元視窗再消失
    （即使程式本身只是開 Tkinter 視窗）。改成優先找 pythonw.exe（無主控台版本），
    並且不管找到哪一個都加上 CREATE_NO_WINDOW，雙重保險徹底不跳視窗。
    """
    global _process

    try:
        python_exe = shutil.which("pythonw") or shutil.which("python") or shutil.which("python3")
        helper_script = _resolve_helper_script()

        if python_exe is None:
            print("[提示] 找不到系統上可用的 python 直譯器，略過載入畫面。")
            return
        if not helper_script or not os.path.exists(helper_script):
            print("[提示] 找不到 splash_helper.py，略過載入畫面。")
            return

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        _process = subprocess.Popen([python_exe, helper_script, text], creationflags=creationflags)
    except Exception as e:
        print(f"[提示] 無法顯示載入畫面（可忽略）: {e}")
        _process = None


def close():
    """關閉載入畫面，通常在主視窗內容載入完成、即將顯示時呼叫。"""
    global _process
    if _process is not None:
        try:
            _process.terminate()
        except Exception:
            pass
        _process = None